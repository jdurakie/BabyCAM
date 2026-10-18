# BabyCAM plan

## Goal and scope

BabyCAM is a quick, cross-platform desktop CAM tool for ordinary 2.5D router
jobs. The normal workflow is **load a DXF → select or create an endmill → assign
profile cuts → inspect a top-down preview → export GRBL G-code**. Linux is the
primary development platform; Windows and macOS are release targets. The UI
should make a small job convenient without turning into a general-purpose CAM
suite.

The first release supports one DXF, one job/setup, one active flat cylindrical
endmill, and many profile operations. Each closed contour can have at most one
profile operation. Profiles can be outside or inside, Through or To depth.
There are no pockets, hole clearing, open-contour cuts, tool changes, machine
connection, 3D preview, or configurable lead-ins/outs in v1. CAMotics
integration can provide 3D inspection later. Multiple active endmills, ramp
entry, G-code Z zero at stock bottom, and G2/G3 output are likely second-revision
work. Operation depths continue to be measured from stock top even if a later
postprocessor supports stock-bottom Z zero.

## DXF import and placement

- Read modelspace LINE, ARC, CIRCLE, LWPOLYLINE, and 2D POLYLINE entities,
  including bulge arcs. Join segments into closed loops where endpoints differ
  by no more than an explicit tolerance (initially 0.01 mm), and report each
  repair. Larger gaps, self-intersections, duplicate/ambiguous loops, and
  impossible offsets cannot receive an operation. Show open geometry for
  reference but do not offer it as a profile cut.
- Honor declared DXF units and convert to mm internally. Ask the user to
  choose units for a unitless or ambiguous DXF. Show unsupported entities
  prominently; they do not silently become toolpaths. Export may proceed after
  acknowledging that warning when all assigned operations are valid.
- Form the placement bounding box from visible, supported modelspace geometry,
  including unassigned contours. Exclude hidden/frozen geometry and
  annotations. Its lower-left corner maps to machine XY (0, 0) by default.
  Keep an editable placement offset; in millimeters, machine XY is
  `DXF XY - import bounding-box minimum + placement offset`. Never silently
  move the drawing to keep cutter motion nonnegative.
- Negative XY or a cutter sweep outside known stock bounds gets a visible,
  unobtrusive warning. These conditions do not require a modal choice or block
  export. When stock width or height is unknown, show the drawing and origin
  without inventing a stock rectangle or claiming a stock-edge check.

## Job, tool, and operation model

- Require measured stock **thickness** and safe Z above stock top. Stock width
  and height are optional. Machine XY (0, 0) stays fixed; the default DXF
  placement puts its bounding-box lower left there. Z zero is the stock top
  for both operations and emitted G-code. All XY rapids occur at safe Z.
- Keep global endmill presets. Selecting one copies it into the project; edits
  to that project copy immediately update all operations referring to it.
  Later edits to a global preset never silently change an existing project.
  Keep tool references in the operation model so adding more active tools
  later does not require redesign. V1 has no per-operation feed, RPM, or
  stepdown overrides.
- V1 tools are flat cylindrical endmills. A newly created tool needs a name,
  tool number, and explicit diameter, feed, plunge rate, spindle RPM,
  stepdown, and answer to **Can plunge into material?** Do not invent
  machining defaults for those
  values. A non-plunging tool can be saved but cannot make v1 profile paths,
  which use controlled vertical plunge. Usable cutting length is optional:
  if provided, reject cuts deeper than it, including Through allowance; if
  absent, show a mild warning and skip that maximum-depth check.
- Require the user to select/create the project tool before assigning cuts.
  Each new operation visibly says **Depth not selected** until the user
  explicitly chooses **Through** or **To depth**. Export is unavailable while
  any operation lacks this choice. Through targets stock thickness plus an
  editable job-level cut-through allowance, initially 0.1 mm. To depth uses a
  positive distance below stock top. The default visual cut-depth mode is
  unset, even when Through is offered prominently.
- Support clicking a contour or selecting multiple contours/layers and
  applying the same side/depth in one action. Default to cutting inside
  profiles before outside profiles, with manual reordering. Give each contour
  an automatic start point away from tabs, dogbones, and tight corners, and
  let the user move it. Present cut direction as **Climb** or
  **Conventional**, with Climb as the initial global default and a per-cut
  override.

## Tabs and dogbones

