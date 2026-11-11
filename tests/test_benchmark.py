"""Scenario perturbation and the robustness benchmark (sequential and parallel)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

import main
from harness_demo.benchmark import (
    BenchmarkContext,
    RouterSpec,
    format_robustness_markdown,
    route_with,
    run_robustness,
)
from harness_demo.checks import CheckSettings
from harness_demo.geometry import surface_point
from harness_demo.scenarios import load_scenario, perturb_specs

SC = load_scenario()  # the example scenario of the published run
separation = SC.separation


def _published_routers():
    """The robustness routers exactly as main.py builds them for the published run."""
    args = main.parse_args([])
    main.resolve_params(SC, args)
    return main.benchmark_routers(args)


def _terminals(specs):
    pts = []
    for s in specs:
        pts += [(s.emc_class, surface_point(*s.start_xt, SC.fuselage.radius)),
                (s.emc_class, surface_point(*s.end_xt, SC.fuselage.radius))]
    return pts


@pytest.fixture(scope="module")
def ctx(graph, volumes, turns):
    settings = CheckSettings(volumes, separation, SC.min_bend_radius, SC.capacity_k, SC.clearance)
    return BenchmarkContext(graph, settings, turns)


def test_perturbation_keeps_terminals_separable():
    rng = np.random.default_rng(0)
    for _ in range(50):
        pts = _terminals(perturb_specs(SC.cables, rng))
        for (ca, pa), (cb, pb) in itertools.combinations(pts, 2):
            if ca != cb:
                assert np.linalg.norm(pa - pb) > separation(ca, cb)


def test_robustness_rows(ctx):
    rows = run_robustness(ctx, SC.cables, SC.fuselage.radius, {"a": RouterSpec("baseline")}, n_trials=2)
    assert [r["family"] for r in rows] == ["Rastgele sıra", "Rastgele sıra + uç nokta sapması"]
    for r in rows:
        assert r["trials"] == 2 and 0 <= r["emc_zero"] <= 2 and 0 <= r["all_clean"] <= 2
    assert format_robustness_markdown(rows).count("\n") == 1 + len(rows)


def test_parallel_benchmark_matches_sequential(ctx):
    """Trials are drawn in the parent process, so the summary does not depend on the worker count."""
    routers = {"a": RouterSpec("baseline"), "e": _published_routers()["e) Bütünleşik"]}
    seq = run_robustness(ctx, SC.cables, SC.fuselage.radius, routers, n_trials=2, workers=1)
    par = run_robustness(ctx, SC.cables, SC.fuselage.radius, routers, n_trials=2, workers=2)
    assert par == seq


def test_router_specs_cover_every_benchmark_method(ctx, cables):
    for spec in _published_routers().values():
        res = route_with(spec, ctx, cables)
        assert set(res.routes) == {c.name for c in cables}
    with pytest.raises(ValueError):
        route_with(RouterSpec("unknown"), ctx, cables)
