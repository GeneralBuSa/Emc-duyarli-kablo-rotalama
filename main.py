"""End-to-end demo: geometry -> graph -> scenario -> 6 routing methods -> checks -> reports -> exports -> plots.

Run `python main.py --help` for the options (e.g. `--no-images` skips PNG/GIF export, which
needs Chrome, and still writes the metrics, the robustness table, the exports and the interactive page).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import trimesh

from harness_demo.benchmark import (
    BenchmarkContext,
    RouterSpec,
    default_workers,
    format_robustness_markdown,
    run_robustness,
)
from harness_demo.checks import COUPLING_FACTOR, CheckReport, CheckSettings, emc_violation_points
from harness_demo.convergence import (
    convergence_report,
    finest_mesh_vertices,
    format_convergence_markdown,
    refined,
)
from harness_demo.convergence import run_convergence as convergence_study
from harness_demo.dxf import export_developed_dxf
from harness_demo.exact import MAX_MESH_VERTICES as MAX_EXACT_MESH_VERTICES
from harness_demo.exact import MESH_FACTOR as EXACT_MESH_FACTOR
from harness_demo.export import export_routes_json, export_wirelist_csv, generate_wirelist_records
from harness_demo.geometry import ForbiddenVolume
from harness_demo.graph import NoRoutableGraphError, RoutingGraph, build_routing_graph
from harness_demo.metrics import Row, compute_metrics, format_console_table, format_markdown_table
from harness_demo.presentation import write_interactive_page
from harness_demo.routing import (
    ORDER_STRATEGIES,
    NoPathError,
    RoutingResult,
    TurnGraph,
    build_turn_graph,
    length_lower_bound,
    route_baseline,
    route_branched,
    route_bundled,
    route_emc_aware,
    route_integrated,
    route_lagrangian,
)
from harness_demo.scenarios import (
    DEFAULT_SCENARIO_FILE,
    MAX_MESH_VERTICES,
    Cable,
    Scenario,
    ScenarioError,
    build_mesh,
    build_scenario,
    load_scenario,
    section_size,
    validate_scenario,
)

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "outputs"
DOCS_DIR = ROOT / "docs"

# The geometry, cables, EMC rules and cost weights come from the scenario file (see `resolve_params`);
# command-line options override the scenario.
LAGRANGE_ITERATIONS = 60
ROBUSTNESS_TRIALS = 20  # trials per robustness family
ROBUSTNESS_SEED = 7

# Check columns of a metrics row; a method is clean when all of them are 0.
_CHECK_KEYS = ("emc_points", "bend_points", "forbidden_points", "clearance_points", "capacity_edges")


@dataclass(frozen=True)
class Model:
    """The problem instance shared by all pipeline steps."""

    scenario: Scenario
    mesh: trimesh.Trimesh
    volumes: list[ForbiddenVolume]
    graph: RoutingGraph
    cables: list[Cable]
    turns: TurnGraph
    settings: CheckSettings
    radius: float  # section size (from the scenario, or from the bounds of a mesh file)
    length: float

    def check(self, res: RoutingResult, cables: list[Cable] | None = None,
              graph: RoutingGraph | None = None) -> CheckReport:
        """Independent checks of one routing result (by default on this model's graph and cables)."""
        cables, graph = cables or self.cables, graph or self.graph
        return self.settings.check([res.routes[c.name] for c in cables], [c.emc_class for c in cables],
                                   graph.positions)


class _Tee(io.TextIOBase):
    """Writes to the console and keeps a copy for outputs/run_log.txt."""

    def __init__(self, stream: io.TextIOBase) -> None:
        self.stream, self.buffer_text = stream, io.StringIO()

    def write(self, s: str) -> int:
        self.stream.write(s)
        self.buffer_text.write(s)
        return len(s)

    def flush(self) -> None:
        self.stream.flush()


def _int_in(lo: int, hi: int | None = None):
    """argparse type: an integer in [lo, hi] (hi optional), with a readable error message."""
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"tam sayı olmalı: {text!r}") from None
        if value < lo or (hi is not None and value > hi):
            raise argparse.ArgumentTypeError(
                f"{lo} ile {hi} arasında olmalı" if hi is not None else f"en az {lo} olmalı")
        return value
    return parse


