"""Machine-readable exports: a wirelist (CSV) and the 3D route polylines (JSON).

wirelist.csv  One row per cable of the chosen method: wire id, both terminals (equipment, node,
              coordinates), routed length, bundle partners and the cable's own verification
              margins (EMC separation, bend radius, keep-out distance), recomputed from geometry
              with the independent checks, plus an OK/CHECK status and the length the cable runs
              close to (within COUPLING_FACTOR x the separation of) a route of another class.
routes.json   Every method's routes as 3D polylines through mesh vertices on the skin, with the
              run parameters and the check results. Deterministic (no timings) and compact (one
              array per line), so a re-run with the same inputs reproduces the file byte for byte.

The polylines are exactly what the checks evaluate; the lifted, smoothed cables of the
interactive page are visual only and are not exported.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from .checks import (
    COUPLING_FACTOR,
    CheckSettings,
    circumradii,
    distance_to_volume,
    distances_to_polyline,
    min_distance_to_polyline,
    resample_polyline,
)
from .graph import RoutingGraph
from .metrics import Row
from .routing import RoutingResult
from .scenarios import Cable

Record = dict[str, Any]  # one wirelist row

FORMAT = "emc-harness-routes/2"
__all__ = ["COUPLING_FACTOR", "WIRELIST_COLUMNS", "export_routes_json", "export_wirelist_csv",
           "generate_wirelist_records"]
WIRELIST_COLUMNS: tuple[str, ...] = (
    "wire_id", "cable_name", "emc_class",
    "from_location", "from_node", "from_x_m", "from_y_m", "from_z_m",
    "to_location", "to_node", "to_x_m", "to_y_m", "to_z_m",
    "length_m", "mass_kg", "waypoint_count", "bundled_with", "shared_length_m", "sharing_ratio",
    "min_emc_margin_m", "min_bend_radius_m", "bend_limit_m", "min_keepout_distance_m", "near_parallel_length_m",
    "status",
)


def _round(x: float | None, digits: int = 4) -> float | None:
    """Rounded float; None for missing or infinite values (empty cell / JSON null)."""
    return None if x is None or not np.isfinite(x) else round(float(x), digits)


def _bundle_partners(graph: RoutingGraph, cables: Sequence[Cable], routes: dict[str, list[int]]):
    """For every cable: the set of other cables sharing at least one edge, and the shared length."""
    users: dict[int, list[str]] = {}
    edges = {c.name: np.unique(graph.path_edge_ids(routes[c.name])) for c in cables}
    for name, eids in edges.items():
        for e in eids:
            users.setdefault(int(e), []).append(name)
    out = {}
    for name, eids in edges.items():
        shared = [int(e) for e in eids if len(users[int(e)]) > 1]
        partners = sorted({o for e in shared for o in users[e] if o != name})
        out[name] = (partners, float(graph.lengths[shared].sum()) if shared else 0.0)
    return out


def _margins(paths: dict[str, list[int]], positions: np.ndarray, classes: dict[str, str], settings: CheckSettings):
    """Per-cable verification margins recomputed with the independent check primitives.

    min_emc_margin_m       min over foreign-class routes of (distance - required separation); < 0 = violation
    min_bend_radius_m      smallest three-vertex circumradius along the route
    bend_limit_m           largest required bend radius along the route: the minimum bend radius, or with
                           the bundle bend rule the radius the bundle at that turn needs; a vertex whose
                           radius is below its own limit is a violation
    min_keepout_distance_m smallest distance to any keep-out volume; < clearance = violation
    near_parallel_length_m route length closer than COUPLING_FACTOR x separation to a foreign-class route
                           (informative, not a violation)

    Distances to other routes are exact (point to segment), like the EMC check.
    """
    names = list(paths)
    polylines = {n: positions[np.asarray(paths[n])] for n in names}
    required = settings.required_radii([paths[n] for n in names], [classes[n] for n in names])
    limits = dict(zip(names, required)) if required is not None else {}
    samples = {n: resample_polyline(p) for n, p in polylines.items()}
    trees = {n: cKDTree(s) for n, s in samples.items()}
    out = {}
    for name, pts in samples.items():
        emc = np.inf
        mids, seg_len = 0.5 * (pts[1:] + pts[:-1]), np.linalg.norm(np.diff(pts, axis=0), axis=1)
        coupled = np.zeros(len(mids), dtype=bool)
        for other, tree in trees.items():
            sep = settings.separation_fn(classes[name], classes[other]) if other != name else 0.0
            if sep > 0.0:
                emc = min(emc, min_distance_to_polyline(pts, samples[other], tree) - sep)
                zone = COUPLING_FACTOR * sep
                coupled |= distances_to_polyline(mids, samples[other], tree, cutoff=zone) < zone
        p = polylines[name]
        radii = circumradii(p[:-2], p[1:-1], p[2:]) if len(p) >= 3 else np.empty(0)
        limit = limits.get(name, np.full(len(radii), settings.min_bend_radius))
        bend = float(radii.min()) if len(radii) else np.inf
        bend_limit = float(limit.max()) if len(limit) else settings.min_bend_radius
        keepout = min((float(distance_to_volume(pts, v).min()) for v in settings.volumes), default=np.inf)
        ok = emc >= 0.0 and bool(np.all(radii >= limit)) and keepout >= settings.clearance
        out[name] = (emc, bend, bend_limit, keepout, float(seg_len[coupled].sum()), "OK" if ok else "CHECK")
    return out


def generate_wirelist_records(
    graph: RoutingGraph,
    cables: Sequence[Cable],
    routes: dict[str, list[int]],
    settings: CheckSettings | None = None,
    mass_fn: Callable[[str], float] | None = None,
) -> list[Record]:
    """One wirelist record per cable; verification columns are filled when `settings` is given,
    the mass when `mass_fn` (kg/m by EMC class) is given."""
    pos = graph.positions
    partners = _bundle_partners(graph, cables, routes)
    margins = {}
    if settings is not None:
        margins = _margins({c.name: routes[c.name] for c in cables}, pos, {c.name: c.emc_class for c in cables},
                           settings)
    records = []
    for k, c in enumerate(cables, start=1):
        path = routes[c.name]
        length = graph.path_length(path)
        mates, shared = partners[c.name]
        emc, bend, bend_limit, keepout, parallel, status = margins.get(c.name, (None, None, None, None, None, ""))
        records.append({
            "wire_id": f"W{k:02d}", "cable_name": c.name, "emc_class": c.emc_class,
            "from_location": c.from_location, "from_node": c.start,
            **dict(zip(("from_x_m", "from_y_m", "from_z_m"), (round(float(v), 4) for v in pos[c.start]))),
            "to_location": c.to_location, "to_node": c.end,
            **dict(zip(("to_x_m", "to_y_m", "to_z_m"), (round(float(v), 4) for v in pos[c.end]))),
            "length_m": round(length, 4),
            "mass_kg": None if mass_fn is None else round(length * mass_fn(c.emc_class), 4),
            "waypoint_count": len(path),
            "bundled_with": mates, "shared_length_m": round(shared, 4),
            "sharing_ratio": round(shared / length, 3) if length > 1e-9 else 0.0,
            "min_emc_margin_m": _round(emc), "min_bend_radius_m": _round(bend), "bend_limit_m": _round(bend_limit),
            "min_keepout_distance_m": _round(keepout), "near_parallel_length_m": _round(parallel, 3),
            "status": status,
        })
    return records


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value: str) -> str:
    """A text cell a spreadsheet will not run as a formula (OWASP: prefix such cells with a quote)."""
    return "'" + value if value.startswith(_FORMULA_START) else value


def export_wirelist_csv(records: list[Record], csv_path: Path) -> None:
    """Write wirelist records as CSV (bundle partners joined with ';', missing values empty).

    Text cells (names from the scenario file) that start like a formula get a leading quote.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=WIRELIST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for r in records:
            cells = {k: (";".join(v) if isinstance(v, list) else "" if v is None else v) for k, v in r.items()}
            writer.writerow({k: _csv_text(v) if isinstance(v, str) else v for k, v in cells.items()})


