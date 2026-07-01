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


# --- 0. release metadata --------------------------------------------------------
# bl_info and blender_manifest.toml are bumped together on release; catching a
# drift here is far cheaper than a rejected store upload.
section("release metadata")
import tomllib
with open(os.path.join(os.path.dirname(_HERE), "blender_manifest.toml"), "rb") as _f:
    _mani = tomllib.load(_f)
check("manifest version == bl_info version",
      tuple(int(x) for x in _mani["version"].split(".")) == ER.bl_info["version"],
      "%s vs %s" % (_mani["version"], ER.bl_info["version"]))
check("tagline <= 64 chars, no trailing period",
      len(_mani["tagline"]) <= 64 and not _mani["tagline"].endswith("."))
check("tests/docs excluded from the built zip",
      any("tests" in p for p in _mani.get("build", {}).get("paths_exclude_pattern", []))
      and any("docs" in p for p in _mani.get("build", {}).get("paths_exclude_pattern", [])))

# --- 1. safe math evaluator ---------------------------------------------------
section("safe_eval")
check("20/2 -> 10", ER._safe_eval("20/2") == 10.0)
check("(2+3)*4 -> 20", ER._safe_eval("(2+3)*4") == 20.0)
check("unary minus -> -3", ER._safe_eval("-3") == -3.0)
check("rejects names", ER._safe_eval("__import__('os')") is None)
check("rejects calls", ER._safe_eval("len([1])") is None)
check("rejects garbage", ER._safe_eval("abc") is None)
check("rejects power (DoS guard)", ER._safe_eval("9**9**9") is None)
check("1/0 -> None (no crash)", ER._safe_eval("1/0") is None)
check("empty -> None", ER._safe_eval("") is None)
check("tuple '1,2' -> None", ER._safe_eval("1,2") is None)

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


def ellipse_ring(bm, n, r, sx):
    """An edge-connected ellipse: a circle stretched by sx along x."""
    vs = [bm.verts.new((sx * r * math.cos(2 * math.pi * i / n),
                        r * math.sin(2 * math.pi * i / n), 0)) for i in range(n)]
    bm.verts.ensure_lookup_table()
    for i in range(n): bm.edges.new((vs[i], vs[(i + 1) % n]))
    return vs


# a clearly non-circular ellipse must be REJECTED (skipped), not "sort of" set
for sx in (2.0, 3.0):
    bm = bmesh.new(); ellipse_ring(bm, 24, 1.0, sx)
    check("ellipse %g:1 -> 0 circles" % sx, n_valid(bm) == 0, "got %d" % n_valid(bm)); bm.free()

# real-world mess: a slightly jittered ring is still recognized
bm = bmesh.new()
js = [bm.verts.new(((1 + 0.01 * math.sin(i * 12.9898)) * math.cos(2 * math.pi * i / 32),
                    (1 + 0.01 * math.sin(i * 12.9898)) * math.sin(2 * math.pi * i / 32), 0))
      for i in range(32)]
bm.verts.ensure_lookup_table()
for i in range(32): bm.edges.new((js[i], js[(i + 1) % 32]))
check("jittered ring (1%% noise) -> 1 circle r~1", n_valid(bm) == 1 and radii(bm) == [1.0],
      "%s" % radii(bm)); bm.free()

# doubled (duplicate) vertices on the ring — classic messy import geometry
bm = bmesh.new(); orig = ring_verts(bm, 16, 1.0)
dups = [bm.verts.new(v.co) for v in orig]
bm.verts.ensure_lookup_table()
for a, b in zip(orig, dups): bm.edges.new((a, b))
check("doubled verts -> still 1 circle r~1", n_valid(bm) == 1 and radii(bm) == [1.0],
      "n=%d %s" % (n_valid(bm), radii(bm))); bm.free()

