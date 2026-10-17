# BabyCAM plan

## Goal

A fast desktop workflow for ordinary 2.5D router jobs: open a DXF, choose an
endmill, assign inside and outside profiles to visible geometry, adjust their
dogbones and holding tabs, inspect the toolpaths, and save GRBL G-code. Linux is
the primary development platform; Windows and macOS are release targets. All
internal dimensions and emitted coordinates use mm.

## First useful release

1. **Open and inspect DXF.** Import modelspace geometry, show its layers and
   dimensions, and fit it in a pan/zoom canvas. Support LINE, ARC, CIRCLE,
   LWPOLYLINE, and 2D POLYLINE, including bulge arcs and simple joined loops.
   Show unsupported, open, duplicate, or invalid geometry as explicit import
   warnings. Require the user to confirm units when the DXF is unitless or its
   units are ambiguous. Convert once to mm; retain the source units in the
   project. Do not silently alter geometry to make a cut possible.
2. **Set up the job.** Define stock width, height, thickness, XY origin at its
   lower-left corner, Z zero on stock top, safe Z, and one active endmill. Keep
   the DXF coordinates intact. By default, map the lower-left of the entire
   imported modelspace geometry's bounding box to machine XY (0, 0). Show a
   separate, editable X/Y placement offset; machine coordinates are
   `DXF point - DXF bounding-box minimum + placement offset`. Draw the stock
   boundary, placement, and origin on the canvas. The first postprocessor
   targets GRBL with G21, G90, G17, G94, and G54.
3. **Assign operations.** Select a contour by clicking it or select a layer to
   batch assign. Provide outside and inside profiles. Each operation has a final
   depth, stepdown, feed, plunge, spindle speed, and cut direction. Offer a
   "cut through" depth from stock thickness plus a user-set allowance.
4. **Edit paths.** Give each contour independent dogbone and holding-tab
   toggles. When dogbones are enabled, find sharp corners the cutter-side
   offset would leave rounded: concave notches on outside profiles and convex
   corners of inside-profile cutouts. Ignore smooth arc/polyline approximations
   and corners with enough existing relief. Start with diagonal dogbones based
   on the selected tool radius. Highlight proposed corners and let the user
   suppress or restore an individual relief. Keep the source DXF unchanged.
   When tabs are enabled, reuse fast_cabinet's long-straight-segment placement
   idea to suggest positions for that contour. Let the user move, add, and
   remove tabs and set width, remaining height, and count. Default tabs on for
   outside profiles and off for inside profiles; either can be toggled. Display
   the resulting cutter sweep and warn when a relief or tab does not fit its
   local geometry or stock clearance.
5. **Preview and export.** Overlay cutter-center paths, entry points, dogbones,
   tabs, travel moves, cut order, and depth passes. Give a compact job summary
   and block export on invalid geometry, impossible offsets, unsafe rapid XY
   motion, or missing required settings. Warn and require an explicit choice
   when the cutter sweep extends beyond stock; cutting a stock edge can be
   intentional. Write one `.nc` file for the active endmill.
6. **Reopen a job.** Save a small project file with the DXF reference/hash,
   confirmed units, stock/setup, tool snapshot, operation assignments, and
   order, including dogbone and tab edits. Keep a separate editable tool
   library. Warn when a referenced DXF has changed or moved.

The first release does not need pockets, hole clearing, a machine connection,
3D toolpaths, V-carving, adaptive clearing, automatic nesting, or a library of
postprocessors. A second revision should support multiple endmills and a
G-code output option for Z zero on stock bottom. Operation depths remain
defined from stock top in both revisions; only the postprocessor's Z-coordinate
translation changes.

## Main window

The canvas occupies most of the window. A short workflow strip reads
**DXF → Setup → Tool → Operations → Preview/Export**. The left panel lists
layers and selectable contours; the right panel edits the selected operation.
The bottom area lists warnings and shows the generated program summary. A
normal job should take only a few selections, without a wizard or modal dialog
for each contour.

## Code boundaries

```text
DXF file → import/normalize → contours in mm → operations + path edits → moves
                                             ↓                            ↓
                                      project/tool library        preview + GRBL post
```