def _float_in(lo: float, hi: float):
    """argparse type: a finite float in [lo, hi]."""
    def parse(text: str) -> float:
        try:
            value = float(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"sayı olmalı: {text!r}") from None
        if not (math.isfinite(value) and lo <= value <= hi):
            raise argparse.ArgumentTypeError(f"{lo} ile {hi} arasında olmalı")
        return value
    return parse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options (numeric options are range checked)."""
    p = argparse.ArgumentParser(description="EMC duyarlı kablo demeti rotalama demosu: tüm çıktıları üretir.")
    p.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO_FILE, metavar="DOSYA",
                   help="senaryo dosyası (TOML): gövde, yasak hacimler, EMC kuralları ve kablolar "
                        "(varsayılan: harness_demo/examples/fuselage_section.toml)")
    p.add_argument("--no-images", action="store_true",
                   help="PNG/GIF üretme (Chrome/kaleido gerekmez); metrikler, sağlamlık tablosu ve sayfa yine yazılır")
    p.add_argument("--force-images", action="store_true",
                   help="rotalar ve görüntüleyici değişmemiş olsa da 3B görselleri yeniden yakala")
    p.add_argument("--out", type=Path, default=OUT_DIR, help="çıktı klasörü (varsayılan: outputs/)")
    p.add_argument("--docs", type=Path, default=DOCS_DIR, help="etkileşimli sayfa klasörü (varsayılan: docs/)")
    p.add_argument("--trials", type=_int_in(0, 10_000), default=ROBUSTNESS_TRIALS,
                   help="sağlamlık testinde her deney ailesi için deneme sayısı; 0 = atla")
    p.add_argument("--workers", type=_int_in(1, 64), default=default_workers(),
                   help="sağlamlık testi için paralel işlem sayısı; 1 = sıralı, sonuç değişmez "
                        "(varsayılan: Linux'ta 4, Windows/macOS'ta 1)")
    p.add_argument("--capacity", type=_int_in(1, 100), default=None,
                   help="ayrıt kapasitesi K [kablo], en az 1 (varsayılan: senaryodaki rules.capacity_k, yoksa 2)")
    p.add_argument("--bundle-area-mm2", type=_float_in(0.0, 1e5), default=0.0,
                   help="0 dışında: kapasite kablo sayısı yerine demet kesit alanıdır [mm²]; d), e), f) yöntemleri "
                        "ve kapasite denetimi kabloların kesit alanlarını toplar")
    p.add_argument("--clearance", type=_float_in(0.0, 0.5), default=None,
                   help="yasak hacim güvenlik payı [m], 0 ile 0.5 arası (varsayılan: rules.clearance_m, yoksa 0.05)")
    p.add_argument("--reuse-factor", type=_float_in(0.01, 1.0), default=None,
                   help="kullanılmış ayrıtın maliyet çarpanı (varsayılan: routing.reuse_factor, yoksa 0.4)")
    p.add_argument("--emc-penalty", type=_float_in(0.0, 1e4), default=None,
                   help="başka sınıfın rotasına yakın ayrıtların ek maliyeti × uzunluk "
                        "(varsayılan: routing.emc_penalty, yoksa 20)")
    p.add_argument("--coupling-penalty", type=_float_in(0.0, 1e4), default=None,
                   help="e) ve f): başka sınıfın rotasına ayrım mesafesinin 2 katından yakın ayrıtların ek maliyeti "
                        "× uzunluk; uzun yan yana koşuları azaltır; 0 = kapalı "
                        "(varsayılan: routing.coupling_penalty, yoksa 0)")
    p.add_argument("--bundle-bend-factor", type=_float_in(0.0, 100.0), default=None,
                   help="demet bükülme kuralı: dönüş yarıçapı >= bu sayı × demet çapı; 0 = kapalı "
                        "(varsayılan: rules.bundle_bend_factor, yoksa 0)")
    p.add_argument("--order", choices=ORDER_STRATEGIES, default="input",
                   help="kablo önceliklendirme stratejisi (varsayılan: input)")
    p.add_argument("--strict", action="store_true",
                   help="e) yöntemi ana senaryoda ya da sağlamlık testinde bir denetimden kalırsa "
                        "çıkış kodu 1 olsun (CI)")
    p.add_argument("--exact", type=_float_in(0.0, 3600.0), default=0.0, metavar="SANİYE",
                   help="küçük bir örnekte (0,2 m mesh, S4 + D2) e)'yi kesin MILP çözümüyle karşılaştır; "
                        "değer çözücü süre sınırıdır, 0 = atla -> exact.md")
    p.add_argument("--convergence", action="store_true",
                   help="mesh yakınsama çalışmasını da çalıştır (1x, 1.5x, 2x mesh) -> convergence.md")
    return p.parse_args(argv)


def _display(path: Path) -> str:
    """Path relative to the repository when possible (keeps local paths out of run_log.txt)."""
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix() + "/"
    except ValueError:
        return path.name + "/"


def _display_file(path: Path | None) -> str:
    """A scenario file relative to the repository when possible (keeps local paths out of run_log.txt)."""
    if path is None:
        return "-"
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def _finite(x: object) -> object:
    """JSON has no Infinity: unknown bounds are written as null."""
    return None if isinstance(x, float) and not math.isfinite(x) else x


class UsageError(RuntimeError):
    """A problem with the inputs that is reported as a message instead of a traceback."""


def main(argv: list[str] | None = None) -> None:
    """Run the full pipeline and write all outputs (console output is also saved to run_log.txt).

    Exits with code 2 on an input problem (invalid scenario, no capacity-feasible solution) and,
    with --strict, with code 1 when the integrated method fails a check.
    """
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tee = _Tee(sys.stdout)  # type: ignore[arg-type]
    failures: list[str] = []
    try:
        with contextlib.redirect_stdout(tee):
            failures = run(args)
    except (UsageError, NoPathError, NoRoutableGraphError) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    (args.out / "run_log.txt").write_text(tee.buffer_text.getvalue(), encoding="utf-8")
    if args.strict and failures:
        print("--strict: " + "; ".join(failures), file=sys.stderr)
        raise SystemExit(1)


def run(args: argparse.Namespace) -> list[str]:
    """The pipeline: build -> route -> check -> report -> export -> benchmark -> present.

    Returns the --strict failures (empty when the integrated method passes every check).
    """
    t_start = time.perf_counter()
    try:
        scenario = load_scenario(args.scenario)
    except ScenarioError as exc:
        raise UsageError(str(exc)) from None
    resolve_params(scenario, args)
    check_study_sizes(scenario, args)
    args.out.mkdir(parents=True, exist_ok=True)
    model = build_model(scenario, args)
    results = route_all(model, args)
    classes = {c.name: c.emc_class for c in model.cables}
    mass_fn = scenario.cable_mass if scenario.has_masses else None
    rows = [compute_metrics(res, model.graph, model.check(res), mass_fn, classes) for res in results]
    report_metrics(model, results, rows, args)
    write_exports(model, results, rows, args)
    failures = []
    integrated = next(r for r in rows if r["method"] == "integrated")
    if any(integrated[k] for k in _CHECK_KEYS):
        failures.append("e) ana senaryoda temiz değil")
    if args.trials > 0:
        bench = run_benchmark(model, args)
        failures += [f"e) sağlamlık testinde {r['all_clean']}/{r['trials']} temiz ({r['family']})"
                     for r in bench if r["method"] == "e) Bütünleşik" and r["all_clean"] < r["trials"]]
    if args.convergence:
        run_convergence(scenario, args)
    if args.exact:
        run_exact(scenario, args)
    write_presentation(model, results, rows, args)
    print(f"\nÇıktılar: {_display(args.out)}, {_display(args.docs)}  "
          f"(toplam süre {time.perf_counter() - t_start:.1f} s)")
    return failures


def resolve_params(scenario: Scenario, args: argparse.Namespace) -> None:
    """Fill the options left unset on the command line from the scenario's [rules] and [routing] tables."""
    for option, value in (("capacity", scenario.capacity_k), ("clearance", scenario.clearance),
                          ("reuse_factor", scenario.reuse_factor), ("emc_penalty", scenario.emc_penalty),
                          ("coupling_penalty", scenario.coupling_penalty),
                          ("bundle_bend_factor", scenario.bundle_bend_factor)):
        if getattr(args, option, None) is None:
            setattr(args, option, value)


