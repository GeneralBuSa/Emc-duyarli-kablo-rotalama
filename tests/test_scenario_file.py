"""Scenario files: the example loads as the published scenario, and broken files are reported in full."""

from __future__ import annotations

import tomllib

import numpy as np
import pytest

import main
from harness_demo.geometry import BoxVolume, SphereVolume, surface_point
from harness_demo.scenarios import DEFAULT_SCENARIO_FILE, ScenarioError, load_scenario, parse_scenario


def _example() -> dict:
    return tomllib.loads(DEFAULT_SCENARIO_FILE.read_text(encoding="utf-8"))


def test_example_scenario():
    sc = load_scenario()
    assert [c.name for c in sc.cables] == ["P1", "P2", "S1", "S2", "D1", "D2", "S3", "P3", "P4", "D3", "S4"]
    assert sc.cables[0].from_location == "Arka raf (sağ)" and sc.cables[-1].to_location == "Ön panel (sağ)"
    assert (sc.fuselage.radius, sc.fuselage.length, sc.fuselage.n_axial, sc.fuselage.seed) == (2.0, 6.0, 61, 42)
    assert sc.min_bend_radius == 0.10 and sc.exact_cables == ("S4", "D2")
    assert sc.classes == ("power", "signal", "data")
    assert sc.separation("power", "data") == sc.separation("data", "power") == 0.20
    assert sc.separation("signal", "signal") == 0.0
    assert sc.cable_area("power") == pytest.approx(np.pi / 4 * 0.015**2)
    box, sphere, panel = sc.volumes
    assert isinstance(box, BoxVolume) and isinstance(panel, BoxVolume) and isinstance(sphere, SphereVolume)
    assert sphere.center == tuple(surface_point(3.0, 50.0, 2.0)) and sphere.radius == 0.5


def test_problems_are_listed_with_their_location():
    data = _example()
    data["fuselage"]["radius_m"] = -1.0
    data["fuselage"]["n_axial"] = 10.5
    data["classes"]["power"]["diameter_m"] = "15 mm"
    del data["separation_m"]["signal-data"]
    data["keep_out"][0]["hi"] = [1.0, 0.0, 0.0]  # below lo
    data["keep_out"][1]["shape"] = "cone"
    data["cables"][0]["class"] = "fiber"
    data["cables"][1]["from"]["theta"] = 200.0
    data["cables"][2]["name"] = "P2"
    data["cables"][3]["colour"] = "red"
    data["study"]["exact_cables"] = ["S9"]
    with pytest.raises(ScenarioError) as exc:
        parse_scenario(data, "test.toml")
    text = str(exc.value)
    for expected in (
        "fuselage.radius_m: 0'dan büyük",
        "fuselage.n_axial: tam sayı olmalı",
        "classes.power.diameter_m: sayı olmalı",
        "separation_m.signal-data: eksik",
        "keep_out[0]: lo her eksende hi'den küçük olmalı",
        "keep_out[1].shape:",
        "cables[0].class: [classes] altında tanımlı değil ('fiber'",
        "cables[1].from.theta: 0 ile 180 arasında olmalı",
        "'P2' adı birden çok kabloda",
        "cables[3].colour: bilinmeyen alan",
        "study.exact_cables:",
    ):
        assert expected in text, expected
    assert len(exc.value.problems) >= 11


def test_missing_sections_and_bad_files(tmp_path):
    with pytest.raises(ScenarioError, match="fuselage: eksik"):
        parse_scenario({k: v for k, v in _example().items() if k != "fuselage"})
    bad = tmp_path / "bad.toml"
    bad.write_text("[fuselage\nradius_m = 2", encoding="utf-8")
    with pytest.raises(ScenarioError, match="TOML sözdizimi hatası"):
        load_scenario(bad)
    with pytest.raises(ScenarioError, match="okunamadı"):
        load_scenario(tmp_path / "yok.toml")


def test_sphere_needs_exactly_one_centre():
    data = _example()
    data["keep_out"][1]["center"] = [3.0, 1.0, 1.5]
    with pytest.raises(ScenarioError, match="tam olarak biri"):
        parse_scenario(data)
    del data["keep_out"][1]["center_on_surface"]
    assert parse_scenario(data).volumes[1].center == (3.0, 1.0, 1.5)


def test_cli_reports_an_invalid_scenario_file(tmp_path, capsys):
    bad = tmp_path / "senaryo.toml"
    bad.write_text(DEFAULT_SCENARIO_FILE.read_text(encoding="utf-8").replace("radius_m = 2.0", "radius_m = 0"),
                   encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main.main(["--scenario", str(bad), "--no-images", "--out", str(tmp_path / "o"), "--docs", str(tmp_path / "d")])
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "senaryo dosyası geçersiz" in err and "fuselage.radius_m" in err
    assert not (tmp_path / "o").exists()  # nothing is written for an unusable scenario
