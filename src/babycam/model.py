"""CAM data kept independent of the desktop toolkit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

Point = tuple[float, float]


@dataclass(frozen=True)
class Contour:
    id: str
    layer: str
    points: tuple[Point, ...]  # Closed: last point equals first point.
    error: str | None = None


@dataclass(frozen=True)
class ReferencePath:
    layer: str
    points: tuple[Point, ...]


@dataclass
class Drawing:
    path: Path
    units: str
    contours: list[Contour]
    references: list[ReferencePath]
    warnings: list[str]
    bounds: tuple[float, float, float, float]


@dataclass
class Tool:
    number: int = 1
    name: str = ""
    diameter: float | None = None
    feed: float | None = None
    plunge: float | None = None
    rpm: int | None = None
    stepdown: float | None = None
    can_plunge: bool = False
    cutting_length: float | None = None


@dataclass
class Setup:
    thickness: float | None = None
    safe_z: float | None = None
    offset_x: float = 0.0
    offset_y: float = 0.0
    through_allowance: float = 0.1
    spindle_delay: float = 2.0
    automatic_spindle: bool = False
    default_tabs: bool = False
    default_dogbones: bool = False
    tab_min: int = 2
    tab_max: int = 4
    tab_width: float | None = None  # None follows measured stock thickness.
    tab_rise: float = 75.0
    dogbone_clearance: float = 0.25
    stock_width: float | None = None
    stock_height: float | None = None


@dataclass
class Operation:
    contour_id: str
    side: str  # inside | outside
    depth_mode: str | None = None  # through | depth
    depth: float | None = None
    direction: str = "climb"  # climb | conventional
    tabs: bool = False
    tab_anchors: tuple[Point, ...] | None = None  # None uses automatic placement.
    dogbones: bool = False
    tab_min: int | None = None
    tab_max: int | None = None
    tab_width: float | None = None
    tab_rise: float | None = None
    suppressed_dogbones: set[int] = field(default_factory=set)


@dataclass
class Job:
    drawing: Drawing | None = None
    tool: Tool = field(default_factory=Tool)
    setup: Setup = field(default_factory=Setup)
    operations: list[Operation] = field(default_factory=list)