# uneven angular spacing: the Kasa fit must find the TRUE center, not the centroid
bm = bmesh.new()
uv = []
for i in range(24):
    a = 2 * math.pi * i / 24 + 0.35 * math.sin(2 * math.pi * i / 24)   # bunched-up spacing
    uv.append(bm.verts.new((2 * math.cos(a), 2 * math.sin(a), 0)))
bm.verts.ensure_lookup_table()
for i in range(24): bm.edges.new((uv[i], uv[(i + 1) % 24]))
_v = ER._valid_circles(ER._find_circles(bm.verts[:]))
check("uneven spacing -> exact center + radius",
      len(_v) == 1 and _v[0][1][0].length < 1e-6 and abs(_v[0][1][2] - 2.0) < 1e-6,
      "c=%s r=%s" % ((_v[0][1][0], _v[0][1][2]) if _v else (None, None))); bm.free()

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

# fat / short tubes (radius >= ring spacing): a wide, short cylinder used to be
# mis-read as ONE circle — its points look nearly co-planar (out-of-plane / big
# radius is tiny) — and then collapse to a flat sliver on resize. Each ring must
# still be found independently, at any radius.
for r in (4.0, 8.0, 20.0, 60.0):
    bm = stacked([0.0, 2.0], n=32, r=r)
    check("fat stack r=%g spacing 2 -> 2" % r, n_valid(bm) == 2, "got %d" % n_valid(bm)); bm.free()
bm = stacked([0.0, 1.0, 2.0], n=32, r=20.0)
check("fat 3-stack r=20 spacing 1 -> 3", n_valid(bm) == 3, "got %d" % n_valid(bm)); bm.free()

bm = stacked([i * 2.0 for i in range(10)], n=64)
t0 = time.perf_counter(); got = n_valid(bm); dt = time.perf_counter() - t0
check("perf 10x64 stack -> 10 fast", got == 10 and dt < 0.5, "%d in %.3fs" % (got, dt)); bm.free()


def connected_rings(specs, n=24):
    """One connected piece of rings from (radius, center, normal) specs."""
    bm = bmesh.new()
    rings = [ring_verts(bm, n, r, center=c, normal=nrm) for r, c, nrm in specs]
    for k in range(len(rings) - 1):
        for a, b in zip(rings[k], rings[k + 1]): bm.edges.new((a, b))
    return bm


# cone / funnel: stacked rings of DIFFERENT radii in one connected piece — each
# end must be found as its own ring with its own radius (the docstring promise)
bm = connected_rings([(1.0, (0, 0, 0), (0, 0, 1)), (3.0, (0, 0, 2), (0, 0, 1))])
check("cone 2 rings -> [1, 3]", radii(bm) == [1.0, 3.0], "%s" % radii(bm)); bm.free()

bm = connected_rings([(1.0, (0, 0, 0), (0, 0, 1)), (2.0, (0, 0, 1.5), (0, 0, 1)),
                      (3.0, (0, 0, 3.5), (0, 0, 1))])
check("funnel 3 rings -> [1, 2, 3]", radii(bm) == [1.0, 2.0, 3.0], "%s" % radii(bm)); bm.free()

# stacks along X and along a diagonal — nothing may silently assume the Z axis
bm = connected_rings([(1.0, (d, 0, 0), (1, 0, 0)) for d in (0.0, 2.0, 4.0)], n=16)
check("stack along X -> 3", n_valid(bm) == 3, "got %d" % n_valid(bm)); bm.free()

_ax = Vector((1, 1, 1)).normalized()
bm = connected_rings([(1.0, tuple(_ax * d), tuple(_ax)) for d in (0.0, 2.0, 4.0)], n=16)
check("stack along diagonal -> 3", n_valid(bm) == 3, "got %d" % n_valid(bm)); bm.free()