def benchmark_routers(args: argparse.Namespace) -> dict[str, RouterSpec]:
    """Methods compared in the robustness benchmark, with this run's cost weights."""
    cost: dict[str, Any] = {"reuse_factor": args.reuse_factor, "emc_penalty": args.emc_penalty}
    turn_aware = {**cost, "coupling_penalty": args.coupling_penalty}
    return {
        "c) tek geçiş": RouterSpec("emc_aware", {**cost, "reroute_rounds": 0}),
        "c) + söküp yeniden rotalama": RouterSpec("emc_aware", cost),
        "e) Bütünleşik": RouterSpec("integrated", turn_aware),
        "f) Dallı demet": RouterSpec("branched", turn_aware),
    }


def check_study_sizes(scenario: Scenario, args: argparse.Namespace) -> None:
    """Refuse --convergence / --exact up front when they cannot run (mesh file) or would be too large."""
    if scenario.mesh_file is not None and (args.convergence or args.exact):
        raise UsageError("--convergence ve --exact yalnızca üretilen yarım silindir gövdeyle çalışır "
                         "(mesh dosyası sıklaştırılamaz)")
    if args.convergence and (n := finest_mesh_vertices(scenario.fuselage)) > MAX_MESH_VERTICES:
        raise UsageError(f"--convergence: en ince mesh {n} köşe olur, en çok {MAX_MESH_VERTICES} olmalı "
                         "(senaryoda n_axial / n_circ küçültülmeli)")
    if args.exact:
        p = refined(scenario.fuselage, EXACT_MESH_FACTOR)
        if p.n_axial * p.n_circ > MAX_EXACT_MESH_VERTICES:
            raise UsageError(f"--exact: kesin çözüm mesh'i {p.n_axial * p.n_circ} köşe olur, en çok "
                             f"{MAX_EXACT_MESH_VERTICES} olmalı (senaryoda n_axial / n_circ küçültülmeli)")