The creation UI has global **Always create tabs** and **Always create
dogbones** switches. Both start unchecked; unchecked means do not add the
corresponding edit to newly created paths, including outside Through paths.
Checked means request that edit on every newly created profile, even a
partial-depth or inside profile. Switch changes affect future paths only.
Each contour can override either switch, and dogbone candidates and tab
locations can be individually edited. The source DXF stays unchanged.

- **Dogbones:** Find cutter-side sharp corners needing relief: concave
  notches on an outside profile and convex corners of an inside cutout.
  Smooth approximated curves or corners with enough existing relief are not
  candidates. Generate diagonal reliefs with cutter radius plus editable
  job-level extra clearance, initially 0.25 mm. If no corner needs relief,
  an enabled operation succeeds with zero dogbones. Allow a candidate to be
  suppressed or restored, and allow dogbones to be switched per contour.
- **Tabs:** Automatically place tabs per contour, preferring long straight
  edges and supporting arcs/circles where feasible. Use a job-level default
  and per-contour override for minimum and maximum count, initially 2 and 4.
  Keep adding well-spaced valid tabs
  until reaching the maximum or no location remains; fail the placement if
  the result is below the minimum. The initial tab width is measured stock
  thickness, editable as a job default and per contour. Tab **rise** defaults
  to 75% of nominal cut depth, also editable as a default and per contour:
  a 10 mm nominal cut leaves a 7.5 mm high tab, so the cutter goes 2.5 mm
  below the top over it. For Through, calculate the percentage from stock
  thickness, excluding the cut-through allowance. Tabs requested on To depth
  cuts still make raised bridges in those grooves.
- Per-cut **Auto** preserves existing tabs and adds well-spaced tabs until the
  maximum or no valid location remains, failing if below the automatic minimum.
  **Manual** locks the selected cut for centerline clicks that add or remove
  tabs; right-click in the drawing or Esc ends placement. Manual counts are
  user chosen.
  **Clear** removes all tabs from that cut. Stored tab anchors follow DXF
  placement and are reprojected when the cutter path changes.
- A requested edit that cannot fit the contour's local geometry must be
  impossible to create: explain the problem inline, including when a global
  switch requested it during bulk creation. If a tool edit would make an
  existing tab or dogbone invalid, reject that tool edit with an explanation.
  When the cutter sweep approaches or overlaps other assigned geometry meant
  to remain, warn but allow the user to proceed.

## Preview, warnings, and export

Use PySide6 with Qt Widgets for the v1 desktop UI. Build the main window with
`QMainWindow` and the interactive 2D canvas with `QGraphicsView`/
`QGraphicsScene`: draw each contour as a path item and use separate items for
selected-cut handles and overlays. The CAM model and toolpath calculations
remain independent of Qt. Give the canvas most of the space, with
layers/contours on one side, selected-operation settings on the other, and a
prominent **Export GCode** button below the cuts list. Keep the button grey
without cuts, red when a cut cannot be exported, yellow when export is allowed
with warnings, and green when ready. Show only warning and error counts below
the button; make full messages available through **Status details**. Do not
show routine import diagnostics or program bounds as warnings. The workflow
should take a few selections without a wizard or modal dialog for each contour.

Use a top-down, live 2D editor. Refresh it after tool, operation, placement,
start-point, tab, or dogbone edits. Show thin paths for the job, a subtle
cutter-width overlay for the selected operation, final-pass cutter motion,
travel moves, start points, tabs, dogbones, and cut order. Earlier stepdown
passes need no separate inspection UI. Color Through cuts distinctly in the
canvas; use a plain **Through** label/icon in the operations list without
adding symbols to each contour. Support Undo/Redo for assignments, placement,
start points, tabs, and dogbones. Warnings should remain visible without
creating routine modal interruptions or unsolicited cut-side suggestions.

The core computes the same move list for preview and export. Generate GRBL
millimeter absolute-coordinate output (G21, G90, G17, G94, G54), initially
approximating curves and dogbones with short G1 segments at a bounded
tolerance. Emit a controlled vertical plunge, stepdowns, feed moves, safe-Z
retracts, and no M6 tool change in v1. Include tool number, name, and diameter
in header comments. In automatic spindle mode emit M3 S, pause for an
editable job-level delay (initially 2 seconds), and emit M5 after the final
safe-Z retract.
Manual spindle mode omits M3/M5 and uses a plain **start spindle** G-code
comment and export-summary reminder. After the final cut, retract to safe Z,
stop the controlled spindle if applicable, keep the last X, and rapid to the
**highest Y reached by any toolpath** for inspection; do not use stock height
to choose the end position. Then end the program.

