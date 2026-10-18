"""Decode the explicit padstack geometry retained in standalone IPC-2581 JSON.

The decoder handles standard ``Circle``, ``Oval``, ``RectCenter`` and
straight/curved ``Contour`` primitives, with rotation and mirror transforms.
Circular boundaries are polygonized for callers that need polygon geometry;
the source dimensions and transforms are never inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


class PadstackGeometryError(ValueError):
    """Pad/via data is incomplete, invalid, or outside the supported subset."""


Point2 = tuple[float, float]


@dataclass(frozen=True)
class LayerPolygon:
    """A finite layer polygon with its source pad-use and copper classification."""

    layer_ref: str
    points: tuple[Point2, ...]
    pad_use: str
    is_copper: bool


@dataclass(frozen=True)
class ViaDescriptor:
    """Explicit via drill and span information, independent of copper polygons."""

    center: Point2
    drill_diameter: float
    span_start: str
    span_end: str
    plating_status: str | None


@dataclass(frozen=True)
class DecodedPadstackGeometry:
    """Decoded per-layer copper polygons and optional explicit via metadata."""

    kind: str
    polygons: tuple[LayerPolygon, ...]
    via: ViaDescriptor | None


def decode_padstack_feature(
    feature: Mapping[str, Any],
    padstack_definitions: Mapping[str, Any] | None = None,
    *,
    circle_segments: int = 64,
    curve_segments_per_circle: int = 72,
) -> DecodedPadstackGeometry:
    """Decode one parser-produced ``pad`` or ``via`` feature.

    ``feature`` is a feature object from ``Board.to_dict()`` and
    ``padstack_definitions`` is its top-level ``padstack_definitions`` mapping.
    Inline primitive data in ``feature["geometry"]["pads"]`` is preferred;
    top-level definitions are used to resolve a referenced primitive when the
    inline map does not contain it. A via with no land pads can still be
    decoded when its center, drill diameter, span, and plating status are
    explicit; it returns a descriptor and no polygons. Pads always require
    land-pad polygon geometry.

    Each output polygon preserves the source ``pad_use`` and exposes
    ``is_copper``: ``REGULAR`` and ``THERMAL`` are copper; ``ANTIPAD`` is not.
    Missing or unrecognized pad-use values are rejected.
    Circle and arc curves are sampled uniformly with the supplied segment
    counts (at least 6 for circles, at least 1 per 5 degrees for arcs).
    Coordinates remain in the Board JSON's declared source units. An absent
    transform means identity; absent pad locations, feature centers, dimensions,
    or via span endpoints are errors. ``plating_status`` is preserved as
    ``None`` when the source omits it, rather than being inferred.
    """
    if not isinstance(feature, Mapping):
        raise PadstackGeometryError("Padstack feature must be an object")
    kind = str(feature.get("kind", "")).strip().casefold()
    if kind not in {"pad", "via"}:
        raise PadstackGeometryError(
            f"Unsupported padstack feature kind: {feature.get('kind')!r}"
        )
    if (
        isinstance(circle_segments, bool)
        or not isinstance(circle_segments, int)
        or not 6 <= circle_segments <= 4096
    ):
        raise PadstackGeometryError("circle_segments must be an integer from 6 to 4096")
    if (
        isinstance(curve_segments_per_circle, bool)
        or not isinstance(curve_segments_per_circle, int)
        or not 6 <= curve_segments_per_circle <= 4096
    ):
        raise PadstackGeometryError(
            "curve_segments_per_circle must be an integer from 6 to 4096"
        )

    geometry = feature.get("geometry")
    if not isinstance(geometry, Mapping):
        raise PadstackGeometryError("Padstack feature is missing geometry")
    raw_pads = geometry.get("pads")
    if kind == "via" and raw_pads in (None, []):
        center = _point(geometry.get("center"), "feature center")
        via = _via_descriptor(geometry, center, require_plating=True)
        return DecodedPadstackGeometry(kind=kind, polygons=(), via=via)
    if not isinstance(raw_pads, list) or not raw_pads:
        raise PadstackGeometryError("Padstack feature is missing pad geometry")
    center = _point(geometry.get("center"), "feature center")
    placement = _parse_transform(
        geometry.get("placement_transform"), "placement transform"
    )

    definitions = padstack_definitions if isinstance(padstack_definitions, Mapping) else {}
    polygons: list[LayerPolygon] = []
    for pad_index, pad in enumerate(raw_pads):
        label = f"pad geometry {pad_index + 1}"
        if not isinstance(pad, Mapping):
            raise PadstackGeometryError(f"{label} must be an object")
        layer_ref = str(pad.get("layer_ref") or "").strip()
        if not layer_ref:
            raise PadstackGeometryError(f"{label} is missing layer_ref")
        pad_use = pad.get("pad_use")
        if not isinstance(pad_use, str) or not pad_use.strip():
            raise PadstackGeometryError(f"{label} is missing pad_use")
        pad_use = pad_use.strip()
        is_copper = _pad_use_is_copper(pad_use, label)
        location = _point(pad.get("location"), f"{label} location")
        pad_transform = _parse_transform(pad.get("transform"), f"{label} transform")
        refs = pad.get("primitive_refs")
        primitive_map = pad.get("primitives")
        if not isinstance(primitive_map, Mapping):
            primitive_map = {}
        if not isinstance(refs, list) or not refs:
            refs = list(primitive_map)
        if not refs:
            raise PadstackGeometryError(f"{label} is missing standard primitive geometry")
        for ref in refs:
            ref_name = str(ref).strip()
            if not ref_name:
                raise PadstackGeometryError(f"{label} has an empty primitive reference")
            primitive = _lookup_primitive(primitive_map, ref_name)
            if primitive is None:
                primitive = _lookup_definition_primitive(
                    definitions, feature, ref_name
                )
            if primitive is None:
                raise PadstackGeometryError(
                    f"{label} is missing geometry for primitive {ref_name!r}"
                )
            points = _primitive_polygon(
                primitive,
                circle_segments=circle_segments,
                curve_segments_per_circle=curve_segments_per_circle,
                context=f"{label} primitive {ref_name!r}",
            )
            # A PadStackPadDef transform acts on primitive-local coordinates.
            # The feature transform then rotates/mirrors both the local pad
            # offset and transformed primitive around the padstack origin.
            transformed = tuple(
                _add(center, _apply(placement, _add(
                    location,
                    _apply(pad_transform, point),
                )))
                for point in points
            )
            polygons.append(
                LayerPolygon(layer_ref=layer_ref, points=_validated_polygon(
                    transformed, f"{label} primitive {ref_name!r}"
                ), pad_use=pad_use, is_copper=is_copper)
            )

    via = _via_descriptor(geometry, center) if kind == "via" else None
    return DecodedPadstackGeometry(kind=kind, polygons=tuple(polygons), via=via)


def _lookup_primitive(
    primitive_map: Mapping[str, Any], reference: str
) -> Mapping[str, Any] | None:
    folded = reference.casefold()
    for key, value in primitive_map.items():
        if str(key).casefold() == folded and isinstance(value, Mapping):
            return value
    return None


def _lookup_definition_primitive(
    definitions: Mapping[str, Any],
    feature: Mapping[str, Any],
    reference: str,
) -> Mapping[str, Any] | None:
    geometry = feature.get("geometry")
    padstack_ref = geometry.get("padstack_ref") if isinstance(geometry, Mapping) else None
    definition = None
    if padstack_ref is not None:
        for key, value in definitions.items():
            if str(key).casefold() == str(padstack_ref).casefold():
                definition = value
                break
    if not isinstance(definition, Mapping):
        return None
    primitives = definition.get("standard_primitives")
    return (
        _lookup_primitive(primitives, reference)
        if isinstance(primitives, Mapping)
        else None
    )


def _primitive_polygon(
    primitive: Mapping[str, Any],
    *,
    circle_segments: int,
    curve_segments_per_circle: int,
    context: str,
) -> tuple[Point2, ...]:
    supported = {"circle", "oval", "rectcenter", "contour"}
    root_tag = _tag(primitive)
    if root_tag in supported:
        shape_nodes = [primitive]
    elif root_tag in {"entrystandard", "standardprimitive"}:
        children = _children(primitive)
        shape_nodes = [child for child in children if _tag(child) in supported]
        unknown = [
            _tag(child) for child in children
            if _tag(child) not in supported | {"xform", "filldescref"}
        ]
        if unknown:
            raise PadstackGeometryError(
                f"{context} has unsupported standard primitive {unknown[0]!r}"
            )
    else:
        raise PadstackGeometryError(
            f"{context} has unsupported or missing standard primitive {root_tag or '<missing>'!r}"
        )
    if len(shape_nodes) != 1:
        raise PadstackGeometryError(f"{context} contains ambiguous primitive shapes")
    shape = shape_nodes[0]
    tag = _tag(shape)
    transform = _parse_transform(_child(shape, "xform"), f"{context} transform")
    if tag == "circle":
        attrs = _attributes(shape)
        diameter = _positive(attrs.get("diameter"), f"{context} diameter")
        radius = diameter / 2.0
        points = tuple(
            (radius * math.cos(2.0 * math.pi * index / circle_segments),
             radius * math.sin(2.0 * math.pi * index / circle_segments))
            for index in range(circle_segments)
        )
    elif tag == "rectcenter":
        attrs = _attributes(shape)
        width = _positive(attrs.get("width"), f"{context} width")
        height = _positive(attrs.get("height"), f"{context} height")
        points = (
            (-width / 2.0, -height / 2.0),
            (width / 2.0, -height / 2.0),
            (width / 2.0, height / 2.0),
            (-width / 2.0, height / 2.0),
        )
    elif tag == "oval":
        attrs = _attributes(shape)
        width = _positive(attrs.get("width"), f"{context} width")
        height = _positive(attrs.get("height"), f"{context} height")
        if math.isclose(width, height, rel_tol=1e-12, abs_tol=1e-15):
            radius = (width + height) / 4.0
            points = tuple(
                (
                    radius * math.cos(2.0 * math.pi * index / circle_segments),
                    radius * math.sin(2.0 * math.pi * index / circle_segments),
                )
                for index in range(circle_segments)
            )
        else:
            arc_segments = max(6, curve_segments_per_circle // 2)
            if width > height:
                radius = height / 2.0
                center_offset = (width - height) / 2.0
                points = tuple(
                    (
                        center_offset + radius * math.cos(
                            -math.pi / 2.0 + math.pi * index / arc_segments
                        ),
                        radius * math.sin(
                            -math.pi / 2.0 + math.pi * index / arc_segments
                        ),
                    )
                    for index in range(arc_segments + 1)
                ) + tuple(
                    (
                        -center_offset + radius * math.cos(
                            math.pi / 2.0 + math.pi * index / arc_segments
                        ),
                        radius * math.sin(
                            math.pi / 2.0 + math.pi * index / arc_segments
                        ),
                    )
                    for index in range(arc_segments + 1)
                )
            else:
                radius = width / 2.0
                center_offset = (height - width) / 2.0
                points = tuple(
                    (
                        radius * math.cos(math.pi * index / arc_segments),
                        center_offset + radius * math.sin(
                            math.pi * index / arc_segments
                        ),
                    )
                    for index in range(arc_segments + 1)
                ) + tuple(
                    (
                        radius * math.cos(
                            math.pi + math.pi * index / arc_segments
                        ),
                        -center_offset + radius * math.sin(
                            math.pi + math.pi * index / arc_segments
                        ),
                    )
                    for index in range(arc_segments + 1)
                )
    elif tag == "contour":
        points = _contour_polygon(
            shape, curve_segments_per_circle=curve_segments_per_circle, context=context
        )
    else:  # defensive: currently all recognized tags are handled above
        raise PadstackGeometryError(f"{context} has unsupported primitive {tag!r}")
    return tuple(_apply(transform, point) for point in points)


def _contour_polygon(
    contour: Mapping[str, Any],
    *,
    curve_segments_per_circle: int,
    context: str,
) -> tuple[Point2, ...]:
    polygon = _child(contour, "polygon")
    if polygon is None:
        raise PadstackGeometryError(f"{context} contour is missing Polygon data")
    begin = _child(polygon, "polybegin")
    if begin is None:
        raise PadstackGeometryError(f"{context} contour is missing PolyBegin")
    begin_attrs = _attributes(begin)
    current = _point_xy(begin_attrs, f"{context} contour PolyBegin")
    points: list[Point2] = [current]
    for segment in _children(polygon):
        segment_tag = _tag(segment)
        if segment_tag in {"filldescref", "xform"}:
            continue
        if segment_tag not in {"polystepsegment", "polystepcurve"}:
            if segment_tag != "polybegin":
                raise PadstackGeometryError(
                    f"{context} contour has unsupported segment {segment_tag!r}"
                )
            continue
        attrs = _attributes(segment)
        endpoint = _point_xy(attrs, f"{context} contour endpoint")
        if segment_tag == "polystepsegment":
            points.append(endpoint)
        else:
            center = (
                _number(attrs.get("centerx"), f"{context} curve center x"),
                _number(attrs.get("centery"), f"{context} curve center y"),
            )
            clockwise = _boolean(attrs.get("clockwise"), f"{context} curve clockwise")
            points.extend(_arc_points(
                current, endpoint, center, clockwise, curve_segments_per_circle
            ))
        current = endpoint
    return _validated_polygon(points, f"{context} contour")


def _arc_points(
    start: Point2,
    end: Point2,
    center: Point2,
    clockwise: bool,
    segments_per_circle: int,
) -> tuple[Point2, ...]:
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    end_radius = math.hypot(end[0] - center[0], end[1] - center[1])
    if radius <= 0 or not math.isclose(radius, end_radius, rel_tol=1e-7, abs_tol=1e-12):
        raise PadstackGeometryError("Contour arc endpoints must share a positive radius")
    sweep = (end_angle - start_angle) % (2.0 * math.pi)
    if clockwise:
        sweep = sweep - 2.0 * math.pi if sweep > 0 else 0.0
    elif sweep == 0.0:
        sweep = 2.0 * math.pi
    if math.isclose(start[0], end[0], abs_tol=1e-12) and math.isclose(
        start[1], end[1], abs_tol=1e-12
    ):
        raise PadstackGeometryError("Full-circle contour arcs are ambiguous")
    count = max(1, math.ceil(abs(sweep) / (2.0 * math.pi) * segments_per_circle))
    return tuple(
        (center[0] + radius * math.cos(start_angle + sweep * index / count),
         center[1] + radius * math.sin(start_angle + sweep * index / count))
        for index in range(1, count + 1)
    )


def _via_descriptor(
    geometry: Mapping[str, Any], center: Point2, *, require_plating: bool = False
) -> ViaDescriptor:
    diameter = _positive(geometry.get("hole_diameter"), "via drill diameter")
    span = geometry.get("span")
    if not isinstance(span, Mapping):
        raise PadstackGeometryError("Via is missing explicit span endpoints")
    attrs = {str(key).casefold(): value for key, value in span.items()}
    start = attrs.get("fromlayer") or attrs.get("startlayer")
    end = attrs.get("tolayer") or attrs.get("endlayer")
    if not str(start or "").strip() or not str(end or "").strip():
        raise PadstackGeometryError("Via is missing explicit span endpoints")
    plating = geometry.get("plating_status")
    if plating is not None:
        plating = str(plating).strip() or None
    if require_plating and plating is None:
        raise PadstackGeometryError(
            "Via without land pads requires explicit plating_status"
        )
    return ViaDescriptor(
        center=center,
        drill_diameter=diameter,
        span_start=str(start).strip(),
        span_end=str(end).strip(),
        plating_status=plating,
    )


def _pad_use_is_copper(pad_use: str, context: str) -> bool:
    normalized = pad_use.strip().casefold()
    if normalized in {"regular", "thermal"}:
        return True
    if normalized == "antipad":
        return False
    raise PadstackGeometryError(
        f"{context} has unsupported pad_use {pad_use!r}; "
        "supported values are REGULAR, THERMAL, and ANTIPAD"
    )


def _parse_transform(value: Any, context: str) -> tuple[float, bool]:
    if value is None:
        return 0.0, False
    node = value
    if isinstance(node, Mapping) and "attributes" in node:
        if _tag(node) != "xform":
            raise PadstackGeometryError(f"{context} has an unknown transform node")
        attrs_value = node.get("attributes")
        if not isinstance(attrs_value, Mapping):
            raise PadstackGeometryError(f"{context} attributes must be an object")
        attrs = {str(key).casefold(): val for key, val in attrs_value.items()}
    elif isinstance(node, Mapping):
        attrs = {str(key).casefold(): val for key, val in node.items()}
    else:
        raise PadstackGeometryError(f"{context} must be an object")
    unsupported = set(attrs) - {"rotation", "mirror"}
    if unsupported:
        raise PadstackGeometryError(
            f"{context} has unsupported transform fields: {', '.join(sorted(unsupported))}"
        )
    rotation = _number(attrs.get("rotation", 0.0), f"{context} rotation")
    mirror = _boolean(attrs.get("mirror", False), f"{context} mirror")
    return rotation, mirror


def _apply(transform: tuple[float, bool], point: Point2) -> Point2:
    degrees, mirror = transform
    x, y = point
    if mirror:
        x = -x
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    result = (cosine * x - sine * y, sine * x + cosine * y)
    if not all(math.isfinite(value) for value in result):
        raise PadstackGeometryError("Transform produced non-finite coordinates")
    return result


def _validated_polygon(points: Any, context: str) -> tuple[Point2, ...]:
    converted: list[Point2] = []
    for point in points:
        if (
            not isinstance(point, (tuple, list))
            or len(point) != 2
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in point)
        ):
            raise PadstackGeometryError(f"{context} contains an invalid 2D point")
        xy = (float(point[0]), float(point[1]))
        if not all(math.isfinite(value) for value in xy):
            raise PadstackGeometryError(f"{context} contains non-finite coordinates")
        if not converted or xy != converted[-1]:
            converted.append(xy)
    if len(converted) > 1 and converted[0] == converted[-1]:
        converted.pop()
    if len(set(converted)) < 3:
        raise PadstackGeometryError(f"{context} must contain at least three distinct points")
    origin = converted[0]
    relative = [(x - origin[0], y - origin[1]) for x, y in converted]
    area2 = sum(
        relative[index][0] * relative[(index + 1) % len(relative)][1]
        - relative[(index + 1) % len(relative)][0] * relative[index][1]
        for index in range(len(relative))
    )
    if not math.isfinite(area2) or area2 == 0.0:
        raise PadstackGeometryError(f"{context} polygon has zero or invalid area")
    extent = max(
        max(x for x, _ in relative) - min(x for x, _ in relative),
        max(y for _, y in relative) - min(y for _, y in relative),
    )
    tolerance = max(extent * extent * 1e-12, 1e-300)
    count = len(converted)
    for first in range(count):
        a, b = converted[first], converted[(first + 1) % count]
        for second in range(first + 1, count):
            if second == first + 1 or (first == 0 and second == count - 1):
                continue
            c, d = converted[second], converted[(second + 1) % count]
            if _segments_intersect(a, b, c, d, tolerance):
                raise PadstackGeometryError(
                    f"{context} polygon boundary self-intersects"
                )
    return tuple(converted)


def _segments_intersect(
    a: Point2, b: Point2, c: Point2, d: Point2, tolerance: float
) -> bool:
    def cross(first: Point2, second: Point2, third: Point2) -> float:
        return (
            (second[0] - first[0]) * (third[1] - first[1])
            - (second[1] - first[1]) * (third[0] - first[0])
        )

    def on_segment(first: Point2, second: Point2, point: Point2) -> bool:
        return (
            min(first[0], second[0]) - tolerance
            <= point[0]
            <= max(first[0], second[0]) + tolerance
            and min(first[1], second[1]) - tolerance
            <= point[1]
            <= max(first[1], second[1]) + tolerance
        )

    ab_c, ab_d = cross(a, b, c), cross(a, b, d)
    cd_a, cd_b = cross(c, d, a), cross(c, d, b)
    if (
        ((ab_c > tolerance and ab_d < -tolerance) or
         (ab_c < -tolerance and ab_d > tolerance))
        and ((cd_a > tolerance and cd_b < -tolerance) or
             (cd_a < -tolerance and cd_b > tolerance))
    ):
        return True
    return (
        abs(ab_c) <= tolerance and on_segment(a, b, c)
        or abs(ab_d) <= tolerance and on_segment(a, b, d)
        or abs(cd_a) <= tolerance and on_segment(c, d, a)
        or abs(cd_b) <= tolerance and on_segment(c, d, b)
    )


def _point(value: Any, context: str) -> Point2:
    if not isinstance(value, Mapping):
        raise PadstackGeometryError(f"{context} is missing explicit x/y coordinates")
    attrs = {str(key).casefold(): val for key, val in value.items()}
    if "x" not in attrs or "y" not in attrs:
        raise PadstackGeometryError(f"{context} is missing explicit x/y coordinates")
    return (
        _number(attrs["x"], f"{context} x"),
        _number(attrs["y"], f"{context} y"),
    )


def _point_xy(attrs: Mapping[str, Any], context: str) -> Point2:
    if "x" not in attrs or "y" not in attrs:
        raise PadstackGeometryError(f"{context} is missing explicit x/y coordinates")
    return (_number(attrs["x"], f"{context} x"), _number(attrs["y"], f"{context} y"))


def _positive(value: Any, context: str) -> float:
    number = _number(value, context)
    if number <= 0:
        raise PadstackGeometryError(f"{context} must be positive")
    return number


def _number(value: Any, context: str) -> float:
    if isinstance(value, bool):
        raise PadstackGeometryError(f"{context} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PadstackGeometryError(f"{context} must be a finite number") from exc
    if not math.isfinite(number):
        raise PadstackGeometryError(f"{context} must be a finite number")
    return number


def _boolean(value: Any, context: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"true", "1"}:
            return True
        if text in {"false", "0"}:
            return False
    raise PadstackGeometryError(f"{context} must be explicitly true or false")


def _tag(node: Any) -> str:
    return str(node.get("tag", "")).strip().casefold() if isinstance(node, Mapping) else ""


def _attributes(node: Any) -> dict[str, Any]:
    if not isinstance(node, Mapping):
        return {}
    attrs = node.get("attributes", {})
    if not isinstance(attrs, Mapping):
        raise PadstackGeometryError(f"{_tag(node) or 'Geometry'} attributes must be an object")
    return {str(key).casefold(): value for key, value in attrs.items()}


def _children(node: Any) -> list[Mapping[str, Any]]:
    if not isinstance(node, Mapping):
        return []
    children = node.get("children", [])
    if not isinstance(children, list) or any(not isinstance(child, Mapping) for child in children):
        raise PadstackGeometryError(f"{_tag(node) or 'Geometry'} children must be objects")
    return children


def _child(node: Any, tag: str) -> Mapping[str, Any] | None:
    return next((child for child in _children(node) if _tag(child) == tag), None)


def _add(first: Point2, second: Point2) -> Point2:
    return (first[0] + second[0], first[1] + second[1])
