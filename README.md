# BabyCAM

A very small cross-platform desktop CAM tool for DXF-based 2.5D profile cuts.
Entirely vibe-coded.

## Setup and run

With [uv](https://docs.astral.sh/uv/) installed:

```sh
./setup.sh
./run.sh
```

On Windows, use PowerShell:

```powershell
.\setup.ps1
.\run.ps1
```

The setup scripts install the locked dependencies into `.venv`. On macOS and
Linux, `run.sh` requires `uv` on your PATH and starts BabyCAM with
`uv run --locked babycam`. It runs from the project directory automatically,
so you can invoke it from elsewhere; any arguments are passed to BabyCAM.
The run scripts will also sync the environment if needed.

On Ubuntu with an X11 desktop, Qt also needs the system package
`libxcb-cursor0`. If the app reports that `libxcb-cursor.so.0` is missing,
install it with `sudo apt install libxcb-cursor0`, then run `./run.sh`
again.

Or install the project into a Python 3.11+ virtual environment and run
`python -m babycam`.

## First workflow

1. Open a DXF. The included `tests/fixtures/keyboard_plate.dxf` is a small
   example. Unitless drawings ask for millimeters or inches. **Open Recent**
   beside **Open DXF** lists the last 30 successfully opened drawings across
   app launches; its menu can also clear the list.
2. Enter measured stock thickness and a safe Z height in **Setup**.
3. Enter the endmill's name, diameter, feed, plunge feed, RPM, and stepdown in
   **Endmill**, then confirm it can plunge. These cutting values have no
   fabricated defaults.
4. Optionally enable **Always create tabs** or **Always create dogbones** in
   **Setup**. These switches apply to newly assigned cuts; each cut can override
   them in **Cuts**. Tab count, width, and rise can also be changed per cut.
5. Use **Manage presets…** to save an endmill in the global tool library.
   **Use preset** copies it into the current job;
   edits to that job copy do not change the preset.
6. Select one or more closed contours and assign an inside or outside cut.
   Select each cut from the cuts list or by clicking its cutter centerline in
   the drawing, then explicitly choose **Through** or **To depth**.
7. Inspect the cutter-center lines and cutter-width sweep, tabs, and dogbones,
   then use **Export GCode** below the cuts list to save a GRBL `.nc` file.
   Grey means no cuts are assigned, red means a required setting blocks export,
   yellow means export is available with warnings, and green means ready.
   The line below the button shows warning and error counts. **Status details**
   shows the full messages, including the saved file path. The solid start
   circle matches the endmill diameter.

For a selected cut, **Tabs → Auto** keeps existing tabs and adds valid ones up
to the auto maximum. **Manual** enters tab placement: click the selected cut's
centerline to add a tab, or click an existing tab to remove it. Right-click in
the drawing or press Esc to finish. **Clear** removes all tabs from that cut.
Auto minimum and maximum counts do not limit manual placement.

Dogbones are proposed only at sharp corners on the cutter side of a profile.
Rounded contours and corners on the opposite side show a count of zero.

The current build has one active endmill and no project saving or packaged
installers yet. The preview marks cuts with missing or invalid depths red and
uses cyan for valid Through cuts and blue for valid To depth cuts. Spindle control
starts unchecked; enable it only when the controller should emit M3/M5.
Negative XY is allowed and reported; the stock thickness is required, while
stock width and height are optional. Validate machine setup and cutting
parameters before running exported code.

## Check

```sh
uv run python -m unittest discover -s tests -v
```