# --- bridged COPLANAR circles (Patrick's field find, 2026-07-02) ---------------
# Two circles in the SAME plane, bridged into one connected piece. The plane
# bisector cannot separate them when their axis projections overlap (no gap on
# any axis), so this needs the ring-tracing fallback. Field case: two r=10
# circles, centers 11.35 apart (heavily overlapping), bridged vert i <-> vert i.
def bridged_coplanar(bm, specs, n=32):
    """Coplanar rings from (radius, center) specs, bridged pairwise i<->i."""
    rings = [ring_verts(bm, n, r, center=c) for r, c in specs]
    for k in range(len(rings) - 1):
        for a, b in zip(rings[k], rings[k + 1]): bm.edges.new((a, b))
    return rings


bm = bmesh.new(); bridged_coplanar(bm, [(10.0, (0, 0, 0)), (10.0, (0, -11.35, 0))])
check("bridged coplanar twins (overlapping) -> [10, 10]", radii(bm) == [10.0, 10.0],
      "%s" % radii(bm)); bm.free()

bm = bmesh.new(); bridged_coplanar(bm, [(4.0, (0, 0, 0)), (9.0, (0, -6.0, 0))])
check("bridged coplanar different radii -> [4, 9]", radii(bm) == [4.0, 9.0],
      "%s" % radii(bm)); bm.free()

bm = bmesh.new(); bridged_coplanar(bm, [(3.0, (0, 0, 0)), (3.0, (0, -10.0, 0))])
check("bridged coplanar separated -> [3, 3]", radii(bm) == [3.0, 3.0],
      "%s" % radii(bm)); bm.free()

# the small-then-big killer (field find #2, 2026-07-02): two SMALL circles far
# apart + their bridges happen to fit ONE big circle through both within
# tolerance (r~5.76 for this pair) — the whole piece was read as a single ring
# and collapsed onto that phantom circle when sized back up
bm = bmesh.new(); bridged_coplanar(bm, [(1.0, (0, 0, 0)), (1.0, (0, -11.35, 0))])
check("bridged coplanar SMALL twins (phantom combined fit) -> [1, 1]",
      radii(bm) == [1.0, 1.0], "%s" % radii(bm)); bm.free()

# spoked wheel: two CONCENTRIC coplanar rings, bridged by spokes — no axis gap
# ever; with close radii the combined fit is also "clean", so both guards
# (bisector veto + tracer veto) are needed
bm = bmesh.new(); bridged_coplanar(bm, [(3.0, (0, 0, 0)), (8.0, (0, 0, 0))])
check("spoked wheel r3/r8 -> [3, 8]", radii(bm) == [3.0, 8.0], "%s" % radii(bm)); bm.free()

bm = bmesh.new(); bridged_coplanar(bm, [(3.0, (0, 0, 0)), (3.5, (0, 0, 0))])
check("spoked wheel CLOSE radii r3/r3.5 -> [3, 3.5]", radii(bm) == [3.0, 3.5],
      "%s" % radii(bm)); bm.free()

# and resized through the core: both rings land on the target radius
bm = bmesh.new(); bridged_coplanar(bm, [(10.0, (0, 0, 0)), (10.0, (0, -11.35, 0))])
for vv in bm.verts: vv.select = True
s, sk = ER._resize_selection(bm, 5.0)
check("bridged coplanar resize -> 2 set, both r~5", (s, sk) == (2, 0) and radii(bm) == [5.0, 5.0],
      "set=%s %s" % ((s, sk), radii(bm))); bm.free()

# jittered bridged pair: the tracing walk must survive real-world noise
bm = bmesh.new()
jr = []
for cy in (0.0, -11.35):
    vs = []
    for i in range(32):
        a = 2 * math.pi * i / 32
        r = 10.0 + 0.08 * math.sin(i * 12.9898 + cy)
        vs.append(bm.verts.new((r * math.cos(a), cy + r * math.sin(a), 0)))
    bm.verts.ensure_lookup_table()
    for i in range(32): bm.edges.new((vs[i], vs[(i + 1) % 32]))
    jr.append(vs)
