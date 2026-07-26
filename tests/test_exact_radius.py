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
import struct
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
# Count of checks this file is expected to run, NOT counting the final "did they
# all run" check itself. Bump it when adding or removing checks — the same
# discipline as bumping bl_info and the manifest together. Without it, a whole
# block dropping out (an `if` that stops being true, an early return) just makes
# the total smaller and still prints "0 FAILED".
EXPECTED_CHECKS = 169

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

# The modal opens pre-filled with the fitted radius, and plain Enter applies
# exactly that. So the tidying done for the header must not change the number:
# rounding to four DECIMALS is harmless at metre scale and butchery at
# millimetre scale, where "open the modal, press Enter" would quietly resize the
# ring it was showing. Significant digits behave the same at every scale.
check("prefill keeps a millimetre-scale radius intact",
      ER._prefill_radius(0.00012) == 0.00012, repr(ER._prefill_radius(0.00012)))
check("prefill keeps a sub-0.0001 radius off zero",
      ER._prefill_radius(4.7e-5) == 4.7e-5, repr(ER._prefill_radius(4.7e-5)))
check("prefill still tidies a long fitted value",
      ER._prefill_radius(1.2345678912) == 1.234568, repr(ER._prefill_radius(1.2345678912)))
check("prefill leaves a plain radius alone",
      ER._prefill_radius(2.5) == 2.5, repr(ER._prefill_radius(2.5)))

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


# --- a perfect ring must never be shaved (Patrick's field find, 2026-07-25) ---
# Found on a real 96-ring model: three mathematically perfect rings (residual 0,
# planarity 0) came back split — 2048 verts into 2044 + 4, and a 32-vert ring
# into 5 + 23 + 4. Each piece then gets its OWN fitted centre, so the ring comes
# back subtly deformed. The bisector was inventing the seam: a high-resolution
# ring has thousands of projections along any in-plane axis, and the widest gap
# among them looks just like the gap between two stacked rings. It was allowed
# through because SOME of the resulting clusters were rings. Now, if the piece
# is already a whole clean circle, a cut only counts when EVERY part is a whole
# ring too — which is still exactly the wide-short-tube case.
for _n in (32, 64, 128, 512, 1024, 2048):
    bm = bmesh.new(); ring_verts(bm, _n, 1.0)
    _g = ER._find_circles(bm.verts[:])
    check("perfect ring, %d verts -> exactly 1 group" % _n,
          len(_g) == 1 and radii(bm) == [1.0], "%d Gruppen %s" % (len(_g), radii(bm)))
    bm.free()

# tilted and off-origin too — the axes the bisector tries are the data's own
for _n in (128, 2048):
    bm = bmesh.new()
    ring_verts(bm, _n, 7.5, center=(3, -4, 5), normal=(1, 2, -0.5))
    _g = ER._find_circles(bm.verts[:])
    check("perfect tilted ring, %d verts -> exactly 1 group" % _n,
          len(_g) == 1 and radii(bm) == [7.5], "%d Gruppen %s" % (len(_g), radii(bm)))
    bm.free()

# the point of it: a shaved ring loses its shape. Resize a high-res ring and
# every vertex must still sit on ONE circle around ONE centre.
bm = bmesh.new(); _hr = ring_verts(bm, 2048, 1.0)
for vv in bm.verts: vv.select = True
_s, _sk = ER._resize_selection(bm, 12.0)
_c = sum((v.co for v in _hr), Vector()) / len(_hr)
_d = [(v.co - _c).length for v in _hr]
check("2048-vert ring resized -> one centre, one radius",
      (_s, _sk) == (1, 0) and max(_d) - min(_d) < 1e-4,
      "set=%s spread=%.2e" % ((_s, _sk), max(_d) - min(_d)))
bm.free()

# Patrick's round trip: 1 -> 50 -> 1 must land back where it started
bm = bmesh.new(); _rt = ring_verts(bm, 2048, 1.0)
_before = [v.co.copy() for v in _rt]
for vv in bm.verts: vv.select = True
ER._resize_selection(bm, 50.0)
ER._resize_selection(bm, 1.0)
_drift = max((v.co - b).length for v, b in zip(_rt, _before))
check("round trip 1 -> 50 -> 1 comes back exactly", _drift < 1e-5,
      "max drift %.2e" % _drift); bm.free()

