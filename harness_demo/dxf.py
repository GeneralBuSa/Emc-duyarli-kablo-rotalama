"""Developed (unrolled) view of the harness as a DXF drawing for CAD and formboard work.

A cylinder is a developable surface: unrolling it onto a plane, (x, theta) -> (x, R * theta),
preserves every length on the skin. Routes run on the skin, so the drawing shows each cable at its
true length, which is what a formboard or a nailboard layout needs. Drawn in millimetres:

  layer OUTLINE    the developed section (0..L along x, 0..pi R around the arc)
  layer KEEPOUT    footprint of every keep-out volume on the skin (convex outline)
  layer POWER, SIGNAL, DATA   one polyline per cable of that EMC class
  layer TERMINALS  a small circle at every terminal
  layer LABELS     the cable name at its start terminal

The file is DXF R12 (AC1009), the most widely readable version. Non-ASCII characters of names are
written as \\U+XXXX escapes, which CAD programs decode.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from .checks import VolumeLike, _inside_volume
from .graph import RoutingGraph
from .scenarios import Cable

MM = 1000.0
LAYERS = {"OUTLINE": 8, "KEEPOUT": 30, "POWER": 1, "SIGNAL": 5, "DATA": 3, "TERMINALS": 7, "LABELS": 7}
TEXT_HEIGHT_MM = 25.0
TERMINAL_RADIUS_MM = 10.0


def develop(points: np.ndarray, radius: float) -> np.ndarray:
    """Skin points (x, y, z) -> developed plane (x, R * theta) [m], theta = atan2(z, y) in [0, pi]."""
    p = np.atleast_2d(points)
    return np.column_stack([p[:, 0], radius * np.arctan2(p[:, 2], p[:, 1])])


def _text(value: str) -> str:
    """A DXF R12 string: ASCII, non-ASCII as \\U+XXXX (names are already free of control characters)."""
    return "".join(ch if 32 <= ord(ch) < 127 else f"\\U+{ord(ch):04X}" for ch in value)


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def tag(self, code: int, value: object) -> None:
        if isinstance(value, float):
            value = f"{value:.4f}"
        self.lines += [str(code), str(value)]

    def polyline(self, layer: str, pts_mm: np.ndarray, closed: bool = False) -> None:
        self.tag(0, "POLYLINE")
        self.tag(8, layer)
        self.tag(66, 1)
        self.tag(70, 1 if closed else 0)
        for code in (10, 20, 30):
            self.tag(code, 0.0)
        for x, y in pts_mm:
            self.tag(0, "VERTEX")
            self.tag(8, layer)
            self.tag(10, float(x))
            self.tag(20, float(y))
            self.tag(30, 0.0)
        self.tag(0, "SEQEND")
        self.tag(8, layer)

    def circle(self, layer: str, center_mm: np.ndarray, r: float) -> None:
        self.tag(0, "CIRCLE")
        self.tag(8, layer)
        self.tag(10, float(center_mm[0]))
        self.tag(20, float(center_mm[1]))
        self.tag(30, 0.0)
        self.tag(40, r)

    def text(self, layer: str, at_mm: np.ndarray, value: str) -> None:
        self.tag(0, "TEXT")
        self.tag(8, layer)
        self.tag(10, float(at_mm[0]))
        self.tag(20, float(at_mm[1]))
        self.tag(30, 0.0)
        self.tag(40, TEXT_HEIGHT_MM)
        self.tag(1, _text(value))


def keepout_footprint(volume: VolumeLike, radius: float, length: float, step: float = 0.01) -> np.ndarray | None:
    """Convex outline [m, developed plane] of the skin points inside the volume (None if it misses the skin)."""
    xs = np.arange(0.0, length + step / 2, step)
    ts = np.arange(0.0, np.pi + step / (2 * radius), step / radius)
    xx, tt = np.meshgrid(xs, ts)
    skin = np.column_stack([xx.ravel(), radius * np.cos(tt.ravel()), radius * np.sin(tt.ravel())])
    inside = develop(skin[_inside_volume(skin, volume)], radius)
    if len(inside) < 3:
        return None
    try:
        hull = ConvexHull(inside)
    except QhullError:  # all points on one line
        return None
    return inside[hull.vertices]


def export_developed_dxf(
    graph: RoutingGraph,
    cables: Sequence[Cable],
    routes: dict[str, list[int]],
    radius: float,
    length: float,
    volumes: Sequence[VolumeLike],
    dxf_path: Path,
) -> None:
    """Write the developed view of `routes` (one method) as DXF R12 in millimetres."""
    w = _Writer()
    for code, value in ((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1009"), (9, "$INSBASE"),
                        (10, 0.0), (20, 0.0), (30, 0.0), (0, "ENDSEC")):
        w.tag(code, value)
    w.tag(0, "SECTION")
    w.tag(2, "TABLES")
    for code, value in ((0, "TABLE"), (2, "LTYPE"), (70, 1), (0, "LTYPE"), (2, "CONTINUOUS"), (70, 0),
                        (3, "Solid line"), (72, 65), (73, 0), (40, 0.0), (0, "ENDTAB")):
        w.tag(code, value)
    w.tag(0, "TABLE")
    w.tag(2, "LAYER")
    w.tag(70, len(LAYERS))
    for name, color in LAYERS.items():
        for code, value in ((0, "LAYER"), (2, name), (70, 0), (62, color), (6, "CONTINUOUS")):
            w.tag(code, value)
    w.tag(0, "ENDTAB")
    w.tag(0, "ENDSEC")

    w.tag(0, "SECTION")
    w.tag(2, "ENTITIES")
    arc = np.pi * radius
    w.polyline("OUTLINE", MM * np.array([[0.0, 0.0], [length, 0.0], [length, arc], [0.0, arc]]), closed=True)
    for vol in volumes:
        outline = keepout_footprint(vol, radius, length)
        if outline is not None:
            w.polyline("KEEPOUT", MM * outline, closed=True)
    pos = graph.positions
    for c in cables:
        pts = MM * develop(pos[routes[c.name]], radius)
        w.polyline(c.emc_class.upper() if c.emc_class.upper() in LAYERS else "OUTLINE", pts)
        for end in (pts[0], pts[-1]):
            w.circle("TERMINALS", end, TERMINAL_RADIUS_MM)
        w.text("LABELS", pts[0] + np.array([TERMINAL_RADIUS_MM, TERMINAL_RADIUS_MM]), c.name)
    w.tag(0, "ENDSEC")
    w.tag(0, "EOF")
    dxf_path.parent.mkdir(parents=True, exist_ok=True)
    dxf_path.write_text("\n".join(w.lines) + "\n", encoding="ascii", newline="\n")
