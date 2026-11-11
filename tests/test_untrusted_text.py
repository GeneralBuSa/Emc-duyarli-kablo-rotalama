"""Scenario texts are untrusted: names reach the page, the wirelist, the console and the markdown reports.

Regression tests of the security audit (cloudflare/security-audit-skill, run-1): an HTML-like name must
not break the page, an Object.prototype name must not crash the viewer, a formula-like name must not run
in a spreadsheet, and control characters, oversized files and lists are rejected with a scenario error.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import tomllib

import numpy as np
import pytest

import main
from harness_demo.exact import md_escape
from harness_demo.geometry import BoxVolume, FuselageParams, build_fuselage
from harness_demo.graph import NoRoutableGraphError, build_routing_graph
from harness_demo.presentation import _json_block
from harness_demo.scenarios import (
    DEFAULT_SCENARIO_FILE,
    MAX_CABLES,
    MAX_FILE_BYTES,
    CableSpec,
    ScenarioError,
    build_scenario,
    load_scenario,
    parse_scenario,
    validate_scenario,
)

EXAMPLE = DEFAULT_SCENARIO_FILE.read_text(encoding="utf-8")
HOSTILE = {  # (original text in the example file, replacement)
    'name = "P1"': 'name = "=HYPERLINK(1)"',
    'name = "P2"': 'name = "@SUM(1)"',
    'name = "Yakıt hattı"': 'name = "</script><b>x</b> &amp;"',
    'at = "Arka raf (sağ)" }\nto = { x = 5.7, theta = 50.0, at = "Ön panel (sağ)" }':
        'at = "<!--<script>" }\nto = { x = 5.7, theta = 50.0, at = "constructor" }',
    'at = "Arka raf (sağ)" }\nto = { x = 5.6, theta = 53.0, at = "Ön panel (sağ)" }':
        'at = "__proto__" }\nto = { x = 5.6, theta = 53.0, at = "toString" }',
}


def _example() -> dict:
    return tomllib.loads(EXAMPLE)


@pytest.fixture(scope="module")
def hostile_run(tmp_path_factory):
    text = EXAMPLE
    for old, new in HOSTILE.items():
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    tmp = tmp_path_factory.mktemp("hostile")
    (tmp / "s.toml").write_text(text, encoding="utf-8")
    main.main(["--scenario", str(tmp / "s.toml"), "--no-images", "--trials", "0", "--workers", "1",
               "--out", str(tmp / "out"), "--docs", str(tmp / "docs")])
    return tmp


def test_json_block_escapes_html_characters():
    payload = {"name": "<!--<script></script>&>", "n": 1}
    block = _json_block(payload)
    assert not set("<>&") & set(block)
    assert json.loads(block) == payload  # JSON.parse reads the escapes back unchanged


def test_html_like_names_do_not_break_the_page(hostile_run):
    html = (hostile_run / "docs" / "index.html").read_text(encoding="utf-8")
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "<" not in data and "&" not in data
    parsed = json.loads(data)
    assert parsed["cables"][0]["from"] == "<!--<script>" and parsed["cables"][0]["to"] == "constructor"
    assert parsed["volumes"][0]["name"] == "</script><b>x</b> &amp;"
    template = (main.Path(main.__file__).parent / "harness_demo" / "viewer" / "template.html").read_text("utf-8")
    assert html.count("<script") == template.count("<script")  # no element was opened by the data
    assert html.count('http-equiv="Content-Security-Policy"') == 1 and "__CSP__" not in html


def test_formula_like_names_are_quoted_in_the_wirelist(hostile_run):
    with (hostile_run / "out" / "wirelist.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["cable_name"] == "'=HYPERLINK(1)" and rows[1]["cable_name"] == "'@SUM(1)"
    assert rows[0]["from_location"] == "<!--<script>"
    for row in rows:
        for column in ("cable_name", "from_location", "to_location", "bundled_with", "status"):
            assert not row[column].startswith(("=", "+", "-", "@", "\t", "\r")), (column, row[column])
    assert float(rows[0]["length_m"]) > 0  # numbers are written as numbers


def test_markdown_text_is_escaped():
    assert md_escape("S|4 *x* <b>#") == "S\\|4 \\*x\\* \\<b\\>\\#"
    assert md_escape("Arka raf (sağ)") == "Arka raf \\(sağ\\)"


@pytest.mark.parametrize("bad", ["demo\x1b[8m", "a\nb", "a\rb", "a‮b", "a b", "\x00"])
def test_control_characters_are_rejected(bad):
    for setter in (
        lambda d: d["scenario"].__setitem__("name", bad),
        lambda d: d["cables"][0].__setitem__("name", bad),
        lambda d: d["cables"][0]["from"].__setitem__("at", bad),
        lambda d: d["keep_out"][0].__setitem__("name", bad),
    ):
        data = _example()
        setter(data)
        with pytest.raises(ScenarioError, match="denetim karakteri") as exc:
            parse_scenario(data)
        assert "\x1b" not in str(exc.value) and "\n  - " in str(exc.value)  # shown with repr, one line each


def test_problem_messages_show_odd_keys_with_repr():
    data = _example()
    data["fuselage"]["x\x1b[2J"] = 1
    with pytest.raises(ScenarioError) as exc:
        parse_scenario(data)
    assert "\x1b" not in str(exc.value) and "fuselage.'x\\x1b[2J': bilinmeyen alan" in str(exc.value)


def test_size_limits():
    data = _example()
    data["cables"][0]["name"] = "x" * 65
    data["scenario"]["description"] = "y" * 1001
    data["fuselage"]["n_axial"], data["fuselage"]["n_circ"] = 2000, 2000
    data["study"]["exact_cables"] = [c["name"] for c in data["cables"][1:9]]
    with pytest.raises(ScenarioError) as exc:
        parse_scenario(data)
    text = str(exc.value)
    for expected in ("cables[0].name: en çok 64 karakter", "scenario.description: en çok 1000 karakter",
                     "fuselage: n_axial × n_circ en çok", "study.exact_cables:"):
        assert expected in text, expected

    data = _example()
    data["cables"] = data["cables"] * (MAX_CABLES // len(data["cables"]) + 1)
    with pytest.raises(ScenarioError, match=f"cables: en çok {MAX_CABLES} tablo"):
        parse_scenario(data)


def test_oversized_file_is_not_read(tmp_path):
    big = tmp_path / "big.toml"
    big.write_text(EXAMPLE + "#" * MAX_FILE_BYTES, encoding="utf-8")
    with pytest.raises(ScenarioError, match="dosya en çok"):
        load_scenario(big)


def test_terminal_checks_compare_whole_cable_names():
    """Cables 'P 1' and 'P 2' are different cables (the first word used to be compared)."""
    params = FuselageParams(n_axial=21, n_circ=21)
    graph = build_routing_graph(build_fuselage(params), [])
    specs = [CableSpec("P 1", "power", (1.0, 60.0), (5.0, 60.0)), CableSpec("P 2", "power", (1.0, 60.0), (5.0, 90.0))]
    cables = build_scenario(graph, params.radius, specs)
    issues = validate_scenario(graph, specs, cables, params.radius, lambda a, b: 0.0)
    assert any("P 1 başlangıç ve P 2 başlangıç aynı düğümü paylaşıyor" in i.message for i in issues)


def test_keep_out_covering_the_whole_skin_is_a_clean_error(tmp_path, capsys):
    params = FuselageParams(n_axial=11, n_circ=11)
    everything = BoxVolume("her yer", (-10.0, -10.0, -10.0), (20.0, 10.0, 10.0))
    with pytest.raises(NoRoutableGraphError):
        build_routing_graph(build_fuselage(params), [everything])

    text = EXAMPLE.replace("lo = [", "lo = [-10.0, -10.0, -10.0] #", 1)
    text = text.replace("hi = [", "hi = [20.0, 10.0, 10.0] #", 1)
    (tmp_path / "s.toml").write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main.main(["--scenario", str(tmp_path / "s.toml"), "--no-images", "--trials", "0",
                   "--out", str(tmp_path / "o"), "--docs", str(tmp_path / "d")])
    assert exc.value.code == 2 and "rotalanabilir ayrıt" in capsys.readouterr().err


def test_large_coordinates_switch_violation_points_to_int32():
    from harness_demo.presentation import _violation_spec

    assert _violation_spec(np.array([[1.0, 2.0, 3.0]]))["dtype"] == "i16"
    spec = _violation_spec(np.array([[40.0, 0.0, 1.0]]))
    assert spec["dtype"] == "i32"
    import base64
    assert np.frombuffer(base64.b64decode(spec["b64"]), "<i4").tolist() == [40000, 0, 1000]


@pytest.mark.skipif(os.environ.get("HARNESS_BROWSER_TESTS") != "1",
                    reason="set HARNESS_BROWSER_TESTS=1 (needs Chrome)")
def test_hostile_names_load_in_headless_chrome(hostile_run):
    from choreographer import Browser

    from harness_demo.capture import _open, browser_options

    async def run():
        async with Browser(**browser_options()) as browser:
            p = await _open(browser, hostile_run / "docs" / "index.html")
            return await p.js("[window.__viewerErrors, window.__viewerReady === true, "
                              "document.scripts.length, document.querySelectorAll('#cabletable tbody tr').length]")

    errors, ready, n_scripts, n_rows = asyncio.run(run())
    assert errors == [] and ready
    assert n_scripts == 7 and n_rows == 11


def test_study_meshes_are_checked_before_any_work(tmp_path, capsys):
    """--convergence refines the mesh 2x (about 4x the vertices) and --exact builds a MILP: both are capped."""
    text = EXAMPLE.replace("n_axial = 61", "n_axial = 400").replace("n_circ = 64", "n_circ = 400")
    assert text != EXAMPLE
    (tmp_path / "s.toml").write_text(text, encoding="utf-8")
    for option, message in (("--convergence", "--convergence: en ince mesh"), ("--exact=5", "--exact: kesin çözüm")):
        with pytest.raises(SystemExit) as exc:
            main.main(["--scenario", str(tmp_path / "s.toml"), "--no-images", "--trials", "0", option,
                       "--out", str(tmp_path / "o"), "--docs", str(tmp_path / "d")])
        assert exc.value.code == 2 and message in capsys.readouterr().err
        assert not (tmp_path / "o").exists()  # refused before anything is built or written
    from harness_demo.convergence import finest_mesh_vertices

    assert finest_mesh_vertices(load_scenario().fuselage) == 121 * 127  # the example study stays far below
