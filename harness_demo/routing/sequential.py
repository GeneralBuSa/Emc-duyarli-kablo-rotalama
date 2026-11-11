"""Sequential routing (methods b, c, e, f): bundling discount, EMC and coupling penalties, capacity
penalty, optional turn-aware graph with the bundle bend rule, Steiner-tree corridors (f), and
rip-up-and-reroute of the cables that still conflict."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import networkx as nx
import numpy as np
from networkx.algorithms.approximation import steiner_tree
from scipy.spatial import cKDTree

from ..checks import COUPLING_FACTOR, bend_violations_per_path, required_bend_radii
from ..graph import RoutingGraph
from ..scenarios import EMC_CLASSES, Cable
from .core import SAMPLE_STEP_M, RoutingResult, densify, shortest_path
from .turn_graph import TurnGraph, path_cost, turn_aware_path

ORDER_STRATEGIES: tuple[str, ...] = ("input", "critical-first", "longest-first", "shortest-first")


def order_cables(
    cables: Sequence[Cable],
    strategy: str = "input",
    positions: np.ndarray | None = None,
) -> list[Cable]:
    """Sort cables according to a designated initial routing priority heuristic.

    Supported strategies:
    - "input": Original list order (default).
    - "critical-first": Highest-EMC-separation classes first (power -> data -> signal),
      and longest cables within the same class first.
    - "longest-first": Longest direct terminal-to-terminal Euclidean distance first,
      establishing trunk corridors early.
    - "shortest-first": Shortest terminal-to-terminal distance first.
    """
    if strategy not in ORDER_STRATEGIES:
        raise ValueError(f"Unknown ordering strategy: {strategy!r}. Expected one of: {', '.join(ORDER_STRATEGIES)}.")
    if strategy == "input":
        return list(cables)
    if positions is None:
        raise ValueError(f"ordering strategy {strategy!r} needs the node positions")

    def dist(c: Cable) -> float:
        return float(np.linalg.norm(positions[c.start] - positions[c.end]))

    if strategy == "longest-first":
        return sorted(cables, key=dist, reverse=True)
    elif strategy == "shortest-first":
        return sorted(cables, key=dist)
    class_priority = {"power": 0, "data": 1, "signal": 2}  # critical-first
    return sorted(cables, key=lambda c: (class_priority.get(c.emc_class, 99), -dist(c)))


@dataclass
class _SequentialConfig:
    """Cost model of the sequential methods (b, c, e)."""

    reuse_factor: float = 0.4
    separation_fn: Callable[[str, str], float] | None = None
    emc_penalty: float = 0.0
    emc_margin: float = 0.03
    capacity: float | None = None  # max load per edge: cables (count mode) or cross-section [m^2] (area mode)
    capacity_penalty: float = 1000.0
    load_fn: Callable[[str], float] | None = None  # load of one cable by EMC class; None = 1 per cable
    turns: TurnGraph | None = None
    reroute_rounds: int = 0
    penalty_growth: float = 2.0  # EMC penalty multiplier per rip-up-and-reroute round
    order_strategy: str = "input"
    restarts: int = 0  # fresh passes with the conflicting cables first (see `_route_sequential`)
    coupling_penalty: float = 0.0  # extra cost (x length) within COUPLING_FACTOR x separation of foreign routes
    # Bundle bend rule (turn-aware routing only): a turn needs radius >= factor x bundle diameter.
    diameter_fn: Callable[[str], float] | None = None
    bundle_bend_factor: float = 0.0
    bundle_diameter_factor: float = 1.25
    preferred: dict[str, np.ndarray] | None = None  # per cable name: edge mask of its corridor (method f)
    tree_factor: float = 0.1  # cost multiplier on the cable's own corridor edges

    @property
    def bundle_rule(self) -> bool:
        """True when the bundle bend rule is part of the cost model."""
        return self.turns is not None and self.bundle_bend_factor > 0.0 and self.diameter_fn is not None


class _RouteGeometry:
    """Per-route derived data (unique edges, dense samples, sample KD-tree, nearby nodes).

    Sequential routing re-evaluates the same placed routes for every cable and every reroute
    round; caching by the route itself (an immutable tuple of node ids) turns those repeated
    computations into lookups without changing any result.
    """

    def __init__(self, graph: RoutingGraph) -> None:
        self.graph = graph
        self.node_tree = cKDTree(graph.positions)
        self._edges: dict[tuple[int, ...], np.ndarray] = {}
        self._samples: dict[tuple[int, ...], np.ndarray] = {}
        self._trees: dict[tuple[int, ...], cKDTree] = {}
        self._near: dict[tuple[tuple[int, ...], float], np.ndarray] = {}
        self._pairs: dict[tuple[tuple[int, ...], tuple[int, ...], float], int] = {}

    def edges(self, path: list[int]) -> np.ndarray:
        key = tuple(path)
        if key not in self._edges:
            self._edges[key] = np.unique(self.graph.path_edge_ids(path))
        return self._edges[key]

    def samples(self, path: list[int]) -> np.ndarray:
        key = tuple(path)
        if key not in self._samples:
            self._samples[key] = densify(self.graph.positions[path], SAMPLE_STEP_M)
        return self._samples[key]

    def sample_tree(self, path: list[int]) -> cKDTree:
        key = tuple(path)
        if key not in self._trees:
            self._trees[key] = cKDTree(self.samples(path))
        return self._trees[key]

    def nodes_near(self, path: list[int], radius: float) -> np.ndarray:
        """Graph nodes within `radius` of the route (KD-tree query around its samples only)."""
        key = (tuple(path), radius)
        if key not in self._near:
            hits = self.node_tree.query_ball_point(self.samples(path), r=radius)
            self._near[key] = np.unique(np.concatenate([np.asarray(h, dtype=int) for h in hits]))
        return self._near[key]

    def close_samples(self, path_a: list[int], path_b: list[int], sep: float) -> int:
        """Number of samples of route a closer than `sep` to a sample of route b (cached per pair)."""
        key = (tuple(path_a), tuple(path_b), sep)
        if key not in self._pairs:
            d, _ = self.sample_tree(path_b).query(self.samples(path_a))
            self._pairs[key] = int(np.count_nonzero(d < sep))
        return self._pairs[key]


def _cable_cost(
    graph: RoutingGraph,
    geo: _RouteGeometry,
    cable: Cable,
    placed: dict[str, list[int]],
    classes: dict[str, str],
    cfg: _SequentialConfig,
    emc_penalty: float,
) -> np.ndarray:
    """Edge costs for `cable` given the routes already placed (excluding the cable itself).

    Bundling: an edge used by any other cable costs `reuse_factor * length`.
    EMC: nodes within sep(c, c') + margin of a route of class c' are "tainted" for class c;
    edges touching a tainted node pay `emc_penalty * length` extra. The KD-tree query keeps
    this local to each route's neighbourhood.
    Capacity: an edge on which this cable would exceed `capacity` pays `capacity_penalty * length`
    (count mode: an edge already carrying `capacity` other cables; area mode: the cross-sections
    of the other cables plus this one exceed the bundle area).
    Coupling: edges within COUPLING_FACTOR x sep of a foreign route pay `coupling_penalty * length`,
    so long side-by-side runs (crosstalk grows with their length) are avoided where it is cheap.
    Corridor (method f): the edges of the cable's corridor (its class tree path) cost `tree_factor * length`.
    """
    w = graph.lengths
    used = np.zeros(graph.n_edges, dtype=bool)
    load = np.zeros(graph.n_edges, dtype=int if cfg.load_fn is None else float)
    tainted = np.zeros(graph.n_nodes, dtype=bool)
    coupled = np.zeros(graph.n_nodes, dtype=bool)
    for name, path in placed.items():
        if name == cable.name:
            continue
        eids = geo.edges(path)
        used[eids] = True
        load[eids] += 1 if cfg.load_fn is None else cfg.load_fn(classes[name])
        if cfg.separation_fn is not None and (emc_penalty > 0.0 or cfg.coupling_penalty > 0.0):
            sep = cfg.separation_fn(cable.emc_class, classes[name])
            if sep > 0.0 and emc_penalty > 0.0:
                tainted[geo.nodes_near(path, sep + cfg.emc_margin)] = True
            if sep > 0.0 and cfg.coupling_penalty > 0.0:
                coupled[geo.nodes_near(path, COUPLING_FACTOR * sep)] = True

    factor = np.where(used, cfg.reuse_factor, 1.0)
    if cfg.preferred is not None and cable.name in cfg.preferred:
        factor = np.where(cfg.preferred[cable.name], np.minimum(factor, cfg.tree_factor), factor)
    cost = w * factor
    ends = graph.edges[:, 0], graph.edges[:, 1]
    if cfg.separation_fn is not None and emc_penalty > 0.0:
        cost = cost + emc_penalty * w * (tainted[ends[0]] | tainted[ends[1]])
    if cfg.coupling_penalty > 0.0:
        cost = cost + cfg.coupling_penalty * w * (coupled[ends[0]] | coupled[ends[1]])
    if cfg.capacity is not None:
        if cfg.load_fn is None:
            full = load >= cfg.capacity
        else:
            full = load + cfg.load_fn(cable.emc_class) > cfg.capacity * (1.0 + 1e-9)
        cost = cost + cfg.capacity_penalty * w * full
    return cost


def _bend_arc_cost(
    graph: RoutingGraph, cable: Cable, placed: dict[str, list[int]], classes: dict[str, str],
    cfg: _SequentialConfig,
) -> np.ndarray | None:
    """Bundle bend rule as a cost per allowed turn (None when the rule is off).

    A turn a-v-c already taken by other cables bends a bundle of diameter
    factor x sqrt(sum of their squared diameters); with this cable added, the turn needs a radius of
    at least bundle_bend_factor x that diameter. Turns that are too sharp for the grown bundle pay
    `capacity_penalty x length` of the edge they enter, like an overloaded edge.
    """
    if not cfg.bundle_rule:
        return None
    turns, diameter = cfg.turns, cfg.diameter_fn
    assert turns is not None and turns.arc_radius is not None and diameter is not None
    sq: dict[int, float] = {}
    for name, path in placed.items():
        if name != cable.name:
            d2 = diameter(classes[name]) ** 2
            for k in np.unique(turns.path_turn_keys(path)).tolist():
                sq[k] = sq.get(k, 0.0) + d2
    keys = turns.arc_keys()
    total = np.full(len(keys), diameter(cable.emc_class) ** 2)
    if sq:
        known = np.array(sorted(sq), dtype=np.int64)
        values = np.array([sq[int(k)] for k in known])
        at = np.minimum(np.searchsorted(known, keys), len(known) - 1)
        total += np.where(known[at] == keys, values[at], 0.0)
    required = cfg.bundle_bend_factor * cfg.bundle_diameter_factor * np.sqrt(total)
    too_sharp = turns.arc_radius < required
    return cfg.capacity_penalty * graph.lengths[turns.de_edge[turns.arc_dst]] * too_sharp


def _route_one(graph: RoutingGraph, cost: np.ndarray, cable: Cable, cfg: _SequentialConfig,
               previous: list[int] | None = None, arc_extra: np.ndarray | None = None) -> list[int]:
    """Shortest path for one cable on the plain or the turn-aware graph.

    When the cable is rerouted, its previous route is still feasible, so its cost under the new
    edge costs bounds the search (Dijkstra stops beyond it; the result is unchanged). With turn
    costs (`arc_extra`) the bound is not used.
    """
    limit = np.inf
    if previous is not None and arc_extra is None:
        eids = graph.path_edge_ids(previous)
        bound = path_cost(cost, eids) if cfg.turns is not None else float(cost[eids].sum())
        limit = bound * (1.0 + 1e-9) + 1e-9
    if cfg.turns is not None:
        return turn_aware_path(cfg.turns, cost, cable.start, cable.end, limit, arc_extra)
    return shortest_path(graph.csr(cost), cable.start, cable.end, limit)


def route_conflicts(
    graph: RoutingGraph,
    routes: dict[str, list[int]],
    classes: dict[str, str],
    separation_fn: Callable[[str, str], float] | None,
    capacity: float | None,
    geo: _RouteGeometry | None = None,
    load_fn: Callable[[str], float] | None = None,
    bundle_bend: tuple[float, float, float, Callable[[str], float]] | None = None,
) -> dict[str, float]:
    """Router-side conflict score per cable (EMC samples too close + overloaded edges; with
    `bundle_bend` = (min radius, bend factor, diameter factor, diameter by class) also the turns
    that are too sharp for their bundle).

    This is the router's own estimate used to decide what to reroute; the reported metrics
    come from the independent checks in `checks.py`.
    """
    geo = geo or _RouteGeometry(graph)
    score = {name: 0.0 for name in routes}
    if separation_fn is not None:
        for a in routes:
            for b in routes:
                if a == b:
                    continue
                sep = separation_fn(classes[a], classes[b])
                if sep > 0.0:
                    score[a] += float(geo.close_samples(routes[a], routes[b], sep))
    if capacity is not None:
        load = np.zeros(graph.n_edges, dtype=int if load_fn is None else float)
        edge_sets = {n: geo.edges(p) for n, p in routes.items()}
        for n, eids in edge_sets.items():
            load[eids] += 1 if load_fn is None else load_fn(classes[n])
        limit = capacity if load_fn is None else capacity * (1.0 + 1e-9)
        for n, eids in edge_sets.items():
            score[n] += float(np.count_nonzero(load[eids] > limit))
    if bundle_bend is not None:
        min_radius, bend_factor, diameter_factor, diameter = bundle_bend
        names = list(routes)
        paths = [routes[n] for n in names]
        required = required_bend_radii(paths, [diameter(classes[n]) for n in names], min_radius, bend_factor,
                                       diameter_factor)
        counts = bend_violations_per_path([graph.positions[np.asarray(p)] for p in paths], min_radius, required)
        for n, k in zip(names, counts):
            score[n] += float(k)
    return score


@dataclass
class _Attempt:
    """Best solution of one sequential pass: total conflict score, routes, routing order, per-cable score."""

    total: float
    routes: dict[str, list[int]]
    order: list[str]
    score: dict[str, float]


def _most_conflicting_first(score: dict[str, float]) -> list[str]:
    """Cables with a conflict, highest score first."""
    return sorted((n for n, s in score.items() if s > 0), key=lambda n: -score[n])


def _route_sequential(
    graph: RoutingGraph, cables: list[Cable], cfg: _SequentialConfig
) -> tuple[dict[str, list[int]], list[str]]:
    """Route cables one by one, then rip up and reroute the ones that still conflict.

    In every reroute round the conflicting cables are removed and routed again, most
    conflicting first, now seeing *all* other routes (not only earlier ones) and with the
    EMC penalty multiplied by `penalty_growth`. The best solution (lowest total conflict
    score) seen over all rounds is returned, so rerouting never makes the result worse.

    Rerouting one cable at a time can get stuck: the same routes come back every round, or a
    larger EMC penalty only trades the EMC conflict for a capacity one. If conflicts remain and
    `cfg.restarts > 0`, the whole pass is repeated from scratch with the conflicting cables
    routed first (priority promotion) up to `cfg.restarts` times, then once from each of the
    other order strategies (followed by one promotion if it still conflicts). Of all these
    candidates the one with the fewest conflicts wins, the shortest among equals: the first
    conflict-free candidate can contain a long detour forced by the grown EMC penalty. A first
    pass without conflicts is never changed by the restarts.
    """
    geo = _RouteGeometry(graph)
    classes = {c.name: c.emc_class for c in cables}
    by_name = {c.name: c for c in cables}

    bundle_bend = None
    if cfg.bundle_rule:
        assert cfg.turns is not None and cfg.diameter_fn is not None
        bundle_bend = (cfg.turns.min_radius, cfg.bundle_bend_factor, cfg.bundle_diameter_factor, cfg.diameter_fn)

    def conflicts(r: dict[str, list[int]]) -> dict[str, float]:
        return route_conflicts(graph, r, classes, cfg.separation_fn, cfg.capacity, geo, cfg.load_fn, bundle_bend)

    def route(cable: Cable, placed: dict[str, list[int]], penalty: float,
              previous: list[int] | None = None) -> list[int]:
        cost = _cable_cost(graph, geo, cable, placed, classes, cfg, penalty)
        extra = _bend_arc_cost(graph, cable, placed, classes, cfg)
        return _route_one(graph, cost, cable, cfg, previous=previous, arc_extra=extra)

    def attempt(initial: list[Cable]) -> _Attempt:
        routes: dict[str, list[int]] = {}
        order: list[str] = []
        for cable in initial:
            routes[cable.name] = route(cable, routes, cfg.emc_penalty)
            order.append(cable.name)
        score = conflicts(routes)
        best = _Attempt(sum(score.values()), dict(routes), list(order), score)
        penalty = cfg.emc_penalty
        for _ in range(cfg.reroute_rounds):
            bad = _most_conflicting_first(score)
            if not bad:
                break
            penalty *= cfg.penalty_growth
            for name in bad:
                placed = {n: p for n, p in routes.items() if n != name}
                routes[name] = route(by_name[name], placed, penalty, previous=routes[name])
                order.remove(name)
                order.append(name)
            score = conflicts(routes)
            if sum(score.values()) < best.total:
                best = _Attempt(sum(score.values()), dict(routes), list(order), score)
        return best

    def promoted(a: _Attempt) -> _Attempt:
        bad = _most_conflicting_first(a.score)
        return attempt([by_name[n] for n in bad] + [by_name[n] for n in a.order if n not in bad])

    def rank(a: _Attempt) -> tuple[float, float]:  # fewest conflicts first, then the shortest
        return a.total, sum(graph.path_length(p) for p in a.routes.values())

    best = attempt(order_cables(cables, cfg.order_strategy, graph.positions))
    if cfg.restarts > 0 and best.total > 0:
        candidates = [best]
        chain = best
        for _ in range(cfg.restarts):
            if chain.total == 0:
                break
            nxt = promoted(chain)
            candidates.append(nxt)
            chain = min(chain, nxt, key=rank)
        for strategy in ORDER_STRATEGIES:
            if strategy != cfg.order_strategy:
                start = attempt(order_cables(cables, strategy, graph.positions))
                candidates.append(start)
                if start.total > 0:
                    candidates.append(promoted(start))
        best = min(candidates, key=rank)
    return {c.name: best.routes[c.name] for c in cables}, best.order


def route_bundled(
    graph: RoutingGraph,
    cables: list[Cable],
    reuse_factor: float = 0.4,
    order_strategy: str = "input",
) -> RoutingResult:
    """Sequential routing that rewards reusing edges of earlier cables."""
    t0 = time.perf_counter()
    cfg = _SequentialConfig(reuse_factor=reuse_factor, order_strategy=order_strategy)
    routes, order = _route_sequential(graph, cables, cfg)
    return RoutingResult("bundled", "b) Demetleme", routes, time.perf_counter() - t0, order=order)


def route_emc_aware(
    graph: RoutingGraph,
    cables: list[Cable],
    separation_fn: Callable[[str, str], float],
    reuse_factor: float = 0.4,
    emc_penalty: float = 20.0,
    emc_margin: float = 0.03,
    reroute_rounds: int = 5,
    order_strategy: str = "input",
) -> RoutingResult:
    """Bundling plus a soft penalty near routes of other EMC classes, with rip-up-and-reroute."""
    t0 = time.perf_counter()
    cfg = _SequentialConfig(reuse_factor=reuse_factor, separation_fn=separation_fn, emc_penalty=emc_penalty,
                            emc_margin=emc_margin, reroute_rounds=reroute_rounds, order_strategy=order_strategy)
    routes, order = _route_sequential(graph, cables, cfg)
    return RoutingResult("emc_aware", "c) EMC duyarlı demetleme", routes, time.perf_counter() - t0, order=order)


def route_integrated(
    graph: RoutingGraph,
    cables: list[Cable],
    separation_fn: Callable[[str, str], float],
    turns: TurnGraph,
    capacity: float,
    reuse_factor: float = 0.4,
    emc_penalty: float = 20.0,
    emc_margin: float = 0.03,
    capacity_penalty: float = 1000.0,
    reroute_rounds: int = 10,
    order_strategy: str = "input",
    restarts: int = 3,
    load_fn: Callable[[str], float] | None = None,
    coupling_penalty: float = 0.0,
    diameter_fn: Callable[[str], float] | None = None,
    bundle_bend_factor: float = 0.0,
    bundle_diameter_factor: float = 1.25,
) -> RoutingResult:
    """EMC-aware bundling on the turn-aware graph with a (near-hard) capacity penalty.

    Bend radius is a hard constraint (forbidden turns do not exist in `turns`), capacity and EMC
    separation are penalties strong enough to be respected whenever the geometry allows it.
    If rip-up-and-reroute ends with conflicts, the pass restarts with the conflicting cables
    first and then from the other order strategies (`restarts = 0` disables this).
    With `load_fn` (cross-section per EMC class) `capacity` is a bundle area instead of a cable count.
    `coupling_penalty` > 0 discourages long runs next to foreign routes; `bundle_bend_factor` > 0 (with
    `diameter_fn`) adds the bundle bend rule: a turn must suit the diameter of the bundle bending there.
    """
    t0 = time.perf_counter()
    cfg = _SequentialConfig(reuse_factor=reuse_factor, separation_fn=separation_fn, emc_penalty=emc_penalty,
                            emc_margin=emc_margin, capacity=capacity, capacity_penalty=capacity_penalty,
                            turns=turns, reroute_rounds=reroute_rounds, order_strategy=order_strategy,
                            restarts=restarts, load_fn=load_fn, coupling_penalty=coupling_penalty,
                            diameter_fn=diameter_fn, bundle_bend_factor=bundle_bend_factor,
                            bundle_diameter_factor=bundle_diameter_factor)
    routes, order = _route_sequential(graph, cables, cfg)
    return RoutingResult("integrated", "e) Bütünleşik (EMC + kapasite + bükülme)", routes,
                         time.perf_counter() - t0, order=order)


def class_trees(
    graph: RoutingGraph,
    cables: list[Cable],
    separation_fn: Callable[[str, str], float],
    emc_penalty: float = 20.0,
    emc_margin: float = 0.03,
) -> dict[str, nx.Graph]:
    """One Steiner tree per EMC class over that class's terminals.

    A harness is a tree: trunks that branch towards the equipment. The trees are built class by
    class (in the order power, signal, data); edges near the trees of the classes already built pay
    the EMC penalty, so the trees of different classes keep apart where the geometry allows.
    The tree is the 2-approximation of Mehlhorn (networkx) on these edge costs.
    """
    geo = _RouteGeometry(graph)
    w = graph.lengths
    trees: dict[str, nx.Graph] = {}
    present = {k.emc_class for k in cables}
    for cls in [c for c in EMC_CLASSES if c in present]:
        tainted = np.zeros(graph.n_nodes, dtype=bool)
        for other, tree in trees.items():
            sep = separation_fn(cls, other)
            if sep > 0.0:
                for u, v in tree.edges():
                    tainted[geo.nodes_near([int(u), int(v)], sep + emc_margin)] = True
        cost = w + emc_penalty * w * (tainted[graph.edges[:, 0]] | tainted[graph.edges[:, 1]])
        terminals = {n for k in cables if k.emc_class == cls for n in (k.start, k.end)}
        g = nx.Graph()
        g.add_weighted_edges_from(zip(graph.edges[:, 0].tolist(), graph.edges[:, 1].tolist(), cost.tolist()))
        trees[cls] = steiner_tree(g, terminals, weight="weight", method="mehlhorn") if len(terminals) >= 2 \
            else nx.Graph()
    return trees


def cable_corridors(
    graph: RoutingGraph,
    cables: list[Cable],
    trees: dict[str, nx.Graph],
    max_detour: float = 1.25,
) -> dict[str, np.ndarray]:
    """Per cable: the edges of its path through its class tree (edge mask), if that path is short enough.

    A class tree can join clusters that a cable never needs to visit (e.g. the left and the right
    side); following the tree there would be a long detour. So a cable only gets its tree path as a
    corridor when it is at most `max_detour` x its own shortest route; otherwise it has none.
    """
    out: dict[str, np.ndarray] = {}
    adj = graph.csr(graph.lengths)
    for c in cables:
        tree = trees.get(c.emc_class)
        if tree is None or c.start not in tree or c.end not in tree:
            continue
        nodes = nx.shortest_path(tree, c.start, c.end)
        tree_len = graph.path_length(nodes)
        if tree_len <= max_detour * graph.path_length(shortest_path(adj, c.start, c.end)) + 1e-9:
            mask = np.zeros(graph.n_edges, dtype=bool)
            mask[graph.path_edge_ids(nodes)] = True
            out[c.name] = mask
    return out


def route_branched(
    graph: RoutingGraph,
    cables: list[Cable],
    separation_fn: Callable[[str, str], float],
    turns: TurnGraph,
    capacity: float,
    tree_factor: float = 0.3,
    max_detour: float = 1.25,
    reuse_factor: float = 0.4,
    emc_penalty: float = 20.0,
    emc_margin: float = 0.03,
    capacity_penalty: float = 1000.0,
    reroute_rounds: int = 10,
    order_strategy: str = "input",
    restarts: int = 3,
    load_fn: Callable[[str], float] | None = None,
    coupling_penalty: float = 0.0,
    diameter_fn: Callable[[str], float] | None = None,
    bundle_bend_factor: float = 0.0,
    bundle_diameter_factor: float = 1.25,
) -> RoutingResult:
    """Method f: branched harness. A Steiner tree per EMC class gives the class its trunks and
    branches (`class_trees`); every cable's path through its tree becomes its corridor (unless that is
    a detour, see `cable_corridors`) at `tree_factor` x length, and the cables are routed like method
    e), so they gather on the shared trunks and leave them only where a branch, the bend radius,
    capacity or EMC separation requires.
    """
    t0 = time.perf_counter()
    trees = class_trees(graph, cables, separation_fn, emc_penalty, emc_margin)
    corridors = cable_corridors(graph, cables, trees, max_detour)
    cfg = _SequentialConfig(reuse_factor=reuse_factor, separation_fn=separation_fn, emc_penalty=emc_penalty,
                            emc_margin=emc_margin, capacity=capacity, capacity_penalty=capacity_penalty,
                            turns=turns, reroute_rounds=reroute_rounds, order_strategy=order_strategy,
                            restarts=restarts, load_fn=load_fn, coupling_penalty=coupling_penalty,
                            diameter_fn=diameter_fn, bundle_bend_factor=bundle_bend_factor,
                            bundle_diameter_factor=bundle_diameter_factor, preferred=corridors,
                            tree_factor=tree_factor)
    routes, order = _route_sequential(graph, cables, cfg)
    return RoutingResult("branched", "f) Dallı demet (Steiner ağacı)", routes, time.perf_counter() - t0, order=order)
