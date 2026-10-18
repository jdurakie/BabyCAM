# BabyCAM

A small cross-platform desktop CAM tool for DXF-based 2.5D profile cuts.
The current build is the first working vertical slice; [PLAN.md](PLAN.md)
describes the full first release.

## Run

With [uv](https://docs.astral.sh/uv/) installed:

```sh
uv sync
uv run babycam
```

On Ubuntu with an X11 desktop, Qt also needs the system package
`libxcb-cursor0`. If the app reports that `libxcb-cursor.so.0` is missing,
install it with `sudo apt install libxcb-cursor0`, then run `uv run babycam`
again.

Or install the project into a Python 3.11+ virtual environment and run
`python -m babycam`.

## First workflow

1. Open a DXF. The included `tests/fixtures/keyboard_plate.dxf` is a small
   example. Unitless drawings ask for millimeters or inches.
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
   Select each cut and explicitly choose **Through** or **To depth**.
7. Inspect the cutter-center lines and cutter-width sweep, tabs, and dogbones,
   then use **Export GCode** below the cuts list to save a GRBL `.nc` file.
   Grey means no cuts are assigned, red means a required setting blocks export,
   yellow means export is available with warnings, and green means ready.
   **Export details…** explains blockers and warnings. The solid start circle
   matches the endmill diameter.

Dogbones are proposed only at sharp corners on the cutter side of a profile.
Rounded contours and corners on the opposite side show a count of zero.

The current build has one active endmill and no project saving or packaged
installers yet. The preview distinguishes Through cuts by color. Spindle control
starts unchecked; enable it only when the controller should emit M3/M5.
Negative XY is allowed and reported; the stock thickness is required, while
stock width and height are optional. Validate machine setup and cutting
parameters before running exported code.

## Check

```sh
uv run python -m unittest discover -s tests -v
```