def _compact_json(obj: object, level: int = 0) -> str:
    """JSON with objects indented but every array of numbers kept on a single line."""
    pad, end = "  " * (level + 1), "  " * level
    if isinstance(obj, dict) and obj:
        items = (f"{pad}{json.dumps(k, ensure_ascii=False)}: {_compact_json(v, level + 1)}" for k, v in obj.items())
        return "{\n" + ",\n".join(items) + "\n" + end + "}"
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        return "[\n" + ",\n".join(pad + _compact_json(x, level + 1) for x in obj) + "\n" + end + "]"
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def export_routes_json(
    graph: RoutingGraph,
    cables: Sequence[Cable],
    results: Sequence[RoutingResult] | RoutingResult,
    json_path: Path,
    rows: Sequence[Row] | None = None,
    parameters: dict[str, object] | None = None,
) -> None:
    """Write every method's routes (3D polylines, metadata, check results) to a compact JSON file."""
    results_list = [results] if isinstance(results, RoutingResult) else list(results)
    row_by_method = {str(r["method"]): r for r in rows or []}
    check_keys = ("total_length_m", "unique_length_m", "bundling_ratio", "emc_points", "bend_points",
                  "forbidden_points", "clearance_points", "capacity_edges")
    pos = graph.positions
    methods: dict[str, object] = {}
    for res in results_list:
        partners = _bundle_partners(graph, cables, res.routes)
        entry: dict[str, object] = {"label": res.label, "routing_order": list(res.order)}
        if res.method in row_by_method:
            row = row_by_method[res.method]
            entry["checks"] = {k: row[k] if isinstance(row[k], int) else _round(row[k]) for k in check_keys}
        entry["cables"] = [{
            "name": c.name, "emc_class": c.emc_class,
            "from_location": c.from_location, "to_location": c.to_location,
            "length_m": round(graph.path_length(res.routes[c.name]), 4),
            "bundled_with": partners[c.name][0], "shared_length_m": round(partners[c.name][1], 4),
            "node_ids": [int(n) for n in res.routes[c.name]],
            "waypoints_xyz": np.round(pos[res.routes[c.name]], 4).tolist(),
        } for c in cables]
        methods[res.method] = entry
    data = {
        "format": FORMAT,
        "description": "EMC-aware wire harness routes on a half-cylinder fuselage section (representative demo data)",
        "units": "m",
        "coordinate_frame": "x along the fuselage axis, z up; the skin is y^2 + z^2 = R^2 with z >= 0",
        "geometry": "polyline through mesh vertices on the skin (not smoothed, not lifted); "
                    "this is exactly what the independent checks evaluate",
        "parameters": parameters or {},
        "methods": methods,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(_compact_json(data) + "\n", encoding="utf-8")
