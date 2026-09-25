# Copyright (C) 2026 Gabriele Vittori
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""In-viewport sketch engine.

Provides a stateful drawing engine that runs on a 2-D (u, v) plane embedded
in 3-D space.  Used by ``Viewport3DWidget`` for the integrated sketch mode.

Entities are stored as tagged tuples to keep serialisation simple:

    ("line",     (u1,v1), (u2,v2))
    ("polyline", [(u,v), ...])
    ("arc",      (u1,v1), (u2,v2), (um,vm))     # 3-point arc: start, end, mid
    ("circle",   (cu,cv), r)
    ("rect",     (u1,v1), (u2,v2))

Coordinates are always 2-D in the local sketch plane basis.  The plane basis
is the same one used by :class:`em3d_modeler.scene.em_objects.ExtrudedObject`,
so a profile produced here can be passed straight to that class without any
remapping.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Sequence, Tuple, Union

import vtk
from shapely.geometry import LineString, Point
from shapely.ops import polygonize, unary_union

UV   = Tuple[float, float]
Vec3 = Tuple[float, float, float]

ORANGE = (1.0, 0.5, 0.0)
YELLOW = (1.0, 0.85, 0.0)

ARC_SEGMENTS    = 32
CIRCLE_SEGMENTS = 64
RECTANGLE_MODES = ("corner-corner", "center-corner")


class SketchEntity(tuple):
    """Tuple-compatible geometry record with persistent sketch metadata."""

    def __new__(cls, values: Sequence, *, entity_id: Optional[str] = None,
                construction: bool = False,
                rectangle_mode: Optional[str] = None):
        record = super().__new__(cls, tuple(values))
        record.entity_id = entity_id or uuid.uuid4().hex
        record.construction = bool(construction)
        record.rectangle_mode = rectangle_mode
        return record


@dataclass(frozen=True)
class SketchEntityRef:
    entity_id: str

    def to_dict(self) -> dict:
        return {"type": "entity", "entity_id": self.entity_id}


@dataclass(frozen=True)
class SketchPointRef:
    entity_id: str
    point_index: int

    def to_dict(self) -> dict:
        return {
            "type": "point",
            "entity_id": self.entity_id,
            "point_index": self.point_index,
        }


SketchRef = Union[SketchEntityRef, SketchPointRef]


@dataclass
class SketchDimension:
    """A named dimensional relation whose references survive geometry edits."""

    dimension_id: str
    kind: str
    refs: Tuple[SketchRef, ...]
    expression: Optional[str] = None
    value: Optional[float] = None
    resolved_value: Optional[float] = None
    axis: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.dimension_id,
            "kind": self.kind,
            "refs": [ref.to_dict() for ref in self.refs],
            "expression": self.expression,
            "value": self.value,
            "resolved_value": self.resolved_value,
            "axis": self.axis,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SketchDimension":
        refs = tuple(_sketch_ref_from_dict(ref) for ref in data["refs"])
        return cls(
            dimension_id=str(data["id"]),
            kind=str(data["kind"]),
            refs=refs,
            expression=data.get("expression"),
            value=_optional_float(data.get("value")),
            resolved_value=_optional_float(data.get("resolved_value")),
            axis=data.get("axis"),
        )


def _optional_float(value) -> Optional[float]:
    return None if value is None else float(value)


def _sketch_ref_from_dict(data: Mapping) -> SketchRef:
    if data["type"] == "entity":
        return SketchEntityRef(str(data["entity_id"]))
    if data["type"] == "point":
        return SketchPointRef(str(data["entity_id"]), int(data["point_index"]))
    raise ValueError(f"Unknown sketch reference type: {data['type']}")


def _json_geometry(entity: tuple) -> list:
    tag = entity[0]
    if tag == "polyline":
        return [tag, [list(point) for point in entity[1]]]
    if tag in ("line", "arc", "rect"):
        return [tag, *[list(point) for point in entity[1:]]]
    if tag == "circle":
        return [tag, list(entity[1]), float(entity[2])]
    raise ValueError(f"Unknown sketch entity type: {tag}")


def _restore_geometry(data: Sequence) -> tuple:
    tag = data[0]
    if tag == "polyline":
        return tag, [tuple(point) for point in data[1]]
    if tag in ("line", "arc", "rect"):
        return (tag, *[tuple(point) for point in data[1:]])
    if tag == "circle":
        return tag, tuple(data[1]), float(data[2])
    raise ValueError(f"Unknown sketch entity type: {tag}")


# ───────────────────────────────────────────────────────── plane basis
def plane_basis(plane_normal: Vec3) -> Tuple[Vec3, Vec3]:
    """Return ``(u_axis, v_axis)`` orthonormal to *plane_normal*.

    The convention matches ``ExtrudedObject._profile_to_polydata`` so that
    a profile authored on (u, v) is extruded into the same world position
    as it was drawn.
    """
    n = list(plane_normal)
    nm = math.sqrt(sum(c * c for c in n))
    if nm < 1e-12:
        n = [0.0, 0.0, 1.0]
    else:
        n = [c / nm for c in n]
    ref = [1.0, 0.0, 0.0] if (abs(n[1]) > 0.1 or abs(n[2]) > 0.1) else [0.0, 1.0, 0.0]
    u = [n[1] * ref[2] - n[2] * ref[1],
         n[2] * ref[0] - n[0] * ref[2],
         n[0] * ref[1] - n[1] * ref[0]]
    um = math.sqrt(sum(c * c for c in u))
    u = [c / um for c in u]
    v = [u[1] * n[2] - u[2] * n[1],
         u[2] * n[0] - u[0] * n[2],
         u[0] * n[1] - u[1] * n[0]]
    return (u[0], u[1], u[2]), (v[0], v[1], v[2])


def world_to_uv(world: Vec3, origin: Vec3, u_axis: Vec3, v_axis: Vec3) -> UV:
    rx = world[0] - origin[0]
    ry = world[1] - origin[1]
    rz = world[2] - origin[2]
    return (rx * u_axis[0] + ry * u_axis[1] + rz * u_axis[2],
            rx * v_axis[0] + ry * v_axis[1] + rz * v_axis[2])


