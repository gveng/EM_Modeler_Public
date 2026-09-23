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
from typing import List, Optional, Tuple

import vtk

UV   = Tuple[float, float]
Vec3 = Tuple[float, float, float]

ORANGE = (1.0, 0.5, 0.0)
YELLOW = (1.0, 0.85, 0.0)

ARC_SEGMENTS    = 32
CIRCLE_SEGMENTS = 64


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


def _rect_polyline(p1: UV, p2: UV) -> List[UV]:
    x1, y1 = p1
    x2, y2 = p2
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]


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
        self.tool: Optional[str] = None
        self.pending: List[UV] = []          # in-progress click points
        self.preview_uv: Optional[UV] = None  # last cursor (u,v) for preview

    # ── tool / state management ───────────────────────────────────────
    def set_tool(self, name: Optional[str]) -> None:
        self.tool = name
        self.pending = []
        self.preview_uv = None

    def cancel_current(self) -> None:
        self.pending = []
        self.preview_uv = None

    def delete_last(self) -> None:
        if self.entities:
            self.entities.pop()

    def close_profile(self) -> None:
        """Close the active polyline by appending its first point."""
        if self.entities and self.entities[-1][0] == "polyline":
            pts = list(self.entities[-1][1])
            if len(pts) >= 2 and pts[0] != pts[-1]:
                pts.append(pts[0])
                self.entities[-1] = ("polyline", pts)

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
                self.entities.append(("line", p1, p2))
                self.pending = []
        elif t == "polyline":
            if not self.pending:
                self.pending = [uv]
                self.entities.append(("polyline", [uv]))
            else:
                pts = list(self.entities[-1][1])
                pts.append(uv)
                self.entities[-1] = ("polyline", pts)
                self.pending.append(uv)
        elif t == "arc":
            self.pending.append(uv)
            if len(self.pending) == 3:
                p1, p2, pm = self.pending
                self.entities.append(("arc", p1, p2, pm))
                self.pending = []
        elif t == "circle":
            self.pending.append(uv)
            if len(self.pending) == 2:
                c, edge = self.pending
                r = _dist(c, edge)
                self.entities.append(("circle", c, r))
                self.pending = []
        elif t == "rect":
            self.pending.append(uv)
            if len(self.pending) == 2:
                p1, p2 = self.pending
                self.entities.append(("rect", p1, p2))
                self.pending = []

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
        self.entities[-1] = ("polyline", new_pts)
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

    # ── profile linearisation ─────────────────────────────────────────
    def build_profile(self) -> List[UV]:
        out: List[UV] = []
        for ent in self.entities:
            tag = ent[0]
            if tag == "line":
                seg = [ent[1], ent[2]]
            elif tag == "polyline":
                seg = list(ent[1])
            elif tag == "arc":
                seg = _arc_polyline(ent[1], ent[2], ent[3])
            elif tag == "circle":
                seg = _circle_polyline(ent[1], ent[2])
            elif tag == "rect":
                seg = _rect_polyline(ent[1], ent[2])
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

        for ent in self.entities:
            tag = ent[0]
            if tag == "line":
                seg = [ent[1], ent[2]]
                is_hi = highlight is not None and (
                    (ent[1], ent[2]) == highlight or (ent[2], ent[1]) == highlight)
            elif tag == "polyline":
                seg = list(ent[1])
                is_hi = False
            elif tag == "arc":
                seg = _arc_polyline(ent[1], ent[2], ent[3])
                is_hi = False
            elif tag == "circle":
                seg = _circle_polyline(ent[1], ent[2])
                is_hi = False
            elif tag == "rect":
                seg = _rect_polyline(ent[1], ent[2])
                is_hi = False
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
            seg = _rect_polyline(self.pending[0], cur)
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
