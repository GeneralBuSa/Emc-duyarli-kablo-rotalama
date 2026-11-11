"""Shared fixtures: the demo's mesh, graph, scenario and routing results, built once per session."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402  (parameters of the published run)
from harness_demo.checks import run_all_checks  # noqa: E402
from harness_demo.geometry import build_fuselage  # noqa: E402
from harness_demo.graph import build_routing_graph  # noqa: E402
from harness_demo.metrics import compute_metrics  # noqa: E402
from harness_demo.routing import (  # noqa: E402
    build_turn_graph,
    route_baseline,
    route_branched,
    route_bundled,
    route_emc_aware,
    route_integrated,
    route_lagrangian,
)
from harness_demo.scenarios import build_scenario, load_scenario  # noqa: E402

SC = load_scenario()  # the example scenario of the published run
separation = SC.separation


@pytest.fixture(scope="session")
def mesh():
    return build_fuselage(SC.fuselage)


@pytest.fixture(scope="session")
def volumes():
    return SC.volumes


@pytest.fixture(scope="session")
def graph(mesh, volumes):
    return build_routing_graph(mesh, volumes, clearance=SC.clearance)


@pytest.fixture(scope="session")
def cables(graph):
    return build_scenario(graph, SC.fuselage.radius, SC.cables)


@pytest.fixture(scope="session")
def turns(graph):
    return build_turn_graph(graph, SC.min_bend_radius)


@pytest.fixture(scope="session")
def results(graph, cables, turns):
    """The six methods exactly as main.py runs them, keyed by method name."""
    out = [
        route_baseline(graph, cables),
        route_bundled(graph, cables, reuse_factor=SC.reuse_factor),
        route_emc_aware(graph, cables, separation, reuse_factor=SC.reuse_factor, emc_penalty=SC.emc_penalty),
        route_lagrangian(graph, cables, capacity=SC.capacity_k, iterations=main.LAGRANGE_ITERATIONS),
        route_integrated(graph, cables, separation, turns, SC.capacity_k, reuse_factor=SC.reuse_factor,
                         emc_penalty=SC.emc_penalty),
        route_branched(graph, cables, separation, turns, SC.capacity_k, reuse_factor=SC.reuse_factor,
                       emc_penalty=SC.emc_penalty),
    ]
    return {r.method: r for r in out}


@pytest.fixture(scope="session")
def metrics(results, graph, cables, volumes):
    """Metric rows (independent checks included) keyed by method name."""
    rows = {}
    for name, res in results.items():
        report = run_all_checks(
            [res.routes[c.name] for c in cables], [c.emc_class for c in cables], graph.positions, volumes,
            separation, SC.min_bend_radius, SC.capacity_k, SC.clearance,
        )
        rows[name] = compute_metrics(res, graph, report)
    return rows
