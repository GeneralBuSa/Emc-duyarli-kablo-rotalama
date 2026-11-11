"""Exact geometry primitives (property-based), scenario validation, area capacity, bounds, convergence,
the exact MILP reference and the robustness of the integrated method."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy.spatial import cKDTree

import main
from harness_demo.benchmark import BenchmarkContext, run_robustness
from harness_demo.checks import (
    CheckSettings,
    count_capacity_violations,
    distances_to_polyline,
    point_segment_distances,
    resample_polyline,
)
from harness_demo.convergence import refined, run_convergence
from harness_demo.exact import solve_exact
from harness_demo.export import generate_wirelist_records
from harness_demo.geometry import BoxVolume, SphereVolume
from harness_demo.presentation import _violation_spec
from harness_demo.routing import length_lower_bound, route_integrated
from harness_demo.scenarios import CableSpec, build_scenario, load_scenario, perturb_specs, validate_scenario

SC = load_scenario()  # the example scenario of the published run
separation = SC.separation


def _published_routers():
    """The robustness routers exactly as main.py builds them for the published run."""
    args = main.parse_args([])
    main.resolve_params(SC, args)
    return main.benchmark_routers(args)

coord = st.floats(-1.0, 1.0, allow_nan=False, allow_infinity=False)
point = st.tuples(coord, coord, coord)


def _brute_distance(p: np.ndarray, poly: np.ndarray) -> float:
    """Distance to a polyline by very dense sampling (reference for the exact routine)."""
    fine = resample_polyline(poly, 1e-4)
    return float(np.linalg.norm(fine - p, axis=1).min())


@settings(max_examples=60, deadline=None)
@given(st.lists(point, min_size=2, max_size=5), st.lists(point, min_size=1, max_size=6))
def test_exact_polyline_distance_matches_brute_force(poly, pts):
    poly, pts = np.array(poly), np.array(pts)
    samples = resample_polyline(poly, 0.05)
    exact = distances_to_polyline(pts, samples, cKDTree(samples), 0.05)
    ref = np.array([_brute_distance(p, poly) for p in pts])
    assert np.all(exact <= ref + 1e-9)  # never larger than the true distance (no missed violation)
    assert np.allclose(exact, ref, atol=2e-4)


def test_exact_distance_catches_what_the_samples_miss():
    # A point 0.098 m from the middle of a segment whose samples are 0.05 m apart: the nearest
    # *sample* is further than 0.1 m away, the segment itself is not.
    seg = np.array([[0.0, 0.0, 0.0], [0.05, 0.0, 0.0]])
    p = np.array([[0.025, 0.098, 0.0]])
    samples = resample_polyline(seg, 0.05)
    assert cKDTree(samples).query(p)[0][0] > 0.1
    assert distances_to_polyline(p, samples, cKDTree(samples), 0.05, cutoff=0.1)[0] == pytest.approx(0.098)


@settings(max_examples=80, deadline=None)
@given(point, point, point, st.floats(0.05, 0.8))
def test_segment_volume_tests_match_dense_sampling(p, q, c, size):
    p, q, c = np.array([p]), np.array([q]), np.array(c)
    ts = np.linspace(0.0, 1.0, 4001)[:, None]
    dense = p + ts * (q - p)
    sphere = SphereVolume("s", tuple(c), size)
    box = BoxVolume("b", tuple(c - size), tuple(c + size / 2))
    for vol in (sphere, box):
        hit = bool(vol.segments_intersect(p, q)[0])
        sampled = bool(vol.contains(dense).any())
        if sampled:
            assert hit  # every sampled hit is found
        elif hit:  # a hit the dense sampling missed can only be a grazing one
            depth = (size - np.linalg.norm(dense - c, axis=1).min()) if vol is sphere else 1e-3
            assert depth < 1e-3
    assert point_segment_distances(c[None], p, q)[0] >= 0.0


def test_capacity_with_loads():
    paths = [[0, 1, 2], [0, 1], [1, 2]]
    assert count_capacity_violations(paths, 2) == 0
    assert count_capacity_violations(paths, 1) == 2
    # Area mode: edge (0, 1) carries 0.5 + 0.7 = 1.2 > 1.0, edge (1, 2) carries 0.5 + 0.2 = 0.7.
    assert count_capacity_violations(paths, 1.0, [0.5, 0.7, 0.2]) == 1


def test_published_scenario_is_valid(graph, cables):
    assert validate_scenario(graph, SC.cables, cables, SC.fuselage.radius, separation) == []


def test_scenario_validation_reports_problems(graph):
    specs = [
        CableSpec("A", "power", (1.0, 90.0), (1.0, 90.0)),  # both ends on the same node
        CableSpec("B", "signal", (1.0, 90.0), (1.5, 90.0)),  # starts on A's node
        CableSpec("D", "data", (1.1, 90.0), (1.6, 90.0)),  # ~0.1 m from A's terminal (0.20 m needed)
        CableSpec("C", "data", (3.0, 50.0), (5.0, 80.0)),  # starts inside the actuator keep-out sphere
        CableSpec("X", "fiber", (1.0, 20.0), (2.0, 20.0)),  # unknown class
    ]
    issues = validate_scenario(graph, specs, build_scenario(graph, SC.fuselage.radius, specs), SC.fuselage.radius,
                               separation)
    text = "\n".join(f"{i.severity}: {i.message}" for i in issues)
    assert "error: A: iki uç aynı düğüme" in text
    assert "error: X: bilinmeyen EMC sınıfı" in text
    assert "warning: C başlangıç ucu" in text and "taşındı" in text
    assert "aynı düğümü paylaşıyor" in text
    assert "D başlangıç (data)" in text and "EMC açısından temiz bir çözüm olamaz" in text


def test_area_capacity_mode(graph, cables, turns, volumes):
    s = CheckSettings(volumes, separation, SC.min_bend_radius, SC.capacity_k, SC.clearance,
                      bundle_area=360e-6, area_fn=SC.cable_area)
    res = route_integrated(graph, cables, separation, turns, s.capacity_limit, reuse_factor=SC.reuse_factor,
                           emc_penalty=SC.emc_penalty, load_fn=s.load_fn)
    report = s.check([res.routes[c.name] for c in cables], [c.emc_class for c in cables], graph.positions)
    assert not any(report.as_dict().values())
    # Three thin cables fit in 360 mm^2, so bundling can go beyond K = 2 cables per edge.
    loads = [1] * len(cables)
    assert count_capacity_violations([res.routes[c.name] for c in cables], 2, loads) > 0


def test_lower_bound_holds(graph, cables, turns, metrics):
    lb = length_lower_bound(graph, cables, turns)
    assert lb <= metrics["integrated"]["total_length_m"] + 1e-9
    assert lb >= metrics["baseline"]["total_length_m"] - 1e-9  # bend only makes routes longer


def test_wirelist_reports_near_parallel_length(graph, cables, results, volumes):
    s = CheckSettings(volumes, separation, SC.min_bend_radius, SC.capacity_k, SC.clearance)
    records = generate_wirelist_records(graph, cables, results["integrated"].routes, s)
    for r in records:
        assert 0.0 <= r["near_parallel_length_m"] <= r["length_m"] + 1e-6


def test_violation_points_are_deduplicated_in_millimetres():
    pts = np.array([[1.0, 2.0, 0.5], [1.001, 2.0, 0.5], [1.2, 2.0, 0.5]])
    spec = _violation_spec(pts)
    mm = np.frombuffer(__import__("base64").b64decode(spec["b64"]), dtype="<i2").reshape(-1, 3)
    assert mm.tolist() == [[1000, 2000, 500], [1200, 2000, 500]]


def test_convergence_study_row(volumes):
    s = CheckSettings(volumes, separation, SC.min_bend_radius, SC.capacity_k, SC.clearance)
    (row,) = run_convergence(SC, s, SC.reuse_factor, SC.emc_penalty, factors=(1.0,))
    assert row["integrated_length_m"] == pytest.approx(66.05, abs=0.006)
    assert row["integrated_emc_points"] == 0 and row["integrated_capacity_edges"] == 0
    assert refined(SC.fuselage, 2.0).n_axial == 121


def test_exact_model_finds_the_shortest_bend_feasible_route(graph, turns):
    """With one cable there is no capacity or EMC coupling: the MILP optimum is the turn-aware shortest path."""
    (cable,) = build_scenario(graph, SC.fuselage.radius, [SC.cables[0]])
    res = solve_exact(graph, turns, [cable], separation, SC.capacity_k, time_limit=60)
    assert res.status == "optimal"
    assert res.routes[cable.name][0] == cable.start and res.routes[cable.name][-1] == cable.end
    assert res.length_m == pytest.approx(length_lower_bound(graph, [cable], turns), abs=1e-6)


def test_exact_model_uses_loads_and_the_upper_bound(graph, turns):
    """Per-cable loads enter the capacity rows; an upper bound below the optimum leaves no solution."""
    (cable,) = build_scenario(graph, SC.fuselage.radius, [SC.cables[0]])
    best = length_lower_bound(graph, [cable], turns)
    at_bound = solve_exact(graph, turns, [cable], separation, 1.0, time_limit=60, upper_bound=best)
    assert at_bound.status == "optimal" and at_bound.length_m == pytest.approx(best, abs=1e-6)
    below = solve_exact(graph, turns, [cable], separation, 1.0, time_limit=60, upper_bound=0.99 * best)
    assert below.status == "infeasible" and not below.routes
    too_heavy = solve_exact(graph, turns, [cable], separation, 1.0, time_limit=60, loads=[1.5])
    assert too_heavy.status == "infeasible"
    fits = solve_exact(graph, turns, [cable], separation, 1.5, time_limit=60, loads=[1.5])
    assert fits.status == "optimal"


def test_exact_study_uses_the_area_capacity_of_the_run(tmp_path, monkeypatch):
    """--exact with --bundle-area-mm2: e) and the MILP get the bundle area and cable cross-sections."""
    import harness_demo.exact as exact_mod
    from harness_demo.exact import ExactResult

    seen = {}

    def fake_solve(graph, turns, cables, separation_fn, capacity, time_limit, loads=None, upper_bound=None):
        seen.update(capacity=capacity, loads=loads, upper_bound=upper_bound, classes=[c.emc_class for c in cables])
        return ExactResult("time limit", {}, float("nan"), float("nan"), 0.0, 0, 0)

    monkeypatch.setattr(exact_mod, "solve_exact", fake_solve)
    args = main.parse_args(["--bundle-area-mm2", "250", "--exact", "5", "--out", str(tmp_path)])
    main.run_exact(SC, args)
    assert seen["capacity"] == pytest.approx(250e-6)
    assert seen["loads"] == pytest.approx([SC.cable_area(c) for c in seen["classes"]])
    assert seen["upper_bound"] is None  # the e) length as an objective bound slows HiGHS down (see exact_study)
    assert "demet alanı 250 mm²" in (tmp_path / "exact.md").read_text(encoding="utf-8")


def test_perturbed_terminals_stay_on_the_skin():
    edge = [CableSpec("A", "power", (0.0, 0.0), (SC.fuselage.length, 180.0))]
    rng = np.random.default_rng(1)
    for _ in range(200):
        (s,) = perturb_specs(edge, rng, length=SC.fuselage.length)
        for x, theta in (s.start_xt, s.end_xt):
            assert 0.0 <= x <= SC.fuselage.length and 0.0 <= theta <= 180.0


def test_capture_fingerprint_follows_page_and_capture_code(tmp_path, monkeypatch):
    page, code = tmp_path / "index.html", tmp_path / "capture.py"
    page.write_text("<html>", encoding="utf-8")
    code.write_text("PLAN_VIEW = 1", encoding="utf-8")
    monkeypatch.setattr(main, "CAPTURE_SOURCE", code)
    first = main.capture_fingerprint(page)
    assert main.capture_fingerprint(page) == first
    code.write_text("PLAN_VIEW = 2", encoding="utf-8")  # e.g. a new camera view: the images must be recaptured
    assert main.capture_fingerprint(page) != first


def test_integrated_is_clean_in_every_robustness_trial(graph, volumes, turns):
    s = CheckSettings(volumes, separation, SC.min_bend_radius, SC.capacity_k, SC.clearance)
    routers = {"e": _published_routers()["e) Bütünleşik"]}
    rows = run_robustness(BenchmarkContext(graph, s, turns), SC.cables, SC.fuselage.radius, routers,
                          main.ROBUSTNESS_TRIALS, main.ROBUSTNESS_SEED)
    assert [(r["all_clean"], r["trials"]) for r in rows] == [(main.ROBUSTNESS_TRIALS, main.ROBUSTNESS_TRIALS)] * 2