def uv_to_world(uv: UV, origin: Vec3, u_axis: Vec3, v_axis: Vec3) -> Vec3:
    u, v = uv
    return (origin[0] + u * u_axis[0] + v * v_axis[0],
            origin[1] + u * u_axis[1] + v * v_axis[1],
            origin[2] + u * u_axis[2] + v * v_axis[2])


# ───────────────────────────────────────────────────────── geometry helpers
def _dist(a: UV, b: UV) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _arc_from_3pts(p1: UV, p2: UV, pm: UV) -> Optional[Tuple[UV, float, float, float]]:
    """Return (center, radius, start_angle, sweep_angle) for arc through p1, pm, p2.

    The arc starts at *p1*, ends at *p2*, and passes through *pm* (mid).
    Sweep direction is chosen so that the arc actually contains *pm*.
    """
    ax, ay = p1
    bx, by = pm
    cx, cy = p2
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy)
          + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx)
          + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    center = (ux, uy)
    r = _dist(center, p1)
    a1 = math.atan2(p1[1] - uy, p1[0] - ux)
    a2 = math.atan2(p2[1] - uy, p2[0] - ux)
    am = math.atan2(pm[1] - uy, pm[0] - ux)
    # Choose sweep direction containing am
    sweep_ccw = (a2 - a1) % (2 * math.pi)
    mid_ccw   = (am - a1) % (2 * math.pi)
    if mid_ccw <= sweep_ccw + 1e-6:
        sweep = sweep_ccw
    else:
        sweep = sweep_ccw - 2 * math.pi
    return center, r, a1, sweep


def _arc_polyline(p1: UV, p2: UV, pm: UV, segments: int = ARC_SEGMENTS) -> List[UV]:
    arc = _arc_from_3pts(p1, p2, pm)
    if arc is None:
        return [p1, p2]
    (cx, cy), r, a0, sweep = arc
    pts: List[UV] = []
    for i in range(segments + 1):
        t = i / segments
        a = a0 + sweep * t
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _circle_polyline(c: UV, r: float, segments: int = CIRCLE_SEGMENTS) -> List[UV]:
    cx, cy = c
    pts: List[UV] = []
    for i in range(segments):
        a = 2 * math.pi * i / segments
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(pts[0])
    return pts


def _rect_polyline(p1: UV, p2: UV,
                   mode: str = "corner-corner") -> List[UV]:
    if mode not in RECTANGLE_MODES:
        raise ValueError(f"Unknown rectangle mode: {mode}")
    x1, y1 = p1
    x2, y2 = p2
    if mode == "corner-corner":
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    x1, y1 = 2.0 * x1 - x2, 2.0 * y1 - y2
    left, right = sorted((x1, x2))
    bottom, top = sorted((y1, y2))
    return [(left, bottom), (right, bottom), (right, top),
            (left, top), (left, bottom)]


def _fillet_corner(p0: UV, p1: UV, p2: UV, radius: float) -> List[UV]:
    """Return polyline approximating a fillet of *radius* at corner p1.

    The returned list begins with the trim point on segment p0-p1 and ends
    with the trim point on segment p1-p2; arc samples are inserted between.
    """
    ax, ay = p0[0] - p1[0], p0[1] - p1[1]
    bx, by = p2[0] - p1[0], p2[1] - p1[1]
    la = math.hypot(ax, ay); lb = math.hypot(bx, by)
    if la < 1e-9 or lb < 1e-9:
        return [p1]
    ax, ay = ax / la, ay / la
    bx, by = bx / lb, by / lb
    cosang = max(-1.0, min(1.0, ax * bx + ay * by))
    angle  = math.acos(cosang)
    if angle < 1e-4 or math.pi - angle < 1e-4:
        return [p1]
    d = radius / math.tan(angle / 2.0)
    d = min(d, 0.49 * la, 0.49 * lb)
    t1 = (p1[0] + ax * d, p1[1] + ay * d)
    t2 = (p1[0] + bx * d, p1[1] + by * d)
    bx_, by_ = ax + bx, ay + by
    bm = math.hypot(bx_, by_)
    if bm < 1e-9:
        return [t1, t2]
    bx_, by_ = bx_ / bm, by_ / bm
    r = d * math.tan(angle / 2.0)
    cdist = r / math.sin(angle / 2.0)
    center = (p1[0] + bx_ * cdist, p1[1] + by_ * cdist)
    a1 = math.atan2(t1[1] - center[1], t1[0] - center[0])
    a2 = math.atan2(t2[1] - center[1], t2[0] - center[0])
    sweep = a2 - a1
    while sweep >  math.pi: sweep -= 2 * math.pi
    while sweep < -math.pi: sweep += 2 * math.pi
    n = max(4, ARC_SEGMENTS // 2)
    out: List[UV] = []
    for i in range(n + 1):
        t = i / n
        a = a1 + sweep * t
        out.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a)))
    return out


def _chamfer_corner(p0: UV, p1: UV, p2: UV, dist: float) -> List[UV]:
    ax, ay = p0[0] - p1[0], p0[1] - p1[1]
    bx, by = p2[0] - p1[0], p2[1] - p1[1]
    la = math.hypot(ax, ay); lb = math.hypot(bx, by)
    if la < 1e-9 or lb < 1e-9:
        return [p1]
    ax, ay = ax / la, ay / la
    bx, by = bx / lb, by / lb
    d = min(dist, 0.49 * la, 0.49 * lb)
    t1 = (p1[0] + ax * d, p1[1] + ay * d)
    t2 = (p1[0] + bx * d, p1[1] + by * d)
    return [t1, t2]


def _polygon_area(points: Sequence[UV]) -> float:
    return 0.5 * sum(
        points[index][0] * points[index + 1][1]
        - points[index + 1][0] * points[index][1]
        for index in range(len(points) - 1)
    )