# and the shape this guard must NOT break: a wide short tube still splits into
# its real rings, because there EVERY cluster is a whole ring
for _r in (4.0, 20.0, 60.0):
    bm = stacked([0.0, 2.0], n=64, r=_r)
    check("wide tube r=%g still splits into 2 rings" % _r, n_valid(bm) == 2,
          "got %d" % n_valid(bm)); bm.free()


# The ring that actually failed, lifted straight out of Patrick's model. It is
# a unit ring sitting ~110 units from the origin — and Blender stores vertex
# coordinates as float32, so at that distance the rounding noise is ~0.1 % of
# the radius. That uneven gap distribution is what the bisector latched onto;
# a ring built at the origin in float64 is too clean to reproduce it, and a
# synthetic fixture would have missed this exactly the way one missed the fan
# lids. Old behaviour: this ring came back split into 5 + 23 + 4.
_FIELD_RING = [
    (-22.421476364, -85.531570435, 60.278800964),
    (-22.60002327, -85.534851074, 60.359668732),
    (-22.76358223, -85.565124512, 60.463405609),
    (-22.90586853, -85.621246338, 60.586021423),
    (-23.021411896, -85.701057434, 60.722805023),
    (-23.105773926, -85.801475525, 60.868503571),
    (-23.155712128, -85.918663025, 61.01751709),
    (-23.169305801, -86.048095703, 61.164112091),
    (-23.146036148, -86.184814453, 61.302658081),
    (-23.0867939, -86.323562622, 61.427837372),
    (-22.993858337, -86.459007263, 61.534832001),
    (-22.870800018, -86.5859375, 61.619537354),
    (-22.72234726, -86.699478149, 61.678688049),
    (-22.554204941, -86.795272827, 61.710021973),
    (-22.372835159, -86.869628906, 61.71232605),
    (-22.185209274, -86.919700623, 61.685516357),
    (-21.998537064, -86.943557739, 61.630622864),
    (-21.819990158, -86.9402771, 61.549755096),
    (-21.656431198, -86.910003662, 61.446018219),
    (-21.514144897, -86.853881836, 61.323402405),
    (-21.398601532, -86.77407074, 61.186618805),
    (-21.314239502, -86.673652649, 61.040920258),
    (-21.2643013, -86.556465149, 60.891906738),
    (-21.250707626, -86.427032471, 60.745311737),
    (-21.27397728, -86.290313721, 60.606761932),
    (-21.333219528, -86.151565552, 60.481586456),
    (-21.42615509, -86.016120911, 60.374591827),
    (-21.549213409, -85.889190674, 60.289886475),
    (-21.697666168, -85.775650024, 60.230735779),
    (-21.865808487, -85.679855347, 60.199401855),
    (-22.047176361, -85.605499268, 60.197097778),
    (-22.234802246, -85.555427551, 60.223907471),
]


def _closed_ring(bm, pts):
    vs = [bm.verts.new(p) for p in pts]
    bm.verts.ensure_lookup_table()
    for i in range(len(vs)):
        bm.edges.new((vs[i], vs[(i + 1) % len(vs)]))
    return vs


bm = bmesh.new(); _closed_ring(bm, _FIELD_RING)
_g = ER._find_circles(bm.verts[:])
check("field ring (unit ring 110 units out) -> exactly 1 group",
      len(_g) == 1 and len(ER._valid_circles(_g)) == 1,
      "%d Gruppen: %s" % (len(_g), [len(vs) for vs, _f in _g])); bm.free()

# and as it is actually used: resized, it has to stay ONE circle around ONE
# centre — a shaved ring gets a second centre and comes back deformed
bm = bmesh.new(); _fr = _closed_ring(bm, _FIELD_RING)
for vv in bm.verts: vv.select = True
_s, _sk = ER._resize_selection(bm, 4.0)
_c = sum((v.co for v in _fr), Vector()) / len(_fr)
_d = [(v.co - _c).length for v in _fr]
check("field ring resized -> one centre, one radius",
      (_s, _sk) == (1, 0) and max(_d) - min(_d) < 1e-3,
      "set=%s spread=%.2e" % ((_s, _sk), max(_d) - min(_d))); bm.free()

# same mechanism, generated: distance from the origin is what breaks it, not
# the vertex count. Rounded to float32 the way Blender stores coordinates.
def _f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