for a, b in zip(*jr): bm.edges.new((a, b))
check("bridged coplanar jittered -> 2 circles r~10",
      n_valid(bm) == 2 and all(abs(r - 10.0) < 0.05 for r in radii(bm)),
      "n=%d %s" % (n_valid(bm), radii(bm))); bm.free()

# a BIG face patch must still be one rejected blob — the tracing fallback may
# not carve block outlines out of it (disjoint 2x2-block cycles etc.)
bm = bmesh.new()
gg = {(i, j): bm.verts.new((i, j, 0)) for i in range(10) for j in range(10)}
bm.verts.ensure_lookup_table()
for i in range(10):
    for j in range(10):
        if i + 1 < 10: bm.edges.new((gg[(i, j)], gg[(i + 1, j)]))
        if j + 1 < 10: bm.edges.new((gg[(i, j)], gg[(i, j + 1)]))
check("10x10 grid -> 0 circles (trace must not carve blocks)", n_valid(bm) == 0,
      "got %d" % n_valid(bm)); bm.free()

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

# v1.9.4 store-review fix: prefs must be registered under the package name, or
# preference lookup breaks when installed as an extension (bl_ext.<repo>.<id>)
check("prefs bl_idname == __package__ (extension compat)",
      ER.EXACTRADIUS_AP_prefs.bl_idname == ER.__package__ == "exact_radius",
      "bl_idname=%s __package__=%s" % (ER.EXACTRADIUS_AP_prefs.bl_idname, ER.__package__))

# shortcut presets: each binds exactly ONE item with the right modifiers
_kc = bpy.context.window_manager.keyconfigs.addon
if _kc:
    def _kmis():
        km = _kc.keymaps.get('Mesh')
        return [k for k in km.keymap_items if k.idname == "mesh.exact_radius"] if km else []

    ER._apply_shortcut('CTRL_ALT_R'); ks = _kmis()
    check("preset CTRL_ALT_R -> 1 item, ctrl+alt",
          len(ks) == 1 and ks[0].type == 'R' and ks[0].ctrl and ks[0].alt and not ks[0].shift)
    ER._apply_shortcut('ALT_SHIFT_R'); ks = _kmis()
    check("preset ALT_SHIFT_R -> 1 item, alt+shift (no stacking)",
          len(ks) == 1 and ks[0].alt and ks[0].shift and not ks[0].ctrl)
    ER._apply_shortcut('NONE')
    check("preset NONE -> no keymap item", len(_kmis()) == 0)
    ER._apply_shortcut('ALT_R'); ks = _kmis()   # back to the default
    check("preset ALT_R -> 1 item, alt only",
          len(ks) == 1 and ks[0].alt and not ks[0].ctrl and not ks[0].shift)
else:
    print("  SKIP shortcut presets (no addon keyconfig in this build)")

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

# --- 6b. resize core — arcs, tilt, cursor, radius 0 ---------------------------
section("resize core — arcs / tilt / cursor")
# tilted ring: resized in its OWN plane, not squashed into world XY
bm = bmesh.new(); ring_verts(bm, 24, 2.5, normal=(1, 1, 1))
for vv in bm.verts: vv.select = True
ER._resize_selection(bm, 1.2)
check("tilted resize -> r~1.2", radii(bm) == [1.2], "%s" % radii(bm))
_nrm = Vector((1, 1, 1)).normalized()
_off = max(abs(v.co.dot(_nrm)) for v in bm.verts)
check("tilted resize stays in its plane", _off < 1e-6, "max off-plane %.2g" % _off); bm.free()

# partial arc: grows around the FITTED center (not the centroid), span preserved
bm = bmesh.new(); ring_verts(bm, 12, 3.0, arc=math.pi / 2)
for vv in bm.verts: vv.select = True
ER._resize_selection(bm, 6.0)
_d = [v.co.length for v in bm.verts]
check("arc resize: all verts at r=6 around the original center",
      max(abs(x - 6.0) for x in _d) < 1e-3, "d=%.4f..%.4f" % (min(_d), max(_d)))
