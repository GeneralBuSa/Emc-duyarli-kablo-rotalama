"""Scenario run parameters, bundle bend rule, coupling penalty, cable mass, area-mode Lagrangian,
branched harness (f), developed-view DXF and skin meshes from a file."""

from __future__ import annotations

import argparse
import math
import re
import tomllib

import numpy as np
import pytest
import trimesh

import main
from harness_demo.checks import (
    CheckSettings,
    bundle_turn_diameters,
    count_bend_violations,
    count_capacity_violations,
    required_bend_radii,
)
from harness_demo.dxf import develop, export_developed_dxf
from harness_demo.export import generate_wirelist_records
from harness_demo.geometry import FuselageParams, build_fuselage, surface_point
from harness_demo.metrics import compute_metrics, format_markdown_table
from harness_demo.routing import (
    NoPathError,
    build_turn_graph,
    cable_corridors,
    class_trees,
    route_branched,
    route_bundled,
    route_integrated,
    route_lagrangian,
    turn_aware_path,
)
from harness_demo.scenarios import (
    DEFAULT_SCENARIO_FILE,
    ScenarioError,
    build_mesh,
    build_scenario,
    load_scenario,
    parse_scenario,
    section_size,
)

SC = load_scenario()
separation = SC.separation


def _example() -> dict:
    return tomllib.loads(DEFAULT_SCENARIO_FILE.read_text(encoding="utf-8"))


def _settings(volumes, **kw) -> CheckSettings:
    return CheckSettings(volumes, separation, SC.min_bend_radius, SC.capacity_k, SC.clearance,
                         area_fn=SC.cable_area, diameter_fn=SC.cable_diameter, **kw)


def _check(s: CheckSettings, res, cables, graph):
    return s.check([res.routes[c.name] for c in cables], [c.emc_class for c in cables], graph.positions)


# ------------------------------------------------------------------ scenario run parameters


def test_rules_and_routing_tables_are_read():
    data = _example()
    data["rules"] |= {"capacity_k": 3, "clearance_m": 0.02, "bundle_bend_factor": 6}
    data["routing"] = {"reuse_factor": 0.5, "emc_penalty": 10.0, "coupling_penalty": 2.0}
    sc = parse_scenario(data)
    assert (sc.capacity_k, sc.clearance, sc.bundle_bend_factor) == (3, 0.02, 6.0)
    assert (sc.reuse_factor, sc.emc_penalty, sc.coupling_penalty) == (0.5, 10.0, 2.0)


def test_command_line_overrides_the_scenario():
    data = _example() | {"rules": {"min_bend_radius_m": 0.1, "capacity_k": 4, "clearance_m": 0.03}}
    data.pop("routing")
    sc = parse_scenario(data)
    args = main.parse_args([])
    main.resolve_params(sc, args)
    assert (args.capacity, args.clearance, args.reuse_factor, args.coupling_penalty) == (4, 0.03, 0.4, 0.0)
    args = main.parse_args(["--capacity", "2", "--clearance", "0.05", "--coupling-penalty", "1.5"])
    main.resolve_params(sc, args)
    assert (args.capacity, args.clearance, args.coupling_penalty) == (2, 0.05, 1.5)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["rules"].__setitem__("capacity_k", 0), "rules.capacity_k"),
    (lambda d: d["rules"].__setitem__("bundle_diameter_factor", 0.5), "rules.bundle_diameter_factor"),
    (lambda d: d.__setitem__("routing", {"reuse_factor": 0}), "routing.reuse_factor"),
    (lambda d: d.__setitem__("routing", {"penalty": 1}), "routing.penalty: bilinmeyen alan"),
    (lambda d: d["classes"]["data"].pop("mass_kg_per_m"), "mass_kg_per_m ya her sınıfta"),
])
def test_invalid_run_parameters_are_reported(change, message):
    data = _example()
    change(data)
    with pytest.raises(ScenarioError, match=re.escape(message)):
        parse_scenario(data)


# ------------------------------------------------------------------ bundle bend rule


def test_bundle_diameter_at_a_shared_turn():
    paths = [[0, 1, 2, 3], [5, 1, 2, 4], [2, 1, 0]]  # turn 0-1-2 shared by cables 0 and 2 (reverse direction)
    d = bundle_turn_diameters(paths, [0.01, 0.02, 0.03], 1.25)
    assert d[0][0] == pytest.approx(1.25 * math.hypot(0.01, 0.03))  # cables 0 and 2 bend together at 1
    assert d[0][1] == pytest.approx(1.25 * 0.01)  # turn 1-2-3: only cable 0
    assert d[1][0] == pytest.approx(1.25 * 0.02)
    req = required_bend_radii(paths, [0.01, 0.02, 0.03], 0.1, 6.0, 1.25)
    assert req[0][1] == pytest.approx(0.1) and req[0][0] == pytest.approx(6 * 1.25 * math.hypot(0.01, 0.03))


