"""Parametric fuselage section and forbidden volumes.

The fuselage is a half cylinder (an arch over the x axis) triangulated as a
structured grid. A small, seeded jitter is applied to interior vertices so the
mesh is not perfectly regular (closer to a real CAD tessellation) and so that
shortest-path ties are broken deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import trimesh


@dataclass(frozen=True)
class FuselageParams:
    """Geometric and meshing parameters of the half-cylinder section."""

    radius: float = 2.0
    length: float = 6.0
    n_axial: int = 61  # vertices along x  -> ~0.10 m spacing
    n_circ: int = 64  # vertices along the arc -> ~0.10 m spacing
    jitter: float = 0.15  # fraction of the grid spacing
    seed: int = 42


def surface_point(x: float, theta_deg: float, radius: float) -> np.ndarray:
    """Return the 3D point on the cylinder at axial position x and angle theta."""
    t = np.deg2rad(theta_deg)
    return np.array([x, radius * np.cos(t), radius * np.sin(t)])


def build_fuselage(params: FuselageParams | None = None) -> trimesh.Trimesh:
    """Build the triangulated half-cylinder surface (theta in [0, pi])."""
    params = params or FuselageParams()
    xs = np.linspace(0.0, params.length, params.n_axial)
    ts = np.linspace(0.0, np.pi, params.n_circ)
    grid_x, grid_t = np.meshgrid(xs, ts, indexing="ij")

    rng = np.random.default_rng(params.seed)
    jx = rng.uniform(-1, 1, grid_x.shape) * params.jitter * (xs[1] - xs[0])
    jt = rng.uniform(-1, 1, grid_t.shape) * params.jitter * (ts[1] - ts[0])
    jx[[0, -1], :] = 0.0  # keep the open ends planar
    jt[:, [0, -1]] = 0.0  # keep the lower edges on z = 0
    grid_x = grid_x + jx
    grid_t = grid_t + jt

    vertices = np.column_stack(
        [
            grid_x.ravel(),
            params.radius * np.cos(grid_t.ravel()),
            params.radius * np.sin(grid_t.ravel()),
        ]
    )

    nc = params.n_circ
    gi, gj = np.meshgrid(np.arange(params.n_axial - 1), np.arange(nc - 1), indexing="ij")
    i, j = gi.ravel(), gj.ravel()
    v00, v10 = i * nc + j, (i + 1) * nc + j
    v01, v11 = v00 + 1, v10 + 1
    # Alternate the quad diagonal (checkerboard) so both diagonal directions exist.
    flip = (i + j) % 2 == 1
    faces_a = np.where(flip[:, None], np.column_stack([v00, v10, v01]), np.column_stack([v00, v10, v11]))
    faces_b = np.where(flip[:, None], np.column_stack([v10, v11, v01]), np.column_stack([v00, v11, v01]))
    faces = np.vstack([faces_a, faces_b])
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


@dataclass(frozen=True)
class BoxVolume:
    """Axis-aligned box keep-out volume."""

    name: str
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]
    prop: str = ""  # visual-only hint for the interactive viewer (e.g. "pipe", "panel")

    def contains(self, points: np.ndarray) -> np.ndarray:
        """Boolean mask of points strictly inside the box."""
        p = np.atleast_2d(points)
        return np.all((p > np.asarray(self.lo)) & (p < np.asarray(self.hi)), axis=1)

    def segments_intersect(self, p: np.ndarray, q: np.ndarray) -> np.ndarray:
        """For E segments (p[i], q[i]): True where the segment passes through the open box (slab method)."""
        lo, hi = np.asarray(self.lo), np.asarray(self.hi)
        d = q - p
        inside_slab = (p > lo) & (p < hi)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):  # overflow -> +-inf is the slab limit
            t1, t2 = (lo - p) / d, (hi - p) / d
        # Parallel to a slab: the whole segment is inside that slab or none of it is.
        t_enter = np.where(d == 0.0, np.where(inside_slab, -np.inf, np.inf), np.minimum(t1, t2))
        t_exit = np.where(d == 0.0, np.where(inside_slab, np.inf, -np.inf), np.maximum(t1, t2))
        return np.maximum(t_enter.max(axis=1), 0.0) < np.minimum(t_exit.min(axis=1), 1.0)

    def inflated(self, margin: float) -> BoxVolume:
        """Box grown by `margin` on every side (conservative clearance envelope)."""
        (x0, y0, z0), (x1, y1, z1) = self.lo, self.hi
        return replace(self, lo=(x0 - margin, y0 - margin, z0 - margin), hi=(x1 + margin, y1 + margin, z1 + margin))

    def to_mesh(self) -> trimesh.Trimesh:
        """Triangle mesh of the box, for visualisation."""
        lo, hi = np.asarray(self.lo), np.asarray(self.hi)
        return trimesh.creation.box(bounds=np.vstack([lo, hi]))


@dataclass(frozen=True)
class SphereVolume:
    """Spherical keep-out volume."""

    name: str
    center: tuple[float, float, float]
    radius: float
    prop: str = ""  # visual-only hint for the interactive viewer (e.g. "actuator")

    def contains(self, points: np.ndarray) -> np.ndarray:
        """Boolean mask of points strictly inside the sphere."""
        p = np.atleast_2d(points)
        return np.linalg.norm(p - np.asarray(self.center), axis=1) < self.radius

    def segments_intersect(self, p: np.ndarray, q: np.ndarray) -> np.ndarray:
        """For E segments (p[i], q[i]): True where the segment comes closer to the centre than the radius."""
        c, d = np.asarray(self.center), q - p
        dd = np.einsum("ij,ij->i", d, d)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            t = np.where(dd > 0.0, np.einsum("ij,ij->i", c - p, d) / dd, 0.0)
        closest = p + np.clip(t, 0.0, 1.0)[:, None] * d
        return np.linalg.norm(closest - c, axis=1) < self.radius

    def inflated(self, margin: float) -> SphereVolume:
        """Sphere grown by `margin` (exact clearance envelope)."""
        return replace(self, radius=self.radius + margin)

    def to_mesh(self) -> trimesh.Trimesh:
        """Triangle mesh of the sphere, for visualisation."""
        m = trimesh.creation.icosphere(subdivisions=3, radius=self.radius)
        m.apply_translation(self.center)
        return m


ForbiddenVolume = BoxVolume | SphereVolume


def mark_forbidden_vertices(vertices: np.ndarray, volumes: list[ForbiddenVolume]) -> np.ndarray:
    """Boolean mask of mesh vertices lying inside any forbidden volume."""
    mask = np.zeros(len(vertices), dtype=bool)
    for vol in volumes:
        mask |= vol.contains(vertices)
    return mask
