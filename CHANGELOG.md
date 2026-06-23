# Changelog

All notable changes to **Exact Radius**. Versions are git-tagged; built zips
live in `../archive/` (local). This file is the canonical record of what was
released and what was uploaded to extensions.blender.org.

## Uploads to extensions.blender.org

| Version | Built | Uploaded | Store status |
|---------|-------|----------|--------------|
| 1.9.1   | 2026-06-23 | 2026-06-23 | submitted — awaiting moderation |

_(Mark "Uploaded" + status here whenever a version is submitted/approved.)_

---

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
