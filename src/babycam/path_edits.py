"""Relief and holding-tab geometry in millimeters."""

from __future__ import annotations

import math

from .model import Point


def _distance(a: Point, b: Point) -> float:
    return math.dist(a, b)


def _area(points: tuple[Point, ...]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:])) / 2


def dogbone_candidates(points: tuple[Point, ...], side: str,
                       clearance: float) -> tuple[Point, ...]:
    """Centers for sharp corners on the cutter side of the source contour."""
    orientation = 1 if _area(points) > 0 else -1
    result: list[Point] = []
    for i in range(len(points) - 1):
        before, corner, after = points[i - 1 if i else -2], points[i], points[i + 1]
        a, b = _distance(before, corner), _distance(after, corner)
        if min(a, b) < 1e-9:
            continue
        incoming = ((corner[0] - before[0]) / a, (corner[1] - before[1]) / a)
        outgoing = ((after[0] - corner[0]) / b, (after[1] - corner[1]) / b)
        cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
        dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
        turn = math.atan2(cross * orientation, dot)
        if (side == "inside" and turn < math.radians(25)) or (side == "outside" and turn > -math.radians(25)):
            continue
        towards_neighbors = ((before[0] - corner[0]) / a + (after[0] - corner[0]) / b,
                             (before[1] - corner[1]) / a + (after[1] - corner[1]) / b)
        length = math.hypot(*towards_neighbors)
        if length < 1e-8:
            continue
        result.append((corner[0] - clearance * towards_neighbors[0] / length,
                       corner[1] - clearance * towards_neighbors[1] / length))
    return tuple(result)