_f = ER._fit_circle(list(bm.verts))
_span = ER._arc_span(list(bm.verts), _f)
check("arc resize keeps the quarter span", 0.15 < _span < 0.35, "span=%.2f" % _span); bm.free()

# radius 0 (the property's hard minimum) collapses onto the center — no crash
bm = bmesh.new(); ring_verts(bm, 16, 1.0)
for vv in bm.verts: vv.select = True
s, sk = ER._resize_selection(bm, 0.0)
check("radius 0 -> collapses onto center, no crash",
      (s, sk) == (1, 0) and max(v.co.length for v in bm.verts) < 1e-9); bm.free()

# 3D-cursor override: the circle is rebuilt AROUND the cursor
bm = bmesh.new(); ring_verts(bm, 16, 1.0)
for vv in bm.verts: vv.select = True
ER._resize_selection(bm, 1.0, cursor=Vector((0.5, 0.0, 0.0)))
_f = ER._fit_circle(list(bm.verts))
check("cursor override recenters the ring",
      _f is not None and (_f[0] - Vector((0.5, 0, 0))).length < 1e-6 and abs(_f[2] - 1.0) < 1e-6,
      "c=%s r=%s" % ((_f[0], _f[2]) if _f else (None, None))); bm.free()

# edge case: a vertex sitting EXACTLY on the new center has no radial direction
# — it deliberately stays put (the rl > 1e-9 guard), everything else lands on
# the radius. No crash, no NaN.
bm = bmesh.new(); ring_verts(bm, 16, 1.0)
for vv in bm.verts: vv.select = True
ER._resize_selection(bm, 1.0, cursor=Vector((1.0, 0.0, 0.0)))   # cursor == a vert
_d = sorted((v.co - Vector((1, 0, 0))).length for v in bm.verts)
check("vert on the center: stays put, rest on the radius, no NaN",
      _d[0] < 1e-9 and abs(_d[1] - 1.0) < 1e-3 and abs(_d[-1] - 1.0) < 1e-3
      and all(x == x for x in _d),
      "d=%.4g..%.4g" % (_d[0], _d[-1])); bm.free()

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

# --- 8. real operator via bpy.ops in edit mode --------------------------------
# Run the ACTUAL registered operator end-to-end. This is the layer that broke in
# v1.9.0 (invoke/execute created the edit bmesh inline without keeping a
# reference, so it was garbage-collected mid-use -> "BMVert has been removed").
# The pure-function tests above never caught it because they hold the bmesh in a
# local var. These do not — they go through the operator like a real key press.
section("operator integration (bpy.ops, edit mode)")


def _ring_object(name, n, r, loc=(0, 0, 0)):
    me = bpy.data.meshes.new(name); o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    b = bmesh.new(); ring_verts(b, n, r); b.to_mesh(me); b.free()
    o.location = loc
    return o


def _deselect_all():
    # after bpy.data.objects.remove() the collection can hold dangling None
    # entries until the depsgraph updates — skip them
    for ob in bpy.context.view_layer.objects:
        if ob is not None:
            ob.select_set(False)


def _ring_radius(o):
    vs = o.data.vertices
    return sum(v.co.length for v in vs) / len(vs)


def _edit_select_all(objs, active):
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active
    bpy.ops.object.mode_set(mode='EDIT')
    for o in objs:
        b = bmesh.from_edit_mesh(o.data)
        for v in b.verts:
            v.select = True
        b.select_flush(True); bmesh.update_edit_mesh(o.data)


def _run_operator_on(objs, active, radius, **props):
    """Run the real operator on objs in edit mode; (result, error_or_None).
    Catches exceptions (e.g. the v1.9.0 ReferenceError) so they surface as a
    clean FAIL instead of aborting the whole suite."""
    res = err = None
    try:
        _edit_select_all(objs, active)
        res = bpy.ops.mesh.exact_radius('EXEC_DEFAULT', radius=radius, **props)
    except Exception as e:
        err = repr(e)
    finally:
        if bpy.context.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
    return res, err


