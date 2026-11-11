"""Exact reference solution on a small instance (mixed-integer program, HiGHS via scipy.optimize.milp).

The sequential methods are heuristics, so their length is compared with the true optimum of the
same problem on an instance small enough to solve exactly:

    min  sum_k length(P_k)
    s.t. P_k is a bend-feasible route of cable k      (flow on the turn graph, as in method e)
         load_e <= K on every edge                    (capacity)
         no point of P_k closer than sep(k, l) to P_l (EMC separation, edge pairs; see below)

Variables are per cable: one binary per allowed turn plus the arcs from a virtual source into the
states leaving the start node and from the states reaching the end node into a virtual sink;
flow conservation on every state makes each cable one path (cycles only add length, so an optimum
never contains one). EMC: for two cables of different classes, two edges whose segments come closer
than the required separation cannot both be used (`u_k(e) + u_l(f) <= 1`); this is slightly
stricter than the sampled check, never weaker. The instance is a coarser mesh of the same section
with a subset of the cables, so the model stays small.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix, csr_matrix, vstack
from scipy.spatial import cKDTree

from .checks import CheckReport, CheckSettings, point_segment_distances
from .convergence import refined
from .geometry import build_fuselage
from .graph import RoutingGraph, build_routing_graph
from .routing import TurnGraph, build_turn_graph, route_integrated
from .scenarios import Cable, Scenario, build_scenario


@dataclass
class ExactResult:
    """Outcome of the exact model."""

    status: str  # "optimal", "time limit", "infeasible", ...
    routes: dict[str, list[int]]
    length_m: float  # total length of the best solution found (nan if none)
    bound_m: float  # proven lower bound on the optimum (nan if none)
    runtime_s: float
    n_vars: int
    n_constraints: int
    bound_source: str = "milp"  # "milp" (branch and bound) or "lp" (LP relaxation, when the MILP found nothing)


def segment_distances(p0: np.ndarray, p1: np.ndarray, q0: np.ndarray, q1: np.ndarray) -> np.ndarray:
    """Row-wise minimum distance between segments [p0, p1] and [q0, q1] (via dense sampling of one side).

    Each segment is short (one mesh edge), so 33 samples on the first segment against the exact
    distance to the second segment are accurate to well below a millimetre.
    """
    ts = np.linspace(0.0, 1.0, 33)
    best = np.full(len(p0), np.inf)
    for t in ts:
        best = np.minimum(best, point_segment_distances(p0 + t * (p1 - p0), q0, q1))
    return best


def _close_edge_pairs(graph: RoutingGraph, sep: float) -> tuple[np.ndarray, np.ndarray]:
    """All ordered pairs (e, f) of distinct edges whose segments come closer than `sep`."""
    a, b = graph.positions[graph.edges[:, 0]], graph.positions[graph.edges[:, 1]]
    mid, half = 0.5 * (a + b), 0.5 * np.linalg.norm(b - a, axis=1)
    tree = cKDTree(mid)
    pairs = tree.query_pairs(sep + 2.0 * half.max(), output_type="ndarray")
    e, f = pairs[:, 0], pairs[:, 1]
    close = segment_distances(a[e], b[e], a[f], b[f]) < sep
    e, f = e[close], f[close]
    return np.concatenate([e, f]), np.concatenate([f, e])


def solve_exact(
    graph: RoutingGraph,
    turns: TurnGraph,
    cables: Sequence[Cable],
    separation_fn: Callable[[str, str], float],
    capacity: float,
    time_limit: float = 120.0,
    loads: Sequence[float] | None = None,
    upper_bound: float | None = None,
) -> ExactResult:
    """Minimum total length subject to bend, capacity and EMC separation (see module docstring).

    loads       per-cable capacity load (e.g. cross-section, with `capacity` the bundle area); default 1.
    upper_bound only solutions not longer than this are searched (e.g. the length of a heuristic
                solution): it prunes the search, and "infeasible" then means no feasible solution is
                shorter than the bound.
    If the solver stops without any solution, the LP relaxation still gives a proven lower bound.
    """
    t0 = time.perf_counter()
    n_states, n_arcs, n_edges = turns.n_states, len(turns.arc_src), graph.n_edges
    length = graph.lengths
    blocks = []  # per cable: (offset, src states, snk states)
    offset = 0
    for c in cables:
        src = np.flatnonzero(turns.de_from == c.start)
        snk = np.flatnonzero(turns.de_to == c.end)
        blocks.append((offset, src, snk))
        offset += n_arcs + len(src) + len(snk)
    n_vars = offset

    cost = np.zeros(n_vars)
    rows: list[csr_matrix] = []
    lo: list[np.ndarray] = []
    hi: list[np.ndarray] = []
    usage = []  # per cable: (n_edges x n_vars) matrix giving u_k(e), the number of times edge e is used
    for (off, src, snk) in blocks:
        n_src, n_snk = len(src), len(snk)
        arc_cols = off + np.arange(n_arcs)
        src_cols = off + n_arcs + np.arange(n_src)
        snk_cols = off + n_arcs + n_src + np.arange(n_snk)
        cost[arc_cols] = length[turns.de_edge[turns.arc_dst]]  # a state's edge is paid when it is entered
        cost[src_cols] = length[turns.de_edge[src]]
        # inflow(s) - outflow(s) = 0 for every state s.
        r = np.concatenate([turns.arc_dst, src, turns.arc_src, snk])
        col = np.concatenate([arc_cols, src_cols, arc_cols, snk_cols])
        val = np.concatenate([np.ones(n_arcs + n_src), -np.ones(n_arcs + n_snk)])
        rows.append(coo_matrix((val, (r, col)), shape=(n_states, n_vars)).tocsr())
        lo.append(np.zeros(n_states))
        hi.append(np.zeros(n_states))
        # Exactly one unit leaves the source and one reaches the sink.
        one = coo_matrix((np.ones(n_src + n_snk), (np.r_[np.zeros(n_src), np.ones(n_snk)].astype(int),
                                                     np.concatenate([src_cols, snk_cols]))), shape=(2, n_vars))
        rows.append(one.tocsr())
        lo.append(np.ones(2))
        hi.append(np.ones(2))
        # Edge usage = inflow of both of its states.
        r = np.concatenate([turns.de_edge[turns.arc_dst], turns.de_edge[src]])
        col = np.concatenate([arc_cols, src_cols])
        usage.append(coo_matrix((np.ones(len(r)), (r, col)), shape=(n_edges, n_vars)).tocsr())

    weights = [1.0] * len(cables) if loads is None else [float(w) for w in loads]
    cap = usage[0] * weights[0]
    for u, w in zip(usage[1:], weights[1:]):
        cap = cap + u * w
    rows.append(cap)
    lo.append(np.zeros(n_edges))
    # Same tolerance as the capacity check in area mode (float sums of cross-sections).
    hi.append(np.full(n_edges, float(capacity) * (1.0 if loads is None else 1.0 + 1e-9)))

    if upper_bound is not None and np.isfinite(upper_bound):
        rows.append(csr_matrix(cost[None, :]))
        lo.append(np.zeros(1))
        hi.append(np.array([upper_bound * (1.0 + 1e-9) + 1e-9]))

    pair_cache: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for i, ci in enumerate(cables):
        for j in range(i + 1, len(cables)):
            sep = separation_fn(ci.emc_class, cables[j].emc_class)
            if sep <= 0.0:
                continue
            if sep not in pair_cache:
                pair_cache[sep] = _close_edge_pairs(graph, sep)
            e, f = pair_cache[sep]
            rows.append(usage[i][e] + usage[j][f])
            lo.append(np.zeros(len(e)))
            hi.append(np.ones(len(e)))

    a = vstack(rows).tocsr()
    constraints = LinearConstraint(a, np.concatenate(lo), np.concatenate(hi))
    res = milp(cost, constraints=constraints, integrality=np.ones(n_vars), bounds=Bounds(0, 1),
               options={"time_limit": time_limit, "disp": False})
    status = {0: "optimal", 1: "time limit", 2: "infeasible", 3: "unbounded"}.get(res.status, res.message)
    routes: dict[str, list[int]] = {}
    total = float("nan")
    if res.x is not None:
        x = np.round(res.x).astype(int)
        for c, (off, src, snk) in zip(cables, blocks):
            routes[c.name] = _extract_path(turns, x, off, src, snk, c)
        total = sum(graph.path_length(p) for p in routes.values())
    bound = res.get("mip_dual_bound")
    bound = float(bound) if bound is not None and np.isfinite(bound) and bound > 0.0 else float("nan")
    source = "milp"
    if status == "optimal":
        bound = float(res.fun)
    elif not np.isfinite(bound) and status != "infeasible":
        # scipy returns no dual bound when the time limit leaves no solution: the LP relaxation of the
        # same model is a (weaker) proven lower bound and solves quickly.
        lp = milp(cost, constraints=constraints, integrality=np.zeros(n_vars), bounds=Bounds(0, 1),
                  options={"time_limit": max(time_limit, 30.0), "disp": False})
        if lp.status == 0:
            bound, source = float(lp.fun), "lp"
    return ExactResult(status, routes, total, bound, time.perf_counter() - t0, n_vars, a.shape[0], source)


def _extract_path(turns: TurnGraph, x: np.ndarray, off: int, src: np.ndarray, snk: np.ndarray,
                  cable: Cable) -> list[int]:
    """Follow the chosen arcs of one cable from its source state to a sink state."""
    n_arcs = len(turns.arc_src)
    chosen = np.flatnonzero(x[off:off + n_arcs])
    succ = dict(zip(turns.arc_src[chosen].tolist(), turns.arc_dst[chosen].tolist()))
    state = int(src[np.flatnonzero(x[off + n_arcs:off + n_arcs + len(src)])[0]])
    end_states = set(snk[np.flatnonzero(x[off + n_arcs + len(src):off + n_arcs + len(src) + len(snk)])].tolist())
    path = [int(turns.de_from[state]), int(turns.de_to[state])]
    while state not in end_states:
        state = succ[state]
        path.append(int(turns.de_to[state]))
    if path[-1] != cable.end:
        raise RuntimeError(f"exact model: route of {cable.name} does not end at its terminal")
    return path


# ------------------------------------------------------------------------------------------ report

MESH_FACTOR = 0.5  # 0.2 m mesh (example scenario): small enough for an exact model
MAX_MESH_VERTICES = 10_000  # the MILP has one binary per directed edge, cable and turn
_STATUS_TR = {"optimal": "kanıtlanmış optimum", "time limit": "süre sınırına ulaşıldı", "infeasible": "uygun çözüm yok"}
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+!|~-])")


def md_escape(text: str) -> str:
    """Scenario text inside a markdown report: markdown characters are backslash-escaped."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def checks_cell(r: CheckReport) -> str:
    """EMC / capacity / bend / keep-out / clearance counts of one solution."""
    return " / ".join(str(v) for v in (r.emc_points, r.capacity_edges, r.bend_points, r.forbidden_points,
                                       r.clearance_points))


