# Changelog

All notable changes to **Exact Radius**. Versions are git-tagged; built zips
live in `../archive/` (local). This file is the canonical record of what was
released and what was uploaded to extensions.blender.org.

## Uploads to extensions.blender.org

| Version | Built | Uploaded | Store status |
|---------|-------|----------|--------------|
| 1.10.0  | 2026-07-02 | — | built; new: coplanar bridged circles are found and set (ring-tracing fallback) |
| 1.9.4   | 2026-06-28 | — | built; addresses reviewer feedback (`__package__`) — upload as the review update |
| 1.9.3   | 2026-06-25 | — | built; superseded by 1.9.4 (not uploaded) |
| 1.9.2   | 2026-06-24 | — | superseded by 1.9.3 (not uploaded) |
| 1.9.1   | 2026-06-23 | 2026-06-23 | submitted — awaiting moderation |

_(Mark "Uploaded" + status here whenever a version is submitted/approved.)_

---

## 1.10.0 — 2026-07-02
- New: circles lying in the SAME plane and bridged into one connected piece
  (e.g. two overlapping circles joined with Bridge Edge Loops) are now found
  and set individually. The plane bisector cannot separate them — their
  projections overlap on every axis, so there is no gap to cut at. A new
  ring-tracing fallback (`_trace_rings`/`_walk_cycle`) follows the edge graph
  instead: each walk takes the neighbour that best continues the circle traced
  so far (deviation from a running circle fit; straightness before a fit
  exists), so bridge edges — which point far off the running circle — are
  never taken. A traced ring is only accepted if it is a clean, nearly full,
  evenly-turning circle of >= 6 verts (`_evenly_turning`: a real ring spreads
  its curvature over every vertex, while the block outlines such a walk can
  trace out of a filled face patch concentrate it in a few 90° corners — and
  those outlines fit a circle within tolerance, so shape alone cannot reject
  them), and only when at least two such rings exist — a lone cycle never
  splits anything, keeping face patches / grids rejected as before.
- Tests: 92 → 99 checks — bridged coplanar pairs (overlapping / separated /
  different radii / jittered / through the real operator), plus a 10×10 face
  patch guarding the fallback against carving block outlines. Green on
  4.5 / 5.0 / 5.3.

## 1.9.4 — 2026-06-28
- Fix: address extensions.blender.org review feedback — access the add-on
  preferences via `__package__` instead of `__name__` (both the
  `AddonPreferences.bl_idname` and the `preferences.addons[...]` lookup). Under
  the extensions namespace the module is `bl_ext.<repo>.exact_radius`, and
  `__package__` is the documented, future-proof identifier for preferences.
  No behaviour change. (Thanks to the reviewer, @nickberckley.)

## 1.9.3 — 2026-06-25
- Fix: a wide, short cylinder (radius comparable to or larger than the ring
  spacing) was mis-read and destroyed on resize — a clearly fat tube collapsed
  to a flat sliver (read as one circle), and a near-cubic multi-ring tube
  exploded into wedge fragments. Both come from the same ambiguity: a tube can be
  read as a few big rings stacked along its axis or as many small rings stacked
  sideways. The plane bisector now requires each cluster to wrap most of the way
  round (new `_arc_span` helper) and, among readings where every cluster is a
  ring, prefers the one with the FEWEST, biggest rings — the true cross-section.
  Tubes now split into their real rings at every radius and aspect ratio.
- Tests: fat / short stacks (radius 4–60) plus a round-trip integrity suite that
  resizes 2- to 7-ring tubes big → small → tiny → back and checks the tube stays
  whole every step. 58 checks, green on 4.5 / 5.0 / 5.3.
- Docs: correct the LoopTools comparison in the README — it does set a custom
  radius and is equivalent for a single loop; the real differences are arc
  *preservation* (it closes open arcs), several rings of one connected mesh in
  one call, multi-object edit, and inline `E`/`S`/`G`-style numeric entry.

## 1.9.2 — 2026-06-24
- Add project `website` (public GitHub mirror) to the manifest, for source +
  issue reporting. (Folded into 1.9.3; never uploaded on its own.)

## 1.9.1 — 2026-06-23
- Fix: multi-object `invoke`/`execute` kept the edit-mode bmesh inline without a
  local reference, so it was garbage-collected mid-use (`ReferenceError: BMesh
  data of type BMVert has been removed`). Hold the bmesh in a local.
- Tests: add real-operator integration checks (single + multi object via
  `bpy.ops` in edit mode) that reproduce that error; 42 checks total.

## 1.9.0 — 2026-06-23
- Multi-object edit: resize circles across every mesh in edit mode at once, not
  only the active object.
- Headless test suite (`tests/`, run via `tests/run.sh` across all installed
  Blender versions); excluded from the built zip.

## 1.8.1 — 2026-06-23
- Multi-circle: one selection can hold many rings (separate holes, or rings
  stacked in one connected mesh); each is fitted and resized independently with
  an "N set / M skipped" count.
- Robust stacking split (knee detection) — works for any number of evenly- or
  unevenly-spaced rings, fixing a silent skip on 5+ evenly-stacked rings.
- Shortcut: preset dropdown (Alt+R default / Ctrl+Alt+R / Alt+Shift+R / Disabled).
- Preferences redesign: shortcut on top, usage help collapsed by default.
- GPL copyright line in source + manifest `copyright` field.

## 1.6.0 — initial
- Exact numeric radius on a selected ring of vertices, at any orientation.
- Least-squares circle fit (Kasa) — correct center for full circles and arcs.
- Non-circle validation (clear error on faces/blobs/collinear selections).
- Modal numeric entry with math (`20/2`), Auto / 3D-Cursor center.
