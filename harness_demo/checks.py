"""Independent constraint checks.

These functions deliberately do NOT use the routing code or the routing graph:
they take plain node paths plus vertex coordinates and recompute everything
from geometry, so they can catch mistakes in the routing logic.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Protocol, runtime_checkable

import numpy as np
from scipy.spatial import cKDTree

SAMPLE_STEP_M = 0.05  # resampling step for distance-based checks
# Coupling zone of two foreign routes: closer than this multiple of their required separation.
# Crosstalk grows with the length two routes run side by side, not only with the minimum gap.
COUPLING_FACTOR = 2.0


@runtime_checkable
class SphereLike(Protocol):
    """Any sphere-shaped keep-out volume (only its raw parameters are used)."""

    @property
    def center(self) -> tuple[float, float, float]: ...

    @property
    def radius(self) -> float: ...


@runtime_checkable
class BoxLike(Protocol):
    """Any axis-aligned box keep-out volume (only its raw parameters are used)."""

    @property
    def lo(self) -> tuple[float, float, float]: ...

    @property
    def hi(self) -> tuple[float, float, float]: ...


VolumeLike = SphereLike | BoxLike


@dataclass(frozen=True)
class CheckSettings:
    """Parameters of the independent checks (shared by the metrics, the benchmark and the exports)."""

    volumes: Sequence[VolumeLike]
    separation_fn: Callable[[str, str], float]
    min_bend_radius: float
    capacity: int
    clearance: float = 0.0
    bundle_area: float | None = None  # area mode: max total cable cross-section per edge [m^2]
    area_fn: Callable[[str], float] | None = None  # cross-section of one cable by EMC class [m^2]
    # Bundle bend rule: a bend needs radius >= bundle_bend_factor x bundle diameter (0 = off).
    bundle_bend_factor: float = 0.0
    bundle_diameter_factor: float = 1.25  # bundle diameter = factor x sqrt(sum of squared cable diameters)
    diameter_fn: Callable[[str], float] | None = None  # outer diameter of one cable by EMC class [m]

    @property
    def capacity_limit(self) -> float:
        """Capacity the routers and the check use: the bundle area in area mode, else the cable count."""
        return self.bundle_area if self.bundle_area is not None else self.capacity

    @property
    def load_fn(self) -> Callable[[str], float] | None:
        """Per-cable edge load in area mode (None = one per cable)."""
        return self.area_fn if self.bundle_area is not None else None

    def required_radii(self, node_paths: Sequence[Sequence[int]], classes: Sequence[str]) -> list[np.ndarray] | None:
        """Required bend radius at every interior vertex of every path (None when the bundle rule is off)."""
        if self.bundle_bend_factor <= 0.0 or self.diameter_fn is None:
            return None
        return required_bend_radii(node_paths, [self.diameter_fn(c) for c in classes], self.min_bend_radius,
                                   self.bundle_bend_factor, self.bundle_diameter_factor)

    def check(self, node_paths: Sequence[Sequence[int]], classes: Sequence[str], positions: np.ndarray) -> CheckReport:
        """All five checks with these settings."""
        loads = None if self.load_fn is None else [self.load_fn(c) for c in classes]
        return run_all_checks(node_paths, classes, positions, self.volumes, self.separation_fn,
                              self.min_bend_radius, self.capacity_limit, self.clearance, loads,
                              self.required_radii(node_paths, classes))


@dataclass
class CheckReport:
    """Violation counts for one routing solution."""

    emc_points: int  # sampled route points closer than the required separation
    bend_points: int  # route vertices whose local bend radius is below the limit
    forbidden_points: int  # sampled route points inside a forbidden volume
    clearance_points: int  # sampled route points outside a volume but closer than the clearance
    capacity_edges: int  # edges carrying more than K cables

    def as_dict(self) -> dict[str, int]:
        """Plain dict view."""
        return asdict(self)


def resample_polyline(points: np.ndarray, step: float = SAMPLE_STEP_M) -> np.ndarray:
    """Return points along the polyline with spacing <= step (vertices included).

    Segment k is split into n_k = ceil(length / step) parts at t = i / n_k, i = 1..n_k
    (vectorised over all segments).

    Deliberately not shared with the router's `routing.densify`: the checks must stay
    independent of the code they verify, so they carry their own copy of this primitive.
    """
    points = np.asarray(points, dtype=float)
    a, d = points[:-1], points[1:] - points[:-1]
    n = np.maximum(1, np.ceil(np.linalg.norm(d, axis=1) / step).astype(int))
    seg = np.repeat(np.arange(len(n)), n)
    i = np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n) + 1
    t = i / n[seg]
    return np.vstack([points[:1], a[seg] + d[seg] * t[:, None]])


def point_segment_distances(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise Euclidean distance from points p to segments [a, b] (degenerate segments allowed)."""
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        t = np.where(denom > 0.0, np.einsum("ij,ij->i", p - a, ab) / denom, 0.0)
    t = np.clip(t, 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def distances_to_polyline(
    points: np.ndarray, samples: np.ndarray, tree: cKDTree, step: float = SAMPLE_STEP_M,
    cutoff: float = np.inf,
) -> np.ndarray:
    """Distance from each point to the polyline through `samples` (spacing <= step), exact below `cutoff`.

    The nearest *sample* is at most step / 2 further away than the nearest point on the polyline,
    so only points with sample distance < cutoff + step / 2 are refined with exact point-to-segment
    distances; every other value is >= cutoff either way. Without a cutoff every value is exact.
    """
    d, _ = tree.query(points)
    if len(samples) < 2 or not len(points):
        return d
    refine = np.flatnonzero(d < cutoff + 0.5 * step)
    if not len(refine):
        return d
    near = tree.query_ball_point(points[refine], r=d[refine] + 0.5 * step + 1e-12)
    counts = np.array([len(n) for n in near])
    k = np.concatenate([np.asarray(n, dtype=int) for n in near])
    owner = np.repeat(refine, counts)
    last = len(samples) - 1
    # Both segments adjacent to every nearby sample: (k-1, k) and (k, k+1), clipped at the ends.
    seg_a = np.concatenate([np.maximum(k - 1, 0), k])
    seg_b = np.concatenate([k, np.minimum(k + 1, last)])
    owners = np.concatenate([owner, owner])
    exact = point_segment_distances(points[owners], samples[seg_a], samples[seg_b])
    out = d.copy()
    np.minimum.at(out, owners, exact)
    return out


def min_distance_to_polyline(
    points: np.ndarray, samples: np.ndarray, tree: cKDTree, step: float = SAMPLE_STEP_M
) -> float:
    """Exact minimum distance from a set of points to the polyline through `samples`.

    Only points whose sample distance is within step / 2 of the smallest one can hold the minimum,
    so only those are refined.
    """
    if not len(points):
        return float("inf")
    return float(distances_to_polyline(points, samples, tree, step, cutoff=float(tree.query(points)[0].min())).min())


def emc_violation_points(
    polylines: Sequence[np.ndarray],
    classes: Sequence[str],
    separation_fn: Callable[[str, str], float],
    step: float = SAMPLE_STEP_M,
) -> np.ndarray:
    """Sampled points closer to a route of another class than the required separation.

    Distances are measured exactly to the other route's segments (not only to its samples), so a
    violation is never missed between two samples of the other route. A point is listed once per
    offending route (a point near two foreign routes appears twice). Returns an (M, 3) array.
    """
    samples = [resample_polyline(p, step) for p in polylines]
    trees = [cKDTree(s) for s in samples]
    hits = [np.empty((0, 3))]
    for i, si in enumerate(samples):
        for j, tj in enumerate(trees):
            if i == j:
                continue
            sep = separation_fn(classes[i], classes[j])
            if sep <= 0.0:
                continue
            d = distances_to_polyline(si, samples[j], tj, step, cutoff=sep)
            hits.append(si[d < sep])
    return np.vstack(hits)


def count_emc_violations(
    polylines: Sequence[np.ndarray],
    classes: Sequence[str],
    separation_fn: Callable[[str, str], float],
    step: float = SAMPLE_STEP_M,
) -> int:
    """Number of EMC violation points (see `emc_violation_points`)."""
    return len(emc_violation_points(polylines, classes, separation_fn, step))


def circumradius(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Radius of the circle through three points (inf if collinear)."""
    ab, bc, ca = np.linalg.norm(b - a), np.linalg.norm(c - b), np.linalg.norm(a - c)
    area2 = np.linalg.norm(np.cross(b - a, c - a))  # = 2 * triangle area
    if area2 < 1e-12:
        return float("inf")
    return float(ab * bc * ca / (2.0 * area2))


def circumradii(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Row-wise `circumradius` for (M, 3) arrays (inf where the three points are collinear)."""
    ab = np.linalg.norm(b - a, axis=1)
    bc = np.linalg.norm(c - b, axis=1)
    ca = np.linalg.norm(a - c, axis=1)
    area2 = np.linalg.norm(np.cross(b - a, c - a), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = ab * bc * ca / (2.0 * area2)
    return np.where(area2 < 1e-12, np.inf, r)


def bundle_turn_diameters(
    node_paths: Sequence[Sequence[int]], diameters: Sequence[float], diameter_factor: float
) -> list[np.ndarray]:
    """Diameter of the bundle that bends at every interior vertex of every path.

    The cables that bend together at vertex v of a turn a-v-c are the ones whose paths contain that
    same turn (in either direction); their bundle has diameter factor x sqrt(sum of squared diameters).
    """
    def key(a: int, v: int, c: int) -> tuple[int, int, int]:
        return (v, min(a, c), max(a, c))

    sq: dict[tuple[int, int, int], float] = {}
    for path, d in zip(node_paths, diameters):
        for k in {key(*t) for t in zip(path, path[1:], path[2:])}:
            sq[k] = sq.get(k, 0.0) + d * d
    return [diameter_factor * np.sqrt(np.array([sq[key(*t)] for t in zip(path, path[1:], path[2:])], dtype=float))
            for path in node_paths]


def required_bend_radii(
    node_paths: Sequence[Sequence[int]], diameters: Sequence[float], min_radius: float,
    bend_factor: float, diameter_factor: float,
) -> list[np.ndarray]:
    """Required radius per interior vertex: max(min_radius, bend_factor x bundle diameter at that turn)."""
    return [np.maximum(min_radius, bend_factor * d)
            for d in bundle_turn_diameters(node_paths, diameters, diameter_factor)]


def bend_violations_per_path(
    polylines: Sequence[np.ndarray], min_radius: float, required: Sequence[np.ndarray] | None = None
) -> list[int]:
    """Interior vertices of each polyline whose circumradius is below the limit (per vertex with `required`)."""
    out = []
    for k, p in enumerate(polylines):
        if len(p) < 3:
            out.append(0)
            continue
        limit = min_radius if required is None else required[k]
        out.append(int(np.count_nonzero(circumradii(p[:-2], p[1:-1], p[2:]) < limit)))
    return out


def count_bend_violations(
    polylines: Sequence[np.ndarray], min_radius: float, required: Sequence[np.ndarray] | None = None
) -> int:
    """Count interior vertices whose three-point circumradius is below min_radius (or below `required`,
    the per-vertex limit of the bundle bend rule)."""
    return sum(bend_violations_per_path(polylines, min_radius, required))


def _inside_volume(points: np.ndarray, volume: VolumeLike) -> np.ndarray:
    """Point-in-volume test recomputed from the volume's raw parameters."""
    if isinstance(volume, SphereLike):
        return np.linalg.norm(points - np.asarray(volume.center), axis=1) < volume.radius
    lo, hi = np.asarray(volume.lo), np.asarray(volume.hi)
    return np.all((points > lo) & (points < hi), axis=1)


def distance_to_volume(points: np.ndarray, volume: VolumeLike) -> np.ndarray:
    """Euclidean distance from each point to the volume (0 inside), from raw parameters."""
    if isinstance(volume, SphereLike):
        return np.maximum(np.linalg.norm(points - np.asarray(volume.center), axis=1) - volume.radius, 0.0)
    lo, hi = np.asarray(volume.lo), np.asarray(volume.hi)
    return np.linalg.norm(np.maximum(np.maximum(lo - points, points - hi), 0.0), axis=1)


def count_forbidden_violations(
    polylines: Sequence[np.ndarray], volumes: Sequence[VolumeLike], step: float = SAMPLE_STEP_M
) -> int:
    """Count sampled route points inside any forbidden volume."""
    count = 0
    for p in polylines:
        s = resample_polyline(p, step)
        inside = np.zeros(len(s), dtype=bool)
        for vol in volumes:
            inside |= _inside_volume(s, vol)
        count += int(inside.sum())
    return count


def count_clearance_violations(
    polylines: Sequence[np.ndarray], volumes: Sequence[VolumeLike], clearance: float, step: float = SAMPLE_STEP_M
) -> int:
    """Count sampled route points outside every volume but closer than `clearance` to one of them."""
    if clearance <= 0.0:
        return 0
    count = 0
    for p in polylines:
        s = resample_polyline(p, step)
        inside = np.zeros(len(s), dtype=bool)
        near = np.zeros(len(s), dtype=bool)
        for vol in volumes:
            inside |= _inside_volume(s, vol)
            near |= distance_to_volume(s, vol) < clearance
        count += int((near & ~inside).sum())
    return count


def count_capacity_violations(
    node_paths: Sequence[Sequence[int]], capacity: float, loads: Sequence[float] | None = None
) -> int:
    """Count undirected edges whose load exceeds `capacity`.

    The load of an edge is the number of cables on it, or with `loads` the sum of their loads
    (e.g. cross-sections, with `capacity` the bundle area; compared with a 1e-9 relative tolerance).
    """
    load: dict[tuple[int, int], float] = {}
    for k, path in enumerate(node_paths):
        for e in {(min(a, b), max(a, b)) for a, b in pairwise(path)}:
            load[e] = load.get(e, 0) + (1 if loads is None else loads[k])
    limit = capacity if loads is None else capacity * (1.0 + 1e-9)
    return sum(1 for n in load.values() if n > limit)


def run_all_checks(
    node_paths: Sequence[Sequence[int]],
    classes: Sequence[str],
    positions: np.ndarray,
    volumes: Sequence[VolumeLike],
    separation_fn: Callable[[str, str], float],
    min_bend_radius: float,
    capacity: float,
    clearance: float = 0.0,
    loads: Sequence[float] | None = None,
    required_radii: Sequence[np.ndarray] | None = None,
) -> CheckReport:
    """Run all five checks on a set of node paths (`loads`: per-cable capacity load, default 1;
    `required_radii`: per-vertex bend limits of the bundle bend rule, default `min_bend_radius`)."""
    polylines = [positions[np.asarray(p)] for p in node_paths]
    return CheckReport(
        emc_points=count_emc_violations(polylines, classes, separation_fn),
        bend_points=count_bend_violations(polylines, min_bend_radius, required_radii),
        forbidden_points=count_forbidden_violations(polylines, volumes),
        clearance_points=count_clearance_violations(polylines, volumes, clearance),
        capacity_edges=count_capacity_violations(node_paths, capacity, loads),
    )
