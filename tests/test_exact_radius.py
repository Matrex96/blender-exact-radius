"""Functional test suite for the Exact Radius add-on.

Run headless with any Blender (4.2+):
    blender --background --python <abs path>/tests/test_exact_radius.py

The runner tests/run.sh runs it across all locally installed Blender versions.
The path is derived from __file__, so it works regardless of the working dir
(Blender builds differ in how they resolve a relative --python path).

Exits non-zero if any check fails.
"""
import importlib.util
import math
import os
import sys
import time

import bpy
import bmesh
from mathutils import Vector

# --- load the add-on module straight from the repo (../__init__.py) -----------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "__init__.py")
_spec = importlib.util.spec_from_file_location("exact_radius", _SRC)
ER = importlib.util.module_from_spec(_spec)
sys.modules["exact_radius"] = ER
_spec.loader.exec_module(ER)

# --- tiny test framework ------------------------------------------------------
_results = []


def check(name, cond, info=""):
    ok = bool(cond)
    _results.append(ok)
    print(("  PASS " if ok else "  FAIL ") + name + (("   " + info) if info else ""))


def section(title):
    print("\n# " + title)


# --- helpers ------------------------------------------------------------------
def ring_verts(bm, n, radius, center=(0, 0, 0), normal=(0, 0, 1), arc=2 * math.pi):
    """Create an edge-connected ring (or partial arc) and return its verts."""
    c = Vector(center)
    nrm = Vector(normal).normalized()
    ref = Vector((1, 0, 0)) if abs(nrm.x) < 0.9 else Vector((0, 1, 0))
    e1 = nrm.cross(ref).normalized()
    e2 = nrm.cross(e1).normalized()
    full = arc >= 2 * math.pi - 1e-9
    vs = []
    for i in range(n):
        a = arc * i / (n if full else (n - 1))
        vs.append(bm.verts.new(c + radius * (math.cos(a) * e1 + math.sin(a) * e2)))
    bm.verts.ensure_lookup_table()
    for i in range(n - 1):
        bm.edges.new((vs[i], vs[i + 1]))
    if full:
        bm.edges.new((vs[-1], vs[0]))
    return vs


def n_valid(bm):
    return len(ER._valid_circles(ER._find_circles(bm.verts[:])))


def radii(bm):
    return sorted(round(fit[2], 3) for _vs, fit in ER._valid_circles(ER._find_circles(bm.verts[:])))


# --- 1. safe math evaluator ---------------------------------------------------
section("safe_eval")
check("20/2 -> 10", ER._safe_eval("20/2") == 10.0)
check("(2+3)*4 -> 20", ER._safe_eval("(2+3)*4") == 20.0)
check("rejects names", ER._safe_eval("__import__('os')") is None)
check("rejects calls", ER._safe_eval("len([1])") is None)
check("rejects garbage", ER._safe_eval("abc") is None)

# --- 2. circle fit & validation ----------------------------------------------
section("fit & validate")
bm = bmesh.new(); ring_verts(bm, 16, 1.0)
v = ER._valid_circles(ER._find_circles(bm.verts[:]))
check("flat ring -> 1 circle r~1", len(v) == 1 and abs(v[0][1][2] - 1.0) < 1e-3,
      "r=%.4f" % (v[0][1][2] if v else -1)); bm.free()

bm = bmesh.new(); ring_verts(bm, 24, 2.5, normal=(1, 1, 1))
v = ER._valid_circles(ER._find_circles(bm.verts[:]))
check("tilted ring -> 1 circle r~2.5", len(v) == 1 and abs(v[0][1][2] - 2.5) < 1e-3,
      "r=%.4f" % (v[0][1][2] if v else -1)); bm.free()

bm = bmesh.new(); ring_verts(bm, 12, 3.0, arc=math.pi / 2)
v = ER._valid_circles(ER._find_circles(bm.verts[:]))
check("quarter arc -> 1 circle r~3", len(v) == 1 and abs(v[0][1][2] - 3.0) < 1e-2,
      "r=%.4f" % (v[0][1][2] if v else -1)); bm.free()