for _dist in (0.0, 50.0, 110.0, 500.0):
    _ax = Vector((0.3, 0.8, -0.5)).normalized()
    _e1 = _ax.cross(Vector((1, 0, 0))).normalized()
    _e2 = _ax.cross(_e1).normalized()
    _off = Vector((0.2, 0.9, 0.4)).normalized() * _dist
    _pts = []
    for _i in range(64):
        _a = 2 * math.pi * _i / 64
        _p = _off + math.cos(_a) * _e1 + math.sin(_a) * _e2
        _pts.append((_f32(_p.x), _f32(_p.y), _f32(_p.z)))
    bm = bmesh.new(); _closed_ring(bm, _pts)
    _g = ER._find_circles(bm.verts[:])
    check("float32 unit ring %g units from origin -> 1 group" % _dist,
          len(_g) == 1, "%d Gruppen: %s" % (len(_g), [len(vs) for vs, _f in _g]))
    bm.free()


# --- triangle-fan lids (cylinder / cone caps) ---------------------------------
# A TRIFAN lid is a ring plus ONE centre vertex joined to every ring vertex. The
# centre sits at radius 0, so a circle fit over the whole lid is far off and the
# lid was rejected outright ("not a circle") — the ring was never even offered.
# The tracer does find the ring; it only threw it away because it insisted on
# >= 2 cycles. A lone cycle that leaves vertices over IS a split (ring + hub).
def fan_cap(bm, n=16, radius=1.0, center=(0, 0, 0), normal=(0, 0, 1)):
    """A triangle-fan lid: returns (ring verts, centre vert)."""
    vs = ring_verts(bm, n, radius, center=center, normal=normal)
    c = bm.verts.new(Vector(center))
    bm.verts.ensure_lookup_table()
    for v in vs:
        bm.edges.new((c, v))
    return vs, c


bm = bmesh.new(); fan_cap(bm)
check("TRIFAN lid -> 1 circle r=1", radii(bm) == [1.0], "%s" % radii(bm)); bm.free()

bm = bmesh.new(); fan_cap(bm, n=32, radius=5.0, center=(2, -3, 4), normal=(1, 1, 0))
check("TRIFAN lid tilted / off-origin -> 1 circle r=5", radii(bm) == [5.0],
      "%s" % radii(bm)); bm.free()

# resizing a lid moves the ring only — the hub has no radial direction and is
# already the centre, so it must stay exactly where it is
bm = bmesh.new(); _ring, _hub = fan_cap(bm, radius=1.0)
for vv in bm.verts: vv.select = True
_s, _sk = ER._resize_selection(bm, 3.0)
check("TRIFAN lid resize -> ring r=3, hub stays at centre",
      (_s, _sk) == (1, 0)
      and all(abs(v.co.length - 3.0) < 1e-6 for v in _ring)
      and _hub.co.length < 1e-9,
      "set=%s ring=%.4f hub=%.4f" % ((_s, _sk), _ring[0].co.length, _hub.co.length))
bm.free()

# the real-world shape: a cylinder with TRIFAN caps on both ends. Both lids must
# come out as their own ring — this is what Patrick's cylinders actually look
# like straight out of Add > Cylinder (Cap Fill Type: Triangle Fan).
bm = bmesh.new()
_lo = ring_verts(bm, 16, 1.0, center=(0, 0, 0))
_hi = ring_verts(bm, 16, 1.0, center=(0, 0, 2))
for a, b in zip(_lo, _hi): bm.edges.new((a, b))
_cl = bm.verts.new(Vector((0, 0, 0))); _ch = bm.verts.new(Vector((0, 0, 2)))
bm.verts.ensure_lookup_table()
for v in _lo: bm.edges.new((_cl, v))
for v in _hi: bm.edges.new((_ch, v))
check("TRIFAN-capped cylinder -> 2 rings r=1", radii(bm) == [1.0, 1.0],
      "%s" % radii(bm)); bm.free()

# guard the relaxed rule: a plain lone ring must NOT be seen as a split (the
# cycle covers every vertex, so there is nothing left over and nothing to peel)
bm = bmesh.new(); _r = ring_verts(bm, 24, 2.0)
check("lone ring is not a trace split", ER._trace_rings(bm.verts[:]) is None,
      "got %s" % (ER._trace_rings(bm.verts[:]) is not None)); bm.free()

bm = bmesh.new(); ring_verts(bm, 24, 2.0, arc=math.pi)
check("lone arc is not a trace split", ER._trace_rings(bm.verts[:]) is None); bm.free()

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