def test_bend_check_uses_per_vertex_limits():
    quarter = np.array([[0.0, 0.0, 0.0], [0.2, 0.0, 0.0], [0.2, 0.2, 0.0]])  # radius 0.1414
    assert count_bend_violations([quarter], 0.1) == 0
    assert count_bend_violations([quarter], 0.1, [np.array([0.2])]) == 1


def test_integrated_and_branched_follow_the_bundle_bend_rule(graph, cables, turns, volumes):
    s = _settings(volumes, bundle_bend_factor=6.0)
    rule = {"diameter_fn": s.diameter_fn, "bundle_bend_factor": 6.0}
    assert _check(s, route_bundled(graph, cables, reuse_factor=SC.reuse_factor), cables, graph).bend_points > 15
    for router in (route_integrated, route_branched):
        res = router(graph, cables, separation, turns, SC.capacity_k, reuse_factor=SC.reuse_factor,
                     emc_penalty=SC.emc_penalty, **rule)
        assert not any(_check(s, res, cables, graph).as_dict().values()), router.__name__


def test_wirelist_status_follows_the_bundle_limit(graph, cables, results, volumes):
    s = _settings(volumes, bundle_bend_factor=6.0)
    bundled = generate_wirelist_records(graph, cables, results["bundled"].routes, s)
    assert any(r["status"] == "CHECK" for r in bundled)
    assert all(r["bend_limit_m"] >= SC.min_bend_radius - 1e-9 for r in bundled)


# ------------------------------------------------------------------ coupling penalty, mass, Lagrangian


def test_coupling_penalty_shortens_side_by_side_runs(graph, cables, turns, volumes):
    s = _settings(volumes)
    near = []
    for penalty in (0.0, 1.0):  # off (default), on
        res = route_integrated(graph, cables, separation, turns, SC.capacity_k, reuse_factor=SC.reuse_factor,
                               emc_penalty=SC.emc_penalty, coupling_penalty=penalty)
        assert not any(_check(s, res, cables, graph).as_dict().values())
        near.append(sum(r["near_parallel_length_m"] for r in generate_wirelist_records(graph, cables, res.routes, s)))
    assert near[0] == pytest.approx(9.276, abs=0.01) and near[1] == pytest.approx(0.247, abs=0.01)


def test_cable_mass_metric_and_column(graph, cables, results, metrics):
    classes = {c.name: c.emc_class for c in cables}
    row = compute_metrics(results["integrated"], graph, _check(_settings([]), results["integrated"], cables, graph),
                          SC.cable_mass, classes)
    expected = sum(graph.path_length(results["integrated"].routes[c.name]) * SC.masses[c.emc_class] for c in cables)
    assert row["mass_kg"] == pytest.approx(expected)
    assert "Kablo kütlesi" in format_markdown_table([row])
    assert "Kablo kütlesi" not in format_markdown_table([metrics["integrated"]])  # no mass, no column
    records = generate_wirelist_records(graph, cables, results["integrated"].routes, mass_fn=SC.cable_mass)
    assert sum(r["mass_kg"] for r in records) == pytest.approx(expected, abs=1e-3)


def test_lagrangian_area_mode(graph, cables, volumes):
    s = _settings(volumes, bundle_area=250e-6)
    res = route_lagrangian(graph, cables, capacity=s.capacity_limit, iterations=20, load_fn=s.load_fn,
                           capacity_label="250 mm²")
    assert res.label == "d) Lagrange gevşetmesi (K=250 mm²)"
    assert _check(s, res, cables, graph).capacity_edges == 0
    loads = [SC.cable_area(c.emc_class) for c in cables]
    assert count_capacity_violations([res.routes[c.name] for c in cables], 250e-6, loads) == 0
    last = res.history[-1]
    assert last["lower_bound"] <= last["upper_bound"] + 1e-9


# ------------------------------------------------------------------ branched harness (f)


def test_branched_corridors_avoid_detours(graph, cables):
    trees = class_trees(graph, cables, separation)
    assert set(trees) == {"power", "signal", "data"}
    for cls, tree in trees.items():
        terminals = {n for c in cables if c.emc_class == cls for n in (c.start, c.end)}
        assert terminals <= set(tree.nodes) and len(tree.edges) == len(tree.nodes) - 1  # a tree over the terminals
    corridors = cable_corridors(graph, cables, trees, max_detour=1.25)
    assert corridors, "some cables must follow their class tree"
    adj = graph.csr(graph.lengths)
    from harness_demo.routing import shortest_path
    for c in cables:
        if c.name in corridors:
            assert graph.lengths[corridors[c.name]].sum() <= 1.25 * graph.path_length(
                shortest_path(adj, c.start, c.end)) + 1e-9