bm = bmesh.new()
g = {(i, j): bm.verts.new((i, j, 0)) for i in range(6) for j in range(6)}
bm.verts.ensure_lookup_table()
for i in range(6):
    for j in range(6):
        if i + 1 < 6: bm.edges.new((g[(i, j)], g[(i + 1, j)]))
        if j + 1 < 6: bm.edges.new((g[(i, j)], g[(i, j + 1)]))
check("filled grid -> 0 circles", n_valid(bm) == 0, "got %d" % n_valid(bm)); bm.free()

bm = bmesh.new()
ln = [bm.verts.new((i, 0, 0)) for i in range(8)]; bm.verts.ensure_lookup_table()
for i in range(7): bm.edges.new((ln[i], ln[i + 1]))
check("collinear -> 0 circles", n_valid(bm) == 0); bm.free()

check("empty selection -> 0 (no crash)", len(ER._valid_circles(ER._find_circles([]))) == 0)

bm = bmesh.new(); two = [bm.verts.new((0, 0, 0)), bm.verts.new((1, 0, 0))]
bm.edges.new((two[0], two[1]))
check("two verts -> 0 (no crash)", n_valid(bm) == 0); bm.free()

# --- 3. multi-circle discovery ------------------------------------------------
section("multi-circle")
bm = bmesh.new(); ring_verts(bm, 16, 1.0, center=(0, 0, 0)); ring_verts(bm, 16, 2.0, center=(10, 0, 0))
check("two separate rings -> 2 (r 1,2)", radii(bm) == [1.0, 2.0], "%s" % radii(bm)); bm.free()

bm = bmesh.new()
for i in range(12): ring_verts(bm, 12, 0.5, center=(i * 3, 0, 0))
check("twelve holes -> 12", n_valid(bm) == 12, "got %d" % n_valid(bm)); bm.free()


def stacked(zs, n=16, r=1.0):
    bm = bmesh.new()
    rings = [ring_verts(bm, n, r, center=(0, 0, z)) for z in zs]
    for k in range(len(rings) - 1):
        for a, b in zip(rings[k], rings[k + 1]): bm.edges.new((a, b))
    return bm


for k in (2, 3, 4, 5, 8, 12):
    bm = stacked([i * 2.0 for i in range(k)])
    check("stacked even k=%d -> %d" % (k, k), n_valid(bm) == k, "got %d" % n_valid(bm)); bm.free()

for zs in ([0.0, 1.3, 5.0], [0.0, 2.0, 2.7, 7.0, 11.5, 12.1]):
    bm = stacked(zs)
    check("stacked uneven (%d) -> %d" % (len(zs), len(zs)), n_valid(bm) == len(zs),
          "got %d" % n_valid(bm)); bm.free()

bm = stacked([i * 2.0 for i in range(10)], n=64)
t0 = time.perf_counter(); got = n_valid(bm); dt = time.perf_counter() - t0
check("perf 10x64 stack -> 10 fast", got == 10 and dt < 0.5, "%d in %.3fs" % (got, dt)); bm.free()

# --- 4. register / unregister -------------------------------------------------
section("register / keymap")
def km_count():
    kc = bpy.context.window_manager.keyconfigs.addon
    if not kc: return None
    km = kc.keymaps.get('Mesh')
    return sum(1 for k in km.keymap_items if k.idname == "mesh.exact_radius") if km else 0

leak_ok = True
for _ in range(3):
    ER.register(); after_reg = km_count(); ER.unregister(); after_unreg = km_count()
    if after_unreg not in (0, None): leak_ok = False
check("3x register/unregister, no keymap leak", leak_ok)
ER.register()
check("operator registered while enabled", hasattr(bpy.types, "MESH_OT_exact_radius"))
check("prefs has show_help (new UI)", "show_help" in ER.EXACTRADIUS_AP_prefs.__annotations__)