def check_settings(scenario: Scenario, volumes: list[ForbiddenVolume], args: argparse.Namespace) -> CheckSettings:
    """Check settings of a run: area capacity with --bundle-area-mm2, the bundle bend rule when its factor > 0."""
    area = args.bundle_area_mm2 * 1e-6 if args.bundle_area_mm2 else None
    return CheckSettings(volumes, scenario.separation, scenario.min_bend_radius, args.capacity, args.clearance,
                         bundle_area=area, area_fn=scenario.cable_area,
                         bundle_bend_factor=args.bundle_bend_factor,
                         bundle_diameter_factor=scenario.bundle_diameter_factor,
                         diameter_fn=scenario.cable_diameter)


def build_model(scenario: Scenario, args: argparse.Namespace) -> Model:
    """Skin mesh, keep-out volumes, routing graph (with clearance), snapped cables and turn graph."""
    try:
        mesh = build_mesh(scenario)
    except ScenarioError as exc:
        raise UsageError(str(exc)) from None
    radius, length = section_size(scenario, mesh)
    volumes = scenario.volumes
    graph = build_routing_graph(mesh, volumes, clearance=args.clearance)
    cables = build_scenario(graph, radius, scenario.cables)
    issues = validate_scenario(graph, scenario.cables, cables, radius, scenario.separation)
    turns = build_turn_graph(graph, scenario.min_bend_radius)
    settings = check_settings(scenario, volumes, args)
    print(f"Senaryo: {scenario.name} ({_display_file(scenario.source)})")
    removed = len(mesh.vertices) - graph.nx_graph.number_of_nodes()
    source = f"mesh dosyası {_display_file(scenario.mesh_file)}" if scenario.mesh_file else "yarım silindir"
    print(f"Mesh ({source}): {len(mesh.vertices)} köşe, {len(mesh.faces)} üçgen | "
          f"Çizge: {graph.nx_graph.number_of_nodes()} düğüm, {graph.n_edges} ayrıt | "
          f"{removed} düğüm yasak hacimler ve {args.clearance} m güvenlik payı nedeniyle çıkarıldı | "
          f"{len(cables)} kablo")
    for issue in issues:
        print(f"Senaryo {'HATASI' if issue.severity == 'error' else 'uyarısı'}: {issue.message}")
    if any(i.severity == "error" for i in issues):
        raise UsageError("senaryo geçersiz (yukarıdaki hatalara bakın)")
    print(f"Dönüş çizgesi: {turns.n_states} yönlü ayrıt, {len(turns.arc_src)} izinli dönüş "
          f"(bükülme yarıçapı >= {scenario.min_bend_radius} m)")
    if turns.n_states and len(turns.arc_src) < 1.5 * turns.n_states:
        print("Uyarı: dönüş çizgesi çok seyrek (yönlü ayrıt başına 1,5'ten az izinli dönüş); mesh aralığı bükülme "
              "yarıçapına göre fazla küçük ya da mesh fazla düzenli. Daha büyük max_edge_m deneyin.")
    return Model(scenario, mesh, volumes, graph, cables, turns, settings, radius, length)


