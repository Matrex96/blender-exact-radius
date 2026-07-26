# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Patrick Tiefenbacher
bl_info = {
    "name": "Exact Radius",
    "author": "Patrick Tiefenbacher",
    "version": (1, 10, 1),
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
import math
import operator as _operator
import time
import numpy as np
from mathutils import Vector
from bpy.props import FloatProperty, EnumProperty, BoolProperty

# A selection is rejected as "not a circle" beyond these limits (see _circle_error)
PLANARITY_MAX = 0.25    # how far out of a single plane the points may sit
RESIDUAL_MAX = 0.20     # how far from a perfect circle the points may sit (rel.)
# A ring-tracing walk gives up once even its best next vertex sits this far off
# the circle traced so far (relative to the radius). Nothing beyond this could
# still pass RESIDUAL_MAX, so walking on only burns time — and a walk that has
# wandered off a ring wanders for the whole component if it is not stopped.
STEP_MAX = 0.35

# Blender runs an operator synchronously: while the search runs there is no Esc
# and no progress bar, so a search that takes minutes is indistinguishable from
# a crash and costs the user their unsaved work. The search therefore carries a
# wall-clock budget and gives up with a reported error.
#
# This is a safety net, NOT a licence to be slow: any selection that reaches it
# is a bug worth fixing. It is deliberately generous, so that hitting it always
# means something is wrong rather than merely big.
SEARCH_BUDGET = 10.0    # seconds


class SearchTimeout(Exception):
    """The circle search ran past SEARCH_BUDGET and gave up."""


_deadline = None        # set for the duration of a search, else None


def _tick():
    """Give up if the search has run past its budget. Called in the hot loops."""
    if _deadline is not None and time.perf_counter() > _deadline:
        raise SearchTimeout


class _budget:
    """Run everything inside under ONE shared deadline.

    Re-entrant on purpose: a multi-object edit searches once per mesh, and each
    of those must eat from the same budget — otherwise the worst case is simply
    multiplied by the number of objects, which is the very thing being bounded.
    """

    def __enter__(self):
        global _deadline
        self._outer = _deadline
        if _deadline is None:
            _deadline = time.perf_counter() + SEARCH_BUDGET
        return self

    def __exit__(self, *exc):
        global _deadline
        _deadline = self._outer
        return False


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


def _is_usable_circle(verts, fit):
    """True if this group is a circle the add-on will act on at all."""
    return fit is not None and _circle_error(verts, fit) is None


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


def _arc_span(verts, fit):
    """Fraction of a full turn the verts cover around the fitted center.

    ~1.0 for a closed ring, ~0.0 for a short wedge. Used by the plane-bisector to
    tell a genuine stacked ring (cuts across a tube into whole cross-sections)
    from the narrow angular wedges an in-plane cut carves out of a wide tube —
    both fit a circle, but only the real ring wraps most of the way round.
    """
    c = np.array(fit[0][:], dtype=float)
    nrm = np.array(fit[1][:], dtype=float)
    ref = np.array([1.0, 0.0, 0.0]) if abs(nrm[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(nrm, ref)
    e1 /= (np.linalg.norm(e1) + 1e-12)
    e2 = np.cross(nrm, e1)
    p = np.array([v.co[:] for v in verts], dtype=float) - c
    ang = np.sort(np.arctan2(p @ e2, p @ e1))
    if ang.size < 2:
        return 0.0
    biggest = max(float(np.max(np.diff(ang))), float(ang[0] + 2.0 * np.pi - ang[-1]))
    return 1.0 - biggest / (2.0 * np.pi)


def _is_full_ring(verts, fit):
    """True if the group is a usable circle that also wraps most of the way
    round — a whole ring, not a short arc or a wedge sliced out of a tube.
    """
    return _is_usable_circle(verts, fit) and _arc_span(verts, fit) > 0.6


def _shell_of(verts, fit):
    """The outer ring of a group, with vertices sitting well inside it dropped.

    A triangle-fan lid is a ring plus ONE hub vertex at radius 0. That hub is a
    tiny minority of the points but it sits a full radius off the circle, so it
    drags the fit's residual over the limit and the whole lid reads as "not a
    circle". Peeling the inside off costs one pass over the points (the fit is
    already in hand), and the caller re-fits the shell.

    Returns the shell, or None when there is nothing inside to peel or the
    inside is too big a share to be hubs (then the group is a real blob).
    """
    if fit is None:
        return None
    c, nrm, radius = fit[0], fit[1], fit[2]
    shell, inside = [], 0
    for v in verts:
        d = v.co - c
        if (d - d.dot(nrm) * nrm).length < 0.5 * radius:
            inside += 1
        else:
            shell.append(v)
    if inside == 0 or len(shell) < 6 or inside * 3 > len(verts):
        return None
    return shell


def _is_ring_cluster(verts):
    """True if `verts` is a whole cross-section ring — hub or no hub.

    Used to score a plane-bisector cut: a cut along the real stacking axis
    turns a piece into whole rings, while any other axis only slices them into
    arcs. A capped cylinder cuts into triangle-fan LIDS, which are whole rings
    with a hub in the middle — so the fit is retried on the shell alone before
    the cluster is written off, otherwise the true axis is never recognised and
    the piece falls to the ring tracer, which carves it into nonsense.
    """
    fit = _fit_circle(verts)
    if _is_full_ring(verts, fit):
        return True
    shell = _shell_of(verts, fit)
    if shell is None:
        return False
    return _is_full_ring(shell, _fit_circle(shell))


def _bisect_by_plane(verts, fit=None):
    """Split a component into parallel clusters along its stacking axis.

    Pulls apart rings stacked in one connected piece (several cross-sections of a
    tube, the two ends of a funnel) — for any number of rings, evenly spaced or
    not. Each principal axis is cut at every gap of at least half its largest
    gap, and scored by how many of the resulting clusters are themselves clean
    circles: the true stacking axis turns into whole rings, while the other axes
    only slice the rings into arcs — so the axis that yields real circles wins,
    regardless of the ring count. A filled face/blob yields no circles and keeps
    its span filled, so it is left unsplit. Returns >= 2 vertex groups or None.

    `fit` is the caller's already-computed _fit_circle for the same vertices,
    passed in to save a fit — it decides how strict the split has to be (below).
    """
    if len(verts) < 6:
        return None
    vlist = list(verts)
    # Is the piece ALREADY one clean, all-the-way-round circle? Then a cut is
    # only believable if every single part of it is a whole ring too — which is
    # exactly the wide-short-tube case this function exists for, where the rings
    # stack so tightly that the whole tube still reads as one flat circle.
    # Anything less (one big ring plus a few shaved-off vertices) is the
    # bisector inventing a seam in a perfect circle: a high-resolution ring has
    # thousands of projections along any in-plane axis, and the widest gap among
    # them can look like a ring-to-ring gap. Those few vertices then get their
    # own fitted centre and the ring comes back subtly deformed.
    whole_is_ring = _is_full_ring(vlist, _fit_circle(vlist) if fit is None else fit)
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
        # Count the clusters that are real cross-section rings: a circle that
        # wraps most of the way round (not a short arc). A wide tube is genuinely
        # ambiguous — it can be read as a few big rings stacked along its axis OR
        # as many small rings stacked sideways — so both readings can come out
        # "all rings". The tie-breaker (below) prefers the reading with the
        # FEWEST, biggest rings, which is the real cross-section; sideways wedge
        # readings always produce more rings, so they lose.
        whole_rings = [_is_ring_cluster(g) for g in groups]
        clean = sum(whole_rings)
        if clean == 0:
            return None
        if clean == len(groups):
            key = (2, -len(groups), sep)     # every cluster a ring -> fewest wins
        else:
            # Only SOME clusters are rings. If the piece is already one clean
            # circle, that is the bisector shaving slivers off a perfect ring
            # rather than finding real rings in it — refuse, unless what falls
            # away is substantial enough to be genuine structure.
            fell_away = sum(len(g) for g, ok in zip(groups, whole_rings) if not ok)
            if whole_is_ring and fell_away * 2 < len(vlist):
                return None
            key = (1, clean, sep)            # only some clusters are rings
        return key, groups

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


def _is_ring_cycle(cyc):
    """True if a traced closed walk may be claimed as one real ring.

    A clean, nearly full, evenly-turning circle of at least 6 vertices. The
    even-turning test is what keeps the tracer from carving block outlines out
    of a filled face patch: those outlines do fit a circle within tolerance,
    so shape alone cannot reject them (see _evenly_turning).
    """
    if cyc is None or len(cyc) < 6:
        return False
    return _is_full_ring(cyc, _fit_circle(cyc)) and _evenly_turning(cyc)


def _walk_cycle(start, sel, used):
    """The best closed walk from `start` over unused selected verts, or None.

    At every step take the neighbour that best continues the ring traced so
    far: once a running circle fit exists, the candidate closest to that
    circle wins; before that (the first, nearly straight steps) the
    straightest continuation wins. A bridge edge points far off the running
    circle, so the walk stays on its own ring and closes there.

    The FIRST step is the exception: there is no direction to continue yet, so
    it can only be tried in every direction. A triangle-fan hub is an ordinary
    neighbour at that point, and stepping onto it first traces a star through
    the middle of the lid instead of its ring — which then reads as "not a
    circle" and loses the ring entirely. Which direction came first in
    `link_edges` decided that, so the same lid worked or failed depending on
    how the mesh was built. Every direction is walked instead, and the first
    one that closes into a real ring wins; the longest closed walk is only the
    fallback.
    """
    fallback = None
    for first in start.link_edges:
        second = first.other_vert(start)
        if second not in sel or second in used:
            continue
        path = [start, second]
        in_path = {start, second}
        while len(path) <= len(sel):
            _tick()         # the walk is where a pathological search burns time
            v, prev = path[-1], path[-2]
            fit = _fit_circle(path[-8:]) if len(path) >= 4 else None
            best = best_score = None
            for e in v.link_edges:
                w = e.other_vert(v)
                if w not in sel or w in used or w is prev:
                    continue
                if w in in_path and (w is not start or len(path) < 3):
                    continue
                if fit is not None:
                    c, nrm, radius = fit[0], fit[1], fit[2]
                    d = w.co - c
                    radial = d - d.dot(nrm) * nrm
                    score = abs(radial.length - radius) / max(radius, 1e-9)
                else:
                    d1, d2 = v.co - prev.co, w.co - v.co
                    if d1.length < 1e-12 or d2.length < 1e-12:
                        score = 2.0
                    else:                       # 0 = straight on, 2 = U-turn
                        score = 1.0 - d1.normalized().dot(d2.normalized())
                if best_score is None or score < best_score:
                    best, best_score = w, score
            if best is None:
                break                           # dead end — try the other way
            if fit is not None and best_score > STEP_MAX:
                break        # even the best next step is nowhere near the ring
            if best is start:                   # closed
                if _is_ring_cycle(path):
                    return path
                if fallback is None or len(path) > len(fallback):
                    fallback = path
                break                           # closed, but not a ring
            path.append(best)
            in_path.add(best)
    return fallback


def _evenly_turning(cycle):
    """True if the cycle turns like a circle: at EVERY vertex, evenly.

    A polygonal ring spreads its 360° of curvature over every vertex; the
    block outlines a best-continuation walk traces out of a filled face patch
    concentrate it in a few 90° corners with straight runs between (and those
    outlines, annoyingly, fit a circle within tolerance). So: no vertex may
    turn much more than the mean, and none may run straight through.
    """
    n = len(cycle)
    turns = []
    for i in range(n):
        a = cycle[i].co - cycle[i - 1].co
        b = cycle[(i + 1) % n].co - cycle[i].co
        if a.length < 1e-12 or b.length < 1e-12:
            return False
        dot = max(-1.0, min(1.0, a.normalized().dot(b.normalized())))
        turns.append(math.acos(dot))
    mean = sum(turns) / n
    return (mean > 1e-9
            and max(turns) <= 2.5 * mean
            and min(turns) >= 0.3 * mean)


def _is_simple_loop(verts):
    """True if the selection is one plain closed ring and nothing else.

    Every vertex has exactly two neighbours inside the selection and it all
    hangs together: then there is nothing branching off it, nothing inside it
    and no second ring, so tracing can only ever hand back the piece itself.
    This is the shape of almost every real selection — a hole, an edge loop —
    and the walk it saves costs a circle fit at every single step, so the two
    cheap passes over the vertices here pay for themselves many times over.
    """
    sel = set(verts)
    if len(sel) < 3:
        return False
    for v in verts:
        n = 0
        for e in v.link_edges:
            if e.other_vert(v) in sel:
                n += 1
                if n > 2:
                    return False
        if n != 2:
            return False
    return len(_connected_groups(verts)) == 1


def _trace_rings(verts):
    """Fallback splitter for rings the plane-bisector cannot separate.

    Rings lying in the SAME plane and bridged into one connected piece (two
    overlapping circles joined by bridge edges) have no separating gap on any
    axis, so _bisect_by_plane returns None for them. Here the edge graph
    itself is followed instead: every closed best-continuation walk that is a
    clean, nearly full, evenly-turning circle of >= 6 verts claims its verts
    as one ring.

    A result only counts as a SPLIT when it actually takes the piece apart:
    either two or more rings were traced, or one ring was traced and vertices
    are left over that it does not contain. The second case is the triangle-fan
    lid every capped cylinder is made of — a ring plus one hub vertex at radius
    0 — where the ring is real but there is only ever one cycle. A lone cycle
    that covers EVERY vertex is just the piece itself (a plain ring, or the
    perimeter of a face patch) and must never split anything.
    Returns >= 2 groups or None.
    """
    if _is_simple_loop(verts):
        return None                     # one plain ring — nothing to peel
    used = set()
    cycles = []
    sel = set(verts)
    failed = 0
    for start in verts:
        _tick()
        if start in used or failed > 32:
            continue
        cyc = _walk_cycle(start, sel, used)
        if _is_ring_cycle(cyc):
            cycles.append(cyc)
            used.update(cyc)
        else:
            failed += 1
    if not cycles:
        return None
    left = [v for v in verts if v not in used]
    if len(cycles) < 2 and (not left or len(left) * 2 >= len(verts)):
        # the lone cycle is the piece itself (nothing left over), or what it
        # leaves behind outweighs it — either way it peeled nothing off
        return None
    return cycles + (_connected_groups(left) if left else [])


def _is_single_ring(verts, fit):
    """True if `verts` is one usable circle, not several rings stacked on it.

    A wide, short tube (radius >> ring spacing) fits a plane well enough to pass
    the flatness test — its out-of-plane spread is tiny next to the big radius —
    so it would be taken for a single circle and then collapse to a flat sliver
    when every vertex is forced onto that one plane. The plane-bisector is the
    honest arbiter: it returns None for a lone ring or arc (it never chops one
    into pieces), and returns >= 2 groups precisely when the selection is really
    several rings stacked along the fit normal — at any radius. So a clean circle
    that the bisector still wants to split is not a single ring.

    The ring-tracer gets the same veto: two coplanar bridged rings can sneak a
    CLEAN combined fit past the residual test — two small circles far apart plus
    their bridges lie close to one big circle through both (a shrunken bridged
    pair), and a spoked wheel's concentric rings average to a clean mid-radius
    circle. The bisector cannot split either (no axis gap), but the tracer finds
    their two real cycles — and like the bisector it never chops a lone ring or
    arc (a single closed walk is not accepted as a split).
    """
    if not _is_usable_circle(verts, fit):
        return False
    # One closed loop that fits a whole circle IS one ring — nothing else can
    # be hiding in it. Rings stacked in a tube are joined by edges ACROSS the
    # rings, so their vertices have four neighbours, not two; and a lone loop
    # that wanders out of a plane (a coil) fails the circle fit above. Worth
    # taking on its own: this is the shape of nearly every real selection, and
    # it skips both the bisector's sort of every projection and the tracer.
    if _is_simple_loop(verts) and _is_full_ring(verts, fit):
        return True
    return (_bisect_by_plane(verts, fit) is None
            and _trace_rings(verts) is None)


def _split_leaves(verts, depth=0):
    """Recursively bisect a component into leaf groups (for stacked rings).

    Stops descending as soon as a group is a single clean ring, so a lone ring
    (or arc) is never chopped into arcs — but a wide, short tube that merely
    *looks* flat is still split, via _is_single_ring.
    """
    _tick()
    fit = _fit_circle(verts)
    if _is_single_ring(verts, fit) or depth >= 8:
        return [verts]
    parts = _bisect_by_plane(verts, fit)
    if not parts:
        parts = _trace_rings(verts)     # coplanar bridged rings — no axis gap
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

    Raises SearchTimeout if the search runs past its budget (see _budget).
    """
    with _budget():
        circles = []
        for comp in _connected_groups(sel):
            _tick()
            fit = _fit_circle(comp)
            if _is_single_ring(comp, fit):
                circles.append((comp, fit))           # one clean circle
                continue
            found = []                                # peel apart stacked rings
            for leaf in _split_leaves(comp):
                f = _fit_circle(leaf)
                if _is_usable_circle(leaf, f):
                    found.append((leaf, f))
            if found:
                circles.extend(found)
            else:
                circles.append((comp, fit))           # one un-usable group = 1 skip
        return circles


def _valid_circles(circles):
    return [(vs, fit) for vs, fit in circles if _is_usable_circle(vs, fit)]


def _timeout_msg():
    return (f"Exact Radius gave up after {SEARCH_BUDGET:g} s — this selection is "
            "too complex to search. Select fewer vertices.")


def _apply_radius(verts, center, normal, radius):
    """Move each vert onto the circle of `radius` around center, in its plane."""
    for v in verts:
        d = v.co - center
        radial = d - d.dot(normal) * normal       # flatten onto the circle plane
        rl = radial.length
        if rl > 1e-9:
            v.co = center + radial * (radius / rl)


def _resize_selection(bm, radius, cursor=None, circles=None):
    """Set every circle in this bmesh's selection to `radius`.

    `cursor` (a local-space Vector) overrides the fitted center when given.
    `circles` is an already-computed _find_circles result for this same
    selection — the operator has to find the circles up front anyway (to count
    them and to refuse an unusable selection), and finding them is by far the
    most expensive thing this add-on does, so it hands the result back in
    rather than paying for it twice.
    Returns (set_count, skipped_count); skipped are selected groups that are not
    usable circles. This is the per-mesh building block the operator runs for
    each object that is in edit mode.
    """
    if circles is None:
        circles = _find_circles(_selected_verts(bm))
    valid = _valid_circles(circles)
    for verts, fit in valid:
        _apply_radius(verts, fit[0] if cursor is None else cursor, fit[1], radius)
    return len(valid), len(circles) - len(valid)


def _edit_meshes(context):
    """Every mesh currently in edit mode (multi-object edit), active one first."""
    objs = [o for o in (getattr(context, "objects_in_mode", None) or [])
            if o.type == 'MESH']
    active = context.edit_object
    if active is not None and active.type == 'MESH':
        if active in objs:
            objs.remove(active)
        objs.insert(0, active)              # active first → prefill from it
    return objs


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
        # validate first (across all edited objects) so a bad selection errors
        # before the modal starts
        total = 0
        first_r = None
        try:
            with _budget():     # one budget for every mesh, not one each
                for o in _edit_meshes(context):
                    bm = bmesh.from_edit_mesh(o.data)   # keep a ref so its verts stay alive
                    valid = _valid_circles(_find_circles(_selected_verts(bm)))
                    total += len(valid)
                    if first_r is None and valid:
                        first_r = round(valid[0][1][2], 4)
        except SearchTimeout:
            self.report({'ERROR'}, _timeout_msg())
            return {'CANCELLED'}
        if total == 0:
            bm = bmesh.from_edit_mesh(context.edit_object.data)
            sel = _selected_verts(bm)
            self.report({'ERROR'}, _circle_error(sel, _fit_circle(sel)) or
                        "Select at least one ring of vertices forming a circle")
            return {'CANCELLED'}
        # pre-fill with the first circle's fitted radius; remember how many
        self._count = total
        self._current = first_r if first_r is not None else 1.0
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
        meshes = _edit_meshes(context)
        # Find the circles ONCE per mesh and keep them: the counts decide
        # whether to refuse the selection at all and whether a 3D-cursor center
        # applies (only meaningful for a single circle total), and the very same
        # groups are then what gets resized. The bmesh wrappers are kept in the
        # list too, so nothing collects them out from under their verts.
        found = []
        total_valid = total_circles = 0
        try:
            with _budget():     # one budget for every mesh, not one each
                for o in meshes:
                    bm = bmesh.from_edit_mesh(o.data)
                    circles = _find_circles(_selected_verts(bm))
                    found.append((o, bm, circles))
                    total_valid += len(_valid_circles(circles))
                    total_circles += len(circles)
        except SearchTimeout:
            # nothing has been moved yet — the search runs before every edit
            self.report({'ERROR'}, _timeout_msg())
            return {'CANCELLED'}
        if total_valid == 0:
            self.report({'ERROR'},
                        "Selection is not a circle — select a ring of vertices")
            return {'CANCELLED'}
        single = total_valid == 1
        for o, bm, circles in found:
            cursor = (_local_cursor(context, o)
                      if self.center_mode == 'CURSOR' and single else None)
            _resize_selection(bm, self.radius, cursor, circles)
            bmesh.update_edit_mesh(o.data)
        n = total_valid
        skipped = total_circles - total_valid
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
        return bpy.context.preferences.addons[__package__].preferences.shortcut
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
    bl_idname = __package__

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