if bpy.context.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')

# single object through the real operator
_deselect_all()
o1 = _ring_object("ER_int_1", 16, 1.0)
res, err = _run_operator_on([o1], o1, 0.5)
check("op single: no exception", err is None, err or "")
check("op single: FINISHED + ring set to 0.5",
      res == {'FINISHED'} and abs(_ring_radius(o1) - 0.5) < 1e-3,
      "res=%s r=%.4f" % (res, _ring_radius(o1)))

# multi-object through the real operator
_deselect_all()
oa = _ring_object("ER_int_a", 16, 1.0, (0, 0, 0))
ob = _ring_object("ER_int_b", 24, 2.0, (8, 0, 0))
res, err = _run_operator_on([oa, ob], oa, 0.3)
check("op multi: no exception", err is None, err or "")
check("op multi: FINISHED", res == {'FINISHED'}, "%s" % (res,))
check("op multi: active ring set to 0.3", abs(_ring_radius(oa) - 0.3) < 1e-3, "r=%.4f" % _ring_radius(oa))
check("op multi: OTHER ring set to 0.3", abs(_ring_radius(ob) - 0.3) < 1e-3, "r=%.4f" % _ring_radius(ob))

for _o in (o1, oa, ob):
    bpy.data.objects.remove(_o, do_unlink=True)

# --- 9. round-trip integrity --------------------------------------------------
# Resize a real 2-ring cylinder through big -> back -> small -> tiny -> original
# and check it stays a WHOLE cylinder every step (both rings at the new radius,
# the two rings still apart). A wide, short cylinder used to be read as one
# circle and collapse to a flat sliver; this stresses every aspect ratio the
# round-trip passes through, at the operator level.
section("round-trip cylinder integrity (operator)")


def _cyl_object(name, n, r, h, nr=2):
    """A connected tube of `nr` rings (radius r) evenly spaced over height h."""
    me = bpy.data.meshes.new(name); o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    b = bmesh.new()
    rings = [ring_verts(b, n, r, center=(0, 0, -h / 2 + h * j / (nr - 1)))
             for j in range(nr)]
    for j in range(nr - 1):
        for a, c in zip(rings[j], rings[j + 1]): b.edges.new((a, c))
    b.to_mesh(me); b.free()
    return o


def _cyl_ok(o, r, h, nr):
    """Still a whole tube: every vert at xy-radius r, all nr ring planes still
    present along z (not collapsed / not exploded)."""
    rad = [math.hypot(v.co.x, v.co.y) for v in o.data.vertices]
    zs = sorted(round(v.co.z, 4) for v in o.data.vertices)
    radius_ok = all(abs(rr - r) < 1e-2 * max(r, 1.0) for rr in rad)
    planes = len(set(zs))
    height_ok = (max(zs) - min(zs)) > 0.5 * h and planes == nr
    return radius_ok and height_ok


for (n, r0, h, nr) in [(32, 8.0, 2.0, 2), (16, 8.0, 2.0, 2), (48, 20.0, 2.0, 2),
                       (32, 5.0, 10.0, 2), (24, 3.0, 1.0, 2), (32, 10.0, 3.0, 2),
                       (32, 3.0, 2.0, 3), (32, 8.0, 2.0, 3),   # near-cubic multi-ring
                       (32, 8.0, 2.0, 5), (24, 5.0, 4.0, 7),   # loop-cut tubes
                       (32, 1.0, 2.0, 3)]:                     # tiny multi-ring
    cyl = _cyl_object("ER_cyl", n, r0, h, nr)
    ok, detail = True, ""
    for tgt in (r0 * 2.0, r0, r0 * 0.5, 0.05, r0):     # big, back, small, tiny, original
        res, err = _run_operator_on([cyl], cyl, tgt)
        if err or res != {'FINISHED'} or not _cyl_ok(cyl, tgt, h, nr):
            ok = False
            detail = "broke at ->%g (err=%s, whole=%s)" % (tgt, err, _cyl_ok(cyl, tgt, h, nr))
            break
    check("roundtrip n=%d r0=%g h=%g rings=%d stays whole" % (n, r0, h, nr), ok, detail)
    bpy.data.objects.remove(cyl, do_unlink=True)