Keep the CAM core independent of Qt. Use a small typed model for contours,
tools, stock, operations, corner reliefs, tabs, and moves. Store operation depths
as positive distances below the stock top. The first postprocessor emits
`Z = -depth`; a later stock-bottom-zero mode can emit
`Z = stock_thickness - depth`. The preview and postprocessor must consume the
same calculated moves so they cannot disagree about cut order or clearance.
Operations should already refer to a tool ID or snapshot, while the first
release validates that all enabled operations use the one active endmill.

Proposed Python modules: `dxf_import`, `geometry`, `model`, `toolpaths`,
`post/grbl`, `project`, and `ui`. PySide6/Qt Widgets fits a native desktop UI
and runs on Linux, Windows, and macOS. `ezdxf` handles DXF structure and units;
Shapely handles geometric validity and offsets. Keep dependencies pinned for
reproducible builds. Package separately on each target OS after the core works
on Linux.

## Relationship to fast_cabinet

Use `fast_cabinet/gcode.py` as the reference for `Program`, depth scheduling,
profile offsets, tab placement, and GRBL conventions. Use
`fast_cabinet/cabinet.py`'s `dogbone_outline` as a geometric reference. Adapt
and test these as standalone functions.
Do not carry over `cabinet.Part`, sheet packing, SVG Y-flipping, or the current
cabinet-specific waste-web rules. A general CAM job needs operation-specific
clearance checks and DXF modelspace coordinates.

The current fast_cabinet generator has **no DXF parser**. Contour
reconstruction and interactive path edits are new work, not extraction tasks.
The existing tests are useful as behavioral examples for safe rapids,
stepdowns, tabs, and bounds.

## Implementation sequence and acceptance checks

| Milestone | Result | Done when |
| --- | --- | --- |
| 1. Vertical slice | Open the Keyboard plate DXF, assign its outer and inner profiles, preview, export GRBL | The outer contour is offset outward, the two inner contours inward, with correct depths and safe retracts. |
| 2. Path edits | Toggle automatic dogbones and tabs per contour, then edit individual suggestions | The preview and G-code agree; smooth curves do not get false dogbones, and invalid relief or tab sites give clear errors. |
| 3. Daily-use polish | Tool library, project save/reopen, layer selection, operation ordering | A saved job reopens with the same toolpaths and edits; changed DXF is detected. |
| 4. Distribution | Native builds for Linux, Windows, and macOS | A clean-machine smoke test opens a DXF and exports matching G-code on each OS. |

The Keyboard project's `plate_test/test_plate_2u_cut.dxf` is the first real
fixture: millimeters (`$INSUNITS=4`), three valid closed LWPOLYLINEs on OUTER,
SWITCH, and STABILIZER layers. The two inner contours are distinct and fully
inside the outer contour. STABILIZER has eight sharp *concave* corners, useful
for testing cutter-side classification: they should not receive automatic
dogbones when STABILIZER is assigned an inside profile. OUTER and SWITCH are
rounded. Add a small rectangular inside-cutout fixture to test dogbone
generation, and keep copies or derived fixtures in BabyCAM's tests so they do
not depend on another checkout.

Add small DXF fixtures for arcs, bulges, circles, unitless files, and malformed
contours. Verify generated moves against invariants: all XY rapids happen above
the work, Z never exceeds configured limits, cutter sweeps outside stock are
flagged, and the final depth matches the job. Inspect representative `.nc`
files in a simulator before calling a milestone complete.

## Interaction defaults to validate in use

1. Automatic dogbones identify geometric candidates, not assembly intent. The
   user can switch off a contour or an individual proposed corner.
2. Tabs are suggested separately for each contour and remain movable. Verify
   whether the outside-on, inside-off defaults match real jobs.
3. The whole imported modelspace geometry determines the bounding-box origin,
   including unselected geometry. Verify this behavior with drawings that
   contain reference layers or annotations.

## References

- [Qt for Python](https://doc.qt.io/qtforpython-6/) and its
  [deployment guide](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)
- [ezdxf units](https://ezdxf.readthedocs.io/en/stable/concepts/units.html)
- [Shapely buffer/offset behavior](https://shapely.readthedocs.io/en/stable/reference/shapely.buffer.html)
