# BabyCAM plan

## Goal

A fast desktop workflow for ordinary 2.5D router jobs: open a DXF, choose an
endmill, assign operations to visible geometry, inspect the toolpaths, and save
GRBL G-code. Linux is the primary development platform; Windows and macOS are
release targets. All internal dimensions and emitted coordinates use mm.

## First useful release

1. **Open and inspect DXF.** Import modelspace geometry, show its layers and
   dimensions, and fit it in a pan/zoom canvas. Support LINE, ARC, CIRCLE,
   LWPOLYLINE, and 2D POLYLINE, including bulge arcs and simple joined loops.
   Show unsupported, open, duplicate, or invalid geometry as explicit import
   warnings. Require the user to confirm units when the DXF is unitless or its
   units are ambiguous. Convert once to mm; retain the source units in the
   project. Do not silently alter geometry to make a cut possible.
2. **Set up the job.** Define stock width, height, thickness, XY origin, Z zero
   on stock top, safe Z, and a single active endmill. Draw the stock boundary and
   origin on the canvas. The first postprocessor targets GRBL with G21, G90,
   G17, G94, and G54.
3. **Assign operations.** Select a contour by clicking it or select a layer to
   batch assign. Provide outside profile, inside profile, pocket, and circular
   hole operations. Each operation exposes only its relevant settings: final
   depth, stepdown, stepover for pockets, feed, plunge, spindle speed, cut
   direction, and optional tabs for outside profiles. Offer a "cut through"
   depth from stock thickness plus a small user-set allowance.
4. **Preview and export.** Overlay cutter-center paths, entry points, tabs,
   travel moves, cut order, and depth passes. Give a compact job summary and
   block export on invalid geometry, impossible offsets, out-of-stock motion,
   unsafe rapid XY motion, or missing required settings. Write one `.nc` file
   for the active endmill; tool changes are a later feature.
5. **Reopen a job.** Save a small project file with the DXF reference/hash,
   confirmed units, stock/setup, tool snapshot, operation assignments, and
   order. Keep a separate editable tool library. Warn when a referenced DXF
   has changed or moved.

The first release does not need a machine connection, 3D toolpaths, V-carving,
adaptive clearing, automatic nesting, or a library of postprocessors.

## Main window

The canvas occupies most of the window. A short workflow strip reads
**DXF → Setup → Tool → Operations → Preview/Export**. The left panel lists
layers and selectable contours; the right panel edits the selected operation.
The bottom area lists warnings and shows the generated program summary. A
normal job should take only a few selections, without a wizard or modal dialog
for each contour.

## Code boundaries

```text
DXF file → import/normalize → contours in mm → operations → toolpath moves
                                             ↓                  ↓
                                      project/tool library   preview + GRBL post
```

Keep the CAM core independent of Qt. Use a small typed model for contours,
tools, stock, operations, and moves. The preview and postprocessor must consume
the same calculated moves so they cannot disagree about cut order or clearance.

Proposed Python modules: `dxf_import`, `geometry`, `model`, `toolpaths`,
`post/grbl`, `project`, and `ui`. PySide6/Qt Widgets fits a native desktop UI
and runs on Linux, Windows, and macOS. `ezdxf` handles DXF structure and units;
Shapely handles geometric validity and offsets. Keep dependencies pinned for
reproducible builds. Package separately on each target OS after the core works
on Linux.

## Relationship to fast_cabinet

Use `fast_cabinet/gcode.py` as the reference for
`Program`, depth scheduling, profile offsets, tab placement, circular-hole
clearing, and GRBL conventions. Adapt and test these as standalone functions.
Do not carry over `cabinet.Part`, sheet packing, SVG Y-flipping, or the current
cabinet-specific waste-web rules. A general CAM job needs operation-specific
clearance checks and DXF modelspace coordinates.

The current fast_cabinet generator has **no DXF parser or general pocket
toolpath**. Pocket clearing and contour reconstruction are new work, not
extraction tasks. The existing tests are useful as behavioral examples for
safe rapids, stepdowns, tabs, and bounds.

## Implementation sequence and acceptance checks

| Milestone | Result | Done when |
| --- | --- | --- |
| 1. Vertical slice | Open a simple DXF, select a loop, assign an outside profile, preview, export GRBL | A rectangle and circle produce correct cutter offsets, multiple depths, optional tabs, and safe retracts. |
| 2. Useful operations | Inside profiles, circular holes, and pockets | Nested contours, tight features, and tool-too-large cases either cut correctly or give a clear error. |
| 3. Daily-use polish | Tool library, project save/reopen, layer selection, operation ordering | A saved job reopens with the same toolpaths; changed DXF is detected. |
| 4. Distribution | Native builds for Linux, Windows, and macOS | A clean-machine smoke test opens a DXF and exports matching G-code on each OS. |

Use small DXF fixtures for straight segments, arcs, bulges, circles, nested
loops, unitless files, and malformed contours. Verify generated moves against
invariants: all XY rapids happen above the work, Z never exceeds configured
limits, offsets stay within stock, and the final depth matches the job. Inspect
representative `.nc` files in a simulator before calling a milestone complete.

## Decisions to settle together

1. Confirm GRBL as the first and only controller dialect, with the machine
   origin at stock lower-left and Z zero on top.
2. Confirm that pockets belong in the first useful release. They are the
   largest new CAM algorithm after DXF import.
3. Choose the initial DXF source examples, ideally two or three files you
   regularly cut, to anchor interaction and geometry edge cases.

## References

- [Qt for Python](https://doc.qt.io/qtforpython-6/) and its
  [deployment guide](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)
- [ezdxf units](https://ezdxf.readthedocs.io/en/stable/concepts/units.html)
- [Shapely buffer/offset behavior](https://shapely.readthedocs.io/en/stable/reference/shapely.buffer.html)
