# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Patrick
bl_info = {
    "name": "Exact Radius",
    "author": "Patrick",
    "version": (1, 8, 1),
    "blender": (4, 2, 0),
    "location": "Edit Mode > Vertex Menu > Exact Radius (default Alt+R)",
    "description": (
        "Make selected rings of vertices perfect circles of an exact radius — "
        "at any orientation, for full circles, holes and partial arcs. Sets "
        "many circles at once and reports how many were set."
    ),
    "category": "Mesh",
}

import bpy
import bmesh
import ast
import operator as _operator
import numpy as np
from mathutils import Vector
from bpy.props import FloatProperty, EnumProperty, BoolProperty

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
    if s[0] < 1e-12 or (s[1] / s[0]) < 0.02:
        return None              # points are essentially collinear — not a circle
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


def _connected_groups(verts):
    """Split a vertex selection into edge-connected components.

    Separate circles (e.g. a dozen holes) come apart here — each ring is its own
    component, so they are fitted and resized independently.
    """
    sel = set(verts)
    seen = set()
    groups = []
    for start in verts:
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        comp = []
        while stack:
            v = stack.pop()
            comp.append(v)
            for e in v.link_edges:
                o = e.other_vert(v)
                if o in sel and o not in seen:
                    seen.add(o)
                    stack.append(o)
        groups.append(comp)
    return groups


def _is_circle(verts):
    fit = _fit_circle(verts)
    return fit is not None and _circle_error(verts, fit) is None


def _bisect_by_plane(verts):
    """Split a component into parallel clusters along its stacking axis.

    Pulls apart rings stacked in one connected piece (several cross-sections of a
    tube, the two ends of a funnel) — for any number of rings, evenly spaced or
    not. Each principal axis is cut at every gap of at least half its largest
    gap, and scored by how many of the resulting clusters are themselves clean
    circles: the true stacking axis turns into whole rings, while the other axes
    only slice the rings into arcs — so the axis that yields real circles wins,
    regardless of the ring count. A filled face/blob yields no circles and keeps
    its span filled, so it is left unsplit. Returns >= 2 vertex groups or None.
    """
    if len(verts) < 6:
        return None
    vlist = list(verts)
    pts = np.array([v.co[:] for v in vlist], dtype=float)
    Q = pts - pts.mean(axis=0)
    try:
        _, _s, vt = np.linalg.svd(Q, full_matrices=False)
    except np.linalg.LinAlgError:
        return None

    def axis_split(axis):
        proj = Q @ axis
        order = np.argsort(proj)
        gaps = np.diff(proj[order])
        spread = float(proj[order[-1]] - proj[order[0]])
        if spread < 1e-9 or gaps.size == 0:
            return None
        tiny = 1e-9 * spread
        # Rank the gaps largest-first and find the "knee": the count of cuts at
        # which the gap sizes drop off most sharply. For stacked rings the big
        # ring-to-ring gaps tower over the ~0 within-ring gaps, so the knee lands
        # exactly between the rings (any number, evenly spaced or not). A face or
        # a single ring has no such drop, so it does not split cleanly.
        ranked = sorted(range(gaps.size), key=lambda j: float(gaps[j]), reverse=True)
        gv = [float(gaps[j]) for j in ranked]
        if gv[0] < tiny:
            return None
        best_i, best_ratio = 1, 0.0
        for i in range(1, gaps.size):
            ratio = gv[i - 1] / max(gv[i], tiny)
            if ratio > best_ratio:
                best_ratio, best_i = ratio, i
        cut = sorted(ranked[:best_i])
        sep = float(sum(gaps[j] for j in cut)) / spread
        bounds = [-1] + cut + [gaps.size]
        groups = []
        for a in range(len(bounds) - 1):
            idx = order[bounds[a] + 1: bounds[a + 1] + 1]
            if idx.size < 3:              # a sliver cluster -> not a clean split
                return None
            groups.append([vlist[k] for k in idx])
        if len(groups) < 2:
            return None
        clean = sum(1 for g in groups if _is_circle(g))
        return (clean, sep), groups

    best = None
    for axis in vt:
        res = axis_split(axis)
        if res is None:
            continue
        key, groups = res
        if best is None or key > best[0]:
            best = (key, groups)
    # Only split when at least one cluster is itself a real circle — that is what
    # tells the true stacking axis (whole rings) from an axis that merely slices
    # the rings into arcs, and it keeps blobs/faces (no circles) unsplit.
    if best is None or best[0][0] == 0:
        return None
    return best[1]


