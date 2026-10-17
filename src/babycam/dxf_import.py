"""Import supported DXF geometry as millimeter contours."""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf import path as ezpath, units
from ezdxf.lldxf.const import DXFStructureError
from shapely.geometry import Polygon
from shapely.validation import explain_validity

from .model import Contour, Drawing, Point, ReferencePath

SUPPORTED = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}
JOIN_TOLERANCE_MM = 0.01
FLATTEN_TOLERANCE_MM = 0.02


class UnitSelectionRequired(ValueError):
    """The drawing does not declare useful length units."""


def _minimal_dxf(path: Path) -> tuple[ezdxf.document.Drawing, list[str]]:
    """Recover simple entity-only DXFs without modern subclass markers.

    Some otherwise usable CAD exports (including the Keyboard fixture) have
    LWPOLYLINE records but omit required AcDbPolyline tags. Keep this fallback
    deliberately narrow; unknown entity records are reported, not guessed.
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) % 2:
        raise ValueError("DXF has an incomplete group-code pair")
    try:
        tags = [(int(lines[i].strip()), lines[i + 1].strip()) for i in range(0, len(lines), 2)]
    except ValueError as exc:
        raise ValueError("DXF contains an invalid group code") from exc

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    warnings = ["Imported a minimal DXF with missing entity subclass records."]
    section = ""
    current_type: str | None = None
    fields: list[tuple[int, str]] = []
    unit_code = 0

    def first(code: int, default: str = "0") -> str:
        return next((value for key, value in fields if key == code), default)

    def emit() -> None:
        nonlocal fields
        if current_type is None:
            return
        layer = first(8, "0")
        attrs = {"layer": layer}
        try:
            if current_type == "LWPOLYLINE":
                vertices: list[list[float]] = []
                for code, value in fields:
                    if code == 10:
                        vertices.append([float(value), 0.0, 0.0])
                    elif code == 20 and vertices:
                        vertices[-1][1] = float(value)
                    elif code == 42 and vertices:
                        vertices[-1][2] = float(value)
                if len(vertices) < 2:
                    raise ValueError("fewer than two vertices")
                msp.add_lwpolyline(vertices, format="xyb", close=bool(int(first(70)) & 1), dxfattribs=attrs)
            elif current_type == "LINE":
                msp.add_line((float(first(10)), float(first(20))),
                             (float(first(11)), float(first(21))), dxfattribs=attrs)
            elif current_type == "CIRCLE":
                msp.add_circle((float(first(10)), float(first(20))), float(first(40)), dxfattribs=attrs)
            elif current_type == "ARC":
                msp.add_arc((float(first(10)), float(first(20))), float(first(40)),
                            float(first(50)), float(first(51)), dxfattribs=attrs)
            else:
                warnings.append(f"Skipped unsupported minimal-DXF entity: {current_type}.")
        except (TypeError, ValueError) as exc:
            warnings.append(f"Skipped malformed {current_type} on {layer}: {exc}.")

    for index, (code, value) in enumerate(tags):
        if code == 0 and value == "SECTION":
            emit()
            current_type, fields = None, []
            section = tags[index + 1][1] if index + 1 < len(tags) and tags[index + 1][0] == 2 else ""
        elif code == 0 and value == "ENDSEC":
            emit()
            current_type, fields = None, []
            section = ""
        elif section == "HEADER" and code == 9 and value == "$INSUNITS":
            if index + 1 < len(tags) and tags[index + 1][0] == 70:
                unit_code = int(tags[index + 1][1])
        elif section == "ENTITIES" and code == 0:
            emit()
            current_type, fields = value, []
        elif section == "ENTITIES" and current_type is not None:
            fields.append((code, value))
    emit()
    doc.units = unit_code
    return doc, warnings


def _close(points: list[Point]) -> tuple[Point, ...]:
    cleaned = [points[0]]
    for point in points[1:]:
        if math.dist(point, cleaned[-1]) > 1e-8:
            cleaned.append(point)
    if math.dist(cleaned[0], cleaned[-1]) <= JOIN_TOLERANCE_MM:
        cleaned[-1] = cleaned[0]
    else:
        cleaned.append(cleaned[0])
    return tuple(cleaned)


def _contour(identifier: str, layer: str, points: list[Point]) -> Contour:
    closed = _close(points)
    error = None
    if len(closed) < 4:
        error = "Contour has fewer than three distinct points"
    else:
        polygon = Polygon(closed)
        if polygon.is_empty or polygon.area <= 1e-8 or not polygon.is_valid:
            error = f"Invalid contour: {explain_validity(polygon)}"
    return Contour(identifier, layer, closed, error)


def _join_segments(segments: list[tuple[str, str, list[Point]]]) -> tuple[list[Contour], list[ReferencePath]]:
    contours: list[Contour] = []
    references: list[ReferencePath] = []
    pending = segments[:]
    while pending:
        identifier, layer, points = pending.pop(0)
        if len(points) < 2:
            continue
        changed = True
        while changed and math.dist(points[0], points[-1]) > JOIN_TOLERANCE_MM:
            changed = False
            for index, (other_id, other_layer, other) in enumerate(pending):
                if layer != other_layer or len(other) < 2:
                    continue
                if math.dist(points[-1], other[0]) <= JOIN_TOLERANCE_MM:
                    points += other[1:]
                elif math.dist(points[-1], other[-1]) <= JOIN_TOLERANCE_MM:
                    points += list(reversed(other[:-1]))
                elif math.dist(points[0], other[-1]) <= JOIN_TOLERANCE_MM:
                    points = other[:-1] + points
                elif math.dist(points[0], other[0]) <= JOIN_TOLERANCE_MM:
                    points = list(reversed(other[1:])) + points
                else:
                    continue
                identifier += "+" + other_id
                pending.pop(index)
                changed = True
                break
        if math.dist(points[0], points[-1]) <= JOIN_TOLERANCE_MM:
            contours.append(_contour(identifier, layer, points))
        else:
            references.append(ReferencePath(layer, tuple(points)))
    return contours, references


def load_dxf(file_path: str | Path, unit_override: int | None = None) -> Drawing:
    source = Path(file_path)
    warnings: list[str] = []
    try:
        doc = ezdxf.readfile(source)
    except DXFStructureError:
        doc, warnings = _minimal_dxf(source)

    source_units = unit_override if unit_override is not None else doc.units
    if source_units == 0:
        raise UnitSelectionRequired("DXF has no declared length units")
    try:
        scale = units.conversion_factor(source_units, units.MM)
    except (TypeError, ValueError) as exc:
        raise UnitSelectionRequired(f"Unsupported DXF length unit: {source_units}") from exc
    if not math.isfinite(scale) or scale <= 0:
        raise UnitSelectionRequired(f"Unsupported DXF length unit: {source_units}")

    contours: list[Contour] = []
    references: list[ReferencePath] = []
    segments: list[tuple[str, str, list[Point]]] = []
    seen_polygons: list[Polygon] = []
    for entity in doc.modelspace():
        kind = entity.dxftype()
        layer_name = entity.dxf.get("layer", "0")
        try:
            layer = doc.layers.get(layer_name)
            hidden = layer.is_off() or layer.is_frozen()
        except ezdxf.DXFTableEntryError:
            hidden = False
        if hidden or entity.dxf.get("invisible", 0):
            continue
        if kind not in SUPPORTED or (kind == "POLYLINE" and entity.is_3d_polyline):
            warnings.append(f"Unsupported visible {kind} on layer {layer_name}.")
            continue
        try:
            graphic = ezpath.make_path(entity)
            vertices = list(graphic.flattening(FLATTEN_TOLERANCE_MM / scale, segments=8))
        except (TypeError, ValueError, DXFStructureError) as exc:
            warnings.append(f"Could not read {kind} on {layer_name}: {exc}.")
            continue
        if not vertices:
            continue
        if any(abs(point.z * scale) > JOIN_TOLERANCE_MM for point in vertices):
            warnings.append(f"Nonplanar {kind} on layer {layer_name} was skipped.")
            continue
        points = [(point.x * scale, point.y * scale) for point in vertices]
        identifier = entity.dxf.get("handle", f"{kind}-{len(contours) + len(segments)}")
        if graphic.is_closed or math.dist(points[0], points[-1]) <= JOIN_TOLERANCE_MM:
            contour = _contour(identifier, layer_name, points)
            if contour.error is None:
                polygon = Polygon(contour.points)
                if any(polygon.equals(other) for other in seen_polygons):
                    contour = Contour(contour.id, contour.layer, contour.points, "Duplicate contour")
                else:
                    seen_polygons.append(polygon)
            contours.append(contour)
        else:
            segments.append((identifier, layer_name, points))
    joined, open_paths = _join_segments(segments)
    contours += joined
    references += open_paths
    for item in joined:
        if "+" in item.id:
            warnings.append(f"Joined DXF segments into closed contour {item.id}.")
    for item in contours:
        if item.error:
            warnings.append(f"{item.layer} contour {item.id}: {item.error}.")
    if references:
        warnings.append(f"{len(references)} open path(s) shown for reference only.")
    all_points = [point for item in contours for point in item.points]
    all_points += [point for item in references for point in item.points]
    if not all_points:
        raise ValueError("DXF has no visible supported 2D geometry")
    xs, ys = zip(*all_points)
    bounds = min(xs), min(ys), max(xs), max(ys)
    return Drawing(source, units.unit_name(source_units), contours, references, warnings, bounds)