def add_dogbones(points: tuple[Point, ...], centers: tuple[Point, ...],
                 radius: float) -> tuple[Point, ...]:
    """Visit each relief center from the nearest cutter-center segment and return."""
    if not centers:
        return points
    edits: dict[int, list[tuple[float, Point, Point]]] = {}
    for center in centers:
        nearest: tuple[float, int, float, Point] | None = None
        for i, (a, b) in enumerate(zip(points, points[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length_squared = dx * dx + dy * dy
            if length_squared < 1e-12:
                continue
            t = max(0.0, min(1.0, ((center[0] - a[0]) * dx + (center[1] - a[1]) * dy) / length_squared))
            foot = (a[0] + t * dx, a[1] + t * dy)
            candidate = (_distance(center, foot), i, t, foot)
            if nearest is None or candidate[0] < nearest[0]:
                nearest = candidate
        if nearest is None or nearest[0] > 2.5 * radius:
            raise ValueError("Dogbone relief cannot fit this contour with the selected endmill.")
        _, i, t, foot = nearest
        edits.setdefault(i, []).append((t, foot, center))
    result: list[Point] = [points[0]]
    for i in range(len(points) - 1):
        for _, foot, center in sorted(edits.get(i, [])):
            result.extend((foot, center, foot))
        result.append(points[i + 1])
    return tuple(result)


def lengths(points: tuple[Point, ...]) -> tuple[float, ...]:
    result = [0.0]
    for a, b in zip(points, points[1:]):
        result.append(result[-1] + _distance(a, b))
    return tuple(result)


def point_at(points: tuple[Point, ...], distances: tuple[float, ...], position: float) -> Point:
    for i in range(len(points) - 1):
        if position <= distances[i + 1] + 1e-9:
            span = distances[i + 1] - distances[i]
            if span < 1e-12:
                return points[i]
            fraction = max(0.0, min(1.0, (position - distances[i]) / span))
            return (points[i][0] + (points[i + 1][0] - points[i][0]) * fraction,
                    points[i][1] + (points[i + 1][1] - points[i][1]) * fraction)
    return points[-1]


def nearest_on_path(points: tuple[Point, ...], point: Point) -> tuple[float, Point, float]:
    """Return perimeter distance, closest point, and distance from a point."""
    distances = lengths(points)
    nearest: tuple[float, Point, float] | None = None
    for i, (a, b) in enumerate(zip(points, points[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        span_squared = dx * dx + dy * dy
        if span_squared < 1e-12:
            continue
        t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / span_squared))
        foot = (a[0] + t * dx, a[1] + t * dy)
        candidate = (distances[i] + t * math.sqrt(span_squared), foot, _distance(point, foot))
        if nearest is None or candidate[2] < nearest[2]:
            nearest = candidate
    if nearest is None:
        raise ValueError("Cut has no usable path for tab placement.")
    return nearest


def validate_tab_centers(points: tuple[Point, ...], centers: tuple[float, ...], width: float,
                         radius: float, avoid: tuple[Point, ...] = ()) -> None:
    """Apply the same fit and spacing limits to automatic and manual tabs."""
    distances = lengths(points)
    perimeter = distances[-1]
    margin = width / 2 + radius
    if width <= 0 or radius <= 0 or perimeter <= width:
        raise ValueError("Tab width cannot fit this contour.")
    for center in centers:
        if not math.isfinite(center) or center < margin or perimeter - center < margin:
            raise ValueError("Tab is too close to the cut start or end.")
        if any(_distance(point_at(points, distances, center), p) < margin + radius
               for p in avoid):
            raise ValueError("Tab is too close to a dogbone relief.")
    for i, center in enumerate(centers):
        for other in centers[i + 1:]:
            separation = abs(center - other)
            if min(separation, perimeter - separation) < width + 2 * radius - 1e-8:
                raise ValueError("Tabs are too close together for this endmill and tab width.")


def place_tabs(points: tuple[Point, ...], width: float, minimum: int, maximum: int,
               radius: float, avoid: tuple[Point, ...] = (),
               existing: tuple[float, ...] = ()) -> tuple[float, ...]:
    """Return well-spaced perimeter positions; reject a contour that cannot fit the minimum."""
    distances = lengths(points)
    perimeter = distances[-1]
    margin = width / 2 + radius
    if width <= 0 or minimum < 1 or maximum < minimum or perimeter <= width:
        raise ValueError("Tab width or count cannot fit this contour.")
    if len(existing) > maximum:
        raise ValueError("Existing tabs exceed the maximum; increase it or clear tabs first.")
    validate_tab_centers(points, existing, width, radius, avoid)
    candidates: list[float] = []
    for i, (a, b) in enumerate(zip(points, points[1:])):
        span = distances[i + 1] - distances[i]
        if span < 2 * margin:
            continue
        usable = span - 2 * margin
        count = max(1, math.ceil(usable / max(width / 4, 1.0)))
        for step in range(count + 1):
            candidates.append(distances[i] + margin + usable * step / count)
    if not candidates:
        # Smooth contours have many short chords but can still hold tabs.
        count = max(1, int(perimeter // (width + 2 * radius)))
        candidates = [perimeter * (i + 0.5) / count for i in range(count)]
    candidates = [s for s in candidates if min(s, perimeter - s) >= margin and
                  all(_distance(point_at(points, distances, s), p) >= margin + radius for p in avoid)]
    selected: list[float] = list(existing)
    candidates = [s for s in candidates if all(
        min(abs(s - center), perimeter - abs(s - center)) >= width + 2 * radius - 1e-8
        for center in selected)]
    while len(selected) < maximum and candidates:
        if selected:
            candidate = max(candidates, key=lambda s: min(min(abs(s - t), perimeter - abs(s - t))
                                                           for t in selected))
        else:
            candidate = max(candidates, key=lambda s: min(s, perimeter - s))
        selected.append(candidate)
        candidates = [s for s in candidates if min(abs(s - candidate), perimeter - abs(s - candidate))
                      >= width + 2 * radius - 1e-8]
    if len(selected) < minimum:
        raise ValueError(f"Only {len(selected)} tab(s) fit; this cut needs at least {minimum}.")
    return tuple(sorted(selected))


def split_for_tabs(points: tuple[Point, ...], centers: tuple[float, ...], width: float
                   ) -> tuple[tuple[Point, bool], ...]:
    """Split the closed path at every tab edge; bool marks the following segment."""
    distances = lengths(points)
    perimeter = distances[-1]
    cuts = set(distances)
    for center in centers:
        cuts.add(center - width / 2)
        cuts.add(center + width / 2)
    ordered = sorted(cuts)
    result: list[tuple[Point, bool]] = []
    for i, position in enumerate(ordered):
        following = (position + ordered[i + 1]) / 2 if i + 1 < len(ordered) else position
        in_tab = any(abs(following - center) < width / 2 - 1e-8 for center in centers)
        result.append((point_at(points, distances, position), in_tab))
    return tuple(result)
