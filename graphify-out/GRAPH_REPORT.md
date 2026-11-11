# Graph Report - Emc-duyarli-kablo-rotalama  (2026-10-29)

## Corpus Check
- 50 files · ~169,332 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 4 file(s) not represented in the graph (top: (none) 2, .css 1, .csv 1)

## Summary
- 727 nodes · 1490 edges · 46 communities (36 shown, 10 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 149 edges (avg confidence: 0.89)
- Token cost: 829,030 input · 0 output

## Community Hubs (Navigation)
- Bağımsız denetimler
- Yayımlanan 3B sayfa (index.html)
- Görüntüleyici sahnesi (viewer.js)
- README görsel yakalama
- README, CI ve çıktı belgeleri
- Dışa aktarım ve denetim ayarları
- Rotalama çekirdeği ve örnekleme
- Sıralı yöntemler ve yönlendirici
- Gövde ve yasak hacim geometrisi
- Etkileşimli sayfa üretimi
- Karşılaştırma görseli (b ve e)
- Güzergâh önbelleği ve kablo sıralama
- Dijkstra ve rotalama API'si
- Senaryo, baseline ve dönüş çizgesi
- Sağlamlık testi yürütücüsü
- Rotalama çizgesi kurulumu
- Metrikler ve tablolar
- c) EMC duyarlı görseli
- 3B sahne inşası (kablo, kelepçe)
- Model kurulumu ve gövde mesh'i
- a) Baseline görseli
- Komut satırı ve hat akışı
- Giriş noktası ve çalışma günlüğü
- b) Demetleme görseli
- Uç nokta sapması ve deneme üretimi
- RoutingGraph veri yapısı
- Sağlamlık testinin testleri
- Oynatma animasyonu ve durum
- Yöntem karşılaştırma grafiği
- e) Bütünleşik görseli
- Kablo seçme ve bilgi kutusu
- 2B grafikler ve sunum adımı
- Rotalama animasyonu (demo.gif)
- d) Lagrange görseli
- İçeriden görünüm görseli
- Lagrange yakınsaması
- Denetim raporu ve Model
- Dönüş çizgesi veri yapısı
- Kamera görünümleri
- Yol gösterimi yardımcısı
- Proje meta verisi

