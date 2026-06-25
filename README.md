# Exact Radius

Give a selected ring of vertices a **perfect circle of an exact radius** —
straight from a shortcut, right in the modeling flow: **Alt+R → type the radius →
Enter**, just like Grab / Scale / Extrude. No menu, no operator panel. The entry
even takes math, so `24/2` turns a measured diameter into its radius on the spot.

Works on one ring or many at once, at any orientation, open arcs included —
non-destructive, undo-able, no scaling or applying.

<p align="center"><img src="docs/demo-editmode.gif" width="300" alt="Exact Radius demo"></p>

## How it compares

The shortcut *is* the point: an exact radius without leaving the keyboard or
hunting through a panel. LoopTools' *Circle* can also make a single loop round at
a custom radius — for one clean loop the two are equivalent — but a few things it
can't do:

- **Arcs stay arcs** — LoopTools closes an open arc into a full circle.
- **Every ring of one connected mesh at once** — e.g. all cross-sections of a
  cylinder in a single call, where LoopTools returns a degenerate result.
- **All objects in Edit Mode at once** — LoopTools works on the active one only.

## Usage

1. In Edit Mode, select one or more rings of vertices — full circles, holes, or
   parts of one (arcs). Several at once is fine: separate holes, or rings stacked
   in one mesh.
2. Press the shortcut (default **Alt+R**), or **Vertex menu → Exact Radius**.
3. **Type the radius and press Enter** — like Move/Scale. You can type a **math
   expression**, e.g. `20/2`. `Esc` cancels. Pressing Enter with nothing typed
   uses the fitted radius.
4. Afterwards, the **F9** redo panel sets the center (Auto / 3D Cursor). With
   several rings selected, each keeps its own fitted center.

### Also handy

- **Any orientation just works** — a ring tilted or rotated anywhere becomes
  perfectly round, because it fits the selection's own plane.
- **Exact center on partial arcs** — a least-squares fit, so even a quarter
  circle gets the right center and radius.
- **It checks the selection** — a face, the whole mesh or a blob gives a clear
  error instead of a mess.

## Preferences

- **Shortcut** — pick the Edit-Mode shortcut from a small list of conflict-free
  presets: **Alt+R** (default), **Ctrl+Alt+R**, **Alt+Shift+R**, or **Disabled**
  (use the *Vertex* menu only). None clash with Blender's built-in mesh shortcuts.

## Install

- **Blender 4.2+**: Preferences → Get Extensions, or *Install from Disk* with the
  built `.zip`.
- Source: `blender --command extension build` produces the installable zip.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