def _capacity_label(args: argparse.Namespace) -> str:
    return f"{args.bundle_area_mm2:g} mm²" if args.bundle_area_mm2 else f"{args.capacity}"


def route_all(model: Model, args: argparse.Namespace) -> list[RoutingResult]:
    """The six routing methods on the model's scenario."""
    g, cables, separation, s = model.graph, model.cables, model.scenario.separation, model.settings
    turn_aware: dict[str, Any] = {
        "reuse_factor": args.reuse_factor, "emc_penalty": args.emc_penalty, "order_strategy": args.order,
        "load_fn": s.load_fn, "coupling_penalty": args.coupling_penalty, "diameter_fn": s.diameter_fn,
        "bundle_bend_factor": s.bundle_bend_factor, "bundle_diameter_factor": s.bundle_diameter_factor,
    }
    return [
        route_baseline(g, cables),
        route_bundled(g, cables, reuse_factor=args.reuse_factor, order_strategy=args.order),
        route_emc_aware(g, cables, separation, reuse_factor=args.reuse_factor, emc_penalty=args.emc_penalty,
                        order_strategy=args.order),
        route_lagrangian(g, cables, capacity=s.capacity_limit, iterations=LAGRANGE_ITERATIONS, load_fn=s.load_fn,
                         capacity_label=_capacity_label(args)),
        route_integrated(g, cables, separation, model.turns, s.capacity_limit, **turn_aware),
        route_branched(g, cables, separation, model.turns, s.capacity_limit, **turn_aware),
    ]