def exact_study(scenario: Scenario, settings: CheckSettings, time_limit: float, reuse_factor: float,
                emc_penalty: float, capacity_text: str) -> str:
    """e) against the exact MILP optimum on a small instance (the scenario's exact_cables); markdown report.

    e), the MILP and the check use the same capacity rule (cable count, or bundle area in area mode).
    The MILP models bend radius, capacity and EMC separation; not the bundle bend rule or the
    coupling penalty (they are reported by the check / ignored).
    """
    p = refined(scenario.fuselage, MESH_FACTOR)
    separation = scenario.separation
    graph = build_routing_graph(build_fuselage(p), scenario.volumes, clearance=settings.clearance)
    cables = build_scenario(graph, p.radius, [s for s in scenario.cables if s.name in scenario.exact_cables])
    turns = build_turn_graph(graph, scenario.min_bend_radius)

    def check(routes: dict[str, list[int]]) -> CheckReport:
        return settings.check([routes[c.name] for c in cables], [c.emc_class for c in cables], graph.positions)

    load_fn = settings.load_fn
    integ = route_integrated(graph, cables, separation, turns, settings.capacity_limit, reuse_factor=reuse_factor,
                             emc_penalty=emc_penalty, load_fn=load_fn)
    e_len = sum(graph.path_length(r) for r in integ.routes.values())
    # No `upper_bound=e_len` here: on this instance the extra objective row stops HiGHS from finding any
    # solution in 120 s (without it: 11.854 m and a bound of 11.472 m), so it is left to callers of solve_exact.
    exact = solve_exact(graph, turns, cables, separation, settings.capacity_limit, time_limit=time_limit,
                        loads=None if load_fn is None else [load_fn(c.emc_class) for c in cables])
    lines = [
        "# Kesin çözümle karşılaştırma",
        "",
        f"Örnek: {p.length / (p.n_axial - 1):.2f} m mesh ({graph.nx_graph.number_of_nodes()} düğüm, "
        f"{turns.n_states} yönlü ayrıt, {len(turns.arc_src)} izinli dönüş), "
        f"kablolar {', '.join(md_escape(n) for n in scenario.exact_cables)}, "
        f"{capacity_text}. Amaç: toplam uzunluk; kısıtlar: bükülme (dönüş çizgesi), kapasite, EMC ayrımı. "
        f"MILP: {exact.n_vars} ikili değişken, {exact.n_constraints} kısıt, HiGHS (scipy.optimize.milp), "
        f"süre sınırı {time_limit:g} s.",
        "",
        "| Çözüm | Toplam uzunluk [m] | Denetimler (EMC / kapasite / bükülme / yasak / boşluk) | Süre [s] |",
        "|:---|---:|:---:|---:|",
        f"| e) Bütünleşik | {e_len:.3f} | {checks_cell(check(integ.routes))} | {integ.runtime_s:.2f} |",
    ]
    status = _STATUS_TR.get(exact.status, exact.status)
    if exact.routes:
        lines.append(f"| MILP en iyi çözüm ({status}) | {exact.length_m:.3f} | "
                     f"{checks_cell(check(exact.routes))} | {exact.runtime_s:.1f} |")
    else:
        lines.append(f"| MILP ({status}) | — | — | {exact.runtime_s:.1f} |")
    lines.append("")
    if exact.status == "infeasible":
        lines.append("- MILP modelinin uygun çözümü yok (modelin EMC kısıtı denetimden biraz daha sıkıdır).")
    if math.isfinite(exact.bound_m):
        source = "" if exact.bound_source == "milp" else " (LP gevşetmesinden; MILP süre içinde çözüm bulamadı)"
        lines.append(f"- Kanıtlanmış alt sınır{source}: {exact.bound_m:.3f} m. e), optimuma en çok "
                     f"%{max(e_len - exact.bound_m, 0.0) / e_len * 100:.1f} uzaktır"
                     + (f"; MILP'in bulduğu en iyi çözümden %{(e_len - exact.length_m) / e_len * 100:.1f} uzundur."
                        if exact.routes else "."))
    elif exact.status != "infeasible":
        lines.append("- Alt sınır elde edilemedi; daha uzun bir süre sınırı (ör. --exact 300) deneyin.")
    if settings.bundle_bend_factor > 0.0:
        lines.append("- MILP demet bükülme kuralını içermez; bükülme sütunu bu kuralı da denetler.")
    lines.append("")
    return "\n".join(lines)