def _point_in_polygon(point: UV, points: Sequence[UV]) -> bool:
    x, y = point
    inside = False
    for start, end in zip(points, points[1:]):
        if ((start[1] > y) != (end[1] > y)):
            crossing_x = start[0] + (y - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
            if x < crossing_x:
                inside = not inside
    return inside


# ───────────────────────────────────────────────────────── engine
class SketchEngine:
    """Stateful sketch session bound to a single plane."""

    TOOLS = ("line", "polyline", "arc", "circle", "rect",
             "fillet", "chamfer", "trim")

    def __init__(self, plane_origin: Vec3, plane_normal: Vec3):
        self.plane_origin = tuple(plane_origin)
        self.plane_normal = tuple(plane_normal)
        self.u_axis, self.v_axis = plane_basis(self.plane_normal)

        self.entities: List[tuple] = []
        self.dimensions: List[SketchDimension] = []
        self.tool: Optional[str] = None
        self.pending: List[UV] = []          # in-progress click points
        self.preview_uv: Optional[UV] = None  # last cursor (u,v) for preview
        self.selected_entity_id: Optional[str] = None
        self._selected_region_anchors: List[UV] = []
        self._active_polyline_id: Optional[str] = None
        self.construction_mode = False
        self.rectangle_mode = "corner-corner"

    def set_construction_mode(self, enabled: bool) -> None:
        """Set construction status for subsequently drawn entities."""
        self.construction_mode = bool(enabled)

    def set_rectangle_mode(self, mode: str) -> None:
        if mode not in RECTANGLE_MODES:
            raise ValueError(f"Unknown rectangle mode: {mode}")
        self.rectangle_mode = mode

    def _append_entity(self, values: Sequence, *,
                       construction: Optional[bool] = None,
                       rectangle_mode: Optional[str] = None) -> SketchEntityRef:
        self._selected_region_anchors.clear()
        record = SketchEntity(
            values,
            construction=self.construction_mode if construction is None else construction,
            rectangle_mode=rectangle_mode,
        )
        self.entities.append(record)
        return SketchEntityRef(record.entity_id)

    def add_line(self, start: UV, end: UV, *,
                 construction: Optional[bool] = None) -> SketchEntityRef:
        return self._append_entity(("line", tuple(start), tuple(end)),
                                   construction=construction)

    def add_polyline(self, points: Sequence[UV], *,
                     construction: Optional[bool] = None) -> SketchEntityRef:
        return self._append_entity(("polyline", [tuple(point) for point in points]),
                                   construction=construction)

    def add_arc(self, start: UV, end: UV, mid: UV, *,
                construction: Optional[bool] = None) -> SketchEntityRef:
        return self._append_entity(("arc", tuple(start), tuple(end), tuple(mid)),
                                   construction=construction)

    def add_circle(self, center: UV, radius: float, *,
                   construction: Optional[bool] = None) -> SketchEntityRef:
        return self._append_entity(("circle", tuple(center), float(radius)),
                                   construction=construction)

    def add_rectangle(self, first: UV, second: UV, *,
                      mode: Optional[str] = None,
                      construction: Optional[bool] = None) -> SketchEntityRef:
        selected_mode = self.rectangle_mode if mode is None else mode
        if selected_mode not in RECTANGLE_MODES:
            raise ValueError(f"Unknown rectangle mode: {selected_mode}")
        return self._append_entity(
            ("rect", tuple(first), tuple(second)),
            construction=construction,
            rectangle_mode=selected_mode,
        )

    def _ensure_entity_records(self) -> None:
        for index, entity in enumerate(self.entities):
            if not isinstance(entity, SketchEntity):
                self.entities[index] = SketchEntity(tuple(entity))

    def entity_ref(self, index: int) -> SketchEntityRef:
        self._ensure_entity_records()
        return SketchEntityRef(self.entities[index].entity_id)

    def point_ref(self, entity: Union[int, SketchEntityRef, str],
                  point_index: int) -> SketchPointRef:
        entity_id = (self.entity_ref(entity).entity_id if isinstance(entity, int)
                     else entity.entity_id if isinstance(entity, SketchEntityRef)
                     else str(entity))
        ref = SketchPointRef(entity_id, int(point_index))
        self._point_position(ref)
        return ref

    def _replace_entity(self, index: int, values: Sequence) -> None:
        old = self.entities[index]
        self.entities[index] = SketchEntity(
            values,
            entity_id=getattr(old, "entity_id", None),
            construction=getattr(old, "construction", False),
            rectangle_mode=getattr(old, "rectangle_mode", None),
        )

    def _entity_index(self, entity_id: str) -> Optional[int]:
        self._ensure_entity_records()
        for index, entity in enumerate(self.entities):
            if entity.entity_id == entity_id:
                return index
        return None

    def _entity_for_ref(self, ref: SketchEntityRef) -> Optional[tuple]:
        index = self._entity_index(ref.entity_id)
        return None if index is None else self.entities[index]

    def find_entity_at_uv(self, uv: UV, tol: float) -> Optional[SketchEntityRef]:
        """Return the nearest entity within *tol* of a sketch-plane point."""
        self._ensure_entity_records()
        best: Optional[Tuple[float, SketchEntityRef]] = None
        for entity in reversed(self.entities):
            tag = entity[0]
            if tag == "line":
                points = [entity[1], entity[2]]
                closed = False
            elif tag == "polyline":
                points = list(entity[1])
                closed = False
            elif tag == "arc":
                points = _arc_polyline(entity[1], entity[2], entity[3])
                closed = False
            elif tag == "circle":
                points = _circle_polyline(entity[1], entity[2])
                closed = True
            elif tag == "rect":
                points = _rect_polyline(
                    entity[1], entity[2],
                    getattr(entity, "rectangle_mode", None) or "corner-corner",
                )
                closed = False
            else:
                continue
            segments = list(zip(points, points[1:]))
            if closed and len(points) > 1:
                segments.append((points[-1], points[0]))
            if not segments:
                continue
            distance = min(
                _point_segment_distance_2d(uv, start, end)
                for start, end in segments
            )
            if distance <= tol and (best is None or distance < best[0]):
                best = (distance, SketchEntityRef(entity.entity_id))
        return best[1] if best else None

    def select_entity_at_uv(self, uv: UV, tol: float, *,
                            preserve_regions: bool = False) -> Optional[SketchEntityRef]:
        if not preserve_regions:
            self._selected_region_anchors.clear()
        ref = self.find_entity_at_uv(uv, tol)
        self.selected_entity_id = ref.entity_id if ref else None
        return ref

    def clear_region_selection(self) -> None:
        self._selected_region_anchors.clear()

    def _closed_profile_contours(self) -> List[dict]:
        self._ensure_entity_records()
        paths = []
        open_paths = []
        coordinates = []
        for entity in self.entities:
            if entity.construction:
                continue
            tag = entity[0]
            if tag == "line":
                points, closed = [entity[1], entity[2]], False
            elif tag == "polyline":
                points = list(entity[1])
                closed = len(points) > 2 and _dist(points[0], points[-1]) <= 1e-8
            elif tag == "arc":
                points, closed = _arc_polyline(entity[1], entity[2], entity[3]), False
            elif tag == "circle":
                points, closed = _circle_polyline(entity[1], entity[2]), True
            elif tag == "rect":
                points = _rect_polyline(
                    entity[1], entity[2],
                    getattr(entity, "rectangle_mode", None) or "corner-corner",
                )
                closed = True
            else:
                continue
            if len(points) < 2:
                continue
            points = [tuple(point) for point in points]
            coordinates.extend(points)
            record = (points, entity.entity_id)
            (paths if closed else open_paths).append(record)

        if not coordinates:
            return []
        extent = max(
            max(point[axis] for point in coordinates)
            - min(point[axis] for point in coordinates)
            for axis in range(2)
        )
        magnitude = max(abs(value) for point in coordinates for value in point)
        tolerance = max(1e-8, extent * 1e-9, magnitude * 1e-12)
        contours = []

        def add_contour(points, entity_ids):
            if len(points) < 4 or _dist(points[0], points[-1]) > tolerance:
                return
            points[-1] = points[0]
            if len(set(points[:-1])) < 3:
                return
            area = abs(_polygon_area(points))
            contours.append({
                "points": points,
                "entity_ids": entity_ids,
                "area": area,
                "parent": None,
            })

        for points, entity_id in paths:
            if _dist(points[0], points[-1]) <= tolerance:
                add_contour(list(points), {entity_id})
            else:
                open_paths.append((points, entity_id))

        unused = set(range(len(open_paths)))
        while unused:
            first_index = min(unused)
            unused.remove(first_index)
            chain, first_id = open_paths[first_index]
            chain = list(chain)
            entity_ids = {first_id}
            while _dist(chain[0], chain[-1]) > tolerance:
                matches = []
                for index in unused:
                    candidate, _ = open_paths[index]
                    start_distance = _dist(chain[-1], candidate[0])
                    end_distance = _dist(chain[-1], candidate[-1])
                    if min(start_distance, end_distance) <= tolerance:
                        matches.append((min(start_distance, end_distance), index,
                                        end_distance < start_distance))
                if not matches:
                    break
                _, index, reverse = min(matches)
                unused.remove(index)
                candidate, entity_id = open_paths[index]
                if reverse:
                    candidate = list(reversed(candidate))
                chain.extend(candidate[1:])
                entity_ids.add(entity_id)
            add_contour(chain, entity_ids)

        for index, contour in enumerate(contours):
            containing = [
                parent_index for parent_index, parent in enumerate(contours)
                if parent_index != index
                and parent["area"] > contour["area"]
                and _point_in_polygon(contour["points"][0], parent["points"])
            ]
            if containing:
                contour["parent"] = min(containing, key=lambda item: contours[item]["area"])
        return contours

    def _polygon_profile(self, polygon) -> List[List[UV]]:
        def normalize_ring(ring, counter_clockwise: bool) -> List[UV]:
            points = list(map(tuple, ring.coords))[:-1]
            is_counter_clockwise = _polygon_area(points + [points[0]]) > 0.0
            if is_counter_clockwise != counter_clockwise:
                points.reverse()
            ring_line = LineString(points + [points[0]])
            source_points = [
                source for contour in self._closed_profile_contours()
                for source in contour["points"][:-1]
                if ring_line.distance(Point(source)) <= 1e-9
            ]
            start_point = source_points[0] if source_points else min(points)
            start = min(range(len(points)), key=lambda index: math.dist(points[index], start_point))
            points = points[start:] + points[:start]
            return points + [points[0]]

        contours = [normalize_ring(polygon.exterior, True)]
        contours.extend(normalize_ring(ring, False) for ring in polygon.interiors)
        return contours

    def _bounded_faces(self):
        contours = self._closed_profile_contours()
        if not contours:
            return []
        linework = [LineString(contour["points"]) for contour in contours]
        faces = [face for face in polygonize(unary_union(linework))
                 if face.is_valid and face.area > 1e-14]
        return sorted(faces, key=lambda face: (face.bounds[0], face.bounds[1], face.area))

    @staticmethod
    def _face_at_uv(faces, uv: UV):
        point = Point(float(uv[0]), float(uv[1]))
        return next((face for face in faces if face.contains(point)), None)

    def bounded_region_profiles(self) -> List[List[List[UV]]]:
        """Return every bounded sketch face as outer contour plus optional holes."""
        return [self._polygon_profile(face) for face in self._bounded_faces()]

    def select_region_at_uv(self, uv: UV, *, additive: bool = False,
                            toggle: bool = False) -> bool:
        """Select the bounded face under *uv*, optionally adding/toggling it."""
        faces = self._bounded_faces()
        face = self._face_at_uv(faces, uv)
        if face is None:
            if not additive:
                self._selected_region_anchors.clear()
            return False

        if not additive:
            self._selected_region_anchors = [tuple(uv)]
        else:
            selected_index = next((
                index for index, anchor in enumerate(self._selected_region_anchors)
                if (selected_face := self._face_at_uv(faces, anchor)) is not None
                and selected_face.equals(face)
            ), None)
            if selected_index is not None and toggle:
                self._selected_region_anchors.pop(selected_index)
            elif selected_index is None:
                self._selected_region_anchors.append(tuple(uv))
        self.selected_entity_id = None
        return True

    def selected_region_profiles(self) -> List[List[List[UV]]]:
        faces = self._bounded_faces()
        selected_faces = []
        valid_anchors = []
        for anchor in self._selected_region_anchors:
            face = self._face_at_uv(faces, anchor)
            if face is not None:
                valid_anchors.append(anchor)
                if not any(face.equals(existing) for existing in selected_faces):
                    selected_faces.append(face)
        self._selected_region_anchors = valid_anchors
        if not selected_faces:
            return []
        combined = unary_union(selected_faces)
        polygons = ([combined] if combined.geom_type == "Polygon"
                    else list(combined.geoms) if combined.geom_type == "MultiPolygon"
                    else [])
        return [self._polygon_profile(polygon) for polygon in polygons]

    def selected_region_profile(self) -> Optional[List[List[UV]]]:
        profiles = self.selected_region_profiles()
        return profiles[0] if len(profiles) == 1 else None

    def operation_regions(self) -> List[List[List[UV]]]:
        """Return selected faces as polygon groups, merging adjacent selections."""
        selected = self.selected_region_profiles()
        if selected:
            return selected
        faces = self._bounded_faces()
        if not faces:
            raise ValueError("Sketch needs at least one closed region")
        if len(faces) > 1:
            raise ValueError("Select a sketch region or Ctrl-select regions before starting the operation")
        return [self._polygon_profile(faces[0])]

    def operation_profile(self) -> Union[List[UV], List[List[UV]]]:
        """Return one operation region in the legacy profile representation."""
        regions = self.operation_regions()
        if len(regions) != 1:
            raise ValueError("This operation requires one connected sketch region")
        region = regions[0]
        return region[0] if len(region) == 1 else region

    def delete_entity(self, entity: Union[SketchEntityRef, str]) -> bool:
        entity_id = entity.entity_id if isinstance(entity, SketchEntityRef) else str(entity)
        index = self._entity_index(entity_id)
        if index is None:
            return False
        self.entities.pop(index)
        self._selected_region_anchors.clear()
        self.dimensions = [
            dimension for dimension in self.dimensions
            if all(ref.entity_id != entity_id for ref in dimension.refs)
        ]
        if self.selected_entity_id == entity_id:
            self.selected_entity_id = None
        if self._active_polyline_id == entity_id:
            self._active_polyline_id = None
            self.pending = []
            self.preview_uv = None
            self.tool = None
        return True

    def delete_selected(self) -> bool:
        if self.selected_entity_id is None:
            return False
        return self.delete_entity(self.selected_entity_id)

    def _point_position(self, ref: SketchPointRef) -> UV:
        entity = self._entity_for_ref(SketchEntityRef(ref.entity_id))
        if entity is None:
            raise LookupError(f"Sketch entity not found: {ref.entity_id}")
        tag = entity[0]
        if tag in ("line", "rect") and ref.point_index in (0, 1):
            return entity[ref.point_index + 1]
        if tag == "polyline" and 0 <= ref.point_index < len(entity[1]):
            return entity[1][ref.point_index]
        if tag == "arc" and 0 <= ref.point_index < 3:
            return entity[ref.point_index + 1]
        if tag == "circle" and ref.point_index == 0:
            return entity[1]
        raise IndexError(f"Point index {ref.point_index} is invalid for {tag}")

    def edit_point(self, ref: SketchPointRef, uv: UV) -> None:
        index = self._entity_index(ref.entity_id)
        if index is None:
            raise LookupError(f"Sketch entity not found: {ref.entity_id}")
        entity = self.entities[index]
        point = (float(uv[0]), float(uv[1]))
        tag = entity[0]
        if tag in ("line", "rect") and ref.point_index in (0, 1):
            values = list(entity)
            values[ref.point_index + 1] = point
        elif tag == "polyline" and 0 <= ref.point_index < len(entity[1]):
            points = list(entity[1])
            points[ref.point_index] = point
            values = (tag, points)
        elif tag == "arc" and 0 <= ref.point_index < 3:
            values = list(entity)
            values[ref.point_index + 1] = point
        elif tag == "circle" and ref.point_index == 0:
            values = (tag, point, entity[2])
        else:
            raise IndexError(f"Point index {ref.point_index} is invalid for {tag}")
        self._replace_entity(index, values)

    def add_dimension(self, kind: str, refs: Sequence[Union[SketchRef, str, Mapping]],
                      expression: Optional[str] = None,
                      value: Optional[float] = None,
                      axis: Optional[str] = None,
                      dimension_id: Optional[str] = None) -> SketchDimension:
        if kind not in ("linear", "aligned", "angular", "radius", "diameter"):
            raise ValueError(f"Unknown dimension kind: {kind}")
        normalized_refs: List[SketchRef] = []
        for ref in refs:
            if isinstance(ref, (SketchEntityRef, SketchPointRef)):
                normalized_refs.append(ref)
            elif isinstance(ref, str):
                normalized_refs.append(SketchEntityRef(ref))
            elif isinstance(ref, Mapping):
                normalized_refs.append(_sketch_ref_from_dict(ref))
            else:
                raise TypeError("Dimension refs must be entity or point references")

        expected_count = 2 if kind in ("linear", "aligned", "angular") else 1
        if len(normalized_refs) != expected_count:
            raise ValueError(f"{kind} dimensions require {expected_count} references")
        if kind in ("linear", "aligned"):
            if not all(isinstance(ref, SketchPointRef) for ref in normalized_refs):
                raise TypeError(f"{kind} dimensions require point references")
            if kind == "linear":
                axis = "u" if axis == "x" else "v" if axis == "y" else axis
                if axis not in ("u", "v"):
                    raise ValueError("Linear dimensions require axis='u' or axis='v'")
        elif not all(isinstance(ref, SketchEntityRef) for ref in normalized_refs):
            raise TypeError(f"{kind} dimensions require entity references")

        for ref in normalized_refs:
            if isinstance(ref, SketchPointRef):
                self._point_position(ref)
            elif self._entity_for_ref(ref) is None:
                raise LookupError(f"Sketch entity not found: {ref.entity_id}")

        dimension = SketchDimension(
            dimension_id=dimension_id or uuid.uuid4().hex,
            kind=kind,
            refs=tuple(normalized_refs),
            expression=expression,
            value=None if value is None else float(value),
            resolved_value=None if value is None else float(value),
            axis=axis,
        )
        if dimension.value is None:
            dimension.value = self.measure_dimension(dimension)
            dimension.resolved_value = dimension.value
        self.dimensions.append(dimension)
        return dimension

    def measure_dimension(self, dimension: Union[str, SketchDimension]) -> float:
        item = self._dimension_for_ref(dimension)
        if item.kind in ("linear", "aligned"):
            first = self._point_position(item.refs[0])
            second = self._point_position(item.refs[1])
            if item.kind == "aligned":
                return _dist(first, second)
            axis_index = 0 if item.axis == "u" else 1
            return abs(second[axis_index] - first[axis_index])
        if item.kind == "angular":
            first = self._entity_vector(item.refs[0])
            second = self._entity_vector(item.refs[1])
            first_length = math.hypot(*first)
            second_length = math.hypot(*second)
            if first_length < 1e-12 or second_length < 1e-12:
                raise ValueError("Angular dimensions require non-zero line directions")
            cosine = (first[0] * second[0] + first[1] * second[1]) / (first_length * second_length)
            return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        entity = self._entity_for_ref(item.refs[0])
        radius = self._entity_radius(entity)
        return 2.0 * radius if item.kind == "diameter" else radius

    def recompute_dimensions(self, resolver: Union[Callable[[str], float], Mapping[str, float]]) -> dict:
        """Resolve expression-backed dimensions using a caller-provided resolver."""
        resolved = {}
        for dimension in self.dimensions:
            if dimension.expression is None:
                continue
            value = (resolver(dimension.expression) if callable(resolver)
                     else resolver[dimension.expression])
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                raise ValueError(f"Non-finite value for dimension {dimension.dimension_id}")
            dimension.resolved_value = numeric_value
            resolved[dimension.dimension_id] = numeric_value
        return resolved

    def apply_dimension(self, dimension: Union[str, SketchDimension]) -> bool:
        """Apply supported dimensional changes deterministically in sketch UV space."""
        try:
            item = self._dimension_for_ref(dimension)
            target = item.resolved_value if item.resolved_value is not None else item.value
        except LookupError:
            return False
        if target is None or not math.isfinite(target) or target < 0.0:
            return False

        if item.kind in ("linear", "aligned"):
            first_ref, second_ref = item.refs
            first = self._point_position(first_ref)
            second = self._point_position(second_ref)
            if item.kind == "linear":
                axis_index = 0 if item.axis == "u" else 1
                sign = 1.0 if second[axis_index] >= first[axis_index] else -1.0
                adjusted = list(second)
                adjusted[axis_index] = first[axis_index] + sign * target
                self.edit_point(second_ref, (adjusted[0], adjusted[1]))
            else:
                delta_u = second[0] - first[0]
                delta_v = second[1] - first[1]
                length = math.hypot(delta_u, delta_v)
                direction = ((1.0, 0.0) if length < 1e-12
                             else (delta_u / length, delta_v / length))
                self.edit_point(second_ref, (first[0] + direction[0] * target,
                                             first[1] + direction[1] * target))
            return True

        if item.kind in ("radius", "diameter"):
            entity_ref = item.refs[0]
            index = self._entity_index(entity_ref.entity_id)
            if index is None:
                return False
            entity = self.entities[index]
            target_radius = target / 2.0 if item.kind == "diameter" else target
            if target_radius <= 0.0:
                return False
            if entity[0] == "circle":
                self._replace_entity(index, ("circle", entity[1], target_radius))
                return True
            if entity[0] == "arc":
                arc = _arc_from_3pts(entity[1], entity[2], entity[3])
                if arc is None:
                    return False
                center = arc[0]
                scaled = []
                for point in entity[1:4]:
                    scaled.append((center[0] + (point[0] - center[0]) * target_radius / arc[1],
                                   center[1] + (point[1] - center[1]) * target_radius / arc[1]))
                self._replace_entity(index, ("arc", *scaled))
                return True
        return False

    def _dimension_for_ref(self, dimension: Union[str, SketchDimension]) -> SketchDimension:
        if isinstance(dimension, SketchDimension):
            return dimension
        for item in self.dimensions:
            if item.dimension_id == dimension:
                return item
        raise LookupError(f"Sketch dimension not found: {dimension}")

    def _entity_vector(self, ref: SketchEntityRef) -> UV:
        entity = self._entity_for_ref(ref)
        if entity is None:
            raise LookupError(f"Sketch entity not found: {ref.entity_id}")
        if entity[0] == "line":
            start, end = entity[1], entity[2]
        elif entity[0] == "polyline" and len(entity[1]) >= 2:
            start, end = entity[1][0], entity[1][-1]
        else:
            raise TypeError("Angular dimensions require line or polyline entities")
        return end[0] - start[0], end[1] - start[1]

    @staticmethod
    def _entity_radius(entity: Optional[tuple]) -> float:
        if entity is None:
            raise LookupError("Sketch entity not found")
        if entity[0] == "circle":
            return float(entity[2])
        if entity[0] == "arc":
            arc = _arc_from_3pts(entity[1], entity[2], entity[3])
            if arc is not None:
                return arc[1]
        raise TypeError("Radius and diameter dimensions require a circle or valid arc")

    def to_dict(self) -> dict:
        self._ensure_entity_records()
        entities = []
        for entity in self.entities:
            entities.append({
                "id": entity.entity_id,
                "geometry": _json_geometry(entity),
                "construction": entity.construction,
                "rectangle_mode": entity.rectangle_mode,
            })
        return {
            "plane_origin": list(self.plane_origin),
            "plane_normal": list(self.plane_normal),
            "entities": entities,
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
            "selected_region_anchors": [list(anchor) for anchor in self._selected_region_anchors],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SketchEngine":
        engine = cls(tuple(data["plane_origin"]), tuple(data["plane_normal"]))
        for item in data.get("entities", []):
            geometry = _restore_geometry(item["geometry"])
            engine.entities.append(SketchEntity(
                geometry,
                entity_id=str(item["id"]),
                construction=bool(item.get("construction", False)),
                rectangle_mode=item.get("rectangle_mode"),
            ))
        engine.dimensions = [SketchDimension.from_dict(item)
                             for item in data.get("dimensions", [])]
        engine._selected_region_anchors = [
            (float(anchor[0]), float(anchor[1]))
            for anchor in data.get("selected_region_anchors", [])
            if isinstance(anchor, (list, tuple)) and len(anchor) == 2
        ]
        return engine

    # ── tool / state management ───────────────────────────────────────
    def set_tool(self, name: Optional[str]) -> None:
        if self.tool == "polyline":
            self._finish_polyline()
        self.tool = name
        self.pending = []
        self.preview_uv = None
        self._active_polyline_id = None
        if name is not None:
            self.selected_entity_id = None

    def cancel_current(self) -> None:
        if self.tool == "polyline":
            self._finish_polyline()
        self.tool = None
        self.pending = []
        self.preview_uv = None
        self._active_polyline_id = None

    def finish_current(self) -> None:
        """Finish a multi-click polyline or abandon incomplete tool input."""
        self.cancel_current()

    def _finish_polyline(self) -> None:
        entity_id = self._active_polyline_id
        if entity_id is not None and len(self.pending) < 2:
            self.delete_entity(entity_id)
        self._active_polyline_id = None
        self.pending = []
        self.preview_uv = None

    def delete_last(self) -> None:
        if self.entities:
            self.delete_entity(self.entity_ref(len(self.entities) - 1))

    def close_profile(self) -> None:
        """Close the active polyline by appending its first point."""
        if self.entities and self.entities[-1][0] == "polyline":
            pts = list(self.entities[-1][1])
            if len(pts) >= 2 and pts[0] != pts[-1]:
                pts.append(pts[0])
                self._replace_entity(len(self.entities) - 1, ("polyline", pts))
            if self.tool == "polyline":
                self.finish_current()

    # ── click / move dispatch ─────────────────────────────────────────
    def on_move(self, uv: UV) -> None:
        self.preview_uv = uv

    def on_click(self, uv: UV) -> None:
        t = self.tool
        if t is None:
            return
        if t == "line":
            self.pending.append(uv)
            if len(self.pending) == 2:
                p1, p2 = self.pending
                self.add_line(p1, p2)
                self.finish_current()
        elif t == "polyline":
            if not self.pending:
                self.pending = [uv]
                self._active_polyline_id = self.add_polyline([uv]).entity_id
            else:
                index = self._entity_index(self._active_polyline_id)
                if index is None:
                    self.pending = []
                    self._active_polyline_id = None
                    self.on_click(uv)
                    return
                pts = list(self.entities[index][1])
                pts.append(uv)
                self._replace_entity(index, ("polyline", pts))
                self.pending.append(uv)
        elif t == "arc":
            self.pending.append(uv)
            if len(self.pending) == 3:
                p1, p2, pm = self.pending
                self.add_arc(p1, p2, pm)
                self.finish_current()
        elif t == "circle":
            self.pending.append(uv)
            if len(self.pending) == 2:
                c, edge = self.pending
                r = _dist(c, edge)
                self.add_circle(c, r)
                self.finish_current()
        elif t == "rect":
            self.pending.append(uv)
            if len(self.pending) == 2:
                p1, p2 = self.pending
                self.add_rectangle(p1, p2)
                self.finish_current()

    # ── modifiers ─────────────────────────────────────────────────────
    def apply_fillet(self, radius: float) -> bool:
        return self._apply_corner_op(lambda p0, p1, p2: _fillet_corner(p0, p1, p2, radius))

    def apply_chamfer(self, dist: float) -> bool:
        return self._apply_corner_op(lambda p0, p1, p2: _chamfer_corner(p0, p1, p2, dist))

    def _apply_corner_op(self, fn) -> bool:
        if not self.entities:
            return False
        tag = self.entities[-1][0]
        if tag != "polyline":
            return False
        pts = list(self.entities[-1][1])
        if len(pts) < 3:
            return False
        # Apply at the second-to-last vertex
        p0, p1, p2 = pts[-3], pts[-2], pts[-1]
        replaced = fn(p0, p1, p2)
        new_pts = pts[:-3] + [p0] + replaced + [p2]
        self._replace_entity(len(self.entities) - 1, ("polyline", new_pts))
        # Pending tail follows the polyline list
        if self.pending:
            self.pending = list(new_pts)
        return True

    # ── selection helpers (axis pick) ─────────────────────────────────
    def find_line_at_uv(self, uv: UV, tol: float) -> Optional[Tuple[UV, UV]]:
        best: Optional[Tuple[float, Tuple[UV, UV]]] = None
        for ent in self.entities:
            if ent[0] == "line":
                p1, p2 = ent[1], ent[2]
                d = _point_segment_distance_2d(uv, p1, p2)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, (p1, p2))
            elif ent[0] == "polyline":
                pts = ent[1]
                for i in range(len(pts) - 1):
                    p1, p2 = pts[i], pts[i + 1]
                    d = _point_segment_distance_2d(uv, p1, p2)
                    if d <= tol and (best is None or d < best[0]):
                        best = (d, (p1, p2))
        return best[1] if best else None

    def find_construction_line_at_uv(self, uv: UV,
                                     tol: float) -> Optional[Tuple[UV, UV]]:
        best: Optional[Tuple[float, Tuple[UV, UV]]] = None
        self._ensure_entity_records()
        for ent in self.entities:
            if ent[0] != "line" or not ent.construction:
                continue
            p1, p2 = ent[1], ent[2]
            distance = _point_segment_distance_2d(uv, p1, p2)
            if distance <= tol and (best is None or distance < best[0]):
                best = (distance, (p1, p2))
        return best[1] if best else None

    # ── profile linearisation ─────────────────────────────────────────
    def build_profile(self) -> List[UV]:
        out: List[UV] = []
        for ent in self.entities:
            tag = ent[0]
            if getattr(ent, "construction", False):
                continue
            if tag == "line":
                seg = [ent[1], ent[2]]
            elif tag == "polyline":
                seg = list(ent[1])
            elif tag == "arc":
                seg = _arc_polyline(ent[1], ent[2], ent[3])
            elif tag == "circle":
                seg = _circle_polyline(ent[1], ent[2])
            elif tag == "rect":
                seg = _rect_polyline(ent[1], ent[2],
                                     getattr(ent, "rectangle_mode", None) or "corner-corner")
            else:
                continue
            if not seg:
                continue
            if out and out[-1] == seg[0]:
                out.extend(seg[1:])
            else:
                out.extend(seg)
        return out

    # ── overlay polydata for VTK rendering ────────────────────────────
    def to_lines_polydata(self,
                          highlight: Optional[Tuple[UV, UV]] = None
                          ) -> Tuple[vtk.vtkPolyData, vtk.vtkPolyData]:
        """Return (normal_lines_polydata, highlight_lines_polydata)."""
        normal_pts = vtk.vtkPoints()
        normal_lines = vtk.vtkCellArray()
        hi_pts = vtk.vtkPoints()
        hi_lines = vtk.vtkCellArray()

        self._ensure_entity_records()
        for ent in self.entities:
            tag = ent[0]
            is_hi = ent.entity_id == self.selected_entity_id
            if tag == "line":
                seg = [ent[1], ent[2]]
                is_hi = is_hi or (
                    highlight is not None and (
                        (ent[1], ent[2]) == highlight
                        or (ent[2], ent[1]) == highlight
                    )
                )
            elif tag == "polyline":
                seg = list(ent[1])
            elif tag == "arc":
                seg = _arc_polyline(ent[1], ent[2], ent[3])
            elif tag == "circle":
                seg = _circle_polyline(ent[1], ent[2])
            elif tag == "rect":
                seg = _rect_polyline(ent[1], ent[2],
                                     getattr(ent, "rectangle_mode", None) or "corner-corner")
            else:
                continue
            target_pts   = hi_pts   if is_hi else normal_pts
            target_lines = hi_lines if is_hi else normal_lines
            self._append_polyline_3d(seg, target_pts, target_lines)

        return (self._make_polydata(normal_pts, normal_lines),
                self._make_polydata(hi_pts, hi_lines))

    def to_vertex_polydata(self) -> vtk.vtkPolyData:
        pts = vtk.vtkPoints()
        verts = vtk.vtkCellArray()
        seen: set = set()
        for ent in self.entities:
            tag = ent[0]
            if tag == "line":
                cands = [ent[1], ent[2]]
            elif tag == "polyline":
                cands = list(ent[1])
            elif tag == "arc":
                cands = [ent[1], ent[2], ent[3]]
            elif tag == "circle":
                cands = [ent[1]]
            elif tag == "rect":
                cands = [ent[1], ent[2]]
            else:
                continue
            for uv in cands:
                key = (round(uv[0], 6), round(uv[1], 6))
                if key in seen:
                    continue
                seen.add(key)
                wx, wy, wz = uv_to_world(uv, self.plane_origin, self.u_axis, self.v_axis)
                pid = pts.InsertNextPoint(wx, wy, wz)
                verts.InsertNextCell(1)
                verts.InsertCellPoint(pid)
        poly = vtk.vtkPolyData()
        poly.SetPoints(pts)
        poly.SetVerts(verts)
        return poly

    def to_preview_polydata(self) -> vtk.vtkPolyData:
        """Build live preview polyline based on current tool & pending pts."""
        pts = vtk.vtkPoints()
        lines = vtk.vtkCellArray()
        if self.tool is None or self.preview_uv is None:
            return self._make_polydata(pts, lines)
        cur = self.preview_uv

        if self.tool == "line" and len(self.pending) == 1:
            self._append_polyline_3d([self.pending[0], cur], pts, lines)
        elif self.tool == "polyline" and self.pending:
            self._append_polyline_3d([self.pending[-1], cur], pts, lines)
        elif self.tool == "arc":
            n = len(self.pending)
            if n == 1:
                self._append_polyline_3d([self.pending[0], cur], pts, lines)
            elif n == 2:
                seg = _arc_polyline(self.pending[0], self.pending[1], cur)
                self._append_polyline_3d(seg, pts, lines)
        elif self.tool == "circle" and len(self.pending) == 1:
            r = _dist(self.pending[0], cur)
            seg = _circle_polyline(self.pending[0], r)
            self._append_polyline_3d(seg, pts, lines)
        elif self.tool == "rect" and len(self.pending) == 1:
            seg = _rect_polyline(self.pending[0], cur, self.rectangle_mode)
            self._append_polyline_3d(seg, pts, lines)

        return self._make_polydata(pts, lines)

    # ── private polydata builders ─────────────────────────────────────
    def _append_polyline_3d(self, uvs: List[UV],
                             vtk_pts: vtk.vtkPoints,
                             vtk_lines: vtk.vtkCellArray) -> None:
        if len(uvs) < 2:
            return
        ids: List[int] = []
        for uv in uvs:
            wx, wy, wz = uv_to_world(uv, self.plane_origin, self.u_axis, self.v_axis)
            ids.append(vtk_pts.InsertNextPoint(wx, wy, wz))
        polyline = vtk.vtkPolyLine()
        polyline.GetPointIds().SetNumberOfIds(len(ids))
        for i, pid in enumerate(ids):
            polyline.GetPointIds().SetId(i, pid)
        vtk_lines.InsertNextCell(polyline)

    @staticmethod
    def _make_polydata(pts: vtk.vtkPoints, cells: vtk.vtkCellArray) -> vtk.vtkPolyData:
        poly = vtk.vtkPolyData()
        poly.SetPoints(pts)
        poly.SetLines(cells)
        return poly


def _point_segment_distance_2d(p: UV, a: UV, b: UV) -> float:
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)