Block export on missing settings or depth choices, invalid assigned geometry,
impossible offsets or requested local edits, non-plunging tools, and unsafe
XY rapids. Report warnings without blocking for negative XY, known-stock
boundary excursions, missing usable cutting length, and nearby kept geometry.
V1 does not validate program bounds against a machine travel envelope. First export asks for a `.nc`
destination and the project remembers it; normal overwrite confirmation
applies thereafter.

## Saving and DXF changes

Save a `.babycam` project with its setup, placement, confirmed DXF units,
embedded source DXF, source identity/hash, project tool copy, assignments and
order, path edits, and export destination. Provide Save/Save As and an unsaved
indicator; autosave can come later. The embedded DXF is the active geometry
until the user explicitly refreshes from the external file. If the original
DXF is missing independently of the project, recreate it alongside the
project file. On detected external changes, keep exact contour matches and
require review of changed or unmatched geometry before export. Do not
silently change the saved job.

## Architecture and source material

```text
DXF → import/normalize → contours in mm → operations + path edits → moves
                                     ↓                            ↓
                             project + tool presets       2D preview + GRBL
```

Keep the CAM core independent of Qt. Use typed models for contours, tools,
stock, operations, tabs, reliefs, and moves. Store depths as positive distances
below stock top; v1 outputs `Z = -depth`. A later stock-bottom output option
can use `Z = stock_thickness - depth` without changing operation definitions.
Proposed Python modules: `dxf_import`, `geometry`, `model`, `toolpaths`,
`post/grbl`, `project`, and `ui`. `ezdxf` handles DXF and units; Shapely can
assist with validity and offsets. Pin dependencies and package native builds
separately on Linux, Windows, and macOS. Review PySide6/Qt's LGPLv3
distribution obligations when preparing those packages.

Use `fast_cabinet/gcode.py` as a reference for `Program`, depth scheduling,
profile offsets, tab placement, and GRBL conventions, and
`fast_cabinet/cabinet.py`'s `dogbone_outline` as a geometric reference. Adapt
these into standalone functions. Do not carry over cabinet parts, sheet
packing, SVG Y-flipping, or cabinet-specific waste-web rules. The existing
generator has no DXF parser; import and interactive path edits are new work.
Its tab placer suggests useful straight-segment and spacing ideas, but the
v1 algorithm must support minimum/maximum tab counts and curved contours.

## Implementation sequence and acceptance checks

| Milestone | Result | Done when |
| --- | --- | --- |
| 1. Vertical slice | DXF import, setup, project tool, inside/outside profiles, explicit depth, preview, GRBL export | Keyboard plate outer profile offsets outward; its two inner contours offset inward; generated depths, units, origin, and safe rapids agree with preview. |
| 2. Path edits | Per-contour automatic dogbones and tabs, global creation switches, editable start points | Only cutter-side corners get relief; local placement failures occur at edit time; tab rise/count and G-code agree with preview. |
| 3. Daily use | Global presets and project copies, save/reopen, bulk selection, ordering, Undo/Redo, DXF change review | A saved job reopens identically; editing its project tool updates its paths; external DXF changes never silently alter it. |
| 4. Distribution | Native Linux, Windows, and macOS packages | A clean-machine smoke test opens a DXF and exports equivalent G-code on each OS. |

The Keyboard project's `plate_test/test_plate_2u_cut.dxf` is the first real
fixture: millimeters (`$INSUNITS=4`), three valid closed LWPOLYLINEs on
OUTER, SWITCH, and STABILIZER layers. The inner two are disjoint and fully
inside OUTER. STABILIZER's eight sharp concave corners should **not** get
automatic dogbones on an inside profile. Add a synthetic rectangular inside
cutout to test positive dogbone generation. Keep copies or derived fixtures
in BabyCAM so tests do not depend on another checkout. Add small DXFs for
arcs, bulges, circles, unitless files, joined segments, and malformed
contours. Verify all XY rapids occur at safe Z, final depths match the job,
path edits appear in both preview and output, and warning/blocking behavior
matches the cases above. Inspect representative `.nc` files in a simulator.

## References

- [Qt for Python](https://doc.qt.io/qtforpython-6/) and its
  [deployment guide](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)
- [ezdxf units](https://ezdxf.readthedocs.io/en/stable/concepts/units.html)
- [Shapely buffer/offset behavior](https://shapely.readthedocs.io/en/stable/reference/shapely.buffer.html)