def _split_leaves(verts, depth=0):
    """Recursively bisect a component into leaf groups (for stacked rings).

    Stops descending as soon as a group is itself a clean circle, so a single
    ring is never chopped into arcs.
    """
    fit = _fit_circle(verts)
    if (fit is not None and _circle_error(verts, fit) is None) or depth >= 8:
        return [verts]
    parts = _bisect_by_plane(verts)
    if not parts:
        return [verts]
    out = []
    for p in parts:
        out.extend(_split_leaves(p, depth + 1))
    return out


def _find_circles(sel):
    """Find every distinct circle in the selection.

    Returns a list of (verts, fit); fit is None / not-a-circle for a group that
    cannot be used (counted as "skipped"). Separate rings split by connectivity;
    rings stacked in one piece split by plane clustering.
    """
    circles = []
    for comp in _connected_groups(sel):
        fit = _fit_circle(comp)
        if fit is not None and _circle_error(comp, fit) is None:
            circles.append((comp, fit))           # one clean circle
            continue
        found = []                                # peel apart stacked rings
        for leaf in _split_leaves(comp):
            f = _fit_circle(leaf)
            if f is not None and _circle_error(leaf, f) is None:
                found.append((leaf, f))
        if found:
            circles.extend(found)
        else:
            circles.append((comp, fit))           # one un-usable group = 1 skip
    return circles