def test_branched_method_is_clean(metrics):
    row = metrics["branched"]
    assert all(row[k] == 0 for k in main._CHECK_KEYS)


# ------------------------------------------------------------------ developed-view DXF


def test_developed_view_preserves_lengths():
    pts = np.array([surface_point(x, t, 2.0) for x, t in ((0.0, 10.0), (1.0, 40.0), (3.0, 150.0))])
    flat = develop(pts, 2.0)
    assert flat[0] == pytest.approx([0.0, 2.0 * np.deg2rad(10.0)])
    # A straight line in the developed view is a helix on the skin of the same length.
    helix = np.array([surface_point(x, 10.0 + 30.0 * x, 2.0) for x in np.linspace(0, 1, 2001)])
    assert np.linalg.norm(np.diff(helix, axis=0), axis=1).sum() == pytest.approx(
        np.linalg.norm(develop(helix[-1], 2.0) - develop(helix[0], 2.0)), rel=1e-6)


def _dxf_entities(text: str) -> list[tuple[str, dict[int, list[str]]]]:
    lines = text.splitlines()
    pairs = list(zip(lines[0::2], lines[1::2]))
    start = pairs.index(("2", "ENTITIES"))
    out: list[tuple[str, dict[int, list[str]]]] = []
    for code, value in pairs[start + 1:]:
        if code == "0":
            out.append((value, {}))
        elif out:
            out[-1][1].setdefault(int(code), []).append(value)
    return out


def test_dxf_export(tmp_path, graph, cables, results, volumes):
    path = tmp_path / "harness.dxf"
    routes = results["integrated"].routes
    export_developed_dxf(graph, cables, routes, SC.fuselage.radius, SC.fuselage.length, volumes, path)
    text = path.read_text(encoding="ascii")
    assert text.startswith("0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n") and text.endswith("0\nEOF\n")
    ents = _dxf_entities(text)
    # One polyline per cable on its class layer, whose vertices give back the route length.
    polylines: list[list[tuple[float, float]]] = []
    layers = []
    for kind, tags in ents:
        if kind == "POLYLINE":
            polylines.append([])
            layers.append(tags[8][0])
        elif kind == "VERTEX":
            polylines[-1].append((float(tags[10][0]), float(tags[20][0])))
    cable_lines = [(lay, p) for lay, p in zip(layers, polylines) if lay in ("POWER", "SIGNAL", "DATA")]
    assert [lay for lay, _ in cable_lines] == [c.emc_class.upper() for c in cables]
    for (_, pts), c in zip(cable_lines, cables):
        flat = np.linalg.norm(np.diff(np.array(pts), axis=0), axis=1).sum() / 1000.0
        assert flat == pytest.approx(graph.path_length(routes[c.name]), rel=2e-3)
    assert layers.count("KEEPOUT") == len(volumes) and layers.count("OUTLINE") == 1
    assert sum(kind == "TEXT" for kind, _ in ents) == len(cables)


def test_dxf_escapes_non_ascii_names(tmp_path, graph, cables, results):
    renamed = [c.__class__(**{**c.__dict__, "name": "Güç-1" if i == 0 else c.name}) for i, c in enumerate(cables)]
    routes = {("Güç-1" if k == cables[0].name else k): v for k, v in results["integrated"].routes.items()}
    path = tmp_path / "h.dxf"
    export_developed_dxf(graph, renamed, routes, SC.fuselage.radius, SC.fuselage.length, [], path)
    assert "G\\U+00FC\\U+00E7-1" in path.read_text(encoding="ascii")


# ------------------------------------------------------------------ skin mesh from a file


def _mesh_scenario(mesh_name="skin.stl", **fuselage) -> dict:
    data = _example()
    data["fuselage"] = {"mesh_file": mesh_name, **fuselage}
    data.pop("keep_out")
    for cable in data["cables"]:
        for end in ("from", "to"):
            x, t = cable[end].pop("x"), cable[end].pop("theta")
            cable[end]["point"] = [float(v) for v in surface_point(x, t, 2.0)]
    return data