def report_metrics(model: Model, results: list[RoutingResult], rows: list[Row],
                   args: argparse.Namespace) -> None:
    """Console table, Lagrangian bounds, clearance sanity check; metrics.md and lagrangian_history.json."""
    # The clearance check is only meaningful if it fires when the margin is ignored: route the
    # baseline on a graph built without clearance and count how close it gets to the volumes.
    plain = build_routing_graph(model.mesh, model.volumes)
    plain_cables = build_scenario(plain, model.radius, model.scenario.cables)
    no_margin = model.check(route_baseline(plain, plain_cables), plain_cables, plain).clearance_points

    lagr = next(r for r in results if r.method == "lagrangian")
    last = lagr.history[-1]
    gap = last["upper_bound"] - last["lower_bound"]
    lagr_line = (f"Lagrange: {len(lagr.history)} iterasyon, alt sınır {last['lower_bound']:.4f} m, "
                 f"üst sınır {last['upper_bound']:.4f} m, fark {gap:.4f} m (%{gap / last['upper_bound'] * 100:.3f})")
    bound_line = integrated_bound_line(model, rows, last["lower_bound"])
    print()
    print(format_console_table(rows))
    print("\n" + lagr_line)
    print(bound_line)
    print(f"Güvenlik payı olmadan kurulan çizgede baseline: {no_margin} boşluk payı ihlali noktası.")

    sc = model.scenario
    notes = [
        f"- Kapasite K = {args.capacity} kablo/ayrıt, minimum bükülme yarıçapı = {sc.min_bend_radius} m, "
        f"yasak hacim güvenlik payı = {args.clearance} m; EMC, yasak hacim ve boşluk denetimleri 0.05 m örnekleme ile "
        "(EMC mesafesi diğer rotanın doğru parçalarına kesin olarak ölçülür).",
    ]
    if args.bundle_area_mm2:
        notes.append(f"- Kesit alanı kapasite modu: demet alanı {args.bundle_area_mm2:g} mm²; kablo çapları "
                     + ", ".join(f"{k} {v * 1000:g} mm" for k, v in sc.diameters.items())
                     + ". d), e), f) yöntemleri ve kapasite denetimi kesit alanlarını toplar.")
    if args.bundle_bend_factor:
        notes.append(f"- Demet bükülme kuralı: bir dönüşün yarıçapı en az {args.bundle_bend_factor:g} × demet çapı "
                     f"olmalı (demet çapı = {sc.bundle_diameter_factor:g} × √Σd²); bükülme sütunu bu kuralı da sayar, "
                     "e) ve f) yöntemleri bu kurala göre rotalar.")
    if args.coupling_penalty:
        notes.append(f"- Kuplaj cezası: başka sınıftan bir rotaya ayrım mesafesinin {COUPLING_FACTOR:g} katından "
                     f"yakın ayrıtlar e) ve f)'de ek {args.coupling_penalty:g} × uzunluk öder.")
    if sc.has_masses:
        notes.append("- Kablo kütlesi: sınıf başına kg/m × rota uzunluğu (" + ", ".join(
            f"{k} {v:g} kg/m" for k, v in sc.masses.items()) + "); demet bağları ve kelepçeler dahil değildir.")
    notes += [
        f"- {lagr_line}.",
        f"- {bound_line}",
        f"- Güvenlik payı olmadan kurulan çizgede baseline {no_margin} boşluk payı ihlali noktası üretir "
        "(boşluk denetiminin çalıştığını gösterir).",
    ]
    md = ["# Metrikler", "", format_markdown_table(rows), "", *notes, ""]
    (args.out / "metrics.md").write_text("\n".join(md), encoding="utf-8")
    history = [{k: _finite(v) for k, v in h.items()} for h in lagr.history]
    (args.out / "lagrangian_history.json").write_text(json.dumps(history, indent=1), encoding="utf-8")


def integrated_bound_line(model: Model, rows: list[Row], lagrange_lb: float) -> str:
    """How far the integrated method can at most be from the shortest total length that meets its constraints.

    Two lower bounds hold for any e) solution: the sum of the shortest bend-feasible routes (bend is a
    hard constraint of e) and, when e) respects capacity, the Lagrangian lower bound of the
    capacity-constrained problem. The larger one gives a guaranteed optimality gap for the length.
    """
    row = next(r for r in rows if r["method"] == "integrated")
    bend_lb = length_lower_bound(model.graph, model.cables, model.turns)
    lb, source = bend_lb, "bükülme kısıtlı en kısa yollar toplamı"
    if row["capacity_edges"] == 0 and lagrange_lb > lb:
        lb, source = lagrange_lb, "Lagrange alt sınırı"
    gap = (row["total_length_m"] - lb) / row["total_length_m"] * 100
    return (f"e) için uzunluk alt sınırı {lb:.2f} m ({source}; bükülme sınırı {bend_lb:.2f} m, Lagrange "
            f"{lagrange_lb:.2f} m): e) toplam uzunluğu {row['total_length_m']:.2f} m, en kısa uygun çözüme "
            f"en çok %{gap:.1f} uzak.")