# --- 10. operator — rejection, transforms, cursor ------------------------------
section("operator — rejection / transform / cursor")


def _mesh_object(name, build):
    """New object whose mesh is built by `build(bmesh)`."""
    me = bpy.data.meshes.new(name); o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    b = bmesh.new(); build(b); b.to_mesh(me); b.free()
    return o


# a filled grid (clearly no circle) is rejected cleanly, not mangled
def _build_grid(b):
    g = {(i, j): b.verts.new((i, j, 0)) for i in range(5) for j in range(5)}
    b.verts.ensure_lookup_table()
    for i in range(5):
        for j in range(5):
            if i + 1 < 5: b.edges.new((g[(i, j)], g[(i + 1, j)]))
            if j + 1 < 5: b.edges.new((g[(i, j)], g[(i, j + 1)]))


_deselect_all()
og = _mesh_object("ER_grid", _build_grid)
_before = [tuple(v.co) for v in og.data.vertices]
res, err = _run_operator_on([og], og, 1.0)
check("op grid: rejected (CANCELLED / error, verts untouched)",
      (res == {'CANCELLED'} or err is not None)
      and [tuple(v.co) for v in og.data.vertices] == _before,
      "res=%s err=%s" % (res, err))
bpy.data.objects.remove(og, do_unlink=True)

# object transform must not matter — the radius is in LOCAL units
_deselect_all()
ot = _ring_object("ER_xform", 16, 1.0)
ot.scale = (2.0, 2.0, 2.0); ot.rotation_euler = (0.4, 0.3, 0.2)
res, err = _run_operator_on([ot], ot, 0.5)
check("op scaled+rotated object: local r=0.5",
      err is None and res == {'FINISHED'} and abs(_ring_radius(ot) - 0.5) < 1e-3,
      "err=%s r=%.4f" % (err, _ring_radius(ot)))
bpy.data.objects.remove(ot, do_unlink=True)


# a cone (two connected rings, r=1 and r=3) → BOTH rings set, tube stays whole
def _build_cone(b):
    r1 = ring_verts(b, 24, 1.0, center=(0, 0, 0))
    r2 = ring_verts(b, 24, 3.0, center=(0, 0, 2))
    for a, c in zip(r1, r2): b.edges.new((a, c))


_deselect_all()
oc = _mesh_object("ER_cone", _build_cone)
res, err = _run_operator_on([oc], oc, 0.7)
_rad = [math.hypot(v.co.x, v.co.y) for v in oc.data.vertices]
_zs = {round(v.co.z, 4) for v in oc.data.vertices}
check("op cone: both rings -> 0.7, two planes kept",
      err is None and res == {'FINISHED'}
      and max(abs(rr - 0.7) for rr in _rad) < 1e-2 and len(_zs) == 2,
      "err=%s rad=%.3f..%.3f planes=%d" % (err, min(_rad), max(_rad), len(_zs)))
bpy.data.objects.remove(oc, do_unlink=True)

# bridged coplanar circles through the real operator (the 2026-07-02 field
# case): both rings get the radius, the two centers stay put
def _build_bridged(b):
    r1 = ring_verts(b, 32, 10.0, center=(0, 0, 0))
    r2 = ring_verts(b, 32, 10.0, center=(0, -11.35, 0))
    for a, c in zip(r1, r2): b.edges.new((a, c))


