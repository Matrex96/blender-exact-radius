# Exact Radius

Type an exact numeric radius and turn any selected ring of vertices into a
**perfect circle of that size** — one ring or many at once, at any orientation,
open arcs included. It fits each selection's own plane and center, then sets
every vertex to the radius you type.

Non-destructive, undo-able, no scaling or applying.

<p align="center"><img src="docs/demo-editmode.gif" width="300" alt="Exact Radius demo"></p>

## What you get over Scale

`S` scales by a *factor* — to land on a real radius you'd have to measure the
ring and do the math. Exact Radius takes the number directly: **Alt+R → type the
radius → Enter**, the same flow as Move/Scale/Extrude, with math expressions
built in (type `24/2` to go from a diameter to its radius).

## What you get over LoopTools → Circle

LoopTools' *Circle* also makes a single loop round at a custom radius — for one
clean loop, the two are equivalent. Exact Radius pulls ahead the moment you want
more than that:

- **Arcs stay arcs.** Select part of a ring and it's set to the radius *as an
  arc*. LoopTools closes an open arc into a full circle.
- **Many rings in one press.** Several separate holes, or every cross-section of
  one connected tube — each is fitted and resized independently in a single
  call. LoopTools does one loop, and returns a degenerate result on a connected
  multi-ring selection.
- **Across objects at once.** It resizes rings on every mesh that's in Edit Mode
  together; LoopTools acts on the active object only.
- **No menu, no panel.** The radius is an inline modal entry (with live math),
  not a value you go hunting for in an operator panel.

## Usage

1. In Edit Mode, select one or more rings of vertices — full circles, holes, or
   parts of one (arcs). Several at once is fine: separate holes, or rings stacked
   in one mesh.
2. Press the shortcut (default **Alt+R**), or **Vertex menu → Exact Radius**.
3. **Type the radius and press Enter** — like Move/Scale. You can type a **math
   expression**, e.g. `20/2`. `Esc` cancels. Pressing Enter with nothing typed
   uses the fitted radius.
4. Afterwards, the **F9** redo panel lets you set the center (Auto / 3D Cursor).
   With several rings selected, each keeps its own fitted center.

### Also handy

- **Any orientation just works** — a ring tilted or rotated anywhere in space
  becomes perfectly round, because it fits the selection's own plane.
- **Exact center on partial arcs** — a least-squares circle fit, so even a
  quarter circle gets the correct center and radius.
- **It checks the selection** — a whole face, the whole mesh or a blob gives a
  clear error instead of a mess.

## Preferences

- **Shortcut** — pick the Edit-Mode shortcut from a small list of conflict-free
  presets: **Alt+R** (default), **Ctrl+Alt+R**, **Alt+Shift+R**, or **Disabled**
  (use the *Vertex* menu only). None of them clash with Blender's built-in mesh
  shortcuts.

## Install

- **Blender 4.2+**: Preferences → Get Extensions, or *Install from Disk* with the
  built `.zip`.
- Source: `blender --command extension build` produces the installable zip.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
