"""Routing methods: valid paths, turn-aware graph, rip-up-and-reroute, Lagrangian bounds and robustness."""

from __future__ import annotations

import math
import random
from itertools import pairwise

import networkx as nx
import numpy as np
import pytest

from harness_demo.checks import circumradius, run_all_checks
from harness_demo.routing import (
    NoPathError,
    order_cables,
    route_conflicts,
    route_emc_aware,
    route_integrated,
    route_lagrangian,
    turn_aware_path,
)
from harness_demo.scenarios import load_scenario

SC = load_scenario()  # the example scenario of the published run
separation = SC.separation


def test_every_route_is_a_valid_path(results, graph, cables):
    for res in results.values():
        assert set(res.routes) == {c.name for c in cables}
        assert sorted(res.order) == sorted(res.routes)
        for c in cables:
            path = res.routes[c.name]
            assert path[0] == c.start and path[-1] == c.end
            assert graph.node_allowed[path].all()
            graph.path_edge_ids(path)  # raises KeyError if two consecutive nodes are not an edge


def test_baseline_matches_networkx(results, graph, cables):
    base = results["baseline"]
    for c in cables:
        expected = nx.shortest_path_length(graph.nx_graph, c.start, c.end, weight="weight")
        assert graph.path_length(base.routes[c.name]) == pytest.approx(expected, rel=1e-9)


def test_turn_graph_has_no_u_turns_or_sharp_turns(turns, graph):
    rng = np.random.default_rng(0)
    idx = rng.choice(len(turns.arc_src), size=2000, replace=False)
    pos = graph.positions
    for k in idx:
        a = turns.de_from[turns.arc_src[k]]
        b = turns.de_to[turns.arc_src[k]]
        c = turns.de_to[turns.arc_dst[k]]
        assert b == turns.de_from[turns.arc_dst[k]]
        assert c != a
        assert circumradius(pos[a], pos[b], pos[c]) >= SC.min_bend_radius


def test_turn_aware_path_is_bend_feasible(turns, graph, cables):
    c = cables[0]
    path = turn_aware_path(turns, graph.lengths.copy(), c.start, c.end)
    pts = graph.positions[path]
    radii = [circumradius(a, b, d) for a, b, d in zip(pts[:-2], pts[1:-1], pts[2:])]
    assert min(radii) >= SC.min_bend_radius
    # Not shorter than the unconstrained shortest path, and not absurdly longer.
    free = nx.shortest_path_length(graph.nx_graph, c.start, c.end, weight="weight")
    assert free - 1e-9 <= graph.path_length(path) <= 1.3 * free


def test_integrated_method_satisfies_all_checks(metrics):
    row = metrics["integrated"]
    assert row["emc_points"] == 0
    assert row["bend_points"] == 0
    assert row["capacity_edges"] == 0
    assert row["forbidden_points"] == 0
    assert row["clearance_points"] == 0


def test_router_conflicts_agree_with_independent_emc_check(results, graph, cables, metrics):
    classes = {c.name: c.emc_class for c in cables}
    for name in ("bundled", "emc_aware"):
        score = route_conflicts(graph, results[name].routes, classes, separation, None)
        assert (sum(score.values()) == 0) == (metrics[name]["emc_points"] == 0)


def test_reroute_removes_order_dependence(graph, cables, volumes):
    """With rip-up-and-reroute the EMC-aware method stays EMC-clean for random cable orders."""
    rng = random.Random(3)
    for _ in range(4):
        order = cables[:]
        rng.shuffle(order)
        res = route_emc_aware(graph, order, separation, SC.reuse_factor, SC.emc_penalty)
        report = run_all_checks([res.routes[c.name] for c in order], [c.emc_class for c in order],
                                graph.positions, volumes, separation, SC.min_bend_radius, SC.capacity_k)
        assert report.emc_points == 0


# Random orders of the robustness benchmark (seed 7) in which rerouting alone gets stuck with EMC conflicts
# (without the coupling penalty, the default; with it two of them no longer get stuck).
STUCK_ORDERS = [
    "S2 S4 D1 P4 S3 D2 S1 P1 P3 P2 D3",
    "P3 P4 S4 D3 S2 D2 P2 P1 S1 S3 D1",
    "P3 S2 D2 D3 P1 D1 P2 S4 S3 P4 S1",
    "P4 S3 S1 S2 P3 S4 D1 D2 P2 P1 D3",
]


@pytest.mark.parametrize("order", STUCK_ORDERS)
def test_restarts_clear_orders_where_rerouting_gets_stuck(graph, cables, turns, volumes, order):
    by_name = {c.name: c for c in cables}
    ordered = [by_name[n] for n in order.split()]

    def check(restarts):
        res = route_integrated(graph, ordered, separation, turns, SC.capacity_k, SC.reuse_factor,
                               SC.emc_penalty, restarts=restarts, coupling_penalty=0.0)
        return run_all_checks([res.routes[c.name] for c in ordered], [c.emc_class for c in ordered],
                              graph.positions, volumes, separation, SC.min_bend_radius, SC.capacity_k,
                              SC.clearance)

    assert check(restarts=0).emc_points > 0
    assert not any(check(restarts=3).as_dict().values())


def test_lagrangian_bounds(results):
    hist = results["lagrangian"].history
    lbs = [h["lower_bound"] for h in hist]
    assert all(b2 >= b1 for b1, b2 in pairwise(lbs))  # best lower bound never decreases
    for h in hist:
        assert math.isfinite(h["dual"])
        assert h["lower_bound"] <= h["upper_bound"] + 1e-9
    last = hist[-1]
    assert (last["upper_bound"] - last["lower_bound"]) / last["upper_bound"] < 1e-3


def test_lagrangian_without_feasible_solution_fails_cleanly(graph, cables):
    """No capacity-feasible solution: the multipliers must stay finite (no NaN/inf steps)."""
    with pytest.raises(NoPathError):
        route_lagrangian(graph, cables[:3], capacity=0, iterations=5)


def test_order_cables_strategies(cables, graph):
    assert order_cables(cables, "input") == cables

    longest = order_cables(cables, "longest-first", graph.positions)
    pos = graph.positions
    dists_long = [np.linalg.norm(pos[c.start] - pos[c.end]) for c in longest]
    assert all(d1 >= d2 - 1e-9 for d1, d2 in pairwise(dists_long))

    shortest = order_cables(cables, "shortest-first", graph.positions)
    dists_short = [np.linalg.norm(pos[c.start] - pos[c.end]) for c in shortest]
    assert all(d1 <= d2 + 1e-9 for d1, d2 in pairwise(dists_short))

    critical = order_cables(cables, "critical-first", graph.positions)
    classes = [c.emc_class for c in critical]
    power_end = max(i for i, cl in enumerate(classes) if cl == "power")
    data_start = min(i for i, cl in enumerate(classes) if cl == "data")
    signal_start = min(i for i, cl in enumerate(classes) if cl == "signal")
    assert power_end < data_start < signal_start

    with pytest.raises(ValueError):
        order_cables(cables, "invalid-strategy")
    with pytest.raises(ValueError):  # distance-based strategies need the node positions
        order_cables(cables, "longest-first")


def test_routing_with_smart_ordering(graph, cables, turns):
    for strategy in ("critical-first", "longest-first"):
        res = route_integrated(
            graph, cables, separation, turns, capacity=SC.capacity_k,
            order_strategy=strategy,
        )
        assert set(res.routes) == {c.name for c in cables}
        assert all(len(path) >= 2 for path in res.routes.values())