# Radius 0 is the one value that destroys geometry rather than resizing it:
# every vertex of every selected ring lands on its fitted centre, and with
# several rings selected they all collapse at once. It has to be refused, not
# applied — a "0 circles set to radius 0" style success gives the user no reason
# to reach for undo. Negative values reach the same place: the modal accepts a
# typed minus (3-5 is a legitimate expression) and used to clamp the result up
# to 0, and the redo panel allowed 0 outright.
for _bad in (0.0, -1.0, -0.0001):
    _deselect_all()
    _oz = _ring_object("ER_zero", 16, 1.0)
    _before = [tuple(_v.co) for _v in _oz.data.vertices]
    _res, _err = _run_operator_on([_oz], _oz, _bad)
    _after = [tuple(_v.co) for _v in _oz.data.vertices]
    check("radius %g is refused and moves nothing" % _bad,
          (_res == {'CANCELLED'} or _err is not None) and _before == _after,
          "res=%s err=%s moved=%s" % (_res, _err, _before != _after))
    bpy.data.objects.remove(_oz, do_unlink=True)

# ...while the smallest sane radius still works, so the guard is a floor and not
# a blanket ban on tiny circles
_deselect_all()
_ot = _ring_object("ER_tiny", 16, 1.0)
_res, _err = _run_operator_on([_ot], _ot, 1e-4)
check("a tiny but positive radius still applies",
      _err is None and _res == {'FINISHED'} and abs(_ring_radius(_ot) - 1e-4) < 1e-6,
      "res=%s err=%s r=%.8f" % (_res, _err, _ring_radius(_ot)))
bpy.data.objects.remove(_ot, do_unlink=True)

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

# --- real Blender primitives --------------------------------------------------
# Hand-built bmesh rings are not enough. The triangle-fan lid bug hid behind
# exactly that: a lid built here (ring edges first, then the spokes) traced fine
# while Add > Cylinder failed, because the walk simply took whichever edge came
# first in link_edges. Shapes people actually make must go through the real
# operator, straight from the Add menu.
section("real primitives (Add menu -> operator)")


def _to_object_mode():
    """Back to Object Mode, tolerating a stale active object.

    Tests remove their objects when done, which can leave the view layer with a
    dangling / hidden active object — and then mode_set's poll refuses with
    "Cannot edit hidden object" and takes the whole run down with it.
    """
    if bpy.context.mode == 'OBJECT':
        return
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except RuntimeError:
        # dangling / hidden active object — drop it and try once more
        bpy.context.view_layer.objects.active = None
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            pass


def _primitive(add, **props):
    """Add a primitive, run the operator on everything, return radial distances
    from the object's Z axis (rounded) plus the operator result."""
    _to_object_mode()
    _deselect_all()
    add()
    o = bpy.context.object
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    res = err = None
    try:
        res = bpy.ops.mesh.exact_radius('EXEC_DEFAULT', radius=3.0, **props)
    except Exception as e:                      # a raised error is a FAIL, not a crash
        err = repr(e)
    b = bmesh.from_edit_mesh(o.data)
    rs = sorted({round(math.hypot(v.co.x, v.co.y), 3) for v in b.verts})
    _to_object_mode()
    bpy.data.objects.remove(o, do_unlink=True)
    return rs, res, err


# TRIFAN lids: the ring must land on the radius, the hub stays at 0 (it has no
# radial direction, and it already IS the center)
_rs, _res, _err = _primitive(lambda: bpy.ops.mesh.primitive_cylinder_add(
    vertices=16, radius=1.0, depth=2.0, end_fill_type='TRIFAN'))
check("Add > Cylinder (TRIFAN caps) -> rings at 3, hubs at 0",
      _err is None and _res == {'FINISHED'} and _rs == [0.0, 3.0],
      "err=%s res=%s radii=%s" % (_err, _res, _rs))

_rs, _res, _err = _primitive(lambda: bpy.ops.mesh.primitive_cylinder_add(
    vertices=64, radius=1.0, depth=2.0, end_fill_type='TRIFAN'))
check("Add > Cylinder 64-seg (TRIFAN) -> rings at 3, hubs at 0",
      _err is None and _rs == [0.0, 3.0], "err=%s radii=%s" % (_err, _rs))

_rs, _res, _err = _primitive(lambda: bpy.ops.mesh.primitive_cone_add(
    vertices=32, radius1=1.0, radius2=0.0, depth=2.0, end_fill_type='TRIFAN'))
