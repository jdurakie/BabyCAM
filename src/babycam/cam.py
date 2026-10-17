"""Profile planning and GRBL output for the first BabyCAM vertical slice."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from shapely.geometry import Polygon

from .model import Contour, Job, Operation, Point


class CAMError(ValueError):
    """A job cannot produce a safe, complete program."""


@dataclass(frozen=True)
class PlannedPath:
    operation: Operation
    points: tuple[Point, ...]
    target_depth: float | None


@dataclass(frozen=True)
class Move:
    kind: str  # G0 | G1
    x: float | None = None
    y: float | None = None
    z: float | None = None
    feed: float | None = None


@dataclass(frozen=True)
class Program:
    paths: tuple[PlannedPath, ...]
    moves: tuple[Move, ...]
    warnings: tuple[str, ...]
    bounds: tuple[float, float, float, float, float, float]


def number(value: float) -> str:
    return f"{value:.5f}".rstrip("0").rstrip(".") or "0"


def _positive(value: float | None, label: str) -> float:
    if value is None or not math.isfinite(value) or value <= 0:
        raise CAMError(f"Enter a positive {label}.")
    return value


def _signed_area(points: list[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:])) / 2


def _start_on_long_edge(points: list[Point]) -> tuple[Point, ...]:
    # Start away from tight corners and the many short segments of round offsets.
    index = max(range(len(points) - 1), key=lambda i: math.dist(points[i], points[i + 1]))
    a, b = points[index], points[index + 1]
    middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    return tuple([middle] + points[index + 1:-1] + points[:index + 1] + [middle])


def profile_path(job: Job, operation: Operation) -> tuple[Point, ...]:
    drawing = job.drawing
    if drawing is None:
        raise CAMError("Load a DXF first.")
    contour: Contour | None = next((c for c in drawing.contours if c.id == operation.contour_id), None)
    if contour is None:
        raise CAMError(f"Contour {operation.contour_id} no longer exists.")
    if contour.error:
        raise CAMError(f"{contour.layer} contour: {contour.error}.")
    diameter = _positive(job.tool.diameter, "endmill diameter")
    if operation.side not in {"inside", "outside"}:
        raise CAMError("Choose Inside or Outside for each cut.")
    min_x, min_y, _, _ = drawing.bounds
    points = [(x - min_x + job.setup.offset_x, y - min_y + job.setup.offset_y)
              for x, y in contour.points]
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area <= 1e-8:
        raise CAMError(f"{contour.layer} has invalid closed geometry.")
    signed_radius = diameter / 2 * (1 if operation.side == "outside" else -1)
    radius = diameter / 2
    # Bound the round-corner chord error to about 0.02 mm even for large bits.
    chord_tolerance = min(0.02, radius / 2)
    quarter_segments = max(8, math.ceil(math.pi / (4 * math.acos(1 - chord_tolerance / radius))))
    offset = polygon.buffer(signed_radius, quad_segs=quarter_segments, join_style="round")
    if offset.is_empty or offset.geom_type != "Polygon" or offset.interiors:
        raise CAMError(f"{contour.layer} cannot fit a {number(diameter)} mm {operation.side} profile.")
    ring = list(offset.exterior.coords)
    if len(ring) < 4:
        raise CAMError(f"{contour.layer} offset has too few points.")
    climb = operation.direction == "climb"
    if operation.direction not in {"climb", "conventional"}:
        raise CAMError("Choose Climb or Conventional cut direction.")
    wants_ccw = (operation.side == "inside") == climb
    if (_signed_area(ring) > 0) != wants_ccw:
        ring.reverse()
    return _start_on_long_edge(ring)


def target_depth(job: Job, operation: Operation) -> float:
    if operation.depth_mode == "through":
        return _positive(job.setup.thickness, "measured stock thickness") + job.setup.through_allowance
    if operation.depth_mode == "depth":
        return _positive(operation.depth, "cut depth")
    raise CAMError("Select Through or To depth for every cut.")


def preview_paths(job: Job) -> tuple[list[PlannedPath], list[str]]:
    """Return valid paths even while the rest of the job is incomplete."""
    result: list[PlannedPath] = []
    errors: list[str] = []
    for operation in sorted(job.operations, key=lambda op: op.side == "outside"):
        try:
            points = profile_path(job, operation)
            try:
                depth = target_depth(job, operation)
            except CAMError:
                depth = None
            result.append(PlannedPath(operation, points, depth))
        except CAMError as exc:
            errors.append(str(exc))
    return result, errors


def _depths(total: float, stepdown: float) -> list[float]:
    return [min(total, i * stepdown) for i in range(1, math.ceil(total / stepdown) + 1)]


def plan_program(job: Job) -> Program:
    if job.drawing is None:
        raise CAMError("Load a DXF first.")
    if not job.operations:
        raise CAMError("Assign at least one profile cut.")
    thickness = _positive(job.setup.thickness, "measured stock thickness")
    safe_z = _positive(job.setup.safe_z, "safe Z above stock")
    if job.setup.stock_width is not None:
        _positive(job.setup.stock_width, "stock width")
    if job.setup.stock_height is not None:
        _positive(job.setup.stock_height, "stock height")
    if job.setup.through_allowance < 0 or not math.isfinite(job.setup.through_allowance):
        raise CAMError("Cut-through allowance cannot be negative.")
    if job.setup.spindle_delay < 0 or not math.isfinite(job.setup.spindle_delay):
        raise CAMError("Spindle delay cannot be negative.")
    tool = job.tool
    if not tool.name.strip() or tool.number < 1:
        raise CAMError("Enter an endmill name and positive tool number.")
    _positive(tool.diameter, "endmill diameter")
    feed = _positive(tool.feed, "cut feed")
    plunge = _positive(tool.plunge, "plunge feed")
    _positive(tool.rpm, "spindle RPM")
    stepdown = _positive(tool.stepdown, "stepdown")
    if not tool.can_plunge:
        raise CAMError("This endmill cannot plunge; v1 profiles need a plunging endmill.")
    if tool.cutting_length is not None:
        _positive(tool.cutting_length, "usable cutting length")

    # Stable order: inner features first, outside profiles last.
    operations = sorted(job.operations, key=lambda op: op.side == "outside")
    paths: list[PlannedPath] = []
    for operation in operations:
        depth = target_depth(job, operation)
        if tool.cutting_length is not None and depth > tool.cutting_length + 1e-8:
            raise CAMError(f"Cut depth {number(depth)} mm exceeds the endmill's usable cutting length.")
        paths.append(PlannedPath(operation, profile_path(job, operation), depth))

    warnings: list[str] = []
    if tool.cutting_length is None:
        warnings.append("Usable cutting length is not set; maximum cut depth was not checked.")
    min_x = min(x for path in paths for x, _ in path.points)
    max_x = max(x for path in paths for x, _ in path.points)
    min_y = min(y for path in paths for _, y in path.points)
    max_y = max(y for path in paths for _, y in path.points)
    if min_x < -1e-8 or min_y < -1e-8:
        warnings.append("Cutter path extends into negative XY.")
    if job.setup.stock_width is not None and max_x > job.setup.stock_width + 1e-8:
        warnings.append("Cutter path extends past the entered stock width.")
    if job.setup.stock_height is not None and max_y > job.setup.stock_height + 1e-8:
        warnings.append("Cutter path extends past the entered stock height.")
    if any(path.target_depth > thickness + 1e-8 and path.operation.depth_mode != "through"
           for path in paths):
        warnings.append("A To depth cut extends below measured stock thickness.")

    moves: list[Move] = [Move("G0", z=safe_z)]
    for path in paths:
        start_x, start_y = path.points[0]
        for depth in _depths(path.target_depth, stepdown):
            if moves[-1].kind != "G0" or moves[-1].z != safe_z:
                moves.append(Move("G0", z=safe_z))
            moves.append(Move("G0", x=start_x, y=start_y))
            moves.append(Move("G1", z=-depth, feed=plunge))
            moves.extend(Move("G1", x=x, y=y, feed=feed) for x, y in path.points[1:])
        moves.append(Move("G0", z=safe_z))
    # Last X is intentionally retained; stock height does not determine parking.
    moves.append(Move("G0", y=max_y))

    all_x = [move.x for move in moves if move.x is not None]
    all_y = [move.y for move in moves if move.y is not None]
    all_z = [move.z for move in moves if move.z is not None]
    bounds = (min(all_x), max(all_x), min(all_y), max(all_y), min(all_z), max(all_z))
    return Program(tuple(paths), tuple(moves), tuple(warnings), bounds)


def _comment(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._/#-]", " ", text).strip()


def grbl_gcode(job: Job, program: Program) -> str:
    tool = job.tool
    setup = job.setup
    lines = ["(BabyCAM profile job)",
             f"(Tool T{tool.number} {_comment(tool.name)} diameter {number(tool.diameter)} mm)",
             "(XY zero at job origin; Z zero on stock top)",
             "G21 G90 G17 G94", "G54", f"G0 Z{number(setup.safe_z)}"]
    if setup.automatic_spindle:
        lines.append(f"M3 S{tool.rpm}")
        if setup.spindle_delay:
            lines.append(f"G4 P{number(setup.spindle_delay)}")
    else:
        lines.append("(Start spindle manually before cutting)")
    last_feed: float | None = None
    for move in program.moves[1:]:  # Initial safe-Z move was emitted before spindle start.
        axes = [f"{axis}{number(value)}" for axis, value in
                (("X", move.x), ("Y", move.y), ("Z", move.z)) if value is not None]
        if move.kind == "G1" and move.feed != last_feed:
            axes.append(f"F{number(move.feed)}")
            last_feed = move.feed
        lines.append(f"{move.kind} {' '.join(axes)}")
    if setup.automatic_spindle:
        # M5 belongs before the final parking rapid, after the last safe-Z retract.
        parking = lines.pop()
        lines.extend(["M5", parking])
    lines.extend(["M2", ""])
    return "\n".join(lines)