# --- 5. resize core, single circle -------------------------------------------
# _resize_selection(bm, radius, cursor=None) finds every circle in the bmesh
# selection and sets it to `radius`, returning (set_count, skipped_count). It is
# the per-mesh building block the operator runs for each object in edit mode.
section("resize core — single")
bm = bmesh.new(); ring_verts(bm, 16, 1.0)
for vv in bm.verts: vv.select = True
s, sk = ER._resize_selection(bm, 0.5)
check("single: returns (1 set, 0 skipped)", (s, sk) == (1, 0), "got %s" % ((s, sk),))
check("single: ring now r~0.5", radii(bm) == [0.5], "%s" % radii(bm)); bm.free()

# a non-circle in the selection is reported as skipped, not resized
bm = bmesh.new()
ring_verts(bm, 16, 1.0)
ln = [bm.verts.new((20 + i, 0, 0)) for i in range(6)]; bm.verts.ensure_lookup_table()
for i in range(5): bm.edges.new((ln[i], ln[i + 1]))
for vv in bm.verts: vv.select = True
s, sk = ER._resize_selection(bm, 0.5)
check("mixed: 1 set, 1 skipped", (s, sk) == (1, 1), "got %s" % ((s, sk),)); bm.free()

# --- 6. resize core, many circles + multi-object building block ---------------
section("resize core — many / multi-object")
# many circles in one selection (e.g. a perforated plate) -> all resized
bm = bmesh.new(); ring_verts(bm, 16, 1.0, center=(0, 0, 0)); ring_verts(bm, 24, 2.0, center=(8, 0, 0))
for vv in bm.verts: vv.select = True
s, sk = ER._resize_selection(bm, 0.7)
check("many: 2 set", s == 2, "set=%d" % s)
check("many: both now r~0.7", radii(bm) == [0.7, 0.7], "%s" % radii(bm)); bm.free()

# multi-object: two independent bmeshes, as execute() loops objects_in_mode
bms = []
for r in (1.0, 2.0):
    b = bmesh.new(); ring_verts(b, 16, r)
    for vv in b.verts: vv.select = True
    bms.append(b)
total = sum(ER._resize_selection(b, 0.3)[0] for b in bms)
check("multi-object: each object's circle set", total == 2, "total=%d" % total)
check("multi-object: every mesh now r~0.3", all(radii(b) == [0.3] for b in bms),
      "%s" % [radii(b) for b in bms])
for b in bms: b.free()

# --- 7. _edit_meshes glue (fake context — no GUI needed) ----------------------
# execute()/invoke() iterate _edit_meshes(context); verify it returns every mesh
# in edit mode (active first) and ignores non-meshes. The actual population of
# context.objects_in_mode is Blender's job (verified live via MCP separately).
section("_edit_meshes glue")


class _FakeObj:
    def __init__(self, name, kind='MESH'):
        self.name = name; self.type = kind


class _FakeCtx:
    def __init__(self, in_mode, active):
        self.objects_in_mode = in_mode; self.edit_object = active


_a, _b, _lamp = _FakeObj('A'), _FakeObj('B'), _FakeObj('L', 'LIGHT')
check("meshes only, active first",
      [o.name for o in ER._edit_meshes(_FakeCtx([_b, _a, _lamp], _a))] == ['A', 'B'])
check("falls back to active when objects_in_mode empty",
      [o.name for o in ER._edit_meshes(_FakeCtx([], _a))] == ['A'])
check("nothing in edit mode -> empty", ER._edit_meshes(_FakeCtx([], None)) == [])

# --- summary ------------------------------------------------------------------
nf = _results.count(False)
print("\n=== %d/%d passed, %d FAILED  (Blender %s) ===" % (
    len(_results) - nf, len(_results), nf, bpy.app.version_string))
if nf:
    sys.exit(1)