check("Add > Cone (TRIFAN base) -> base ring at 3",
      _err is None and _res == {'FINISHED'} and 3.0 in _rs,
      "err=%s res=%s radii=%s" % (_err, _res, _rs))

_rs, _res, _err = _primitive(lambda: bpy.ops.mesh.primitive_circle_add(
    vertices=32, radius=1.0, fill_type='TRIFAN'))
check("Add > Circle (TRIFAN disc) -> ring at 3, hub at 0",
      _err is None and _rs == [0.0, 3.0], "err=%s radii=%s" % (_err, _rs))

# and the fills that always worked must keep working
for _fill in ('NGON', 'NOTHING'):
    _rs, _res, _err = _primitive(lambda f=_fill: bpy.ops.mesh.primitive_cylinder_add(
        vertices=16, radius=1.0, depth=2.0, end_fill_type=f))
    check("Add > Cylinder (%s caps) -> both rings at 3" % _fill,
          _err is None and _rs == [3.0], "err=%s radii=%s" % (_err, _rs))

_rs, _res, _err = _primitive(lambda: bpy.ops.mesh.primitive_circle_add(
    vertices=32, radius=1.0, fill_type='NGON'))
check("Add > Circle (NGON disc) -> ring at 3", _err is None and _rs == [3.0],
      "err=%s radii=%s" % (_err, _rs))

# A whole UV sphere is not a supported selection — it is a solid, not a ring —
# and what the tracer makes of one depends on the sphere's own topology, which
# differs between Blender versions. So this only pins down that it does not
# EXPLODE: on 5.1/5.3 the tracer used to invent 20 pieces out of 34 real rows.
# The equator has to be among whatever it does find.
_to_object_mode(); _deselect_all()
bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
_sph = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
_b = bmesh.from_edit_mesh(_sph.data)
_sv = ER._valid_circles(ER._find_circles([v for v in _b.verts if v.select]))
_n = len(_sv)
check("uv sphere 16x8 -> a handful of rings, equator among them",
      2 <= _n <= 12 and any(abs(f[2] - 1.0) < 1e-3 for _vs, f in _sv),
      "n=%d radii=%s" % (_n, sorted({round(f[2], 3) for _vs, f in _sv})))
_to_object_mode()
bpy.data.objects.remove(_sph, do_unlink=True)

# Suzanne is the most tangled thing in the Add menu and, like the sphere, not a
# supported selection. She is however the best guard available against a
# splitter that starts inventing rings in complicated geometry, which is the way
# a change to the bisector or the tracer goes wrong. The exact count depends on
# the version's mesh, so this only pins the league.
_to_object_mode(); _deselect_all()
bpy.ops.mesh.primitive_monkey_add()
_suz = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
_b = bmesh.from_edit_mesh(_suz.data)
_sz = ER._valid_circles(ER._find_circles([v for v in _b.verts if v.select]))
check("suzanne -> a stable handful of rings, no invented ones",
      8 <= len(_sz) <= 20, "n=%d" % len(_sz))
_to_object_mode()
bpy.data.objects.remove(_suz, do_unlink=True)

# a flat grid is not a circle and must stay refused through the real operator
_to_object_mode(); _deselect_all()
bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=10)
_grid = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
_gerr = None
try:
    bpy.ops.mesh.exact_radius('EXEC_DEFAULT', radius=3.0)
except Exception as e:
    _gerr = repr(e)
check("Add > Grid -> refused, nothing moved", _gerr is not None, "err=%s" % _gerr)
_to_object_mode()
bpy.data.objects.remove(_grid, do_unlink=True)

# A torus is a bent tube. Selecting the whole thing is not a documented use,
# but it is easy to do by accident — and however the splitter reads it (the
# minor cross-sections or the major rings), every group it hands back must be
# a WHOLE ring. Half a ring gets its own fitted centre and deforms on resize,
# which is the same defect the field-ring guard exists for. This checks the
# property, not the count, so it holds whichever reading wins.
_to_object_mode(); _deselect_all()
bpy.ops.mesh.primitive_torus_add(major_segments=24, minor_segments=12,
                                 major_radius=1.0, minor_radius=0.25)
_tor = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
_b = bmesh.from_edit_mesh(_tor.data)
_tv = ER._valid_circles(ER._find_circles([v for v in _b.verts if v.select]))
_spans = [ER._arc_span(vs, f) for vs, f in _tv]
check("torus -> every group a whole ring, none halved",
      len(_tv) > 0 and min(_spans) > 0.9,
      "n=%d kleinster arc_span %.3f" % (len(_tv), min(_spans) if _spans else -1))