_deselect_all()
obr = _mesh_object("ER_bridged", _build_bridged)
res, err = _run_operator_on([obr], obr, 4.0)
_dA = [(v.co - Vector((0, 0, 0))).length for v in obr.data.vertices[:32]]
_dB = [(v.co - Vector((0, -11.35, 0))).length for v in obr.data.vertices[32:]]
check("op bridged coplanar: both rings -> 4, centers kept",
      err is None and res == {'FINISHED'}
      and max(abs(x - 4.0) for x in _dA) < 1e-2 and max(abs(x - 4.0) for x in _dB) < 1e-2,
      "err=%s res=%s A=%.3f..%.3f B=%.3f..%.3f" % (err, res, min(_dA), max(_dA), min(_dB), max(_dB)))
bpy.data.objects.remove(obr, do_unlink=True)

# ... and the ROUND-TRIP: big -> small -> big again. Shrunken far-apart twins
# masquerade as one big circle (see the phantom-fit test above); sizing back up
# used to collapse both rings onto that phantom.
_deselect_all()
orb = _mesh_object("ER_bridged_rt", _build_bridged)
_ok, _detail = True, ""
for _tgt in (1.0, 10.0, 0.5, 10.0):
    res, err = _run_operator_on([orb], orb, _tgt)
    _dA = [(v.co - Vector((0, 0, 0))).length for v in orb.data.vertices[:32]]
    _dB = [(v.co - Vector((0, -11.35, 0))).length for v in orb.data.vertices[32:]]
    if not (err is None and res == {'FINISHED'}
            and max(abs(x - _tgt) for x in _dA) < 1e-2
            and max(abs(x - _tgt) for x in _dB) < 1e-2):
        _ok, _detail = False, "broke at ->%g (err=%s res=%s)" % (_tgt, err, res)
        break
check("op bridged coplanar round-trip small->big stays two rings", _ok, _detail)
bpy.data.objects.remove(orb, do_unlink=True)

# CURSOR center mode, single ring: the circle is rebuilt around the 3D cursor.
# (The cursor must not sit exactly ON a vertex — such a vertex has no radial
# direction and deliberately stays put; see the section 6b edge-case check.)
_old_cursor = bpy.context.scene.cursor.location.copy()
_deselect_all()
ocur = _ring_object("ER_cursor", 16, 1.0)
bpy.context.scene.cursor.location = (0.5, 0.0, 0.0)
res, err = _run_operator_on([ocur], ocur, 1.0, center_mode='CURSOR')
_d = [(v.co - Vector((0.5, 0, 0))).length for v in ocur.data.vertices]
check("op cursor single: ring recentered on the cursor",
      err is None and res == {'FINISHED'} and max(abs(x - 1.0) for x in _d) < 1e-3,
      "err=%s d=%.4f..%.4f" % (err, min(_d), max(_d)))
bpy.data.objects.remove(ocur, do_unlink=True)

# CURSOR center mode with MANY circles: the cursor is ignored (documented), each
# ring keeps its own center — nothing gets dragged toward the cursor
_deselect_all()
oma = _ring_object("ER_cur_a", 16, 1.0, (0, 0, 0))
omb = _ring_object("ER_cur_b", 24, 2.0, (8, 0, 0))
bpy.context.scene.cursor.location = (5.0, 5.0, 5.0)
res, err = _run_operator_on([oma, omb], oma, 0.5, center_mode='CURSOR')
def _uniform(o, r):
    return max(abs(v.co.length - r) for v in o.data.vertices) < 1e-3
check("op cursor multi: cursor ignored, own centers kept",
      err is None and res == {'FINISHED'} and _uniform(oma, 0.5) and _uniform(omb, 0.5),
      "err=%s" % err)
bpy.context.scene.cursor.location = _old_cursor
for _o in (oma, omb):
    bpy.data.objects.remove(_o, do_unlink=True)

# --- summary ------------------------------------------------------------------
nf = _results.count(False)
print("\n=== %d/%d passed, %d FAILED  (Blender %s) ===" % (
    len(_results) - nf, len(_results), nf, bpy.app.version_string))
if nf:
    sys.exit(1)
