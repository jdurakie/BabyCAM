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

Or install the project into a Python 3.11+ virtual environment and run
`python -m babycam`.

## First workflow

1. Open a DXF. The included `tests/fixtures/keyboard_plate.dxf` is a small
   example. Unitless drawings ask for millimeters or inches.
2. Enter measured stock thickness and a safe Z height in **Setup**.
3. Enter the endmill's name, diameter, feed, plunge feed, RPM, and stepdown in
   **Endmill**, then confirm it can plunge. These cutting values have no
   fabricated defaults.
4. Select one or more closed contours and assign an inside or outside cut.
   Select each cut and explicitly choose **Through** or **To depth**.
5. Inspect the cutter-center paths and export a GRBL `.nc` file.

The current slice has one active endmill and no tabs, dogbones, project saving,
or packaged installers yet. The preview distinguishes Through cuts by color.
Negative XY is allowed and reported; the stock thickness is required, while
stock width and height are optional. Validate machine setup and cutting
parameters before running exported code.

## Check

```sh
uv run python -m unittest discover -s tests -v
```