_to_object_mode()
bpy.data.objects.remove(_tor, do_unlink=True)


# --- partial loop selections --------------------------------------------------
# Selecting a loop by hand rarely catches every vertex: a box-select in wireframe
# misses one, an ngon breaks the loop, a Shift+Alt click stops short. Leaving one
# or two out must not change what the selection IS.
#
# It used to. With the top loop of a cylinder one vertex short, the fitted main
# axis tilts a few degrees, the within-ring gaps stop being exactly zero, and the
# knee search picks a ratio deep in the tail of the gap list — proposing thirty
# cuts of two vertices each. That gets thrown out for having sliver clusters, and
# with the real axis gone an in-plane axis wins instead: the tube came back carved
# into sixteen vertical wedges at eight different radii, reported as a green
# "16 circles set to radius 2".
section("partial loop selections")


def _cyl_bm(n, keep_top):
    """Open cylinder as a loose bmesh: whole bottom loop + `keep_top` of the top."""
    _to_object_mode(); _deselect_all()
    bpy.ops.mesh.primitive_cylinder_add(vertices=n, radius=1.0, depth=2.0,
                                        end_fill_type='NOTHING')
    _o = bpy.context.object
    _b = bmesh.new(); _b.from_mesh(_o.data); _b.verts.ensure_lookup_table()
    bpy.data.objects.remove(_o, do_unlink=True)
    _bot = [_v for _v in _b.verts if _v.co.z < 0]
    _top = sorted((_v for _v in _b.verts if _v.co.z > 0),
                  key=lambda _v: math.atan2(_v.co.y, _v.co.x))
    return _b, _bot + _top[:keep_top]


for _keep in (32, 31, 30, 24, 17):
    _pb, _psel = _cyl_bm(32, _keep)
    _pc = ER._find_circles(_psel)
    _pv = ER._valid_circles(_pc)
    for _vs, _f in _pv:
        ER._apply_radius(_vs, _f[0], _f[1], 2.0)
    _pz = {round(_v.co.z, 3) for _v in _psel}
    _pr = sorted({round(math.hypot(_v.co.x, _v.co.y), 3) for _v in _psel})
    check("cylinder with %d/32 of the top loop: two rings, never a wedge stack" % _keep,
          len(_pc) <= 2 and len(_pz) == 2,
          "%d groups (%d valid), %d z levels, radii %s"
          % (len(_pc), len(_pv), len(_pz), _pr if len(_pr) <= 4 else "%d distinct" % len(_pr)))
    _pb.free()


# --- search time budget -------------------------------------------------------
# A pathological selection can send the ring tracer on a walk lasting minutes: a
# dense triangle-fan disc did exactly that (1024 verts took 1.1 s, 2048 several
# minutes). Blender runs the operator synchronously, so there is no Esc and no
# progress bar — the window stops repainting, the user kills Blender and loses
# unsaved work. The search therefore carries a wall-clock budget and gives up
# with a clean error instead of hanging.
#
# This is a SAFETY NET, not a fix. A selection that trips it is still a bug, and
# nothing here may be read as "slow is fine": the speed section below pins the
# real cost, and every operator check demands FINISHED — a timeout returns
# CANCELLED and so FAILS those checks rather than quietly passing them.
#
# These checks expire the budget on purpose instead of leaning on a slow
# selection, so they keep testing the net once the search gets fast.
section("search time budget")


def _expire_budget():
    """Put the module in 'budget already blown' state."""
    ER._deadline = time.perf_counter() - 1.0


def _clear_budget():
    ER._deadline = None


def _fan_disc_bm(n):
    """A real Add > Circle triangle-fan disc as a standalone bmesh."""
    _to_object_mode()
    _deselect_all()
    bpy.ops.mesh.primitive_circle_add(vertices=n, radius=1.0, fill_type='TRIFAN')
    _o = bpy.context.object
    _bm = bmesh.new()
    _bm.from_mesh(_o.data)
    _bm.verts.ensure_lookup_table()
    bpy.data.objects.remove(_o, do_unlink=True)
    return _bm


def _raises_timeout(fn):
    """True if fn() gives up with SearchTimeout (any other error re-raises)."""
    try:
        fn()
    except ER.SearchTimeout:
        return True
    return False