def write_exports(model: Model, results: list[RoutingResult], rows: list[Row],
                  args: argparse.Namespace) -> None:
    """wirelist.csv (integrated method, with per-cable margins), routes.json (all methods, 3D polylines)
    and, for the half-cylinder section, the developed view harness_developed.dxf (integrated method)."""
    sc = model.scenario
    integrated = next(r for r in results if r.method == "integrated")
    mass_fn = sc.cable_mass if sc.has_masses else None
    wirelist = generate_wirelist_records(model.graph, model.cables, integrated.routes, model.settings, mass_fn)
    export_wirelist_csv(wirelist, args.out / "wirelist.csv")
    parameters = {
        "capacity_k": args.capacity, "bundle_area_mm2": args.bundle_area_mm2 or None,
        "min_bend_radius_m": sc.min_bend_radius, "clearance_m": args.clearance,
        "bundle_bend_factor": args.bundle_bend_factor or None,
        "reuse_factor": args.reuse_factor, "emc_penalty": args.emc_penalty,
        "coupling_penalty": args.coupling_penalty or None,
        "order_strategy": args.order,
        "separation_m": {f"{a}-{b}": sc.separation(a, b) for i, a in enumerate(sc.classes) for b in sc.classes[i + 1:]},
    }
    export_routes_json(model.graph, model.cables, results, args.out / "routes.json", rows=rows, parameters=parameters)
    written = "wirelist.csv, routes.json"
    if sc.mesh_file is None:
        export_developed_dxf(model.graph, model.cables, integrated.routes, model.radius, model.length,
                             model.volumes, args.out / "harness_developed.dxf")
        written += ", harness_developed.dxf"
    n_ok = sum(r["status"] == "OK" for r in wirelist)
    margins = [m for r in wirelist if isinstance(m := r["min_emc_margin_m"], float)]
    worst = f"{min(margins) * 100:+.1f} cm" if margins else "—"
    print(f"Kablo listesi (e yöntemi): {n_ok}/{len(wirelist)} kablo tüm paylarla OK, "
          f"en küçük EMC payı {worst} -> {written}")


def run_benchmark(model: Model, args: argparse.Namespace) -> list[Row]:
    """Robustness benchmark (random order, perturbed terminals) -> robustness.md; returns the summary rows."""
    t0 = time.perf_counter()
    ctx = BenchmarkContext(model.graph, model.settings, model.turns)
    bench = run_robustness(ctx, model.scenario.cables, model.radius, benchmark_routers(args),
                           args.trials, ROBUSTNESS_SEED, workers=args.workers, length=model.length)
    table = format_robustness_markdown(bench)
    mode = f"{args.workers} paralel işlem" if args.workers > 1 else "sıralı"
    print(f"\nSağlamlık testi ({args.trials} deneme / deney, {mode}, {time.perf_counter() - t0:.1f} s):\n{table}")
    (args.out / "robustness.md").write_text(
        "# Sağlamlık testi\n\n"
        f"Her deney {args.trials} denemedir (seed = {ROBUSTNESS_SEED}). \"Rastgele sıra\": özgün senaryo, kablolar "
        "rastgele sırayla rotalanır. \"Uç nokta sapması\": ayrıca her uç nokta ±0,05 m eksenel ve ±1,5° çevresel "
        "kaydırılır. Tüm sonuçlar bağımsız denetimlerle ölçülmüştür.\n\n" + table + "\n",
        encoding="utf-8",
    )
    return bench


def run_convergence(scenario: Scenario, args: argparse.Namespace) -> None:
    """Mesh convergence study (1x, 1.5x, 2x mesh) -> convergence.md."""
    resolve_params(scenario, args)
    t0 = time.perf_counter()
    settings = check_settings(scenario, scenario.volumes, args)
    rows = convergence_study(scenario, settings, args.reuse_factor, args.emc_penalty,
                             coupling_penalty=args.coupling_penalty)
    print(f"\nMesh yakınsama çalışması ({time.perf_counter() - t0:.1f} s):\n{format_convergence_markdown(rows)}")
    (args.out / "convergence.md").write_text(convergence_report(rows), encoding="utf-8")


