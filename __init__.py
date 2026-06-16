# SPDX-License-Identifier: GPL-3.0-or-later
bl_info = {
    "name": "Exact Radius",
    "author": "Patrick",
    "version": (1, 7, 0),
    "blender": (4, 2, 0),
    "location": "Edit Mode > Vertex Menu > Exact Radius (default Alt+R)",
    "description": (
        "Make a selected ring of vertices a perfect circle of an exact radius — "
        "at any orientation, for full circles, holes and partial arcs."
    ),
    "category": "Mesh",
}

import bpy
import bmesh
import ast
import operator as _operator
import numpy as np
import rna_keymap_ui
from mathutils import Vector
from bpy.props import FloatProperty, EnumProperty

# A selection is rejected as "not a circle" beyond these limits (see _circle_error)
PLANARITY_MAX = 0.25    # how far out of a single plane the points may sit
RESIDUAL_MAX = 0.20     # how far from a perfect circle the points may sit (rel.)

# Tiny safe arithmetic evaluator for the modal entry, so the user can type a
# math expression like "20/2" (diameter -> radius). Only numbers and + - * / and
# parentheses — no names, calls or attributes, so eval() is never reached.
_MATH_OPS = {
    ast.Add: _operator.add, ast.Sub: _operator.sub,
    ast.Mult: _operator.mul, ast.Div: _operator.truediv,
    ast.USub: _operator.neg, ast.UAdd: _operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _MATH_OPS:
        return _MATH_OPS[type(node.op)](_eval_node(node.left),
                                        _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _MATH_OPS:
        return _MATH_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported expression")


def _safe_eval(expr):
    """Evaluate a tiny arithmetic expression. Returns float or None."""
    try:
        return float(_eval_node(ast.parse(expr, mode='eval').body))
    except Exception:
        return None


def _selected_verts(bm):
    return [v for v in bm.verts if v.select]


def _local_cursor(context, obj):
    """3D cursor in the object's local space (safe against degenerate matrices)."""
    try:
        inv = obj.matrix_world.inverted()
    except ValueError:
        inv = obj.matrix_world
    return inv @ context.scene.cursor.location


def _fit_circle(verts):
    """Fit a plane AND a circle to the selected verts.

    Returns (center: Vector, normal: Vector, radius: float, rel_residual: float,
    planarity: float) or None if there are < 3 verts / the fit is degenerate.

    The circle center is a least-squares fit (Kasa), not the centroid — so it is
    the true center even for a partial arc (a quarter circle), and robust to
    uneven vertex spacing.
    """
    if len(verts) < 3:
        return None
    pts = np.array([v.co[:] for v in verts], dtype=float)
    c0 = pts.mean(axis=0)
    Q = pts - c0
    try:
        _, s, vt = np.linalg.svd(Q, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    planarity = float(s[2] / (s[0] + 1e-12))
    e1, e2, normal = Vector(vt[0]), Vector(vt[1]), Vector(vt[2])
    # project the points onto the plane's 2D basis (e1, e2)
    u = Q @ vt[0]
    v = Q @ vt[1]
    # algebraic circle fit: minimize sum((u-a)^2 + (v-b)^2 - R^2)^2
    a_mat = np.column_stack([2.0 * u, 2.0 * v, np.ones_like(u)])
    b_vec = u * u + v * v
    try:
        sol, *_ = np.linalg.lstsq(a_mat, b_vec, rcond=None)
    except np.linalg.LinAlgError:
        return None
    ca, cb, cc = sol
    r2 = cc + ca * ca + cb * cb
    if r2 <= 1e-12:
        return None
    radius = float(np.sqrt(r2))
    dist = np.sqrt((u - ca) ** 2 + (v - cb) ** 2)
    rel_residual = float(np.sqrt(np.mean((dist - radius) ** 2)) / radius)
    center = Vector(c0) + float(ca) * e1 + float(cb) * e2
    return center, normal, radius, rel_residual, planarity


def _circle_error(verts, fit):
    """Human-readable reason the selection is not a usable circle, or None."""
    if len(verts) < 3:
        return "Select at least 3 vertices forming a circle or arc"
    if fit is None:
        return "Selection is not a circle — select a ring of vertices"
    _, _, _radius, rel_residual, planarity = fit
    if planarity > PLANARITY_MAX:
        return "Selection is not flat — select a single ring / circle"
    if rel_residual > RESIDUAL_MAX:
        return "Selection is not a circle — select a ring or arc of vertices"
    return None


class MESH_OT_exact_radius(bpy.types.Operator):
    """Make the selected ring of vertices a perfect circle of an exact radius.

    Fits the selection's own plane and circle center, then sets every vertex to
    the target radius around that center. Works at any orientation and for
    partial arcs (e.g. a quarter circle). Non-destructive, no scaling/applying.
    """
    bl_idname = "mesh.exact_radius"
    bl_label = "Exact Radius"
    bl_options = {'REGISTER', 'UNDO'}

    radius: FloatProperty(
        name="Radius",
        description="Target radius in object/local units",
        default=1.0,
        min=0.0,
        soft_max=1000.0,
        unit='LENGTH',
        precision=4,
    )
    center_mode: EnumProperty(
        name="Center",
        items=[
            ('AUTO', "Auto",
             "Fit the circle center automatically (full circles and arcs)"),
            ('CURSOR', "3D Cursor",
             "Use the 3D cursor as the center (in local coordinates)"),
        ],
        default='AUTO',
    )

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return obj is not None and obj.type == 'MESH'

    def _set_header(self, context):
        if self._typed:
            val = _safe_eval(self._typed)
            shown = f"{self._typed} = {val:.4g}" if val is not None else f"{self._typed} …"
        else:
            shown = f"{self._current:.4g} (current)"
        txt = (f"Exact Radius: {shown}    "
               "[type a value or math, e.g. 20/2 · Enter = apply · Esc = cancel]")
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.header_text_set(txt)

    def _clear_header(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.header_text_set(None)

    def invoke(self, context, event):
        # validate first so a bad selection errors before the modal starts
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        sel = _selected_verts(bm)
        fit = _fit_circle(sel)
        err = _circle_error(sel, fit)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        # pre-fill with the fitted radius
        self._current = round(fit[2], 4)
        self.radius = self._current
        self._typed = ""
        self._set_header(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER'}:
            return {'PASS_THROUGH'}
        if event.value != 'PRESS':
            return {'RUNNING_MODAL'}

        t = event.type
        if t in {'RET', 'NUMPAD_ENTER'}:
            val = _safe_eval(self._typed) if self._typed else self._current
            self.radius = max(0.0, val) if val is not None else self._current
            self._clear_header(context)
            return self.execute(context)
        if t in {'ESC', 'RIGHTMOUSE'}:
            self._clear_header(context)
            return {'CANCELLED'}
        if t == 'BACK_SPACE':
            self._typed = self._typed[:-1]
            self._set_header(context)
            return {'RUNNING_MODAL'}

        # Everything else via the produced character (layout-independent — so
        # "/" works on a German keyboard too, where it is Shift+7).
        ch = event.unicode
        if ch:
            if ch == ',':                       # comma -> decimal point
                self._typed += '.'
            elif ch in '0123456789.+-*/() ':    # number or math expression
                self._typed += ch
        self._set_header(context)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        sel = _selected_verts(bm)
        fit = _fit_circle(sel)
        err = _circle_error(sel, fit)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        center, normal, _r, _rel, _pl = fit
        if self.center_mode == 'CURSOR':
            center = _local_cursor(context, obj)
        moved = 0
        for v in sel:
            d = v.co - center
            radial = d - d.dot(normal) * normal   # flatten onto the circle plane
            rl = radial.length
            if rl > 1e-9:
                v.co = center + radial * (self.radius / rl)
                moved += 1
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"{moved} verts set to radius {self.radius:.4g}")
        return {'FINISHED'}


def _menu(self, context):
    self.layout.operator(MESH_OT_exact_radius.bl_idname, icon='MESH_CIRCLE')


# --- Keymap: one user-editable shortcut in the addon keyconfig ---
# Registered in the addon keyconfig so the preferences can show it as an
# editable hotkey widget (rna_keymap_ui.draw_kmi) — the user can rebind it to
# any combination they like, or disable it. Default: Alt+R in Edit Mode.
addon_keymaps = []


def register_keymap():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
    kmi = km.keymap_items.new(MESH_OT_exact_radius.bl_idname, 'R', 'PRESS', alt=True)
    addon_keymaps.append((km, kmi))


def unregister_keymap():
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()


class EXACTRADIUS_AP_prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Make a selected ring of vertices a perfect circle",
                  icon='MESH_CIRCLE')
        box.label(text="Exact radius, at any orientation. There is no pop-up "
                       "window — it all happens right on the shortcut.")

        box = layout.box()
        box.label(text="How to use", icon='INFO')
        col = box.column(align=True)
        col.label(text="1.   In Edit Mode, select a ring of vertices")
        col.label(text="       (a full circle, a hole, or part of one — an arc)")
        col.label(text="2.   Press the shortcut   (or  Vertex menu > Exact Radius)")
        col.label(text="3.   Type the radius, press Enter — done")
        col.separator()
        col.label(text="Good to know:")
        col.label(text="       20/2     math works  (e.g. turn a diameter into a radius)")
        col.label(text="       tilted / rotated circles just work")
        col.label(text="       partial arcs work too  (the center is fitted)")
        col.label(text="       Esc  cancels        F9  afterwards to set the center")
        col.label(text="       a non-circle (whole face / mesh) shows an error")

        box = layout.box()
        box.label(text="Shortcut", icon='PREFERENCES')
        box.label(text="Click the key field and press your own combo · "
                       "uncheck the box to disable")
        wm = context.window_manager
        kc = wm.keyconfigs.addon
        km = kc.keymaps.get('Mesh') if kc else None
        drawn = False
        if km:
            for kmi in km.keymap_items:
                if kmi.idname == MESH_OT_exact_radius.bl_idname:
                    box.context_pointer_set("keymap", km)
                    rna_keymap_ui.draw_kmi([], kc, km, kmi, box, 0)
                    drawn = True
                    break
        if not drawn:
            box.label(text="Restart Blender to edit the shortcut.", icon='ERROR')


classes = (EXACTRADIUS_AP_prefs, MESH_OT_exact_radius)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_edit_mesh_vertices.append(_menu)
    register_keymap()


def unregister():
    unregister_keymap()
    bpy.types.VIEW3D_MT_edit_mesh_vertices.remove(_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