check("the budget is 10 s", ER.SEARCH_BUDGET == 10.0, repr(ER.SEARCH_BUDGET))

_bud_bm = _fan_disc_bm(64)
_bud_verts = _bud_bm.verts[:]
_bud_sel = set(_bud_verts)
_bud_rim = [_v for _v in _bud_verts if _v.co.length > 0.5]

# The net has to sit INSIDE the expensive walk, not just at the entrance to the
# search: the fan disc that started all this is a single connected component, so
# a check that only runs per component would never fire on it.
_expire_budget()
try:
    check("_walk_cycle gives up when the budget is blown",
          _raises_timeout(lambda: ER._walk_cycle(_bud_rim[0], _bud_sel, set())))
    check("_trace_rings gives up when the budget is blown",
          _raises_timeout(lambda: ER._trace_rings(_bud_verts)))
    check("_find_circles gives up when the budget is blown",
          _raises_timeout(lambda: ER._find_circles(_bud_verts)))
finally:
    _clear_budget()

# ...and it must cost nothing when there is time left: same disc, same answer.
check("a live budget leaves the result untouched",
      len(ER._valid_circles(ER._find_circles(_bud_verts))) == 1)
_bud_bm.free()

# Self-calibrating end-to-end proof that the net actually cuts a long search
# short: time the unbounded search, then hand it a quarter of that. Written
# relative to the measured cost on purpose — it keeps working no matter how much
# faster the search gets.
_bud_bm = _fan_disc_bm(1024)
_bud_verts = _bud_bm.verts[:]
_t0 = time.perf_counter()
ER._find_circles(_bud_verts)
_full = time.perf_counter() - _t0

_real_budget = ER.SEARCH_BUDGET
ER.SEARCH_BUDGET = _full / 4.0
try:
    _t0 = time.perf_counter()
    _timed_out = _raises_timeout(lambda: ER._find_circles(_bud_verts))
    _cut = time.perf_counter() - _t0
finally:
    ER.SEARCH_BUDGET = _real_budget
    _clear_budget()
check("a long search is cut short well before it would finish",
      _timed_out and _cut < _full / 2.0,
      "full %.1f ms, budget %.1f ms, gave up after %.1f ms"
      % (_full * 1000, _full / 4.0 * 1000, _cut * 1000))
_bud_bm.free()

# The operator must turn a timeout into a REPORTED error carrying the budget
# message. (Blender turns any {'ERROR'} report into a RuntimeError on the bpy.ops
# call, so an exception here is expected and correct — what must never happen is
# a raw SearchTimeout escaping, which reaches the user as a bare traceback.)
# And nothing may move on the way out.
_deselect_all()
_bud_obj = _ring_object("ER_budget", 16, 1.0)
_before = [tuple(_v.co) for _v in _bud_obj.data.vertices]
_expire_budget()
try:
    _res, _err = _run_operator_on([_bud_obj], _bud_obj, 0.5)
finally:
    _clear_budget()
_after = [tuple(_v.co) for _v in _bud_obj.data.vertices]
check("operator refuses the run on timeout",
      _res == {'CANCELLED'} or _err is not None, "res=%s err=%s" % (_res, _err))
check("operator reports the budget message, never a raw SearchTimeout",
      "gave up after" in (_err or "") and "SearchTimeout" not in (_err or ""),
      _err or "no error reported")
check("geometry is untouched after a timeout", _before == _after)
bpy.data.objects.remove(_bud_obj, do_unlink=True)

# One budget for the whole run, not a fresh one per mesh — otherwise a
# multi-object edit simply multiplies the hang by the number of objects.
_deselect_all()
_bud_a = _ring_object("ER_budget_a", 16, 1.0, (0, 0, 0))
_bud_b = _ring_object("ER_budget_b", 16, 1.0, (8, 0, 0))
_seen_deadlines = []
_real_find = ER._find_circles


def _deadline_spy(sel):
    _seen_deadlines.append(ER._deadline)
    return _real_find(sel)


ER._find_circles = _deadline_spy
try:
    _res, _err = _run_operator_on([_bud_a, _bud_b], _bud_a, 0.5)
finally:
    ER._find_circles = _real_find
check("every mesh in one run shares a single deadline",
      _err is None and _res == {'FINISHED'} and len(_seen_deadlines) == 2
      and _seen_deadlines[0] is not None
      and _seen_deadlines[0] == _seen_deadlines[1],
      "deadlines=%s" % (_seen_deadlines,))