## God Nodes (most connected - your core abstractions)
1. `RoutingGraph` - 43 edges
2. `Cable` - 30 edges
3. `RoutingResult` - 24 edges
4. `separation()` - 23 edges
5. `run_all_checks()` - 18 edges
6. `route_integrated()` - 17 edges
7. `run_robustness()` - 16 edges
8. `generate_wirelist_records()` - 16 edges
9. `route_emc_aware()` - 16 edges
10. `write_interactive_page()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `ctx()` --uses--> `BenchmarkContext`  [INFERRED]
  tests/test_benchmark.py → harness_demo/benchmark.py
- `Model` --uses--> `CheckSettings`  [INFERRED]
  main.py → harness_demo/checks.py
- `ctx()` --uses--> `CheckSettings`  [INFERRED]
  tests/test_benchmark.py → harness_demo/checks.py
- `Model` --uses--> `RoutingGraph`  [INFERRED]
  main.py → harness_demo/graph.py
- `conflicts()` --indirect_call--> `graph()`  [INFERRED]
  harness_demo/routing/sequential.py → tests/conftest.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Five Routing Methods (a-e) compared on the same scenario** — readme_en_method_a_baseline, readme_en_method_b_bundling, readme_en_method_c_emc_aware_bundling, readme_en_method_d_lagrangian_relaxation, readme_en_method_e_integrated [EXTRACTED 1.00]
- **Conflicting Routing Objectives (bundling vs EMC, capacity, bend, keep-out)** — readme_en_bundling_ratio, readme_en_emc_separation, readme_en_edge_capacity, readme_en_min_bend_radius, readme_en_keepout_volumes_clearance [EXTRACTED 1.00]
- **Outputs verified by independent checks** — readme_en_independent_checks, outputs_metrics_metrics_table, outputs_robustness_robustness_table, readme_en_wirelist_export, outputs_run_log_run_log [INFERRED 0.85]
- **Five compared cable routing methods** — docs_index_method_baseline, docs_index_method_bundled, docs_index_method_emc_aware, docs_index_method_lagrangian, docs_index_method_integrated [EXTRACTED 1.00]
- **EMC/bend/keep-out constraint verification** — docs_index_emc_separation_matrix, docs_index_min_bend_radius, docs_index_clearance_envelope, docs_index_verification_margins, docs_index_route_metrics [INFERRED 0.85]
- **Per-method 3D cable scene construction** — docs_index_buildmethod, docs_index_centreline, docs_index_tangents, docs_index_buildclamps, docs_index_buildviolations [EXTRACTED 1.00]
- **Routing quality metrics compared across scenarios** — outputs_comparison_emc_violation_metric, outputs_comparison_capacity_violation_metric, outputs_comparison_bend_violation_metric, outputs_comparison_bundling_score [EXTRACTED 1.00]
- **Forbidden zones avoided by cable routes** — outputs_comparison_yasak_bakim_kapagi, outputs_comparison_yasak_yakit_hatti, outputs_comparison_yasak_hareketli_yuzey_zarfi, outputs_comparison_forbidden_volume [EXTRACTED 1.00]
- **Equipment endpoints connected by routed cables** — outputs_comparison_arka_raf_sol, outputs_comparison_arka_raf_sag, outputs_comparison_on_panel_sol, outputs_comparison_on_panel_sag [INFERRED 0.85]
- **Integrated constraint-aware routing visualization** — outputs_demo_integrated_scenario, outputs_demo_forbidden_volume, outputs_demo_cable_classes, outputs_demo_emc_violation_point, outputs_demo_bundling_metric [INFERRED 0.85]
- **Lagrangian primal-dual bounding scheme** — outputs_lagrangian_convergence_lagrangian_relaxation, outputs_lagrangian_convergence_lower_bound, outputs_lagrangian_convergence_upper_bound, outputs_lagrangian_convergence_duality_gap [INFERRED 0.95]
- **Five routing methods compared on same scenario** — outputs_metrics_chart_baseline_method, outputs_metrics_chart_demetleme_method, outputs_metrics_chart_emc_duyarli_method, outputs_metrics_chart_lagrange_method, outputs_metrics_chart_butunlesik_method [EXTRACTED 1.00]
- **Routing evaluation metrics (bundling, EMC, capacity)** — outputs_metrics_chart_bundling_ratio, outputs_metrics_chart_emc_violation, outputs_metrics_chart_capacity_violation [EXTRACTED 1.00]
- **Baseline routing quality metrics (EMC, capacity, bend, bundling)** — outputs_routes_baseline_emc_violation_points, outputs_routes_baseline_capacity_violations, outputs_routes_baseline_bend_violations, outputs_routes_baseline_bundling_score [EXTRACTED 1.00]
- **Forbidden zones avoided by cable routing** — outputs_routes_baseline_maintenance_hatch, outputs_routes_baseline_fuel_line, outputs_routes_baseline_moving_surface_envelope [EXTRACTED 1.00]
- **Forbidden volumes (yasak hacim) avoided by routes** — outputs_routes_bundled_keepout_maintenance_hatch, outputs_routes_bundled_keepout_fuel_line, outputs_routes_bundled_keepout_moving_surface_envelope [EXTRACTED 1.00]
- **Route quality metrics reported for bundled strategy** — outputs_routes_bundled_emc_violation_metric, outputs_routes_bundled_capacity_overflow_metric, outputs_routes_bundled_bend_count_metric, outputs_routes_bundled_bundling_ratio_metric [EXTRACTED 1.00]
- **Forbidden Volumes Avoided by Routing** — outputs_routes_emc_aware_forbidden_maintenance_hatch, outputs_routes_emc_aware_forbidden_fuel_line, outputs_routes_emc_aware_forbidden_moving_surface [EXTRACTED 1.00]
- **EMC-Segregated Cable Classes** — outputs_routes_emc_aware_power_cable, outputs_routes_emc_aware_signal_cable, outputs_routes_emc_aware_data_cable, outputs_routes_emc_aware_emc_duyarli_demetleme [INFERRED 0.85]
- **Avionics Equipment Endpoints** — outputs_routes_emc_aware_rear_rack_left, outputs_routes_emc_aware_rear_rack_right, outputs_routes_emc_aware_front_panel_left, outputs_routes_emc_aware_front_panel_right [EXTRACTED 1.00]
- **Integrated Cable Routing Problem Setup** — outputs_routes_integrated_fuselage_section_model, outputs_routes_integrated_equipment_endpoints, outputs_routes_integrated_keep_out_zones, outputs_routes_integrated_cable_classes [INFERRED 0.85]
- **EMC-aware cable routing problem setup** — outputs_routes_lagrangian_fuselage_panel, outputs_routes_lagrangian_forbidden_zones, outputs_routes_lagrangian_connector_endpoints, outputs_routes_lagrangian_cable_types [INFERRED 0.85]
- **Integrated routing constraints visualized in 3D viewer** — outputs_viewer_inside_scenario_e_integrated, outputs_viewer_inside_forbidden_volumes, outputs_viewer_inside_fuselage_frame_brackets, outputs_viewer_inside_endpoint_racks_panels, outputs_viewer_inside_route_metrics_hud [INFERRED 0.85]

## Communities (46 total, 10 thin omitted)

### Community 0 - "Bağımsız denetimler"
Cohesion: 0.05
Nodes (30): BoxLike, circumradii(), circumradius(), count_bend_violations(), count_capacity_violations(), count_clearance_violations(), count_emc_violations(), count_forbidden_violations() (+22 more)

### Community 1 - "Yayımlanan 3B sayfa (index.html)"
Cohesion: 0.06
Nodes (38): Kablo Demeti 3B (interactive 3D viewer page), buildClamps(), buildMethod(), buildViolations(), Cable classes: power, signal, data, captionHtml(), centreline(), Keep-out clearance margin (0.05 m) (+30 more)

### Community 2 - "Görüntüleyici sahnesi (viewer.js)"
Cohesion: 0.04
Nodes (43): CABLE_R, cableLabels, CAPTURE, CENTER, chipEls, chips, CLASS_TR, controls (+35 more)

### Community 3 - "README görsel yakalama"
Cohesion: 0.07
Nodes (12): _capture(), capture_readme_images(), _open(), _Page, _save_gif(), _embedded_data(), page(), test_capture_uses_only_existing_viewer_api() (+4 more)

### Community 4 - "README, CI ve çıktı belgeleri"
Cohesion: 0.10
Nodes (31): CI Workflow (GitHub Actions), CI Smoke Run (main.py --no-images --trials 3 --workers 2), Embedded Data JSON Script (__DATA_JSON__), three.js CDN Import Map, window.__viewerErrors Error Collector, Viewer HTML Template, Metrics Table (metrics.md), Robustness Table (robustness.md) (+23 more)

### Community 5 - "Dışa aktarım ve denetim ayarları"
Cohesion: 0.10
Nodes (13): CheckSettings, _bundle_partners(), _compact_json(), export_routes_json(), export_wirelist_csv(), generate_wirelist_records(), _margins(), _round() (+5 more)

### Community 7 - "Sıralı yöntemler ve yönlendirici"
Cohesion: 0.17
Nodes (10): route_with(), RoutingResult, _cable_cost(), route_bundled(), route_emc_aware(), route_integrated(), _route_sequential(), _SequentialConfig (+2 more)

### Community 8 - "Gövde ve yasak hacim geometrisi"
Cohesion: 0.13
Nodes (5): BoxVolume, default_forbidden_volumes(), SphereVolume, test_distance_to_volume(), test_inflated_volume_contains_clearance_neighbourhood()

### Community 9 - "Etkileşimli sayfa üretimi"
Cohesion: 0.14
Nodes (4): assemble_page(), _flat(), _volume_spec(), write_interactive_page()

### Community 10 - "Karşılaştırma görseli (b ve e)"
Cohesion: 0.13
Nodes (19): Routing Scenario Comparison (comparison.png), Connector: Rear Rack Right (Arka raf sag), Connector: Rear Rack Left (Arka raf sol), Bend Radius Violation Metric (bukulme), Bundling Score (demetlenme), Cable Class: Data (veri), Cable Class: Power (guc), Cable Class: Signal (sinyal) (+11 more)

### Community 11 - "Güzergâh önbelleği ve kablo sıralama"
Cohesion: 0.12
Nodes (4): order_cables(), _RouteGeometry, test_published_metrics(), test_order_cables_strategies()

### Community 12 - "Dijkstra ve rotalama API'si"
Cohesion: 0.16
Nodes (6): NoPathError, shortest_path(), _repair_feasible(), route_lagrangian(), _route_one(), turn_aware_path()

### Community 13 - "Senaryo, baseline ve dönüş çizgesi"
Cohesion: 0.16
Nodes (10): route_baseline(), build_turn_graph(), build_scenario(), equipment_location(), cables(), graph(), mesh(), results() (+2 more)

### Community 14 - "Sağlamlık testi yürütücüsü"
Cohesion: 0.18
Nodes (10): BenchmarkContext, _evaluate(), _evaluate_in_worker(), format_robustness_markdown(), _init_worker(), RouterSpec, run_robustness(), _summary() (+2 more)

### Community 15 - "Rotalama çizgesi kurulumu"
Cohesion: 0.15
Nodes (5): mark_forbidden_vertices(), build_routing_graph(), _segment_clips_volume(), test_clearance_removes_more_nodes(), test_edges_do_not_enter_volumes()

### Community 16 - "Metrikler ve tablolar"
Cohesion: 0.17
Nodes (6): _cells(), compute_metrics(), format_console_table(), format_markdown_table(), _finite(), report_metrics()

### Community 17 - "c) EMC duyarlı görseli"
Cohesion: 0.17
Nodes (16): EMC-Aware Bundling Routes (Figure c), Cable Clamps / Harness Support Points, Data Cable Class (veri), EMC-Aware Bundling (EMC duyarli demetleme), EMC Violation Point Marker, Forbidden Zone: Fuel Line (Yakit hatti), Forbidden Zone: Maintenance Hatch (Bakim kapagi), Forbidden Zone: Moving Surface Envelope (Hareketli yuzey zarfi) (+8 more)

### Community 18 - "3B sahne inşası (kablo, kelepçe)"
Cohesion: 0.19
Nodes (15): buildAccessPanel(), buildActuator(), buildClamps(), buildMethod(), buildPipe(), buildViolations(), centreline(), distToPolyline() (+7 more)

### Community 20 - "Model kurulumu ve gövde mesh'i"
Cohesion: 0.18
Nodes (3): build_fuselage(), FuselageParams, build_model()

### Community 21 - "a) Baseline görseli"
Cohesion: 0.19
Nodes (13): routes_baseline.png - Baseline (independent Dijkstra) 3D route render, Baseline: Independent Dijkstra Routing (bagimsiz Dijkstra), Bend Radius Violations (bukulme) - 0, Bundling Score (demetlenme) - 0.162, Cable Classes: power (guc), signal (sinyal), data (veri), Capacity Violations (kapasite ayrit) - 20, EMC Violation Points (EMC ihlali noktasi) - 980, Equipment Endpoints: Arka raf (sol/sag), On panel (sol/sag) (+5 more)

### Community 22 - "Komut satırı ve hat akışı"
Cohesion: 0.20
Nodes (5): default_workers(), parse_args(), run(), run_benchmark(), write_exports()

### Community 23 - "Giriş noktası ve çalışma günlüğü"
Cohesion: 0.17
Nodes (3): main(), _Tee, test_main_without_images()

### Community 24 - "b) Demetleme görseli"
Cohesion: 0.23
Nodes (12): routes_bundled.png - Bundled Routing 3D Render (b) Demetleme), Bend Count Metric (bukulme), Bundling Ratio Metric (demetlenme), Bundling Strategy (Demetleme), Cable Classes: power / signal / data, Corridor Capacity Overflow Metric (kapasite asimi), EMC Violation Points Metric (EMC ihlali noktasi), Equipment Endpoints (Arka raf sol/sag, On panel sol/sag) (+4 more)

### Community 25 - "Uç nokta sapması ve deneme üretimi"
Cohesion: 0.24
Nodes (3): _trial_cables(), CableSpec, perturb_specs()

### Community 26 - "RoutingGraph veri yapısı"
Cohesion: 0.18
Nodes (3): RoutingGraph, route_conflicts(), conflicts()

### Community 27 - "Sağlamlık testinin testleri"
Cohesion: 0.28
Nodes (5): surface_point(), ctx(), _terminals(), test_perturbation_keeps_terminals_separable(), test_router_specs_cover_every_benchmark_method()

### Community 28 - "Oynatma animasyonu ve durum"
Cohesion: 0.31
Nodes (9): captionHtml(), ease(), finish(), loop(), play(), progress(), renderStats(), select() (+1 more)

### Community 29 - "Yöntem karşılaştırma grafiği"
Cohesion: 0.33
Nodes (9): Method Comparison Chart (metrics_chart.png), a) Baseline routing method, Demetlenme orani (bundling ratio) metric, e) Butunlesik (integrated) routing method, Kapasite ihlali (capacity violation edges) metric, b) Demetleme (bundling) routing method, c) EMC duyarli (EMC-aware) routing method, EMC ihlali (EMC violation points) metric (+1 more)

### Community 30 - "e) Bütünleşik görseli"
Cohesion: 0.25
Nodes (8): Integrated Routing Result Figure (EMC + Capacity + Bending), Cable Clamps / Support Points Along Routes, Cable Classes: Power, Signal, Data, Endpoints: Rear Rack (L/R) and Front Panel (L/R), 3D Fuselage Section with Frames and Stringers, Scenario e) Integrated Routing (EMC + Capacity + Bend Constraints), Keep-Out Volumes: Maintenance Hatch, Fuel Line, Moving Surface Envelope, Integrated Result: 0 EMC Points, 0 Capacity Splits, 0 Bend Violations, Bundling 0.382

### Community 31 - "Kablo seçme ve bilgi kutusu"
Cohesion: 0.25
Nodes (8): camera, clearSelection(), marginHtml(), pick(), selectCable(), sep(), setHover(), updateHover()

### Community 32 - "2B grafikler ve sunum adımı"
Cohesion: 0.29
Nodes (3): plot_convergence(), plot_metrics_chart(), write_presentation()

### Community 33 - "Rotalama animasyonu (demo.gif)"
Cohesion: 0.36
Nodes (8): Demetlenme (bundling) metric, Cable classes: power, signal, data, Demo GIF: 3D EMC-aware cable routing in fuselage, EMC ihlali noktasi (EMC violation point marker), Yasak hacim (forbidden volume / keep-out zone), 3D fuselage frame/stringer tunnel model, Scenario e) Butunlesik (EMC + kapasite + bukulme) integrated routing, Sequential cable routing progress (P1 power, 1/11)

### Community 34 - "d) Lagrange görseli"
Cohesion: 0.36
Nodes (8): Lagrange Gevsetmesi (K=2) Rota Gorseli, Kablo Tipleri (power, signal, data), Uc Noktalar (Arka raf sol/sag, On panel sol/sag), EMC Ihlali Noktasi, Yasak Hacimler (Bakim kapagi, Yakit hatti, Hareketli yuzey zarfi), Govde Paneli 3D Modeli (frame/stringer izgarasi), Lagrange Gevsetmesi (Lagrangian Relaxation, K=2), Rota Metrikleri (EMC, kapasite, bukulme, demetlenme)

### Community 35 - "İçeriden görünüm görseli"
Cohesion: 0.39
Nodes (7): 3D Viewer Inside-Fuselage Screenshot, Cable Type Legend (power/signal/data), Cable Endpoints (rear racks, front panels left/right), Forbidden Volumes (fuel line, moving surface envelope, maintenance hatch), Fuselage Frames with Cable Clamps/Brackets, Route Metrics HUD (EMC 0, capacity 0, bending 0, bundling 0.382), Scenario e) Integrated (EMC + Capacity + Bending)

### Community 36 - "Lagrange yakınsaması"
Cohesion: 0.52
Nodes (7): Lagrangian Relaxation Convergence Chart, Duality Gap (final 0.002%), L(lambda) Iteration Value, Lagrangian Relaxation (Lagrange gevsetmesi), Lower Bound (best dual), Total Cable Length Objective [m], Upper Bound (best feasible solution)

### Community 40 - "Kamera görünümleri"
Cohesion: 0.67
Nodes (3): flyTo(), setToggle(), view()

## Ambiguous Edges - Review These
- `Route Metrics: EMC 0 points, capacity violations 35 edges, bend violations 8, bundling 0.468` → `Cable Clamps / Harness Support Points`  [AMBIGUOUS]
  outputs/routes_emc_aware.png · relation: conceptually_related_to

## Knowledge Gaps
- **84 isolated node(s):** `D`, `CAPTURE`, `CABLE_R`, `CLASS_TR`, `host` (+79 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 305 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Route Metrics: EMC 0 points, capacity violations 35 edges, bend violations 8, bundling 0.468` and `Cable Clamps / Harness Support Points`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `Embedded routing data (#data JSON)` connect `Yayımlanan 3B sayfa (index.html)` to `Etkileşimli sayfa üretimi`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **Why does `RoutingGraph` connect `RoutingGraph veri yapısı` to `Dışa aktarım ve denetim ayarları`, `Rotalama çekirdeği ve örnekleme`, `Sıralı yöntemler ve yönlendirici`, `Denetim raporu ve Model`, `Güzergâh önbelleği ve kablo sıralama`, `Dijkstra ve rotalama API'si`, `Senaryo, baseline ve dönüş çizgesi`, `Sağlamlık testi yürütücüsü`, `Rotalama çizgesi kurulumu`, `Metrikler ve tablolar`, `Ayrıt araması ve CSR`, `Model kurulumu ve gövde mesh'i`, `Uç nokta sapması ve deneme üretimi`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `RoutingGraph` (e.g. with `BenchmarkContext` and `_trial_cables()`) actually correct?**
  _`RoutingGraph` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `Cable` (e.g. with `_evaluate()` and `_evaluate_in_worker()`) actually correct?**
  _`Cable` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `separation()` (e.g. with `build_model()` and `route_all()`) actually correct?**
  _`separation()` has 10 INFERRED edges - model-reasoned connections that need verification._
- **What connects `D`, `CAPTURE`, `CABLE_R` to the rest of the system?**
  _84 weakly-connected nodes found - possible documentation gaps or missing edges._