def run_exact(scenario: Scenario, args: argparse.Namespace) -> None:
    """e) against the exact MILP optimum on a small instance (the scenario's exact_cables) -> exact.md."""
    from harness_demo.exact import exact_study

    resolve_params(scenario, args)
    settings = check_settings(scenario, scenario.volumes, args)
    capacity_text = (f"demet alanı {args.bundle_area_mm2:g} mm²" if settings.bundle_area is not None
                     else f"K = {args.capacity}")
    text = exact_study(scenario, settings, args.exact, args.reuse_factor, args.emc_penalty, capacity_text)
    print("\n" + text)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "exact.md").write_text(text, encoding="utf-8")


CAPTURE_SOURCE = ROOT / "harness_demo" / "capture.py"  # camera views, captions and frame layout of the images


def capture_fingerprint(page: Path) -> str:
    """SHA-256 over the page and the capture code: the 3D images change only when one of them changes."""
    h = hashlib.sha256()
    for part in (page, CAPTURE_SOURCE):
        data = part.read_bytes()
        h.update(len(data).to_bytes(8, "little") + data)
    return h.hexdigest()


def write_presentation(model: Model, results: list[RoutingResult], rows: list[Row],
                       args: argparse.Namespace) -> None:
    """Interactive page always; unless --no-images also the 2D charts and the 3D images captured from the page."""
    sc = model.scenario
    classes = [c.emc_class for c in model.cables]
    violations = {}
    for res, row in zip(results, rows):
        pts = emc_violation_points([model.graph.positions[res.routes[c.name]] for c in model.cables], classes,
                                   sc.separation)
        assert len(pts) == row["emc_points"], "violation points must match the reported metric"
        violations[res.method] = pts

    # Per-cable verification margins (same computation as the wirelist), shown in the page's tooltips.
    margins = {
        res.method: {str(r["cable_name"]): {"emc": r["min_emc_margin_m"], "bend": r["min_bend_radius_m"],
                                            "keepout": r["min_keepout_distance_m"],
                                            "parallel": r["near_parallel_length_m"], "status": r["status"]}
                     for r in generate_wirelist_records(model.graph, model.cables, res.routes, model.settings)}
        for res in results
    }
    page = args.docs / "index.html"
    write_interactive_page(
        model.mesh, model.volumes, model.cables, results, rows, violations, page, model.radius,
        model.length, sc.separation, clearance=args.clearance, margins=margins,
        min_bend_radius=sc.min_bend_radius,
    )
    if args.no_images:
        return

    import kaleido

    from harness_demo.capture import capture_readme_images
    from harness_demo.visualize import plot_convergence, plot_metrics_chart

    # The headless browser for the 2D charts starts only now, so it never competes with the timed routing.
    # plotly warns that per-call kaleido options are ignored while a shared server runs; that is intended.
    warnings.filterwarnings("ignore", message="The kopts argument is ignored if using a server")
    kaleido.start_sync_server(n=1, silence_warnings=True)
    try:
        lagr = next(r for r in results if r.method == "lagrangian")
        plot_convergence(lagr.history, args.out / "lagrangian_convergence.png")
        plot_metrics_chart(rows, args.out / "metrics_chart.png")
    finally:
        kaleido.stop_sync_server(silence_warnings=True)
    # The page contains no timings, so an unchanged page and capture code (camera views, captions,
    # frame layout) mean unchanged 3D images: skip the slow capture.
    methods = [r.method for r in results]
    images = [args.out / f"routes_{m}.png" for m in methods] + [
        args.out / name for name in ("comparison.png", "viewer_inside.png", "demo.gif")]
    stamp = args.out / ".capture_fingerprint"
    fingerprint = capture_fingerprint(page)
    if (not args.force_images and all(p.is_file() for p in images) and stamp.is_file()
            and stamp.read_text(encoding="utf-8").strip() == fingerprint):
        print("3B görseller güncel (sayfa değişmedi); yakalama atlandı (--force-images ile zorlanabilir).")
        return
    t0 = time.perf_counter()
    written = capture_readme_images(page, args.out, methods, comparison=("bundled", "integrated"),
                                    animated="integrated")
    stamp.write_text(fingerprint + "\n", encoding="utf-8")
    print(f"3B görseller etkileşimli sayfadan yakalandı ({len(written)} dosya, {time.perf_counter() - t0:.1f} s).")


if __name__ == "__main__":
    main()
