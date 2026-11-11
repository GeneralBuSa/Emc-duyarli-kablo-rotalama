"""End-to-end smoke test of `python main.py --no-images` and the generated interactive page."""

from __future__ import annotations

import base64
import hashlib
import json
import re

import numpy as np
import pytest

import main
from harness_demo.scenarios import load_scenario


def test_main_without_images(tmp_path):
    out, docs = tmp_path / "out", tmp_path / "docs"
    main.main(["--no-images", "--trials", "1", "--workers", "1", "--out", str(out), "--docs", str(docs)])

    expected = ("metrics.md", "robustness.md", "lagrangian_history.json", "run_log.txt", "wirelist.csv", "routes.json",
                "harness_developed.dxf")
    for name in expected:
        assert (out / name).is_file(), name
    assert not list(out.glob("*.png")), "--no-images must not export images"

    log = (out / "run_log.txt").read_text(encoding="utf-8")
    assert "e) Bütünleşik" in log and str(tmp_path) not in log  # no local paths in the log
    json.loads((out / "lagrangian_history.json").read_text(encoding="utf-8"))  # strict JSON (no Infinity)

    page = (docs / "index.html").read_text(encoding="utf-8")
    assert "__DATA_JSON__" not in page and "__THREE_BUNDLE__" not in page and "__CSP__" not in page
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert [m["key"] for m in data["methods"]] == ["baseline", "bundled", "emc_aware", "lagrangian", "integrated",
                                                     "branched"]
    assert data["initial"] == "integrated" and data["clearance"] == load_scenario().clearance
    n_vertices = len(_decode(data["mesh"]["v"])) // 3
    assert _decode(data["mesh"]["f"]).max() < n_vertices
    for m in data["methods"]:
        assert sorted(m["order"]) == sorted(m["routes"])
        assert all(0 <= i < n_vertices for path in m["routes"].values() for i in path)
        violations = _decode(m["violations"])
        assert len(violations) % 3 == 0 and (len(violations) == 0) == (m["emc_points"] == 0)
        assert len(violations) <= 3 * m["emc_points"]  # de-duplicated on a 5 mm grid
        assert "runtime_s" not in m  # no timings: identical results give an identical page


def _decode(spec: dict) -> np.ndarray:
    return np.frombuffer(base64.b64decode(spec["b64"]),
                         dtype={"f32": "<f4", "u16": "<u2", "u32": "<u4", "i16": "<i2"}[spec["dtype"]])


def test_page_has_no_network_dependencies_and_a_strict_csp(tmp_path):
    main.main(["--no-images", "--trials", "0", "--out", str(tmp_path / "out"), "--docs", str(tmp_path / "docs")])
    page = (tmp_path / "docs" / "index.html").read_text(encoding="utf-8")
    assert "cdn." not in page and "importmap" not in page and "<script src" not in page
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert "default-src 'none'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    scripts = re.findall(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", page, re.S)
    for body in scripts:  # every executable inline script is allowed by its own hash
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        assert f"'sha256-{digest}'" in csp
    assert len(scripts) == csp.count("'sha256-")


@pytest.mark.parametrize("option", [["--capacity", "0"], ["--clearance", "-1"], ["--trials", "-1"],
                                    ["--workers", "0"], ["--clearance", "nan"], ["--capacity", "abc"]])
def test_invalid_options_are_rejected_with_a_message(option, capsys):
    with pytest.raises(SystemExit) as exc:
        main.parse_args(option)
    assert exc.value.code == 2
    assert "olmalı" in capsys.readouterr().err


def test_strict_mode_exit_code(tmp_path, monkeypatch):
    args = ["--no-images", "--trials", "0", "--strict", "--out", str(tmp_path / "o"), "--docs", str(tmp_path / "d")]
    main.main(args)  # the published configuration passes
    monkeypatch.setattr(main, "run", lambda a: ["e) ana senaryoda temiz değil"])
    with pytest.raises(SystemExit) as exc:
        main.main(args)
    assert exc.value.code == 1


def test_input_errors_exit_cleanly(tmp_path, monkeypatch, capsys):
    def fail(_args):
        raise main.UsageError("senaryo geçersiz")
    monkeypatch.setattr(main, "run", fail)
    with pytest.raises(SystemExit) as exc:
        main.main(["--no-images", "--out", str(tmp_path / "o"), "--docs", str(tmp_path / "d")])
    assert exc.value.code == 2 and "senaryo geçersiz" in capsys.readouterr().err