def _valid_circles(circles):
    return [(vs, fit) for vs, fit in circles
            if fit is not None and _circle_error(vs, fit) is None]


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
        n = getattr(self, "_count", 1)
        head = "Exact Radius" + (f"  ({n} circles)" if n > 1 else "")
        txt = (f"{head}: {shown}    "
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
        valid = _valid_circles(_find_circles(sel))
        if not valid:
            # fall back to the single-selection reason for a helpful message
            self.report({'ERROR'}, _circle_error(sel, _fit_circle(sel)) or
                        "Select at least one ring of vertices forming a circle")
            return {'CANCELLED'}
        # pre-fill with the first circle's fitted radius; remember how many
        self._count = len(valid)
        self._current = round(valid[0][1][2], 4)
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

    def cancel(self, context):
        # always clear the viewport header, even on an external modal teardown
        self._clear_header(context)

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        sel = _selected_verts(bm)
        circles = _find_circles(sel)
        valid = _valid_circles(circles)
        if not valid:
            self.report({'ERROR'},
                        "Selection is not a circle — select a ring of vertices")
            return {'CANCELLED'}
        # 3D-cursor center only makes sense for a single circle
        cursor = (_local_cursor(context, obj)
                  if self.center_mode == 'CURSOR' and len(valid) == 1 else None)
        for verts, fit in valid:
            center, normal = fit[0], fit[1]
            if cursor is not None:
                center = cursor
            for v in verts:
                d = v.co - center
                radial = d - d.dot(normal) * normal   # flatten onto circle plane
                rl = radial.length
                if rl > 1e-9:
                    v.co = center + radial * (self.radius / rl)
        bmesh.update_edit_mesh(obj.data)
        n = len(valid)
        skipped = len(circles) - n
        r = self.radius
        note = ("  (3D-cursor center applies to a single ring only)"
                if self.center_mode == 'CURSOR' and n > 1 else "")
        if skipped:                                   # yellow: not all were circles
            self.report({'WARNING'}, f"{n} set / {skipped} skipped{note}")
        elif n == 1:
            self.report({'INFO'}, f"Circle set to radius {r:.4g}")
        else:
            self.report({'INFO'}, f"{n} circles set to radius {r:.4g}{note}")
        return {'FINISHED'}


def _menu(self, context):
    self.layout.operator(MESH_OT_exact_radius.bl_idname, icon='MESH_CIRCLE')


# --- Keymap: a simple preset dropdown in the addon keyconfig ---
# A handful of safe, conflict-free combos (none clash with Blender's mesh keys)
# plus "Disabled" — picked from a plain dropdown in the preferences, so there is
# no fiddly raw keymap widget to accidentally rebind onto the mouse.
addon_keymaps = []

# id -> (key or None, ctrl, alt, shift, label)
_SHORTCUTS = {
    'ALT_R':       ('R', False, True,  False, "Alt + R"),
    'CTRL_ALT_R':  ('R', True,  True,  False, "Ctrl + Alt + R"),
    'ALT_SHIFT_R': ('R', False, True,  True,  "Alt + Shift + R"),
    'NONE':        (None, False, False, False, "Disabled"),
}
_SHORTCUT_ITEMS = [
    ('ALT_R', "Alt + R", "Default shortcut"),
    ('CTRL_ALT_R', "Ctrl + Alt + R", "Alternative shortcut"),
    ('ALT_SHIFT_R', "Alt + Shift + R", "Alternative shortcut"),
    ('NONE', "Disabled", "No shortcut — use the Vertex menu instead"),
]


def _apply_shortcut(key_id):
    """Bind exactly one keymap item (or none) for the chosen preset."""
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    km = kc.keymaps.get('Mesh') or kc.keymaps.new(name='Mesh', space_type='EMPTY')
    for kmi in list(km.keymap_items):           # clear any previous binding(s)
        if kmi.idname == MESH_OT_exact_radius.bl_idname:
            km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    key, ctrl, alt, shift, _label = _SHORTCUTS.get(key_id, _SHORTCUTS['ALT_R'])
    if key is None:
        return                                  # "Disabled"
    kmi = km.keymap_items.new(MESH_OT_exact_radius.bl_idname, key, 'PRESS',
                              ctrl=ctrl, alt=alt, shift=shift)
    addon_keymaps.append((km, kmi))


def _current_shortcut():
    try:
        return bpy.context.preferences.addons[__name__].preferences.shortcut
    except Exception:
        return 'ALT_R'


def register_keymap():
    _apply_shortcut(_current_shortcut())


def unregister_keymap():
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()


def _update_shortcut(self, context):
    _apply_shortcut(self.shortcut)


class EXACTRADIUS_AP_prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    shortcut: EnumProperty(
        name="Shortcut",
        description="Keyboard shortcut for Exact Radius (Edit Mode)",
        items=_SHORTCUT_ITEMS,
        default='ALT_R',
        update=_update_shortcut,
    )
    show_help: BoolProperty(
        name="How to use",
        description="Show a short how-to for Exact Radius",
        default=False,
    )

    def draw(self, context):
        layout = self.layout

        # The shortcut is the one setting people come here for — keep it on top.
        row = layout.row(align=True)
        row.label(text="Shortcut (Edit Mode)", icon='PREFERENCES')
        row.prop(self, "shortcut", text="")

        # Everything else is just help — collapsed by default so it is not a
        # wall of text. Click to expand.
        box = layout.box()
        box.prop(self, "show_help",
                 text="How to use Exact Radius",
                 icon='TRIA_DOWN' if self.show_help else 'TRIA_RIGHT',
                 emboss=False)
        if self.show_help:
            col = box.column(align=True)
            col.label(text="Make a selected ring of vertices a perfect circle of an",
                      icon='MESH_CIRCLE')
            col.label(text="exact radius, at any orientation — no pop-up, it happens "
                           "right on the shortcut.")
            col.separator()
            col.label(text="1.   In Edit Mode, select a ring of vertices")
            col.label(text="        (a full circle, a hole, or part of one — an arc)")
            col.label(text="2.   Press the shortcut   (or  Vertex menu > Exact Radius)")
            col.label(text="3.   Type the radius, press Enter — done")
            col.separator()
            col.label(text="Good to know:")
            col.label(text="     •  select many rings at once — each gets the radius, "
                           "with a count (e.g. 12 circles set)")
            col.label(text="     •  20/2  math works  (turn a diameter into a radius)")
            col.label(text="     •  tilted / rotated circles and partial arcs just work")
            col.label(text="     •  Esc cancels  ·  F9 afterwards to set the center")
            col.label(text="     •  a non-circle (whole face / mesh) shows an error")


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