for _o in (_bud_a, _bud_b):
    bpy.data.objects.remove(_o, do_unlink=True)


# --- speed --------------------------------------------------------------------
# Finding the circles is by far the most expensive thing here, and the operator
# used to do it twice per run (once to count, once to resize) — plus a third
# time in invoke. Every F9 tweak paid the whole bill again. These two checks
# nail down that it happens once and that a heavy selection stays well inside
# the ~200 ms where an action still feels instant.
section("speed")

_calls = {"n": 0}
_real_find = ER._find_circles


def _counting_find(sel):
    _calls["n"] += 1
    return _real_find(sel)


_deselect_all()
_perf_me = bpy.data.meshes.new("ER_perf")
_perf_obj = bpy.data.objects.new("ER_perf", _perf_me)
bpy.context.scene.collection.objects.link(_perf_obj)
_pb = bmesh.new()
for _i in range(100):                       # 100 holes in a plate
    ring_verts(_pb, 16, 1.0, center=(_i * 3, (_i % 10) * 3, 0))
_pb.to_mesh(_perf_me); _pb.free()

ER._find_circles = _counting_find
try:
    _t0 = time.perf_counter()
    _res, _err = _run_operator_on([_perf_obj], _perf_obj, 0.7)
    _dt = time.perf_counter() - _t0
finally:
    ER._find_circles = _real_find

check("operator finds the circles exactly once", _calls["n"] == 1,
      "called %d times" % _calls["n"])
check("100 rings / 1600 verts resized in well under 200 ms",
      _err is None and _res == {'FINISHED'} and _dt < 0.2,
      "%.1f ms" % (_dt * 1000))

# the pure core on the same load, so a slow-down is visible even if the operator
# glue changes around it
_pb = bmesh.new()
for _i in range(100):
    ring_verts(_pb, 16, 1.0, center=(_i * 3, (_i % 10) * 3, 0))
_t0 = time.perf_counter(); _found = n_valid(_pb); _dt = time.perf_counter() - _t0
check("_find_circles: 100 rings under 100 ms", _found == 100 and _dt < 0.1,
      "%d in %.1f ms" % (_found, _dt * 1000))
_pb.free()

# a plain ring must not pay for the ring tracer at all (the walk fits a circle
# at every step); 1024 verts is a heavily subdivided hole
_pb = bmesh.new(); ring_verts(_pb, 1024, 5.0)
_t0 = time.perf_counter(); _found = n_valid(_pb); _dt = time.perf_counter() - _t0
check("_find_circles: single 1024-vert ring under 20 ms", _found == 1 and _dt < 0.02,
      "%d in %.1f ms" % (_found, _dt * 1000))
_pb.free()

# Dense triangle-fan discs — the shape this whole release is about, and the one
# that blew up. A hand-built ring skips the tracer through the _is_simple_loop
# fast path and so never measures the walk at all; only a real fan disc does.
# Cost must stay in the same league as the plain ring above, not explode:
# before this was pinned, 1024 verts took 1.1 s and 2048 took over six minutes.
def _timed_find(verts):
    """(seconds, n_valid). A timeout yields None so it FAILS — never passes."""
    _t = time.perf_counter()
    try:
        _n = len(ER._valid_circles(ER._find_circles(verts)))
    except ER.SearchTimeout:
        return time.perf_counter() - _t, None
    return time.perf_counter() - _t, _n


for _n, _limit in ((512, 0.05), (1024, 0.10), (2048, 0.25)):
    _pb = _fan_disc_bm(_n)
    _dt, _got = _timed_find(_pb.verts[:])
    check("_find_circles: %d-vert triangle-fan disc under %d ms" % (_n, _limit * 1000),
          _got == 1 and _dt < _limit,
          "%s in %.1f ms" % ("TIMED OUT" if _got is None else "%d circles" % _got,
                             _dt * 1000))
    _pb.free()

bpy.data.objects.remove(_perf_obj, do_unlink=True)

# --- summary ------------------------------------------------------------------
check("every check in this file ran", len(_results) == EXPECTED_CHECKS,
      "%d of %d — a block was skipped" % (len(_results), EXPECTED_CHECKS))

nf = _results.count(False)
print("\n=== %d/%d passed, %d FAILED  (Blender %s) ===" % (
    len(_results) - nf, len(_results), nf, bpy.app.version_string))
if nf:
    sys.exit(1)