def test_mesh_file_scenario_routes_cleanly(tmp_path):
    build_fuselage(FuselageParams(n_axial=21, n_circ=22, jitter=0.0)).export(tmp_path / "skin.stl")
    sc = parse_scenario(_mesh_scenario(max_edge_m=0.15), path=tmp_path / "sc.toml")
    mesh = build_mesh(sc)
    radius, length = section_size(sc, mesh)
    assert radius == pytest.approx(2.0, abs=1e-6) and length == pytest.approx(6.0, abs=1e-6)
    assert np.linalg.norm(np.diff(mesh.vertices[mesh.edges_unique], axis=1), axis=2).max() <= 0.15 + 1e-9
    from harness_demo.graph import build_routing_graph
    graph = build_routing_graph(mesh, sc.volumes)
    cables = build_scenario(graph, radius, sc.cables)
    turns = build_turn_graph(graph, sc.min_bend_radius)
    res = route_integrated(graph, cables, sc.separation, turns, 3)
    s = CheckSettings(sc.volumes, sc.separation, sc.min_bend_radius, 3)
    report = s.check([res.routes[c.name] for c in cables], [c.emc_class for c in cables], graph.positions)
    assert report.bend_points == 0 and report.capacity_edges == 0


def test_mesh_file_cli_run(tmp_path):
    build_fuselage(FuselageParams(n_axial=21, n_circ=22, jitter=0.0)).export(tmp_path / "skin.stl")
    data = _mesh_scenario(max_edge_m=0.3)
    data["cables"] = data["cables"][:3]
    data["study"] = {"exact_cables": ["P1"]}
    text = _to_toml(data)
    (tmp_path / "sc.toml").write_text(text, encoding="utf-8")
    out, docs = tmp_path / "out", tmp_path / "docs"
    main.main(["--scenario", str(tmp_path / "sc.toml"), "--no-images", "--trials", "0", "--capacity", "3",
               "--out", str(out), "--docs", str(docs)])
    assert (out / "wirelist.csv").is_file() and (docs / "index.html").is_file()
    assert not (out / "harness_developed.dxf").exists()  # the developed view needs the half-cylinder
    with pytest.raises(SystemExit) as exc:
        main.main(["--scenario", str(tmp_path / "sc.toml"), "--no-images", "--trials", "0", "--convergence",
                   "--out", str(out), "--docs", str(docs)])
    assert exc.value.code == 2


@pytest.mark.parametrize("mesh_name, message", [
    ("../skin.stl", "senaryo dosyasının klasöründe"),
    ("skin.step", "senaryo dosyasının klasöründe"),
    ("missing.stl", "okunamadı"),
])
def test_bad_mesh_files_are_reported(tmp_path, mesh_name, message):
    sub = tmp_path / "sc"
    sub.mkdir()
    try:
        sc = parse_scenario(_mesh_scenario(mesh_name), path=sub / "sc.toml")
    except ScenarioError as exc:
        assert message in str(exc)
        return
    with pytest.raises(ScenarioError, match=message):
        build_mesh(sc)


def test_garbage_mesh_file_is_an_input_error(tmp_path):
    (tmp_path / "skin.stl").write_bytes(b"\x00\x01 not a mesh")
    sc = parse_scenario(_mesh_scenario(), path=tmp_path / "sc.toml")
    with pytest.raises(ScenarioError, match=r"fuselage.mesh_file"):
        build_mesh(sc)


def test_mesh_mode_needs_3d_terminals():
    data = _example()
    data["fuselage"] = {"mesh_file": "skin.stl"}
    with pytest.raises(ScenarioError) as exc:
        parse_scenario(data)
    text = str(exc.value)
    assert "point = [x, y, z] ile verilmeli" in text and "center_on_surface: mesh dosyasıyla kullanılamaz" in text


def test_turn_aware_no_path_message(graph, turns, cables):
    blocked = np.full(graph.n_edges, np.inf)
    with pytest.raises(NoPathError, match="bükülme yarıçapına"):
        turn_aware_path(turns, blocked, cables[0].start, cables[0].end)


def _to_toml(data: dict) -> str:
    """Minimal TOML writer for the scenario dictionaries of these tests."""
    def value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if isinstance(v, list):
            return "[" + ", ".join(value(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{ " + ", ".join(f"{k} = {value(x)}" for k, x in v.items()) + " }"
        return repr(v)

    lines = []
    for key, table in data.items():
        if isinstance(table, list):
            for item in table:
                lines.append(f"[[{key}]]")
                lines += [f"{k} = {value(v)}" for k, v in item.items()]
        else:
            lines.append(f"[{key}]")
            for k, v in table.items():
                if isinstance(v, dict) and key == "classes":
                    lines.append(f"[{key}.{k}]")
                    lines += [f"{kk} = {value(vv)}" for kk, vv in v.items()]
                else:
                    lines.append(f"{k} = {value(v)}")
    return "\n".join(lines) + "\n"


def test_toml_writer_round_trips():
    data = _example()
    assert tomllib.loads(_to_toml(data)) == data


def test_args_namespace_defaults_are_unset():
    args = main.parse_args([])
    assert isinstance(args, argparse.Namespace)
    assert args.capacity is None and args.clearance is None and args.bundle_bend_factor is None


def test_trimesh_is_available():
    assert hasattr(trimesh, "Trimesh")
