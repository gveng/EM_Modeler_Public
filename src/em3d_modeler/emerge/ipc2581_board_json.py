"""Preview selected PCB features from ``ipc2581_pcb.Board.to_dict()`` JSON.

The standalone parser's JSON contract is consumed directly: ``layers``,
``nets``, ``stackups``, ``features`` and ``units``. Polygon, contour, line,
polyline, and explicitly defined padstack features are rendered. Geometry on
layers explicitly classified as documentation is omitted from the copper
preview. Tracks require explicit
positive widths and are expanded to round-cap/round-join buffers (8 segments
per quadrant). Padstack primitives require explicit dimensions and supported
transforms; via geometry requires explicit drill, plating, and layer span.
An explicit ``board_outlines`` profile (or the legacy ``outline`` field) can
produce dielectric preview solids, including curved edges and cutouts. Imported
objects remain preview-only; this module does not parse XML or infer missing
dimensions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
from typing import Any, Mapping

import vtk

from .ipc2581_padstack_geometry import (
    PadstackGeometryError,
    ViaDescriptor,
    decode_padstack_feature,
)


class BoardJSONImportError(ValueError):
    """The board JSON is malformed or uses geometry outside the preview subset."""


_MM_PER_UNIT = {
    "millimeter": 1.0,
    "millimeters": 1.0,
    "mm": 1.0,
    "inch": 25.4,
    "inches": 25.4,
    "in": 25.4,
    "mil": 0.0254,
    "mils": 0.0254,
    "micron": 0.001,
    "microns": 0.001,
    "um": 0.001,
}


@dataclass(frozen=True)
class BoardPreviewGroup:
    """One VTK mesh and the board metadata needed to identify and persist it."""

    name: str
    net: str
    layer: str
    layer_type: str
    feature_count: int
    polydata: vtk.vtkPolyData
    metadata: dict[str, Any]
    object_role: str = "copper"
    step_geometry: dict[str, Any] | None = None


def load_board_json(path: str | Path) -> dict[str, Any]:
    """Load a standalone ``Board.to_dict()`` JSON document."""
    try:
        with Path(path).expanduser().open("r", encoding="utf-8-sig") as stream:
            document = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BoardJSONImportError(f"Unable to read board JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise BoardJSONImportError("Board JSON root must be an object")
    return document


def via_plating_requests(board: dict[str, Any]) -> list[tuple[str, int]]:
    """Return one required plating-thickness input per plated-via padstack."""
    features = board.get("features", [])
    if not isinstance(features, list):
        raise BoardJSONImportError("Board JSON features must be a list")
    requests: dict[str, int] = {}
    for index, feature in enumerate(features, start=1):
        if not isinstance(feature, dict) or str(feature.get("kind", "")).strip().casefold() != "via":
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        plating = str(geometry.get("plating_status") or "").strip().casefold()
        if plating != "plated":
            continue
        padstack_ref = str(geometry.get("padstack_ref") or "").strip()
        key = f"padstack:{padstack_ref}" if padstack_ref else f"via-feature:{index}"
        requests[key] = requests.get(key, 0) + 1
    return list(requests.items())


def _polygon_parts(geometry: Any) -> list[Any]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        return [
            part
            for part in geometry.geoms
            if part.geom_type == "Polygon" and not part.is_empty and part.area > 0
        ]
    return []


def _profile_solid(
    geometry: Any,
    *,
    z_bottom: float,
    z_top: float,
    scale: float,
) -> dict[str, Any]:
    from shapely.geometry.polygon import orient

    parts = _polygon_parts(geometry)
    if not parts or not math.isfinite(z_bottom) or not math.isfinite(z_top) or z_top <= z_bottom:
        raise BoardJSONImportError("Native PCB extrusion must have valid profiles and positive thickness")
    profiles = []
    for raw_polygon in parts:
        polygon = orient(raw_polygon, sign=1.0)
        if not polygon.is_valid or polygon.area <= 0:
            raise BoardJSONImportError("Native PCB profile contains invalid polygon geometry")
        profiles.append({
            "outer": [
                [float(x) * scale, float(y) * scale]
                for x, y in list(polygon.exterior.coords)[:-1]
            ],
            "holes": [
                [
                    [float(x) * scale, float(y) * scale]
                    for x, y in list(ring.coords)[:-1]
                ]
                for ring in polygon.interiors
            ],
        })
    return {
        "kind": "extrusion",
        "profiles": profiles,
        "z_bottom": float(z_bottom),
        "z_top": float(z_top),
    }


def _profile_preview_polydata(
    geometry: Any,
    *,
    z_bottom: float,
    z_top: float,
    scale: float,
    label: str,
) -> vtk.vtkPolyData:
    triangles = [
        triangle
        for polygon in _polygon_parts(geometry)
        for triangle in _triangulate_polygon_with_holes(polygon, label=label)
    ]
    if not triangles:
        raise BoardJSONImportError(f"{label} has no printable copper area")
    base = _polygons_to_polydata(triangles, z=z_bottom, scale=scale)
    return _extrude_polydata(base, height=z_top - z_bottom)


def _via_barrel_polydata(
    via: ViaDescriptor,
    *,
    z_bottom: float,
    z_top: float,
    inner_radius: float,
    outer_radius: float,
    scale: float,
    segments: int = 64,
) -> vtk.vtkPolyData:
    if (
        not math.isfinite(z_bottom)
        or not math.isfinite(z_top)
        or z_top <= z_bottom
        or not math.isfinite(inner_radius)
        or not math.isfinite(outer_radius)
        or inner_radius <= 0
        or outer_radius <= inner_radius
    ):
        raise BoardJSONImportError("Plated via needs a finite, positive barrel thickness and span")

    points = vtk.vtkPoints()
    cells = vtk.vtkCellArray()
    ring_ids = []
    center_x, center_y = via.center[0] * scale, via.center[1] * scale
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        cos_angle, sin_angle = math.cos(angle), math.sin(angle)
        ring_ids.append((
            points.InsertNextPoint(
                center_x + outer_radius * scale * cos_angle,
                center_y + outer_radius * scale * sin_angle,
                z_bottom,
            ),
            points.InsertNextPoint(
                center_x + outer_radius * scale * cos_angle,
                center_y + outer_radius * scale * sin_angle,
                z_top,
            ),
            points.InsertNextPoint(
                center_x + inner_radius * scale * cos_angle,
                center_y + inner_radius * scale * sin_angle,
                z_bottom,
            ),
            points.InsertNextPoint(
                center_x + inner_radius * scale * cos_angle,
                center_y + inner_radius * scale * sin_angle,
                z_top,
            ),
        ))
    for index, current in enumerate(ring_ids):
        following = ring_ids[(index + 1) % segments]
        for face in (
            (current[0], current[1], following[1], following[0]),
            (current[2], following[2], following[3], current[3]),
            (current[1], following[1], following[3], current[3]),
            (current[0], current[2], following[2], following[0]),
        ):
            cells.InsertNextCell(len(face))
            for point_id in face:
                cells.InsertCellPoint(point_id)
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)
    return polydata


def board_json_to_preview_groups(
    board: dict[str, Any],
    *,
    mm_per_scene_unit: float = 1.0,
    simulation_ready: bool = False,
    via_plating_thicknesses_mm: Mapping[str, float] | None = None,
    curve_segments_per_circle: int | None = None,
) -> list[BoardPreviewGroup]:
    """Validate Board JSON and create one geometry group per net/layer pair.

    The standalone model retains coordinates and stackup thicknesses in its
    declared source units. The stackup's ordered layer thicknesses define each
    layer's mid-plane, measured from z=0 at the first stackup surface.
    ``modeler_options.dielectric_representation`` can combine dielectric layer
    geometry into one object without discarding stackup metadata. Simulation
    mode also retains validated 2-D profiles for direct STEP BREP export.
    ``curve_segments_per_circle`` controls the linearization of circular and
    curved PCB outlines when explicitly supplied; ``None`` preserves the
    parser's legacy sampling defaults.

    """
    if not isinstance(board, dict):
        raise BoardJSONImportError("Board JSON root must be an object")
    if curve_segments_per_circle is not None and (
        isinstance(curve_segments_per_circle, bool)
        or not isinstance(curve_segments_per_circle, int)
        or not 6 <= curve_segments_per_circle <= 1000
    ):
        raise BoardJSONImportError(
            "Curve segments per circle must be an integer from 6 to 1000"
        )
    modeler_options = board.get("modeler_options", {})
    if not isinstance(modeler_options, dict):
        raise BoardJSONImportError("Board JSON modeler_options must be an object")
    dielectric_representation = modeler_options.get(
        "dielectric_representation", "per_layer"
    )
    if dielectric_representation not in {"per_layer", "single_object"}:
        raise BoardJSONImportError(
            "Board JSON modeler_options.dielectric_representation must be "
            "'per_layer' or 'single_object'"
        )
    try:
        mm_per_scene_unit = float(mm_per_scene_unit)
    except (TypeError, ValueError) as exc:
        raise BoardJSONImportError("Scene unit scale must be finite and positive") from exc
    if not math.isfinite(mm_per_scene_unit) or mm_per_scene_unit <= 0:
        raise BoardJSONImportError("Scene unit scale must be finite and positive")

    units = board.get("units")
    if not isinstance(units, str) or units.strip().casefold() not in _MM_PER_UNIT:
        raise BoardJSONImportError(f"Unsupported or missing board unit: {units!r}")
    units = units.strip().casefold()
    mm_per_board_unit = _MM_PER_UNIT[units]
    xy_scale = mm_per_board_unit / mm_per_scene_unit

    declared_layers = board.get("layers")
    if not isinstance(declared_layers, list) or not declared_layers:
        raise BoardJSONImportError("Board JSON must contain a non-empty layers list")
    board_layers: dict[str, dict[str, Any]] = {}
    for index, layer in enumerate(declared_layers):
        if not isinstance(layer, dict) or not str(layer.get("name", "")).strip():
            raise BoardJSONImportError(f"Board layer {index + 1} must have a name")
        name = str(layer["name"]).strip()
        if name in board_layers:
            raise BoardJSONImportError(f"Duplicate board layer name: {name!r}")
        board_layers[name] = dict(layer)

    stackups = board.get("stackups")
    if not isinstance(stackups, list) or not stackups:
        raise BoardJSONImportError("Board JSON must contain a non-empty stackups list")
    if len(stackups) != 1:
        raise BoardJSONImportError(
            f"Preview requires exactly one stackup; Board JSON contains {len(stackups)}"
        )
    stackup = stackups[0]
    if not isinstance(stackup, dict):
        raise BoardJSONImportError("Stackup 1 must be an object")
    stackup_layers = stackup.get("layers")
    if not isinstance(stackup_layers, list) or not stackup_layers:
        raise BoardJSONImportError("Stackup 1 must contain a non-empty layers list")

    stackup_by_name: dict[str, dict[str, Any]] = {}
    layer_z_bounds_mm: dict[str, tuple[float, float] | None] = {}
    layer_thickness_mm: dict[str, float | None] = {}
    offset_mm = 0.0
    offset_known = True
    for index, layer in enumerate(stackup_layers):
        if not isinstance(layer, dict) or not str(layer.get("name") or "").strip():
            raise BoardJSONImportError(f"Stackup layer {index + 1} must have a name")
        name = str(layer["name"]).strip()
        if name in stackup_by_name:
            raise BoardJSONImportError(f"Duplicate stackup layer name: {name!r}")
        board_layer = board_layers.get(name)
        role = _stackup_layer_role(layer, board_layer)
        thickness = layer.get("thickness")
        thickness_mm = None
        if thickness is not None:
            thickness_source = _finite_number(
                thickness, f"stackup layer {name!r} thickness"
            )
            if thickness_source < 0 or (
                thickness_source == 0 and role != "reference"
            ):
                raise BoardJSONImportError(
                    f"Stackup layer {name!r} thickness must be positive"
                    + (" or an explicit zero-thickness reference" if role == "reference" else "")
                )
            thickness_mm = thickness_source * mm_per_board_unit
        if role == "reference" and thickness_mm != 0:
            raise BoardJSONImportError(
                f"Reference stackup layer {name!r} must have an explicit zero thickness"
            )
        if offset_known and thickness_mm is not None:
            layer_z_bounds_mm[name] = (offset_mm, offset_mm + thickness_mm)
            offset_mm += thickness_mm
        else:
            layer_z_bounds_mm[name] = None
            offset_known = False
        layer_thickness_mm[name] = thickness_mm
        stackup_by_name[name] = dict(layer)

    stackup_order = {
        name: index for index, name in enumerate(stackup_by_name)
    }
    overall_thickness = stackup.get("overall_thickness")
    if overall_thickness is not None and offset_known:
        overall_thickness_mm = (
            _finite_number(overall_thickness, "stackup overall_thickness")
            * mm_per_board_unit
        )
        tolerance_mm = max(1e-9, abs(overall_thickness_mm) * 1e-6)
        if abs(overall_thickness_mm - offset_mm) > tolerance_mm:
            raise BoardJSONImportError(
                "Stackup overall_thickness disagrees with the sum of explicit layer thicknesses"
            )

    features = board.get("features")
    if not isinstance(features, list) or not features:
        raise BoardJSONImportError("Board JSON must contain a non-empty features list")
    if any(not isinstance(feature, dict) for feature in features):
        raise BoardJSONImportError("Every board feature must be an object")
    try:
        from shapely.geometry import Point, Polygon
        from shapely.ops import unary_union
    except ImportError as exc:
        raise BoardJSONImportError(
            "IPC-2581 board geometry requires the installed Shapely library"
        ) from exc
    if not isinstance(simulation_ready, bool):
        raise BoardJSONImportError("simulation_ready must be a boolean")
    if simulation_ready:
        if via_plating_thicknesses_mm is not None and not isinstance(
            via_plating_thicknesses_mm, Mapping
        ):
            raise BoardJSONImportError("Via plating thicknesses must be a padstack mapping")
        requests = dict(via_plating_requests(board))
        supplied_thicknesses = dict(via_plating_thicknesses_mm or {})
        missing = sorted(set(requests) - set(supplied_thicknesses))
        if missing:
            raise BoardJSONImportError(
                "Missing explicit via plating thickness for: " + ", ".join(missing)
            )
        unknown = sorted(set(supplied_thicknesses) - set(requests))
        if unknown:
            raise BoardJSONImportError(
                "Via plating thickness was supplied for unknown padstacks: "
                + ", ".join(unknown)
            )
        for key, thickness in supplied_thicknesses.items():
            if isinstance(thickness, bool):
                raise BoardJSONImportError(f"Via plating thickness for {key!r} must be positive")
            try:
                value = float(thickness)
            except (TypeError, ValueError) as exc:
                raise BoardJSONImportError(
                    f"Via plating thickness for {key!r} must be positive"
                ) from exc
            if not math.isfinite(value) or value <= 0:
                raise BoardJSONImportError(f"Via plating thickness for {key!r} must be positive")
            supplied_thicknesses[key] = value
    else:
        supplied_thicknesses = {}

    supported_kinds = {"polygon", "line", "polyline", "contour", "pad", "via"}
    unsupported = sorted({
        (str(feature.get("kind", "")).strip().lower() or "<missing kind>")
        for feature in features
        if str(feature.get("kind", "")).strip().lower() not in supported_kinds
    })
    if unsupported:
        kinds = ", ".join(unsupported)
        raise BoardJSONImportError(
            f"Unsupported Board.to_dict() feature kinds: {kinds}. "
            "Preview supports polygon, line, polyline, contour, pad, and via "
            "features with explicit geometry."
        )

    grouped: dict[
        tuple[str, str],
        list[tuple[list[list[tuple[float, float]]], dict[str, Any]]],
    ] = {}
    native_regions: dict[tuple[str, str], list[Any]] = {}
    antipads_by_layer: dict[str, list[tuple[str, Any]]] = {}
    via_records: list[
        tuple[str, ViaDescriptor, dict[str, Any], float, float, int, int]
    ] = []
    padstack_definitions = board.get("padstack_definitions", {})
    if not isinstance(padstack_definitions, dict):
        raise BoardJSONImportError("Board JSON padstack_definitions must be an object")

    for index, feature in enumerate(features):
        kind = str(feature["kind"]).strip().lower()
        label = f"{kind.title()} feature {index + 1}"
        layer_name = str(feature.get("layer") or "").strip()
        if kind != "via" and _is_non_conductor_feature_layer(
            board_layers.get(layer_name)
        ):
            continue
        net_name = str(feature.get("net") or "").strip() or "No Net"
        attributes = feature.get("attributes", {})
        if not isinstance(attributes, dict):
            raise BoardJSONImportError(f"{label} attributes must be an object")
        bbox = feature.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise BoardJSONImportError(f"{label} bbox must contain four coordinates")
            bbox = [
                _finite_number(value, f"{label} bbox coordinate")
                for value in bbox
            ]
        feature_metadata = {
            "kind": kind,
            "source_tag": feature.get("source_tag"),
            "attributes": dict(attributes),
            "bbox": bbox,
        }
        if kind == "via":
            raw_geometry = feature.get("geometry")
            padstack_ref = str(
                raw_geometry.get("padstack_ref") or ""
                if isinstance(raw_geometry, dict)
                else ""
            ).strip()
            feature_metadata["via_padstack_key"] = (
                f"padstack:{padstack_ref}"
                if padstack_ref
                else f"via-feature:{index + 1}"
            )

        if kind in {"pad", "via"}:
            try:
                decoded = decode_padstack_feature(
                    feature,
                    padstack_definitions,
                    circle_segments=curve_segments_per_circle or 64,
                    curve_segments_per_circle=curve_segments_per_circle or 72,
                )
            except PadstackGeometryError as exc:
                raise BoardJSONImportError(f"{label}: {exc}") from exc

            polygons_by_layer: dict[str, list[list[tuple[float, float]]]] = {}
            pad_uses_by_layer: dict[str, list[str]] = {}
            for pad_polygon in decoded.polygons:
                layer_name = pad_polygon.layer_ref
                _conductor_layer_info(
                    layer_name,
                    label,
                    board_layers,
                    stackup_by_name,
                    layer_thickness_mm,
                    layer_z_bounds_mm,
                )
                pad_uses_by_layer.setdefault(layer_name, []).append(pad_polygon.pad_use)
                pad_shape = None
                if simulation_ready:
                    pad_shape = Polygon(pad_polygon.points)
                    if not pad_shape.is_valid or pad_shape.is_empty or pad_shape.area <= 0:
                        raise BoardJSONImportError(
                            f"{label} has invalid pad geometry on layer {layer_name!r}"
                        )
                    if pad_polygon.pad_use.strip().casefold() == "antipad":
                        antipads_by_layer.setdefault(layer_name, []).append(
                            (net_name, pad_shape)
                        )
                        continue
                if not pad_polygon.is_copper:
                    continue
                pad_points = list(pad_polygon.points)
                if decoded.via is not None:
                    if simulation_ready:
                        drill_shape = Point(decoded.via.center).buffer(
                            decoded.via.drill_diameter / 2.0,
                            _circle_quadrant_segments(curve_segments_per_circle, 32),
                        )
                        if not pad_shape.covers(drill_shape):
                            raise BoardJSONImportError(
                                f"{label} via land on {layer_name!r} does not enclose its drill"
                            )
                        pad_shape = pad_shape.difference(drill_shape)
                        native_regions.setdefault((net_name, layer_name), []).append(
                            pad_shape
                        )
                    pad_points = _polygon_without_drill(
                        pad_points,
                        decoded.via.center,
                        decoded.via.drill_diameter,
                        f"{label} via land on {layer_name!r}",
                        circle_segments_per_circle=curve_segments_per_circle,
                    )
                    polygons_by_layer.setdefault(layer_name, []).extend(pad_points)
                else:
                    if simulation_ready:
                        native_regions.setdefault((net_name, layer_name), []).append(
                            pad_shape
                        )
                    polygons_by_layer.setdefault(layer_name, []).append(pad_points)

            if kind == "pad" and not polygons_by_layer:
                raise BoardJSONImportError(
                    f"{label} contains no copper pad geometry; ANTIPAD definitions "
                    "are clearance geometry, not copper lands"
                )

            for layer_name, rendered_polygons in polygons_by_layer.items():
                layer_metadata = {
                    **feature_metadata,
                    "source_geometry": dict(feature.get("geometry") or {}),
                    "pad_uses": pad_uses_by_layer.get(layer_name, []),
                    "stroke_model": None,
                }
                grouped.setdefault((net_name, layer_name), []).append(
                    (rendered_polygons, layer_metadata)
                )

            if decoded.via is not None:
                via = decoded.via
                status = (via.plating_status or "").strip().casefold()
                if status not in {"plated", "nonplated", "non-plated"}:
                    raise BoardJSONImportError(
                        f"{label}: plating_status {via.plating_status!r} is not a "
                        "supported explicit PLATED/NONPLATED value"
                    )
                start_layer, start_stackup, start_bounds = _conductor_layer_info(
                    via.span_start,
                    f"{label} via span start",
                    board_layers,
                    stackup_by_name,
                    layer_thickness_mm,
                    layer_z_bounds_mm,
                )
                end_layer, end_stackup, end_bounds = _conductor_layer_info(
                    via.span_end,
                    f"{label} via span end",
                    board_layers,
                    stackup_by_name,
                    layer_thickness_mm,
                    layer_z_bounds_mm,
                )
                del start_layer, end_layer, start_stackup, end_stackup
                start_index = stackup_order[via.span_start]
                end_index = stackup_order[via.span_end]
                if start_index == end_index:
                    raise BoardJSONImportError(
                        f"{label}: via span endpoints must be distinct conductor layers"
                    )
                start_z_mm = sum(start_bounds) / 2
                end_z_mm = sum(end_bounds) / 2
                via_metadata = {
                    **feature_metadata,
                    "source_geometry": dict(feature.get("geometry") or {}),
                    "pad_uses_by_layer": pad_uses_by_layer,
                    "via": {
                        "center": {"x": via.center[0], "y": via.center[1]},
                        "drill_diameter": via.drill_diameter,
                        "span_start": via.span_start,
                        "span_end": via.span_end,
                        "plating_status": via.plating_status,
                        "representation": (
                            "open bore-wall surface; plating thickness is not "
                            "provided by the source"
                            if status == "plated"
                            else "non-plated drill opening; no copper barrel"
                        ),
                    },
                    "stroke_model": None,
                }
                via_records.append((
                    net_name,
                    via,
                    via_metadata,
                    start_z_mm,
                    end_z_mm,
                    start_index,
                    end_index,
                ))
            continue

        _conductor_layer_info(
            layer_name,
            label,
            board_layers,
            stackup_by_name,
            layer_thickness_mm,
            layer_z_bounds_mm,
        )
        geometry = feature.get("geometry")
        if kind == "polygon":
            rendered_polygons = _polygon_paths(
                geometry,
                label,
                tolerance=0.001 / mm_per_board_unit,
                curve_segments_per_circle=curve_segments_per_circle,
            )
            stroke_model = None
        elif kind == "contour":
            rendered_polygons = _contour_polygons(
                geometry,
                label,
                tolerance=0.001 / mm_per_board_unit,
                curve_segments_per_circle=curve_segments_per_circle,
            )
            stroke_model = None
        else:
            rendered_polygons = _track_polygons(
                kind,
                geometry,
                label,
                tolerance=0.001 / mm_per_board_unit,
                curve_segments_per_circle=curve_segments_per_circle,
            )
            stroke_model = "explicit-width round-cap/round-join buffer; 8 segments/quadrant"
        feature_metadata = {
            **feature_metadata,
            "source_geometry": dict(geometry),
            "stroke_model": stroke_model,
        }
        grouped.setdefault((net_name, layer_name), []).append(
            (rendered_polygons, feature_metadata)
        )
        if simulation_ready:
            for ring in rendered_polygons:
                shape = Polygon(ring)
                if shape.is_empty or not shape.is_valid or shape.area <= 0:
                    raise BoardJSONImportError(
                        f"{label} produced invalid native profile geometry"
                    )
                native_regions.setdefault((net_name, layer_name), []).append(shape)

    nets = board.get("nets", [])
    if not isinstance(nets, list) or any(not isinstance(net, dict) for net in nets):
        raise BoardJSONImportError("Board JSON nets must be a list of objects")
    net_metadata = {
        str(net.get("name", "")).strip(): dict(net)
        for net in nets
        if str(net.get("name", "")).strip()
    }
    warnings = board.get("warnings", [])
    if not isinstance(warnings, list):
        raise BoardJSONImportError("Board JSON warnings must be a list")
    board_metadata = {
        "revision": board.get("revision"),
        "units": units,
        "curve_segments_per_circle": curve_segments_per_circle,
        "modeler_options": dict(modeler_options),
        "layers": [dict(layer) for layer in declared_layers],
        "nets": [dict(net) for net in nets],
        "stackup": dict(stackup),
        "board_outlines": board.get("board_outlines"),
        "warnings": list(warnings),
    }

    outline_polygon = _board_outline_polygon(
        board,
        tolerance=0.001 / mm_per_board_unit,
        curve_segments_per_circle=curve_segments_per_circle,
    )
    if simulation_ready and outline_polygon is None and any(
        _stackup_layer_role(layer, board_layers.get(name)) == "dielectric"
        for name, layer in stackup_by_name.items()
    ):
        raise BoardJSONImportError(
            "Simulation-ready PCB import requires an explicit board outline "
            "to build its dielectric layers and cutouts"
        )
    results = []
    for (net_name, layer_name), feature_records in grouped.items():
        z_bottom_mm, z_top_mm = layer_z_bounds_mm[layer_name]  # validated above
        if simulation_ready:
            region = unary_union(native_regions.get((net_name, layer_name), []))
            if outline_polygon is not None:
                region = region.intersection(outline_polygon)
            clearances = [
                shape
                for clearance_net, shape in antipads_by_layer.get(layer_name, [])
                if clearance_net != net_name
            ]
            if clearances:
                region = region.difference(unary_union(clearances))
            if region.is_empty or not region.is_valid:
                raise BoardJSONImportError(
                    f"Net {net_name!r} on layer {layer_name!r} has no valid copper "
                    "after board cutouts and antipads are applied"
                )
            z_bottom = z_bottom_mm / mm_per_scene_unit
            z_top = z_top_mm / mm_per_scene_unit
            polydata = _profile_preview_polydata(
                region,
                z_bottom=z_bottom,
                z_top=z_top,
                scale=xy_scale,
                label=f"Net {net_name!r} layer {layer_name!r}",
            )
            step_geometry = {
                "schema": "ipc2581-profile-v1",
                "solids": [_profile_solid(
                    region,
                    z_bottom=z_bottom,
                    z_top=z_top,
                    scale=xy_scale,
                )],
            }
        else:
            base_polydata = _polygons_to_polydata(
                [
                    polygon
                    for record in feature_records
                    for polygon in record[0]
                ],
                z=z_bottom_mm / mm_per_scene_unit,
                scale=xy_scale,
            )
            polydata = _extrude_polydata(
                base_polydata,
                height=(z_top_mm - z_bottom_mm) / mm_per_scene_unit,
            )
            step_geometry = None
        if polydata.GetNumberOfPolys() == 0 or polydata.GetNumberOfPoints() == 0:
            raise BoardJSONImportError(
                f"No previewable polygon geometry for net {net_name!r}, "
                f"layer {layer_name!r}"
            )
        layer = board_layers[layer_name]
        stackup_layer = stackup_by_name[layer_name]
        results.append(BoardPreviewGroup(
            name=f"{net_name}_{_safe_name(layer_name)}",
            net=net_name,
            layer=layer_name,
            layer_type=str(layer.get("function") or "unknown"),
            feature_count=len(feature_records),
            polydata=polydata,
            metadata={
                **board_metadata,
                "net": net_name,
                "net_metadata": net_metadata.get(net_name),
                "layer": layer,
                "stackup_layer": stackup_layer,
                "layer_z_bottom_mm": z_bottom_mm,
                "layer_z_top_mm": z_top_mm,
                "layer_thickness_mm": z_top_mm - z_bottom_mm,
                "preview_only": not simulation_ready,
                "simulation_authoritative": simulation_ready,
                "feature_metadata": [record[1] for record in feature_records],
            },
            step_geometry=step_geometry,
        ))

    if simulation_ready:
        native_vias: dict[tuple[str, str, str], dict[str, Any]] = {}
        for (
            net_name,
            via,
            feature_metadata,
            start_z_mm,
            end_z_mm,
            _start_index,
            _end_index,
        ) in via_records:
            if (via.plating_status or "").strip().casefold() != "plated":
                continue
            z_bottom_mm, z_top_mm = sorted((start_z_mm, end_z_mm))
            padstack_key = str(feature_metadata["via_padstack_key"])
            plating_mm = supplied_thicknesses[padstack_key]
            inner_radius = via.drill_diameter / 2.0
            outer_radius = inner_radius + plating_mm / mm_per_board_unit
            z_bottom = z_bottom_mm / mm_per_scene_unit
            z_top = z_top_mm / mm_per_scene_unit
            barrel = _via_barrel_polydata(
                via,
                z_bottom=z_bottom,
                z_top=z_top,
                inner_radius=inner_radius,
                outer_radius=outer_radius,
                scale=xy_scale,
                segments=curve_segments_per_circle or 64,
            )
            key = (net_name, via.span_start, via.span_end)
            bucket = native_vias.setdefault(key, {
                "meshes": [],
                "solids": [],
                "features": [],
                "z_bottom_mm": z_bottom_mm,
                "z_top_mm": z_top_mm,
            })
            bucket["meshes"].append(barrel)
            bucket["solids"].append({
                "kind": "cylinder_tube",
                "center": [via.center[0] * xy_scale, via.center[1] * xy_scale],
                "inner_radius": inner_radius * xy_scale,
                "outer_radius": outer_radius * xy_scale,
                "z_bottom": z_bottom,
                "z_top": z_top,
            })
            feature_metadata["via"]["plating_thickness_mm"] = plating_mm
            feature_metadata["via"]["representation"] = (
                "closed copper barrel solid with explicit radial plating thickness"
            )
            bucket["features"].append(feature_metadata)

        for (net_name, span_start, span_end), bucket in native_vias.items():
            append = vtk.vtkAppendPolyData()
            for barrel in bucket["meshes"]:
                append.AddInputData(barrel)
            append.Update()
            combined = vtk.vtkPolyData()
            combined.DeepCopy(append.GetOutput())
            z_bottom_mm = bucket["z_bottom_mm"]
            z_top_mm = bucket["z_top_mm"]
            results.append(BoardPreviewGroup(
                name=f"{net_name}_Via_{_safe_name(span_start)}_{_safe_name(span_end)}",
                net=net_name,
                layer=f"{span_start}_to_{span_end}",
                layer_type="VIA",
                feature_count=len(bucket["features"]),
                polydata=combined,
                object_role="via",
                metadata={
                    **board_metadata,
                    "net": net_name,
                    "net_metadata": net_metadata.get(net_name),
                    "layer": None,
                    "stackup_layer": None,
                    "layer_z_bottom_mm": z_bottom_mm,
                    "layer_z_top_mm": z_top_mm,
                    "layer_thickness_mm": z_top_mm - z_bottom_mm,
                    "preview_only": False,
                    "simulation_authoritative": True,
                    "feature_metadata": bucket["features"],
                },
                step_geometry={
                    "schema": "ipc2581-profile-v1",
                    "solids": bucket["solids"],
                },
            ))
    else:
        for (
            net_name,
            via,
            feature_metadata,
            start_z_mm,
            end_z_mm,
            _start_index,
            _end_index,
        ) in via_records:
            if (via.plating_status or "").strip().casefold() != "plated":
                continue
            z_bottom_mm, z_top_mm = sorted((start_z_mm, end_z_mm))
            via_layer = f"{via.span_start}_to_{via.span_end}"
            wall = _via_wall_polydata(
                via,
                z_bottom=z_bottom_mm / mm_per_scene_unit,
                z_top=z_top_mm / mm_per_scene_unit,
                scale=xy_scale,
            )
            results.append(BoardPreviewGroup(
                name=(
                    f"{net_name}_Via_"
                    f"{_safe_name(via.span_start)}_{_safe_name(via.span_end)}"
                ),
                net=net_name,
                layer=via_layer,
                layer_type="VIA",
                feature_count=1,
                polydata=wall,
                object_role="via",
                metadata={
                    **board_metadata,
                    "net": net_name,
                    "net_metadata": net_metadata.get(net_name),
                    "layer": None,
                    "stackup_layer": None,
                    "layer_z_bottom_mm": z_bottom_mm,
                    "layer_z_top_mm": z_top_mm,
                    "layer_thickness_mm": z_top_mm - z_bottom_mm,
                    "preview_only": True,
                    "simulation_authoritative": False,
                    "feature_metadata": [feature_metadata],
                },
            ))
    if outline_polygon is not None:
        dielectric_groups: list[BoardPreviewGroup] = []
        outline_points = [
            (float(x), float(y))
            for x, y in list(outline_polygon.exterior.coords)[:-1]
        ]
        for name, stackup_layer in stackup_by_name.items():
            role = _stackup_layer_role(stackup_layer, board_layers.get(name))
            if role in {"conductor", "reference"}:
                continue
            if role != "dielectric":
                raise BoardJSONImportError(
                    f"Stackup layer {name!r} cannot be classified as conductor, "
                    "dielectric, or a zero-thickness reference from its "
                    "Board.to_dict() metadata"
                )
            thickness_mm = layer_thickness_mm[name]
            z_bounds_mm = layer_z_bounds_mm[name]
            if thickness_mm is None or thickness_mm <= 0:
                raise BoardJSONImportError(
                    f"Dielectric layer {name!r} has no explicit positive thickness"
                )
            if z_bounds_mm is None:
                raise BoardJSONImportError(
                    f"Dielectric layer {name!r} has an ambiguous z position because "
                    "an earlier stackup layer has no explicit thickness"
                )
            z_bottom_mm, z_top_mm = z_bounds_mm
            dielectric_polygon = outline_polygon
            via_drill_count = 0
            for (
                _net_name,
                via,
                _feature_metadata,
                _start_z_mm,
                _end_z_mm,
                start_index,
                end_index,
            ) in via_records:
                if not min(start_index, end_index) < stackup_order[name] < max(
                    start_index, end_index
                ):
                    continue
                try:
                    from shapely.geometry import Point
                except ImportError as exc:
                    raise BoardJSONImportError(
                        "Via drill preview requires the installed Shapely geometry library"
                    ) from exc
                drill = Point(via.center).buffer(
                    via.drill_diameter / 2.0,
                    _circle_quadrant_segments(curve_segments_per_circle, 16),
                )
                if not outline_polygon.contains(drill):
                    raise BoardJSONImportError(
                        f"Via drill at {via.center!r} is not fully inside the board profile"
                    )
                dielectric_polygon = dielectric_polygon.difference(drill)
                via_drill_count += 1
            if (
                dielectric_polygon.is_empty
                or not dielectric_polygon.is_valid
                or dielectric_polygon.geom_type != "Polygon"
            ):
                raise BoardJSONImportError(
                    f"Via drills create unsupported geometry in dielectric layer {name!r}"
                )
            solid = _extrude_polygon_with_holes(
                dielectric_polygon,
                z_bottom=z_bottom_mm / mm_per_scene_unit,
                z_top=z_top_mm / mm_per_scene_unit,
                scale=xy_scale,
            )
            step_geometry = {
                "schema": "ipc2581-profile-v1",
                "solids": [_profile_solid(
                    dielectric_polygon,
                    z_bottom=z_bottom_mm / mm_per_scene_unit,
                    z_top=z_top_mm / mm_per_scene_unit,
                    scale=xy_scale,
                )],
            } if simulation_ready else None
            dielectric_groups.append(BoardPreviewGroup(
                name=f"IPC2581_Dielectric_{_safe_name(name)}",
                net="",
                layer=name,
                layer_type="DIELECTRIC",
                feature_count=1,
                polydata=solid,
                object_role="dielectric",
                metadata={
                    **board_metadata,
                    "net": None,
                    "layer": board_layers.get(name),
                    "stackup_layer": stackup_layer,
                    "layer_z_bottom_mm": z_bottom_mm,
                    "layer_z_top_mm": z_top_mm,
                    "layer_thickness_mm": z_top_mm - z_bottom_mm,
                    "object_role": "dielectric",
                    "board_outline": [
                        {"x": x, "y": y} for x, y in outline_points
                    ],
                    "board_cutout_count": len(outline_polygon.interiors),
                    "via_drill_count": via_drill_count,
                    "preview_only": not simulation_ready,
                    "simulation_authoritative": simulation_ready,
                },
                step_geometry=step_geometry,
            ))
        if dielectric_representation == "single_object" and dielectric_groups:
            append = vtk.vtkAppendPolyData()
            for group in dielectric_groups:
                append.AddInputData(group.polydata)
            append.Update()
            combined_polydata = vtk.vtkPolyData()
            combined_polydata.DeepCopy(append.GetOutput())
            layer_records = [
                {
                    "name": group.layer,
                    "stackup_layer": group.metadata["stackup_layer"],
                    "layer_z_bottom_mm": group.metadata["layer_z_bottom_mm"],
                    "layer_z_top_mm": group.metadata["layer_z_top_mm"],
                    "layer_thickness_mm": group.metadata["layer_thickness_mm"],
                    "via_drill_count": group.metadata["via_drill_count"],
                }
                for group in dielectric_groups
            ]
            z_bottom_mm = min(
                record["layer_z_bottom_mm"] for record in layer_records
            )
            z_top_mm = max(
                record["layer_z_top_mm"] for record in layer_records
            )
            step_geometry = {
                "schema": "ipc2581-profile-v1",
                "solids": [
                    solid
                    for group in dielectric_groups
                    for solid in (group.step_geometry or {}).get("solids", [])
                ],
            } if simulation_ready else None
            results.append(BoardPreviewGroup(
                name="IPC2581_Dielectric",
                net="",
                layer="Dielectric",
                layer_type="DIELECTRIC",
                feature_count=1,
                polydata=combined_polydata,
                object_role="dielectric",
                metadata={
                    **board_metadata,
                    "net": None,
                    "layer": None,
                    "stackup_layer": None,
                    "stackup_layers": layer_records,
                    "layer_z_bottom_mm": z_bottom_mm,
                    "layer_z_top_mm": z_top_mm,
                    "layer_thickness_mm": z_top_mm - z_bottom_mm,
                    "dielectric_material_thickness_mm": sum(
                        record["layer_thickness_mm"] for record in layer_records
                    ),
                    "object_role": "dielectric",
                    "board_outline": [
                        {"x": x, "y": y} for x, y in outline_points
                    ],
                    "board_cutout_count": len(outline_polygon.interiors),
                    "preview_only": not simulation_ready,
                    "simulation_authoritative": simulation_ready,
                },
                step_geometry=step_geometry,
            ))
        else:
            results.extend(dielectric_groups)
    return results


def _finite_number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BoardJSONImportError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise BoardJSONImportError(f"{label} must be a finite number")
    return result


def _conductor_layer_info(
    layer_name: str,
    label: str,
    board_layers: dict[str, dict[str, Any]],
    stackup_by_name: dict[str, dict[str, Any]],
    layer_thickness_mm: dict[str, float | None],
    layer_z_bounds_mm: dict[str, tuple[float, float] | None],
) -> tuple[dict[str, Any], dict[str, Any], tuple[float, float]]:
    if layer_name not in board_layers:
        raise BoardJSONImportError(
            f"{label} references unknown board layer {layer_name!r}"
        )
    if layer_name not in stackup_by_name:
        raise BoardJSONImportError(
            f"{label} layer {layer_name!r} has no position in the selected stackup"
        )
    board_layer = board_layers[layer_name]
    stackup_layer = stackup_by_name[layer_name]
    if _stackup_layer_role(stackup_layer, board_layer) != "conductor":
        raise BoardJSONImportError(
            f"{label} layer {layer_name!r} is not identified as a conductor by "
            "its Board layer function or stackup material"
        )
    thickness_mm = layer_thickness_mm[layer_name]
    if thickness_mm is None or thickness_mm <= 0:
        raise BoardJSONImportError(
            f"Conductor layer {layer_name!r} has no explicit positive thickness "
            "in the selected stackup"
        )
    z_bounds_mm = layer_z_bounds_mm[layer_name]
    if z_bounds_mm is None:
        raise BoardJSONImportError(
            f"Conductor layer {layer_name!r} has an ambiguous z position because "
            "an earlier stackup layer has no explicit thickness"
        )
    return board_layer, stackup_layer, z_bounds_mm


def _polygon_without_drill(
    points: list[tuple[float, float]],
    center: tuple[float, float],
    drill_diameter: float,
    label: str,
    *,
    circle_segments_per_circle: int | None = None,
) -> list[list[tuple[float, float]]]:
    try:
        from shapely.geometry import Point, Polygon
        from shapely.ops import triangulate
    except ImportError as exc:
        raise BoardJSONImportError(
            "Via land preview requires the installed Shapely geometry library"
        ) from exc

    pad_polygon = Polygon(points)
    drill = Point(center).buffer(
        drill_diameter / 2.0,
        _circle_quadrant_segments(circle_segments_per_circle, 16),
    )
    if not pad_polygon.is_valid or pad_polygon.is_empty:
        raise BoardJSONImportError(f"{label} is not a valid polygon")
    if not pad_polygon.covers(drill):
        raise BoardJSONImportError(
            f"{label} does not fully enclose its explicit drill opening"
        )
    annulus = pad_polygon.difference(drill)
    if annulus.is_empty or not annulus.is_valid:
        raise BoardJSONImportError(f"{label} drill opening removes the complete pad")
    parts = list(annulus.geoms) if annulus.geom_type == "MultiPolygon" else [annulus]
    polygons: list[list[tuple[float, float]]] = []
    triangulated_area = 0.0
    for part in parts:
        if part.geom_type != "Polygon":
            raise BoardJSONImportError(
                f"{label} drill subtraction produced unsupported {part.geom_type} geometry"
            )
        if part.interiors:
            triangles = [
                triangle
                for triangle in triangulate(part)
                if part.covers(triangle)
            ]
            for triangle in triangles:
                ring = [
                    (float(x), float(y))
                    for x, y in list(triangle.exterior.coords)[:-1]
                ]
                if len(ring) == 3:
                    polygons.append(ring)
                    triangulated_area += triangle.area
        else:
            ring = [
                (float(x), float(y))
                for x, y in list(part.exterior.coords)[:-1]
            ]
            polygons.append(ring)
            triangulated_area += part.area
    if not polygons or not math.isclose(
        triangulated_area,
        annulus.area,
        rel_tol=1e-6,
        abs_tol=max(1e-12, annulus.area * 1e-8),
    ):
        raise BoardJSONImportError(
            f"{label} drill opening could not be triangulated completely"
        )
    return polygons


def _via_wall_polydata(
    via: ViaDescriptor,
    *,
    z_bottom: float,
    z_top: float,
    scale: float,
    segments: int = 64,
) -> vtk.vtkPolyData:
    if not math.isfinite(z_bottom) or not math.isfinite(z_top) or z_top <= z_bottom:
        raise BoardJSONImportError("Via span must have a finite positive height")
    radius = via.drill_diameter * scale / 2.0
    if not math.isfinite(radius) or radius <= 0:
        raise BoardJSONImportError("Via drill diameter must be finite and positive")
    center_x, center_y = via.center[0] * scale, via.center[1] * scale
    points = vtk.vtkPoints()
    cells = vtk.vtkCellArray()
    rings = []
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        rings.append((
            points.InsertNextPoint(x, y, z_bottom),
            points.InsertNextPoint(x, y, z_top),
        ))
    for index, ring in enumerate(rings):
        next_ring = rings[(index + 1) % segments]
        cells.InsertNextCell(4)
        for point_id in (ring[0], rings[index][1], next_ring[1], next_ring[0]):
            cells.InsertCellPoint(point_id)
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)
    return polydata


def _circle_quadrant_segments(
    segments_per_circle: int | None,
    default: int,
) -> int:
    if segments_per_circle is None:
        return default
    return max(1, math.ceil(segments_per_circle / 4))


def _polygon_points(
    geometry: Any,
    label: str,
    *,
    tolerance: float = 0.001,
    curve_segments_per_circle: int | None = None,
) -> list[tuple[float, float]]:
    if not isinstance(geometry, dict):
        raise BoardJSONImportError(f"{label} geometry must be an object")
    raw_points = geometry.get("points")
    segments = geometry.get("segments")
    if not isinstance(raw_points, list) or len(raw_points) < 4:
        raise BoardJSONImportError(
            f"{label} geometry.points must contain a closed polygon with at least 4 entries"
        )
    if not isinstance(segments, list) or len(segments) != len(raw_points) - 1:
        raise BoardJSONImportError(
            f"{label} geometry.segments must describe every polygon edge"
        )
    if geometry.get("closed") is not True:
        raise BoardJSONImportError(f"{label} geometry must explicitly be closed")
    subpath_count = geometry.get("subpath_count")
    if subpath_count is not None:
        if type(subpath_count) is not int:
            raise BoardJSONImportError(
                f"{label} geometry.subpath_count must be the integer 1"
            )
        if subpath_count != 1:
            raise BoardJSONImportError(
                f"{label} has {subpath_count} subpaths; multiple polygon subpaths are not supported"
            )

    points: list[tuple[float, float]] = []
    for point_index, point in enumerate(raw_points):
        if not isinstance(point, dict) or "x" not in point or "y" not in point:
            raise BoardJSONImportError(
                f"{label} point {point_index + 1} must be an object with x and y"
            )
        points.append((
            _finite_number(point["x"], f"{label} x coordinate"),
            _finite_number(point["y"], f"{label} y coordinate"),
        ))
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise BoardJSONImportError(f"{label} segment {index + 1} must be an object")
        endpoint = segment.get("to")
        if not isinstance(endpoint, dict) or not {"x", "y"}.issubset(endpoint):
            raise BoardJSONImportError(
                f"{label} segment {index + 1} must have an x/y 'to' point"
            )
        endpoint_point = (
            _finite_number(endpoint["x"], f"{label} segment endpoint x"),
            _finite_number(endpoint["y"], f"{label} segment endpoint y"),
        )
        if endpoint_point != points[index + 1]:
            raise BoardJSONImportError(
                f"{label} segment {index + 1} endpoint does not match geometry points"
            )

    points = _profile_contour_points(
        geometry,
        label=label,
        tolerance=tolerance,
        curve_segments_per_circle=curve_segments_per_circle,
    )
    try:
        from shapely.geometry import Polygon
    except ImportError as exc:
        raise BoardJSONImportError(
            "Polygon feature preview requires the installed Shapely geometry library"
        ) from exc
    polygon = Polygon(points)
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise BoardJSONImportError(
            f"{label} must form one valid, non-zero-area polygon"
        )
    return points


def _polygon_paths(
    geometry: Any,
    label: str,
    *,
    tolerance: float = 0.001,
    curve_segments_per_circle: int | None = None,
) -> list[list[tuple[float, float]]]:
    """Read all explicitly closed polygon paths in a polygon feature."""
    if not isinstance(geometry, dict):
        raise BoardJSONImportError(f"{label} geometry must be an object")

    raw_points = geometry.get("points")
    segments = geometry.get("segments")
    if raw_points or segments:
        return [_polygon_points(
            geometry,
            label,
            tolerance=tolerance,
            curve_segments_per_circle=curve_segments_per_circle,
        )]

    paths = geometry.get("paths")
    if not isinstance(paths, list) or not paths:
        return [_polygon_points(
            geometry,
            label,
            tolerance=tolerance,
            curve_segments_per_circle=curve_segments_per_circle,
        )]
    subpath_count = geometry.get("subpath_count")
    if subpath_count is not None and (
        type(subpath_count) is not int or subpath_count != len(paths)
    ):
        raise BoardJSONImportError(
            f"{label} geometry.subpath_count does not match geometry.paths"
        )
    return [
        _polygon_points(
            path,
            f"{label} polygon path {index + 1}",
            tolerance=tolerance,
            curve_segments_per_circle=curve_segments_per_circle,
        )
        for index, path in enumerate(paths)
    ]


def _board_outline_points(outline: Any) -> list[tuple[float, float]]:
    """Return one explicitly closed, straight-sided outline; never use a bbox."""
    contours = None
    if isinstance(outline, list):
        contours = outline
    elif isinstance(outline, dict):
        if outline.get("holes") or outline.get("interiors"):
            raise BoardJSONImportError("Board outline holes are not supported")
        if "contours" in outline or "outlines" in outline:
            contours = outline.get("contours", outline.get("outlines"))
        else:
            contours = [outline]
    else:
        raise BoardJSONImportError("Board outline must be one explicit polygon contour")

    if not isinstance(contours, list) or not contours:
        raise BoardJSONImportError("Board outline contains no explicit contours")
    if len(contours) != 1:
        raise BoardJSONImportError(
            f"Board outline has {len(contours)} contours; multiple outlines are not supported"
        )
    contour = contours[0]
    if not isinstance(contour, dict):
        raise BoardJSONImportError(
            "Board outline contour must contain explicit points and straight segments"
        )
    if contour.get("kind") not in (None, "polygon", "outline", "profile"):
        raise BoardJSONImportError(
            f"Unsupported board outline kind: {contour.get('kind')!r}"
        )
    geometry = contour.get("geometry", contour)
    if isinstance(geometry, dict) and (
        geometry.get("holes") or geometry.get("interiors")
    ):
        raise BoardJSONImportError("Board outline holes are not supported")
    return _polygon_points(geometry, "Board outline")


def _board_outline_polygon(
    board: dict[str, Any],
    *,
    tolerance: float,
    curve_segments_per_circle: int | None = None,
) -> Any | None:
    """Build the explicit IPC profile polygon, retaining its cutout contours."""
    raw_outlines = board.get("board_outlines")
    if raw_outlines is None:
        legacy_outline = board.get("outline")
        if legacy_outline is None:
            return None
        exterior = _board_outline_points(legacy_outline)
        contours = [exterior]
    else:
        if not isinstance(raw_outlines, list) or not raw_outlines:
            raise BoardJSONImportError("Board.to_dict() board_outlines must be a non-empty list")
        if len(raw_outlines) != 1:
            raise BoardJSONImportError(
                f"Board has {len(raw_outlines)} Step/Profile outlines; "
                "multiple outlines are not supported"
            )
        profile = raw_outlines[0]
        if not isinstance(profile, dict):
            raise BoardJSONImportError("Board outline entry must be an object")
        raw_contours = profile.get("contours")
        if not isinstance(raw_contours, list) or not raw_contours:
            raise BoardJSONImportError("Step/Profile has no explicit contours")
        exterior_contours = []
        cutout_contours = []
        for index, contour in enumerate(raw_contours):
            if not isinstance(contour, dict):
                raise BoardJSONImportError(
                    f"Step/Profile contour {index + 1} must be an object"
                )
            kind = str(contour.get("kind") or "").strip().casefold()
            points = _profile_contour_points(
                contour,
                label=f"Step/Profile contour {index + 1}",
                tolerance=tolerance,
                curve_segments_per_circle=curve_segments_per_circle,
            )
            if kind in {"outline", "polygon", "profile"}:
                exterior_contours.append(points)
            elif kind in {"cutout", "hole"}:
                cutout_contours.append(points)
            else:
                raise BoardJSONImportError(
                    f"Step/Profile contour {index + 1} has unsupported kind {kind!r}"
                )
        if len(exterior_contours) != 1:
            raise BoardJSONImportError(
                f"Step/Profile has {len(exterior_contours)} outer contours; "
                "exactly one is supported"
            )
        contours = [exterior_contours[0], *cutout_contours]

    try:
        from shapely.geometry import Polygon
        from shapely.geometry.polygon import orient
    except ImportError as exc:
        raise BoardJSONImportError(
            "Board profile preview requires the installed Shapely geometry library"
        ) from exc

    polygon = Polygon(contours[0], holes=contours[1:])
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise BoardJSONImportError(
            "Board profile outer contour and cutouts must form one valid polygon"
        )
    return orient(polygon, sign=1.0)


def _profile_contour_points(
    contour: dict[str, Any],
    *,
    label: str,
    tolerance: float,
    closed: bool = True,
    curve_segments_per_circle: int | None = None,
) -> list[tuple[float, float]]:
    if contour.get("closed") is not closed:
        if closed:
            raise BoardJSONImportError(f"{label} is not explicitly closed")
        raise BoardJSONImportError(f"{label} is not explicitly open")
    raw_points = contour.get("points")
    segments = contour.get("segments")
    min_points = 3 if closed else 2
    if not isinstance(raw_points, list) or len(raw_points) < min_points:
        raise BoardJSONImportError(
            f"{label} needs at least {min_points} explicit points"
        )
    if not isinstance(segments, list) or len(segments) != len(raw_points) - 1:
        raise BoardJSONImportError(f"{label} has inconsistent point/segment counts")
    points = []
    for index, point in enumerate(raw_points):
        if not isinstance(point, dict) or "x" not in point or "y" not in point:
            raise BoardJSONImportError(f"{label} point {index + 1} needs finite x/y coordinates")
        points.append((
            _finite_number(point["x"], f"{label} x coordinate"),
            _finite_number(point["y"], f"{label} y coordinate"),
        ))
    if closed and points[0] != points[-1]:
        raise BoardJSONImportError(f"{label} does not repeat its first point")
    if not closed and points[0] == points[-1]:
        raise BoardJSONImportError(f"{label} must have distinct open endpoints")

    path = [points[0]]
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise BoardJSONImportError(f"{label} segment {index + 1} must be an object")
        endpoint = points[index + 1]
        kind = str(segment.get("kind") or "").strip().casefold()
        if kind == "polystepsegment":
            path.append(endpoint)
            continue
        if kind != "polystepcurve":
            raise BoardJSONImportError(
                f"{label} has unsupported segment kind {kind or '<missing>'!r}"
            )
        center = segment.get("center")
        if not isinstance(center, dict) or not {"x", "y"}.issubset(center):
            raise BoardJSONImportError(f"{label} curve {index + 1} has no explicit center")
        center_point = (
            _finite_number(center["x"], f"{label} curve center x"),
            _finite_number(center["y"], f"{label} curve center y"),
        )
        clockwise = segment.get("clockwise")
        if not isinstance(clockwise, bool):
            raise BoardJSONImportError(
                f"{label} curve {index + 1} has no explicit direction"
            )
        start = path[-1]
        start_radius = math.dist(start, center_point)
        end_radius = math.dist(endpoint, center_point)
        radius = (start_radius + end_radius) / 2
        radius_delta = abs(start_radius - end_radius)
        allowed_center_correction = min(
            max(tolerance * 10.0, radius * 1e-5),
            radius * 1e-2,
        )
        chord_x = endpoint[0] - start[0]
        chord_y = endpoint[1] - start[1]
        chord_length = math.hypot(chord_x, chord_y)
        center_correction = 0.0
        if chord_length > 1e-15:
            center_correction = (
                end_radius * end_radius - start_radius * start_radius
            ) / (2.0 * chord_length)
            shift_x = center_correction * chord_x / chord_length
            shift_y = center_correction * chord_y / chord_length
            within_preview_tolerance = (
                abs(center_correction) <= allowed_center_correction
            )
            within_source_precision = _arc_center_correction_fits_source_precision(
                center,
                shift_x,
                shift_y,
            )
            within_radius_limit = abs(center_correction) <= radius * 1e-2
            if radius <= 0 or not (
                within_preview_tolerance
                or (within_source_precision and within_radius_limit)
            ):
                raise BoardJSONImportError(
                    f"{label} curve {index + 1} has inconsistent endpoint radii "
                    f"(difference {radius_delta:.9g}; required center correction "
                f"{abs(center_correction):.9g}, allowed "
                f"{allowed_center_correction:.9g})"
            )
        if chord_length > 1e-15:
            center_point = (
                center_point[0] + center_correction * chord_x / chord_length,
                center_point[1] + center_correction * chord_y / chord_length,
            )
        radius = math.dist(start, center_point)
        start_angle = math.atan2(start[1] - center_point[1], start[0] - center_point[0])
        end_angle = math.atan2(endpoint[1] - center_point[1], endpoint[0] - center_point[0])
        sweep = (end_angle - start_angle) % (2 * math.pi)
        if clockwise:
            sweep = sweep - 2 * math.pi if sweep > 1e-14 else -2 * math.pi
        elif sweep <= 1e-14:
            sweep = 2 * math.pi
        if curve_segments_per_circle is not None:
            count = max(
                1,
                math.ceil(
                    curve_segments_per_circle * abs(sweep) / (2.0 * math.pi)
                ),
            )
        else:
            ratio = min(2.0, tolerance / radius)
            max_angle = min(
                2 * math.acos(max(-1.0, min(1.0, 1.0 - ratio))),
                math.pi / 2,
            )
            if max_angle <= 0:
                raise BoardJSONImportError(f"{label} curve tolerance is too small")
            count = max(1, math.ceil(abs(sweep) / max_angle))
        if count > 100_000:
            raise BoardJSONImportError(
                f"{label} curve requires more than 100000 segments at preview tolerance"
            )
        for step in range(1, count + 1):
            if step == count:
                path.append(endpoint)
            else:
                angle = start_angle + sweep * step / count
                path.append((
                    center_point[0] + radius * math.cos(angle),
                    center_point[1] + radius * math.sin(angle),
                ))
    if closed:
        if path[0] != path[-1]:
            raise BoardJSONImportError(f"{label} tessellation is not closed")
        path.pop()
    elif path[-1] != points[-1]:
        raise BoardJSONImportError(f"{label} tessellation has an incorrect endpoint")
    if len(set(path)) < min_points:
        raise BoardJSONImportError(
            f"{label} has fewer than {min_points} distinct vertices"
        )
    return path


def _arc_center_correction_fits_source_precision(
    source_center: dict[str, Any],
    shift_x: float,
    shift_y: float,
) -> bool:
    half_steps = []
    for axis in ("x", "y"):
        try:
            decimal_value = Decimal(str(source_center[axis]))
            if not decimal_value.is_finite():
                return False
            half_step = float(
                Decimal(5).scaleb(decimal_value.as_tuple().exponent - 1)
            )
        except (InvalidOperation, KeyError, TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(half_step) or half_step < 0:
            return False
        half_steps.append(half_step)

    return all(
        abs(shift) <= half_step
        or math.isclose(abs(shift), half_step, rel_tol=1e-12, abs_tol=1e-15)
        for shift, half_step in zip((shift_x, shift_y), half_steps)
    )


def _contour_polygons(
    geometry: Any,
    label: str,
    *,
    tolerance: float,
    curve_segments_per_circle: int | None = None,
) -> list[list[tuple[float, float]]]:
    """Validate and tessellate one explicitly closed copper contour."""
    if not isinstance(geometry, dict):
        raise BoardJSONImportError(f"{label} geometry must be an object")
    subpath_count = geometry.get("subpath_count")
    if type(subpath_count) is not int:
        raise BoardJSONImportError(
            f"{label} geometry.subpath_count must be an integer"
        )

    try:
        from shapely.geometry import MultiPolygon, Polygon
    except ImportError as exc:
        raise BoardJSONImportError(
            "Contour feature preview requires the installed Shapely geometry library"
        ) from exc

    def read_path(path_geometry: Any, path_label: str) -> list[tuple[float, float]]:
        if not isinstance(path_geometry, dict):
            raise BoardJSONImportError(f"{path_label} must be an object")
        raw_points = path_geometry.get("points")
        segments = path_geometry.get("segments")
        if not isinstance(raw_points, list) or not isinstance(segments, list):
            raise BoardJSONImportError(
                f"{path_label} must contain explicit points and segments"
            )
        if len(segments) != len(raw_points) - 1:
            raise BoardJSONImportError(
                f"{path_label} has inconsistent point/segment counts"
            )

        # Each segment endpoint must agree with the path's canonical points.
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                raise BoardJSONImportError(
                    f"{path_label} segment {index + 1} must be an object"
                )
            endpoint = segment.get("to")
            if not isinstance(endpoint, dict) or not {"x", "y"}.issubset(endpoint):
                raise BoardJSONImportError(
                    f"{path_label} segment {index + 1} must have an x/y 'to' point"
                )
            endpoint_point = (
                _finite_number(endpoint["x"], f"{path_label} segment endpoint x"),
                _finite_number(endpoint["y"], f"{path_label} segment endpoint y"),
            )
            point = raw_points[index + 1]
            if not isinstance(point, dict) or not {"x", "y"}.issubset(point):
                raise BoardJSONImportError(
                    f"{path_label} point {index + 2} must be an object with x and y"
                )
            point_xy = (
                _finite_number(point["x"], f"{path_label} x coordinate"),
                _finite_number(point["y"], f"{path_label} y coordinate"),
            )
            if endpoint_point != point_xy:
                raise BoardJSONImportError(
                    f"{path_label} segment {index + 1} endpoint "
                    "does not match geometry points"
                )
        return _profile_contour_points(
            path_geometry,
            label=path_label,
            tolerance=tolerance,
            curve_segments_per_circle=curve_segments_per_circle,
        )

    if "paths" not in geometry:
        if subpath_count != 1:
            raise BoardJSONImportError(
                f"{label} has {subpath_count} subpaths but no explicit geometry.paths"
            )
        outer_points = read_path(geometry, label)
        polygons = [Polygon(outer_points)]
    else:
        paths = geometry["paths"]
        if not isinstance(paths, list) or not paths:
            raise BoardJSONImportError(f"{label} geometry.paths must be a non-empty list")
        if subpath_count != len(paths):
            raise BoardJSONImportError(
                f"{label} geometry.subpath_count does not match geometry.paths"
            )
        exterior_points: list[list[tuple[float, float]]] = []
        cutout_points: list[list[tuple[float, float]]] = []
        for index, path_geometry in enumerate(paths):
            path_label = f"{label} subpath {index + 1}"
            role = (
                str(path_geometry.get("role") or "").strip().casefold()
                if isinstance(path_geometry, dict)
                else ""
            )
            if not role and len(paths) == 1:
                role = "polygon"
            if role not in {"polygon", "cutout"}:
                raise BoardJSONImportError(
                    f"{path_label} has unsupported or missing role {role or '<missing>'!r}"
                )
            points = read_path(path_geometry, path_label)
            if role == "polygon":
                exterior_points.append(points)
            else:
                cutout_points.append(points)
        if not exterior_points:
            raise BoardJSONImportError(
                f"{label} has no outer polygon paths"
            )
        exterior_polygons = [Polygon(points) for points in exterior_points]
        cutout_polygons = [Polygon(points) for points in cutout_points]
        if any(
            polygon.is_empty or not polygon.is_valid or polygon.area <= 0
            for polygon in exterior_polygons + cutout_polygons
        ):
            raise BoardJSONImportError(
                f"{label} contour paths must form valid, non-zero-area polygons"
            )
        if len(exterior_polygons) > 1 and not MultiPolygon(exterior_polygons).is_valid:
            raise BoardJSONImportError(
                f"{label} outer polygon paths must be disjoint"
            )

        clipped_exteriors = list(exterior_polygons)
        for cutout_polygon in cutout_polygons:
            containers = [
                index
                for index, exterior_polygon in enumerate(exterior_polygons)
                if exterior_polygon.covers(cutout_polygon)
            ]
            if len(containers) != 1:
                raise BoardJSONImportError(
                    f"{label} cutout path must be contained by exactly one outer polygon path"
                )
            exterior_index = containers[0]
            clipped_exteriors[exterior_index] = clipped_exteriors[
                exterior_index
            ].difference(cutout_polygon)

        polygons = []
        for exterior in clipped_exteriors:
            if exterior.geom_type == "Polygon":
                polygons.append(exterior)
            elif exterior.geom_type == "MultiPolygon":
                polygons.extend(exterior.geoms)
            else:
                raise BoardJSONImportError(
                    f"{label} cutout subtraction produced unsupported "
                    f"{exterior.geom_type} geometry"
                )

    if not polygons or any(
        polygon.is_empty or not polygon.is_valid or polygon.area <= 0
        for polygon in polygons
    ):
        raise BoardJSONImportError(
            f"{label} contour paths must form valid, non-zero-area polygons"
        )
    if len(polygons) > 1 and not MultiPolygon(polygons).is_valid:
        raise BoardJSONImportError(
            f"{label} outer polygon paths must be disjoint"
        )

    rendered: list[list[tuple[float, float]]] = []
    for polygon in polygons:
        if polygon.interiors:
            rendered.extend(_triangulate_polygon_with_holes(polygon, label=label))
        else:
            rendered.append(
                [(float(x), float(y)) for x, y in list(polygon.exterior.coords)[:-1]]
            )
    return rendered


def _stackup_layer_role(
    stackup_layer: dict[str, Any],
    board_layer: dict[str, Any] | None,
) -> str:
    conductor_functions = {
        "signal", "power", "ground", "mixed", "plane", "conductor", "conductive",
    }
    dielectric_functions = {
        "dielectric", "insulator", "substrate", "prepreg", "core",
        "dielcore", "dielprepreg", "dielectriccore", "dielectricprepreg",
    }
    stackup_function = str(stackup_layer.get("function") or "").strip().casefold()
    board_function = str((board_layer or {}).get("function") or "").strip().casefold()
    function = stackup_function or board_function
    material = str(stackup_layer.get("material") or "").strip().casefold()
    if function == "surface" and material in {"", "air"}:
        return "reference"
    copper_material = "copper" in material or material in {"cu", "cu foil"}
    if function in conductor_functions or copper_material:
        return "conductor"
    if function in dielectric_functions or material:
        return "dielectric"
    return "unknown"


def _is_non_conductor_feature_layer(
    board_layer: dict[str, Any] | None,
) -> bool:
    if board_layer is None:
        return False
    function = str(board_layer.get("function") or "").strip().casefold()
    return function in {
        "assembly",
        "board_outline",
        "dielectric",
        "dielcore",
        "dielprepreg",
        "document",
        "documentation",
        "drill",
        "pastemask",
        "silkscreen",
        "soldermask",
    }

def _constrained_triangles(
    polygon: Any,
) -> list[list[tuple[float, float]]] | None:
    """Triangulate a polygon with holes using Shapely's constrained Delaunay.

    Returns ``None`` when the installed Shapely lacks the function or when the
    result does not cover the polygon, so callers can use another triangulator.
    """
    try:
        import shapely
    except ImportError:
        return None
    constrained_triangulate = getattr(shapely, "constrained_delaunay_triangles", None)
    if constrained_triangulate is None:
        return None
    try:
        pieces = list(constrained_triangulate(polygon).geoms)
    except Exception:
        return None
    triangles: list[list[tuple[float, float]]] = []
    triangulated_area = 0.0
    try:
        from shapely import prepared
    except ImportError:
        return None
    allowed = prepared.prep(polygon.buffer(1e-12 * max(1.0, abs(polygon.area))))
    for piece in pieces:
        ring = [(float(x), float(y)) for x, y in list(piece.exterior.coords)[:-1]]
        if len(ring) != 3 or piece.area <= 1e-18:
            continue
        if not allowed.covers(piece):
            return None
        triangles.append(ring)
        triangulated_area += piece.area
    if not triangles or not math.isclose(
        triangulated_area,
        polygon.area,
        rel_tol=1e-6,
        abs_tol=max(1e-12, polygon.area * 1e-8),
    ):
        return None
    return triangles


def _triangulate_polygon_with_holes(
    polygon: Any,
    *,
    label: str,
) -> list[list[tuple[float, float]]]:
    constrained = _constrained_triangles(polygon)
    if constrained is not None:
        return constrained

    contour_points = vtk.vtkPoints()
    contour_lines = vtk.vtkCellArray()
    rings = [polygon.exterior, *polygon.interiors]
    for ring in rings:
        ids = [
            contour_points.InsertNextPoint(float(x), float(y), 0.0)
            for x, y in ring.coords
        ]
        contour = vtk.vtkPolyLine()
        contour.GetPointIds().SetNumberOfIds(len(ids))
        for index, point_id in enumerate(ids):
            contour.GetPointIds().SetId(index, point_id)
        contour_lines.InsertNextCell(contour)

    contours = vtk.vtkPolyData()
    contours.SetPoints(contour_points)
    contours.SetLines(contour_lines)
    triangulator = vtk.vtkContourTriangulator()
    triangulator.SetInputData(contours)
    triangulator.Update()
    triangulated = triangulator.GetOutput()
    triangles = []
    triangulated_area = 0.0
    for index in range(triangulated.GetNumberOfCells()):
        cell = triangulated.GetCell(index)
        coords = [
            tuple(float(value) for value in cell.GetPoints().GetPoint(point_index)[:2])
            for point_index in range(cell.GetNumberOfPoints())
        ]
        if len(coords) < 3:
            continue
        for fan_index in range(1, len(coords) - 1):
            triangle = [coords[0], coords[fan_index], coords[fan_index + 1]]
            area2 = (
                triangle[0][0] * triangle[1][1]
                - triangle[1][0] * triangle[0][1]
                + triangle[1][0] * triangle[2][1]
                - triangle[2][0] * triangle[1][1]
                + triangle[2][0] * triangle[0][1]
                - triangle[0][0] * triangle[2][1]
            )
            if abs(area2) <= 1e-18:
                continue
            if area2 < 0:
                triangle.reverse()
            triangulated_area += abs(area2) / 2
            triangles.append(triangle)
    if not triangles or not math.isclose(
        triangulated_area,
        polygon.area,
        rel_tol=1e-6,
        abs_tol=max(1e-12, polygon.area * 1e-8),
    ):
        raise BoardJSONImportError(
            f"{label} could not be triangulated completely for VTK preview"
        )
    return triangles


def _extrude_polygon_with_holes(
    polygon: Any,
    *,
    z_bottom: float,
    z_top: float,
    scale: float,
) -> vtk.vtkPolyData:
    if not math.isfinite(z_bottom) or not math.isfinite(z_top) or z_top <= z_bottom:
        raise BoardJSONImportError("Dielectric extrusion needs finite positive height")
    rings = [polygon.exterior, *polygon.interiors]
    triangles = _triangulate_polygon_with_holes(
        polygon,
        label="Board profile",
    )

    points = vtk.vtkPoints()
    cells = vtk.vtkCellArray()
    point_ids: dict[tuple[float, float, float], int] = {}

    def add_face(vertices: list[tuple[float, float, float]]) -> None:
        cells.InsertNextCell(len(vertices))
        for x, y, z in vertices:
            vertex = (x * scale, y * scale, z)
            point_id = point_ids.get(vertex)
            if point_id is None:
                point_id = points.InsertNextPoint(*vertex)
                point_ids[vertex] = point_id
            cells.InsertCellPoint(point_id)

    for triangle in triangles:
        add_face([(x, y, z_bottom) for x, y in reversed(triangle)])
        add_face([(x, y, z_top) for x, y in triangle])

    for ring in rings:
        coords = [(float(x), float(y)) for x, y in list(ring.coords)]
        for start, end in zip(coords, coords[1:]):
            add_face([
                (start[0], start[1], z_bottom),
                (end[0], end[1], z_bottom),
                (end[0], end[1], z_top),
                (start[0], start[1], z_top),
            ])

    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)
    return polydata


def _track_polygons(
    kind: str,
    geometry: Any,
    label: str,
    *,
    tolerance: float,
    curve_segments_per_circle: int | None = None,
) -> list[list[tuple[float, float]]]:
    if (
        kind == "polyline"
        and isinstance(geometry, dict)
        and not geometry.get("points")
        and not geometry.get("segments")
        and isinstance(geometry.get("paths"), list)
        and geometry["paths"]
    ):
        # An AOI clip can split one track into several disjoint visible runs.
        paths = geometry["paths"]
        subpath_count = geometry.get("subpath_count")
        if subpath_count is not None and (
            type(subpath_count) is not int or subpath_count != len(paths)
        ):
            raise BoardJSONImportError(
                f"{label} geometry.subpath_count does not match geometry.paths"
            )
        polygons: list[list[tuple[float, float]]] = []
        for index, path in enumerate(paths):
            path_label = f"{label} polyline path {index + 1}"
            if not isinstance(path, dict):
                raise BoardJSONImportError(f"{path_label} must be an object")
            role = str(path.get("role") or "polyline").strip().casefold()
            if role != "polyline" or path.get("closed"):
                raise BoardJSONImportError(
                    f"{path_label} must be an open polyline path"
                )
            polygons.extend(_single_track_polygons(
                kind,
                {
                    **geometry,
                    "points": path.get("points"),
                    "segments": path.get("segments"),
                    "closed": False,
                },
                path_label,
                tolerance=tolerance,
                curve_segments_per_circle=curve_segments_per_circle,
            ))
        return polygons
    return _single_track_polygons(
        kind,
        geometry,
        label,
        tolerance=tolerance,
        curve_segments_per_circle=curve_segments_per_circle,
    )


def _single_track_polygons(
    kind: str,
    geometry: Any,
    label: str,
    *,
    tolerance: float,
    curve_segments_per_circle: int | None = None,
) -> list[list[tuple[float, float]]]:
    if not isinstance(geometry, dict):
        raise BoardJSONImportError(f"{label} geometry must be an object")
    width = geometry.get("width")
    if width is None:
        raise BoardJSONImportError(
            f"{label} has no explicit width; zero-width centerlines are not rendered"
        )
    width = _finite_number(width, f"{label} width")
    if width <= 0:
        raise BoardJSONImportError(f"{label} width must be positive")

    if kind == "line":
        raw_points = [geometry.get("start"), geometry.get("end")]
    else:
        raw_points = geometry.get("points")
        if not isinstance(raw_points, list) or len(raw_points) < 2:
            raise BoardJSONImportError(f"{label} must have at least two path points")

    points = []
    for index, point in enumerate(raw_points):
        if not isinstance(point, dict) or "x" not in point or "y" not in point:
            raise BoardJSONImportError(
                f"{label} point {index + 1} must be an object with x and y"
            )
        points.append((
            _finite_number(point["x"], f"{label} x coordinate"),
            _finite_number(point["y"], f"{label} y coordinate"),
        ))
    if len(set(points)) < 2:
        raise BoardJSONImportError(f"{label} centerline has zero length")
    if kind == "polyline":
        _validate_segment_endpoints(geometry, points, label)
        points = _profile_contour_points(
            geometry,
            label=label,
            tolerance=tolerance,
            closed=False,
            curve_segments_per_circle=curve_segments_per_circle,
        )
    if any(points[index] == points[index + 1] for index in range(len(points) - 1)):
        raise BoardJSONImportError(f"{label} has a zero-length centerline segment")

    try:
        import shapely
        from shapely.geometry import LineString
        from shapely.ops import triangulate
    except ImportError as exc:
        raise BoardJSONImportError(
            "Finite-width track preview requires the installed Shapely geometry library"
        ) from exc

    centerline = LineString(points)
    buffered = centerline.buffer(
        width * 0.5,
        _circle_quadrant_segments(curve_segments_per_circle, 8),
        cap_style=1,
        join_style=1,
    )
    if buffered.is_empty or not buffered.is_valid:
        raise BoardJSONImportError(f"{label} width buffer produced invalid geometry")
    parts = list(buffered.geoms) if buffered.geom_type == "MultiPolygon" else [buffered]
    polygons: list[list[tuple[float, float]]] = []
    for part in parts:
        if part.geom_type != "Polygon":
            raise BoardJSONImportError(
                f"{label} width buffer produced unsupported {part.geom_type} geometry"
            )
        if part.interiors or not part.equals(part.convex_hull):
            # VTK's extrusion cap treats a concave polygon cell as convex.
            # Constrained triangulation preserves the buffer's narrow boundary edges.
            constrained_triangulate = getattr(
                shapely, "constrained_delaunay_triangles", None
            )
            if callable(constrained_triangulate):
                triangles = list(constrained_triangulate(part).geoms)
            else:
                triangles = [
                    triangle
                    for triangle in triangulate(part)
                    if part.covers(triangle)
                ]
            triangulated_area = sum(triangle.area for triangle in triangles)
            if not triangles or not math.isclose(
                triangulated_area,
                part.area,
                rel_tol=1e-6,
                abs_tol=max(1e-12, part.area * 1e-8),
            ):
                raise BoardJSONImportError(
                    f"{label} width buffer could not be triangulated completely"
                )
            for triangle in triangles:
                ring = [(float(x), float(y)) for x, y in list(triangle.exterior.coords)[:-1]]
                if len(ring) == 3:
                    polygons.append(ring)
        else:
            polygons.append(
                [(float(x), float(y)) for x, y in list(part.exterior.coords)[:-1]]
            )
    if not polygons:
        raise BoardJSONImportError(f"{label} width buffer produced no visible area")
    return polygons


def _validate_segment_endpoints(
    geometry: dict[str, Any],
    points: list[tuple[float, float]],
    label: str,
) -> None:
    segments = geometry.get("segments")
    if not isinstance(segments, list) or len(segments) != len(points) - 1:
        raise BoardJSONImportError(
            f"{label} geometry.segments must describe every path edge"
        )
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise BoardJSONImportError(
                f"{label} segment {index + 1} must be an object"
            )
        endpoint = segment.get("to")
        if not isinstance(endpoint, dict) or not {"x", "y"}.issubset(endpoint):
            raise BoardJSONImportError(
                f"{label} segment {index + 1} must have an x/y 'to' point"
            )
        endpoint_point = (
            _finite_number(endpoint["x"], f"{label} segment endpoint x"),
            _finite_number(endpoint["y"], f"{label} segment endpoint y"),
        )
        if endpoint_point != points[index + 1]:
            raise BoardJSONImportError(
                f"{label} segment {index + 1} endpoint does not match geometry points"
            )


def _polygons_to_polydata(
    polygons: list[list[tuple[float, float]]],
    *,
    z: float,
    scale: float,
) -> vtk.vtkPolyData:
    points = vtk.vtkPoints()
    cells = vtk.vtkCellArray()
    for polygon in polygons:
        cells.InsertNextCell(len(polygon))
        for x, y in polygon:
            point_id = points.InsertNextPoint(x * scale, y * scale, z)
            cells.InsertCellPoint(point_id)
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)
    return polydata


def _extrude_polydata(
    base_polydata: vtk.vtkPolyData,
    *,
    height: float,
) -> vtk.vtkPolyData:
    """Extrude polygon cells without walls on shared internal edges."""
    if not math.isfinite(height) or height <= 0:
        raise BoardJSONImportError("Conductor thickness must be finite and positive")

    points = vtk.vtkPoints()
    faces = vtk.vtkCellArray()
    point_ids: dict[tuple[float, float, float], int] = {}

    def add_face(vertices: list[tuple[float, float, float]]) -> None:
        faces.InsertNextCell(len(vertices))
        for vertex in vertices:
            point_id = point_ids.get(vertex)
            if point_id is None:
                point_id = points.InsertNextPoint(*vertex)
                point_ids[vertex] = point_id
            faces.InsertCellPoint(point_id)

    boundary_edges: dict[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        tuple[tuple[float, float, float], tuple[float, float, float], int],
    ] = {}
    for cell_index in range(base_polydata.GetNumberOfPolys()):
        cell = base_polydata.GetCell(cell_index)
        vertices = [
            tuple(float(value) for value in cell.GetPoints().GetPoint(point_index))
            for point_index in range(cell.GetNumberOfPoints())
        ]
        if len(vertices) < 3:
            continue

        bottom = [vertices[0], *reversed(vertices[1:])]
        top = [(x, y, z + height) for x, y, z in vertices]
        add_face(bottom)
        add_face(top)

        for start, end in zip(vertices, vertices[1:] + vertices[:1]):
            if start == end:
                continue
            edge_key = (start, end) if start < end else (end, start)
            previous = boundary_edges.get(edge_key)
            if previous is None:
                boundary_edges[edge_key] = (start, end, 1)
            else:
                boundary_edges[edge_key] = (
                    previous[0],
                    previous[1],
                    previous[2] + 1,
                )

    for start, end, count in boundary_edges.values():
        if count != 1:
            continue
        start_top = (start[0], start[1], start[2] + height)
        end_top = (end[0], end[1], end[2] + height)
        add_face([start, end, end_top, start_top])

    output = vtk.vtkPolyData()
    output.SetPoints(points)
    output.SetPolys(faces)
    return output


def _safe_name(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )
    return safe.strip("_") or "unnamed"
