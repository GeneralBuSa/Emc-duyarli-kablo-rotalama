"""Mesh convergence study: do the results depend on the 0.1 m mesh?

The required separations (0.10-0.20 m) are only one or two mesh edges, so the routing graph is
refined (more vertices along the axis and around the arc, same geometry, keep-out volumes,
terminals and parameters) and the baseline and integrated methods are rerun on every mesh. The
independent checks measure each result exactly as in the main run.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from .checks import CheckSettings
from .geometry import FuselageParams, build_fuselage
from .graph import build_routing_graph
from .metrics import Row
from .routing import build_turn_graph, length_lower_bound, route_baseline, route_integrated
from .scenarios import Scenario, build_scenario

FACTORS: tuple[float, ...] = (1.0, 1.5, 2.0)  # mesh refinement factors (1.0 = the published mesh)


def refined(params: FuselageParams, factor: float) -> FuselageParams:
    """Same section with `factor` times as many mesh intervals in both directions."""
    return replace(params, n_axial=round((params.n_axial - 1) * factor) + 1,
                   n_circ=round((params.n_circ - 1) * factor) + 1)


def finest_mesh_vertices(params: FuselageParams, factors: Sequence[float] = FACTORS) -> int:
    """Vertex count of the finest refined mesh (the study's memory and time grow with it)."""
    return max(q.n_axial * q.n_circ for q in (refined(params, f) for f in factors))


def run_convergence(
    scenario: Scenario,
    settings: CheckSettings,
    reuse_factor: float,
    emc_penalty: float,
    factors: Sequence[float] = FACTORS,
    **router_options: Any,
) -> list[Row]:
    """One row per refinement factor: mesh size, baseline and integrated lengths, checks, runtime.

    `router_options` go to the integrated method (e.g. coupling_penalty); the bundle bend rule and the
    area capacity come from `settings`, exactly as in the main run.
    """
    rows = []
    for f in factors:
        p = refined(scenario.fuselage, f)
        mesh = build_fuselage(p)
        graph = build_routing_graph(mesh, scenario.volumes, clearance=settings.clearance)
        cables = build_scenario(graph, p.radius, scenario.cables)
        turns = build_turn_graph(graph, settings.min_bend_radius)

        def check(routes: dict[str, list[int]], graph=graph, cables=cables):
            return settings.check([routes[c.name] for c in cables], [c.emc_class for c in cables], graph.positions)

        base = route_baseline(graph, cables)
        t0 = time.perf_counter()
        integ = route_integrated(graph, cables, scenario.separation, turns, settings.capacity_limit,
                                 reuse_factor=reuse_factor, emc_penalty=emc_penalty, load_fn=settings.load_fn,
                                 diameter_fn=settings.diameter_fn, bundle_bend_factor=settings.bundle_bend_factor,
                                 bundle_diameter_factor=settings.bundle_diameter_factor, **router_options)
        runtime = time.perf_counter() - t0
        report = check(integ.routes)
        rows.append({
            "factor": f, "spacing_m": p.length / (p.n_axial - 1), "nodes": graph.nx_graph.number_of_nodes(),
            "edges": graph.n_edges,
            "baseline_length_m": sum(graph.path_length(r) for r in base.routes.values()),
            "baseline_emc": check(base.routes).emc_points,
            "integrated_length_m": sum(graph.path_length(r) for r in integ.routes.values()),
            "bend_bound_m": length_lower_bound(graph, cables, turns),
            **{f"integrated_{k}": v for k, v in report.as_dict().items()},
            "runtime_s": runtime,
        })
    return rows


def format_convergence_markdown(rows: list[Row]) -> str:
    """Markdown table of the convergence study."""
    out = [
        "| Mesh | Ayrıt aralığı [m] | Düğüm | a) uzunluk [m] | a) EMC [nokta] | e) uzunluk [m] "
        "| e) / bükülme alt sınırı | e) EMC / kapasite / bükülme / yasak / boşluk | e) süre [s] |",
        "|:---|---:|---:|---:|---:|---:|---:|:---:|---:|",
    ]
    for r in rows:
        checks = " / ".join(str(r[f"integrated_{k}"]) for k in
                            ("emc_points", "capacity_edges", "bend_points", "forbidden_points", "clearance_points"))
        out.append(
            f"| {r['factor']:g}× | {r['spacing_m']:.3f} | {r['nodes']} | {r['baseline_length_m']:.2f} "
            f"| {r['baseline_emc']} | {r['integrated_length_m']:.2f} "
            f"| {r['integrated_length_m'] / r['bend_bound_m']:.3f} | {checks} | {r['runtime_s']:.2f} |"
        )
    return "\n".join(out)


def convergence_report(rows: list[Row]) -> str:
    """convergence.md: explanation and table."""
    return (
        "# Mesh yakınsama çalışması\n\n"
        "Aynı geometri, yasak hacimler, uç noktalar ve parametrelerle mesh her iki yönde 1,5 ve 2 kat sıklaştırılır; "
        "baseline ve bütünleşik yöntem yeniden çalıştırılır ve bağımsız denetimlerle ölçülür. "
        "\"e) / bükülme alt sınırı\": e) toplam uzunluğunun, bükülme kısıtlı en kısa yollar toplamına oranı.\n\n"
        + format_convergence_markdown(rows) + "\n"
    )
