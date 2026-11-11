"""Cable scenarios: loading a scenario file, snapping terminals to the graph and validating them.

A scenario is plain data (a TOML file, see `examples/fuselage_section.toml`): the fuselage section,
the keep-out volumes, the EMC classes with their diameters and separations, the bend rule and the
cables. `load_scenario` checks the whole file and reports every problem with its location.
"""

from __future__ import annotations

import math
import tomllib
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
import trimesh.remesh

from .geometry import BoxVolume, ForbiddenVolume, FuselageParams, SphereVolume, build_fuselage, surface_point
from .graph import RoutingGraph

# EMC classes the routers, checks and viewer know (colours, cable thickness, names).
EMC_CLASSES: tuple[str, ...] = ("power", "signal", "data")
VOLUME_PROPS: tuple[str, ...] = ("", "pipe", "actuator", "panel")  # visual models of the 3D viewer
DEFAULT_SCENARIO_FILE = Path(__file__).with_name("examples") / "fuselage_section.toml"

# Size limits of a scenario file: they keep a file from asking for unbounded work or memory.
MAX_FILE_BYTES = 1 << 20
MAX_CABLES = 500
MAX_VOLUMES = 500
MAX_MESH_VERTICES = 200_000  # n_axial * n_circ
MAX_EXACT_CABLES = 6
MAX_LABEL = 64  # cable, equipment and keep-out names
MAX_TEXT = 1000  # scenario description
MAX_MESH_FILE_BYTES = 50 << 20
MESH_SUFFIXES = (".stl", ".obj", ".ply")

# Run defaults when the scenario file does not set them (representative values, see README).
DEFAULT_CAPACITY_K = 2  # max cables per edge (deliberately tight so the constraint is active)
DEFAULT_CLEARANCE_M = 0.05  # minimum distance between a route and a keep-out volume
DEFAULT_REUSE_FACTOR = 0.4  # cost multiplier on already used edges
DEFAULT_EMC_PENALTY = 20.0  # extra cost (x edge length) near routes of other EMC classes
# Extra cost (x edge length) within twice the separation of foreign routes (methods e and f). Off by default:
# it improves the example scenario but lengthens e) on a finer mesh, in other cable orders and on the exact
# instance (README, "Demet bükülme kuralı ve kuplaj cezası").
DEFAULT_COUPLING_PENALTY = 0.0
# Bundle diameter ~ 1.25 x sqrt(sum of squared cable diameters): round cables never pack densely.
DEFAULT_BUNDLE_DIAMETER_FACTOR = 1.25


def unsafe_text(value: str) -> bool:
    """Control, format (bidi) and line/paragraph separator characters: they could forge console, log,
    CSV or markdown lines, so scenario texts must not contain them."""
    return any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in value)


def _shown(key: str) -> str:
    """A TOML key for a problem message (quoted keys may hold any character)."""
    return repr(key) if unsafe_text(key) else key


@dataclass(frozen=True)
class CableSpec:
    """Cable definition: each terminal in surface coordinates (x [m], theta [deg]) on the half-cylinder
    section, or as a 3D point [m] (`start_xyz`/`end_xyz`, required with a mesh file)."""

    name: str
    emc_class: str
    start_xt: tuple[float, float] | None
    end_xt: tuple[float, float] | None
    from_location: str = ""  # equipment the start terminal belongs to
    to_location: str = ""
    start_xyz: tuple[float, float, float] | None = None
    end_xyz: tuple[float, float, float] | None = None

    def points(self, radius: float) -> tuple[np.ndarray, np.ndarray]:
        """3D positions of both terminals (surface coordinates are placed on a cylinder of `radius`)."""
        return (_terminal_point(self.start_xt, self.start_xyz, radius),
                _terminal_point(self.end_xt, self.end_xyz, radius))


def _terminal_point(xt: tuple[float, float] | None, xyz: tuple[float, float, float] | None,
                    radius: float) -> np.ndarray:
    if xyz is not None:
        return np.asarray(xyz, dtype=float)
    if xt is None:
        raise ValueError("a terminal needs surface coordinates or a 3D point")
    return surface_point(*xt, radius)


@dataclass(frozen=True)
class Cable:
    """Cable snapped to routing-graph nodes."""

    name: str
    emc_class: str
    start: int
    end: int
    from_location: str = ""
    to_location: str = ""


@dataclass(frozen=True)
class Scenario:
    """One routing problem: geometry, keep-out volumes, EMC rules and cables."""

    name: str
    description: str
    fuselage: FuselageParams
    volumes: list[ForbiddenVolume]
    cables: list[CableSpec]
    diameters: dict[str, float]  # outer cable diameter [m] per EMC class
    separations: dict[frozenset[str], float]  # required separation [m] per pair of different classes
    min_bend_radius: float
    exact_cables: tuple[str, ...] = ()  # cables of the small exact (MILP) comparison
    # [rules]: run defaults (the command line may override capacity and clearance).
    capacity_k: int = DEFAULT_CAPACITY_K
    clearance: float = DEFAULT_CLEARANCE_M
    bundle_bend_factor: float = 0.0  # > 0: a bend must have radius >= factor x bundle diameter
    bundle_diameter_factor: float = DEFAULT_BUNDLE_DIAMETER_FACTOR  # bundle diameter = factor x sqrt(sum d^2)
    # [routing]: cost weights of the heuristic methods.
    reuse_factor: float = DEFAULT_REUSE_FACTOR
    emc_penalty: float = DEFAULT_EMC_PENALTY
    coupling_penalty: float = DEFAULT_COUPLING_PENALTY  # extra cost (x edge length) in the coupling zone
    masses: dict[str, float] = field(default_factory=dict)  # cable mass [kg/m] per EMC class (optional)
    # A skin mesh from a file instead of the generated half-cylinder (see `build_mesh`).
    mesh_file: Path | None = None
    mesh_scale: float = 1.0
    mesh_max_edge: float = 0.0  # > 0: triangles are subdivided until no edge is longer [m]
    source: Path | None = field(default=None, compare=False)

    @property
    def classes(self) -> tuple[str, ...]:
        """The scenario's EMC classes in the standard order."""
        return tuple(c for c in EMC_CLASSES if c in self.diameters)

    @property
    def has_masses(self) -> bool:
        """True when every class has a mass per metre (the mass metric is reported then)."""
        return bool(self.diameters) and all(c in self.masses for c in self.diameters)

    def separation(self, class_a: str, class_b: str) -> float:
        """Required separation between two EMC classes (0 for the same class)."""
        return 0.0 if class_a == class_b else self.separations[frozenset((class_a, class_b))]

    def cable_area(self, emc_class: str) -> float:
        """Cross-section [m^2] of one cable of this class (area capacity mode)."""
        return float(np.pi / 4.0 * self.diameters[emc_class] ** 2)

    def cable_diameter(self, emc_class: str) -> float:
        """Outer diameter [m] of one cable of this class."""
        return self.diameters[emc_class]

    def cable_mass(self, emc_class: str) -> float:
        """Mass [kg/m] of one cable of this class (has_masses must be True)."""
        return self.masses[emc_class]


class ScenarioError(ValueError):
    """The scenario file cannot be used; `problems` lists every finding with its location."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{source}: senaryo dosyası geçersiz\n" + "\n".join(f"  - {p}" for p in problems))


_REQUIRED = object()


class _Table:
    """Typed access to one TOML table that records every problem instead of stopping at the first."""

    def __init__(self, data: object, where: str, problems: list[str], silent: bool = False) -> None:
        self.where, self.problems = where, problems
        self.silent = silent  # the table itself is missing (already reported): no "missing" for its fields
        if not isinstance(data, dict):
            problems.append(f"{where}: tablo olmalı")
            data = {}
        self.data: dict[str, Any] = data

    def _get(self, key: str, default: object) -> Any:
        if key in self.data:
            return self.data[key]
        if default is _REQUIRED and not self.silent:
            self.problems.append(f"{self.at(key)}: eksik")
        return None if default is _REQUIRED else default

    def at(self, key: str) -> str:
        return f"{self.where}.{_shown(key)}" if self.where else _shown(key)

    def only(self, *allowed: str) -> None:
        """Report keys that are not part of the format (usually typos)."""
        for key in self.data:
            if key not in allowed:
                self.problems.append(f"{self.at(key)}: bilinmeyen alan (beklenen: {', '.join(allowed)})")

    def number(self, key: str, lo: float, hi: float, default: object = _REQUIRED, *,
               integer: bool = False, above: bool = False) -> Any:
        """A number in [lo, hi] ((lo, hi] with `above`); integers only with `integer`."""
        if key not in self.data:
            return self._get(key, default)
        value = self.data[key]
        ok_type = isinstance(value, int) if integer else isinstance(value, int | float)
        if isinstance(value, bool) or not ok_type:
            self.problems.append(f"{self.at(key)}: {'tam sayı' if integer else 'sayı'} olmalı ({value!r})")
            return None
        if not math.isfinite(value) or value < lo or value > hi or (above and value == lo):
            bound = f"{lo:g}'dan büyük ve en çok {hi:g}" if above else f"{lo:g} ile {hi:g} arasında"
            self.problems.append(f"{self.at(key)}: {bound} olmalı ({value!r})")
            return None
        return value if integer else float(value)

    def text(self, key: str, default: object = _REQUIRED, choices: tuple[str, ...] = (),
             max_len: int = MAX_LABEL) -> Any:
        """A single-line text of at most `max_len` characters without control characters."""
        if key not in self.data:
            return self._get(key, default)
        value = self.data[key]
        if not isinstance(value, str):
            self.problems.append(f"{self.at(key)}: metin olmalı ({value!r})")
            return None
        if unsafe_text(value):
            self.problems.append(f"{self.at(key)}: denetim karakteri, yeni satır ya da yön işareti içermemeli "
                                 f"({value!r})")
            return None
        if len(value) > max_len:
            self.problems.append(f"{self.at(key)}: en çok {max_len} karakter olmalı ({len(value)} karakter)")
            return None
        if choices and value not in choices:
            self.problems.append(f"{self.at(key)}: {', '.join(repr(c) for c in choices)} değerlerinden biri olmalı "
                                 f"({value!r})")
            return None
        return value

    def table(self, key: str, default: object = _REQUIRED) -> _Table:
        return _Table(self._get(key, default) or {}, self.at(key), self.problems, silent=key not in self.data)

    def tables(self, key: str, limit: int) -> list[_Table]:
        """An array of at most `limit` tables ([[key]] in TOML)."""
        value = self._get(key, _REQUIRED)
        if value is None:
            return []
        if not isinstance(value, list):
            self.problems.append(f"{self.at(key)}: [[{key}]] tablo dizisi olmalı")
            return []
        if len(value) > limit:
            self.problems.append(f"{self.at(key)}: en çok {limit} tablo olmalı ({len(value)})")
            return []
        return [_Table(v, f"{self.at(key)}[{i}]", self.problems) for i, v in enumerate(value)]

    def vector(self, key: str) -> tuple[float, float, float] | None:
        value = self._get(key, _REQUIRED)
        if value is None:
            return None
        if (not isinstance(value, list) or len(value) != 3
                or not all(isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
            self.problems.append(f"{self.at(key)}: üç sayılık dizi olmalı, ör. [1.0, 0.0, 2.0] ({value!r})")
            return None
        return (float(value[0]), float(value[1]), float(value[2]))


def _surface_xt(t: _Table, length: float) -> tuple[float, float] | None:
    """A point on the skin: x along the axis [m], theta around the arc [deg]."""
    x, theta = t.number("x", 0.0, length), t.number("theta", 0.0, 180.0)
    return None if x is None or theta is None else (x, theta)


def _volume(t: _Table, fuselage: FuselageParams, mesh_mode: bool = False) -> ForbiddenVolume | None:
    shape = t.text("shape", choices=("box", "sphere"))
    name, prop = t.text("name"), t.text("prop", "", VOLUME_PROPS)
    if shape == "box":
        t.only("name", "shape", "lo", "hi", "prop")
        lo, hi = t.vector("lo"), t.vector("hi")
        if lo is None or hi is None:
            return None
        if not all(a < b for a, b in zip(lo, hi)):
            t.problems.append(f"{t.where}: lo her eksende hi'den küçük olmalı")
            return None
        return BoxVolume(name or "", lo, hi, prop or "")
    if shape == "sphere":
        t.only("name", "shape", "center", "center_on_surface", "radius_m", "prop")
        radius = t.number("radius_m", 0.0, 100.0, above=True)
        if ("center" in t.data) == ("center_on_surface" in t.data):
            t.problems.append(f"{t.where}: center ya da center_on_surface alanlarından tam olarak biri verilmeli")
            return None
        if "center" in t.data:
            center = t.vector("center")
        elif mesh_mode:
            t.problems.append(f"{t.at('center_on_surface')}: mesh dosyasıyla kullanılamaz (center = [x, y, z] verin)")
            return None
        else:
            sub = t.table("center_on_surface")
            sub.only("x", "theta")
            xt = _surface_xt(sub, fuselage.length)
            if xt is None:
                return None
            x, y, z = (float(v) for v in surface_point(*xt, fuselage.radius))
            center = (x, y, z)
        if center is None or radius is None:
            return None
        return SphereVolume(name or "", center, radius, prop or "")
    return None


Terminal = tuple[tuple[float, float] | None, tuple[float, float, float] | None]  # (surface coords, 3D point)


def _terminal(end: _Table, length: float, mesh_mode: bool) -> Terminal | None:
    """One terminal: exactly one of surface coordinates and 3D point is set; None if it cannot be read."""
    end.only("x", "theta", "point", "at")
    if "point" in end.data:
        if "x" in end.data or "theta" in end.data:
            end.problems.append(f"{end.where}: ya x/theta ya da point verilmeli, ikisi birden değil")
            return None
        xyz = end.vector("point")
        return None if xyz is None else (None, xyz)
    if mesh_mode:
        if not end.silent:
            end.problems.append(f"{end.where}: mesh dosyası kullanılırken uç point = [x, y, z] ile verilmeli")
        return None
    xt = _surface_xt(end, length)
    return None if xt is None else (xt, None)


def _cable(t: _Table, classes: list[str], length: float, mesh_mode: bool = False) -> CableSpec | None:
    t.only("name", "class", "from", "to")
    name = t.text("name")
    cls = t.text("class")
    if cls is not None and cls not in classes:
        t.problems.append(f"{t.at('class')}: [classes] altında tanımlı değil ({cls!r}; tanımlı: "
                          f"{', '.join(classes) or 'yok'})")
        cls = None
    ends: list[tuple[Terminal | None, str]] = []
    for key in ("from", "to"):
        end = t.table(key)
        ends.append((_terminal(end, length, mesh_mode), end.text("at", "") or ""))
    (start, start_at), (stop, stop_at) = ends
    if name is None or cls is None or start is None or stop is None:
        return None
    return CableSpec(name, cls, start[0], stop[0], start_at, stop_at, start[1], stop[1])


def _mesh_file(fus: _Table, path: Path | None) -> Path | None:
    """The mesh file of [fuselage]: relative to the scenario file, inside its folder, .stl/.obj/.ply."""
    text = fus.text("mesh_file", max_len=255)
    if text is None:
        return None
    rel = Path(text)
    if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() not in MESH_SUFFIXES:
        fus.problems.append(f"{fus.at('mesh_file')}: senaryo dosyasının klasöründe (ya da alt klasöründe) bir "
                            f"{'/'.join(MESH_SUFFIXES)} dosyası olmalı ({text!r})")
        return None
    return (path.parent if path else Path.cwd()) / rel


def parse_scenario(data: dict[str, Any], source: str = "senaryo", path: Path | None = None) -> Scenario:
    """Scenario from the parsed TOML document; raises `ScenarioError` listing every problem."""
    problems: list[str] = []
    root = _Table(data, "", problems)
    root.only("scenario", "fuselage", "rules", "routing", "classes", "separation_m", "keep_out", "cables", "study")

    meta = root.table("scenario", {})
    meta.only("name", "description")
    name = meta.text("name", path.stem if path else "senaryo", max_len=120)
    description = meta.text("description", "", max_len=MAX_TEXT)

    fus = root.table("fuselage")
    defaults = FuselageParams()
    mesh_mode = "mesh_file" in fus.data
    mesh_file, mesh_scale, mesh_max_edge = None, 1.0, 0.0
    if mesh_mode:
        # A skin mesh from a file: the section size comes from the mesh, terminals are 3D points.
        fus.only("mesh_file", "scale", "max_edge_m")
        mesh_file = _mesh_file(fus, path)
        mesh_scale = fus.number("scale", 0.0, 1000.0, 1.0, above=True) or 1.0
        mesh_max_edge = fus.number("max_edge_m", 0.0, 10.0, 0.0) or 0.0
        fuselage = defaults
    else:
        fus.only("radius_m", "length_m", "n_axial", "n_circ", "jitter", "seed")
        values = {
            "radius": fus.number("radius_m", 0.0, 100.0, above=True),
            "length": fus.number("length_m", 0.0, 1000.0, above=True),
            "n_axial": fus.number("n_axial", 3, 2000, defaults.n_axial, integer=True),
            "n_circ": fus.number("n_circ", 3, 2000, defaults.n_circ, integer=True),
            "jitter": fus.number("jitter", 0.0, 0.45, defaults.jitter),
            "seed": fus.number("seed", 0, 2**32 - 1, defaults.seed, integer=True),
        }
        fuselage = FuselageParams(**{k: v for k, v in values.items() if v is not None})
        if fuselage.n_axial * fuselage.n_circ > MAX_MESH_VERTICES:
            problems.append(f"fuselage: n_axial × n_circ en çok {MAX_MESH_VERTICES} olmalı "
                            f"({fuselage.n_axial} × {fuselage.n_circ})")

    rules = root.table("rules")
    rules.only("min_bend_radius_m", "capacity_k", "clearance_m", "bundle_bend_factor", "bundle_diameter_factor")
    min_bend = rules.number("min_bend_radius_m", 0.0, 10.0)
    run = {
        "capacity_k": rules.number("capacity_k", 1, 100, DEFAULT_CAPACITY_K, integer=True),
        "clearance": rules.number("clearance_m", 0.0, 0.5, DEFAULT_CLEARANCE_M),
        "bundle_bend_factor": rules.number("bundle_bend_factor", 0.0, 100.0, 0.0),
        "bundle_diameter_factor": rules.number("bundle_diameter_factor", 1.0, 3.0, DEFAULT_BUNDLE_DIAMETER_FACTOR),
    }
    routing = root.table("routing", {})
    routing.only("reuse_factor", "emc_penalty", "coupling_penalty")
    run |= {
        "reuse_factor": routing.number("reuse_factor", 0.0, 1.0, DEFAULT_REUSE_FACTOR, above=True),
        "emc_penalty": routing.number("emc_penalty", 0.0, 1e4, DEFAULT_EMC_PENALTY),
        "coupling_penalty": routing.number("coupling_penalty", 0.0, 1e4, DEFAULT_COUPLING_PENALTY),
    }

    cls_table = root.table("classes")
    cls_table.only(*EMC_CLASSES)
    diameters: dict[str, float] = {}
    masses: dict[str, float] = {}
    for cls in EMC_CLASSES:
        if cls in cls_table.data:
            c = cls_table.table(cls)
            c.only("diameter_m", "mass_kg_per_m")
            d = c.number("diameter_m", 0.0, 0.5, above=True)
            if d is not None:
                diameters[cls] = d
            m = c.number("mass_kg_per_m", 0.0, 100.0, None, above=True)
            if m is not None:
                masses[cls] = m
    if "classes" in root.data and not cls_table.data:
        problems.append(f"classes: en az bir EMC sınıfı tanımlanmalı ({', '.join(EMC_CLASSES)})")
    declared_tables = {c: t for c in EMC_CLASSES if isinstance(t := cls_table.data.get(c), dict)}
    with_mass = [c for c, t in declared_tables.items() if "mass_kg_per_m" in t]
    if with_mass and len(with_mass) != len(declared_tables):
        problems.append("classes: mass_kg_per_m ya her sınıfta ya da hiçbirinde verilmeli "
                        f"(verilen: {', '.join(with_mass)})")

    sep_table = root.table("separation_m", {})
    separations: dict[frozenset[str], float] = {}
    for key in sep_table.data:
        pair = key.split("-")
        if len(pair) != 2 or pair[0] == pair[1] or not all(p in cls_table.data for p in pair):
            problems.append(f"{sep_table.at(key)}: 'sınıf-sınıf' biçiminde, tanımlı iki farklı sınıf olmalı")
            continue
        if frozenset(pair) in separations:
            problems.append(f"{sep_table.at(key)}: bu sınıf çifti iki kez verilmiş")
            continue
        value = sep_table.number(key, 0.0, 10.0)
        if value is not None:
            separations[frozenset(pair)] = value
    declared = [c for c in EMC_CLASSES if c in cls_table.data]
    for i, a in enumerate(declared):
        for b in declared[i + 1:]:
            if frozenset((a, b)) not in separations and f"{a}-{b}" not in sep_table.data \
                    and f"{b}-{a}" not in sep_table.data:
                problems.append(f"separation_m.{a}-{b}: eksik (her sınıf çifti için bir ayrım mesafesi gerekir)")

    volumes = [v for t in root.tables("keep_out", MAX_VOLUMES)
               if (v := _volume(t, fuselage, mesh_mode)) is not None] if "keep_out" in root.data else []

    cable_tables = root.tables("cables", MAX_CABLES)
    cables = [s for t in cable_tables if (s := _cable(t, declared, fuselage.length, mesh_mode)) is not None]
    if "cables" in root.data and not root.data["cables"]:
        problems.append("cables: en az bir kablo tanımlanmalı")
    seen: set[str] = set()
    for t in cable_tables:
        if isinstance(name_ := t.data.get("name"), str):
            if name_ in seen:
                problems.append(f"cables: {name_!r} adı birden çok kabloda kullanılmış")
            seen.add(name_)

    study = root.table("study", {})
    study.only("exact_cables")
    exact = study._get("exact_cables", None)
    if exact is None:
        exact = [c.name for c in cables[:2]]
    elif not (isinstance(exact, list) and 0 < len(exact) <= MAX_EXACT_CABLES
              and all(isinstance(n, str) and n in seen for n in exact)):
        problems.append(f"study.exact_cables: tanımlı kablo adlarından oluşan, 1-{MAX_EXACT_CABLES} elemanlı bir "
                        f"dizi olmalı ({exact!r})")
        exact = []

    if problems:
        raise ScenarioError(source, problems)
    assert name is not None and description is not None and min_bend is not None
    assert all(v is not None for v in run.values())
    return Scenario(name, description, fuselage, volumes, cables, diameters, separations, min_bend,
                    tuple(exact), masses=masses, mesh_file=mesh_file, mesh_scale=mesh_scale,
                    mesh_max_edge=mesh_max_edge, source=path, **run)


def load_scenario(path: Path = DEFAULT_SCENARIO_FILE) -> Scenario:
    """Read and check a scenario file (TOML)."""
    try:
        if Path(path).stat().st_size > MAX_FILE_BYTES:
            raise ScenarioError(str(path), [f"dosya en çok {MAX_FILE_BYTES // 1024} KiB olmalı"])
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScenarioError(str(path), [f"okunamadı: {exc.strerror or exc}"]) from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ScenarioError(str(path), [f"TOML sözdizimi hatası: {exc}"]) from None
    return parse_scenario(data, str(path), Path(path))


def build_mesh(scenario: Scenario) -> trimesh.Trimesh:
    """The skin mesh: the generated half-cylinder, or the scenario's mesh file (scaled, optionally subdivided).

    A mesh file is checked like the scenario itself: size, readability and vertex count are reported
    as a ScenarioError.
    """
    if scenario.mesh_file is None:
        return build_fuselage(scenario.fuselage)
    path, where = scenario.mesh_file, str(scenario.source or scenario.mesh_file)
    try:
        if path.stat().st_size > MAX_MESH_FILE_BYTES:
            raise ScenarioError(where, [f"fuselage.mesh_file: en çok {MAX_MESH_FILE_BYTES >> 20} MiB olmalı"])
        mesh = trimesh.load(path, force="mesh", process=True)
    except OSError as exc:
        raise ScenarioError(where, [f"fuselage.mesh_file: okunamadı: {exc.strerror or exc}"]) from None
    except ScenarioError:
        raise
    except Exception as exc:  # any parser error of an untrusted file is an input problem
        raise ScenarioError(where, [f"fuselage.mesh_file: mesh okunamadı ({type(exc).__name__})"]) from None
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ScenarioError(where, ["fuselage.mesh_file: üçgen içeren bir yüzey mesh'i olmalı"])
    vertices, faces = np.asarray(mesh.vertices, dtype=float) * scenario.mesh_scale, np.asarray(mesh.faces)
    if not np.all(np.isfinite(vertices)):
        raise ScenarioError(where, ["fuselage.mesh_file: sonlu olmayan köşe koordinatları var"])
    if scenario.mesh_max_edge > 0.0:
        # Estimate the result first: subdividing a large mesh to a small edge length can explode.
        edges = vertices[faces[:, [1, 2, 0]]] - vertices[faces]
        longest = float(np.linalg.norm(edges, axis=2).max())
        if len(vertices) * (longest / scenario.mesh_max_edge) ** 2 > 4 * MAX_MESH_VERTICES:
            raise ScenarioError(where, [f"fuselage.max_edge_m: bu değerle mesh {MAX_MESH_VERTICES} köşeyi aşar"])
        vertices, faces = trimesh.remesh.subdivide_to_size(vertices, faces, max_edge=scenario.mesh_max_edge)
    mesh = trimesh.Trimesh(vertices, faces, process=True)
    if len(mesh.vertices) > MAX_MESH_VERTICES:
        raise ScenarioError(where, [f"fuselage.mesh_file: en çok {MAX_MESH_VERTICES} köşe olmalı "
                                    f"({len(mesh.vertices)})"])
    return mesh


def section_size(scenario: Scenario, mesh: trimesh.Trimesh) -> tuple[float, float]:
    """(radius, length) of the section: the scenario values, or for a mesh file its distance from the
    x axis and its extent along x (used for the viewer's frame and the terminal perturbation)."""
    if scenario.mesh_file is None:
        return scenario.fuselage.radius, scenario.fuselage.length
    v = np.asarray(mesh.vertices)
    return float(np.hypot(v[:, 1], v[:, 2]).max()), float(v[:, 0].max())


def build_scenario(graph: RoutingGraph, radius: float, specs: list[CableSpec]) -> list[Cable]:
    """Snap each cable terminal to the nearest routable node."""
    cables = []
    for s in specs:
        p, q = s.points(radius)
        cables.append(Cable(s.name, s.emc_class, graph.nearest_node(p), graph.nearest_node(q),
                            s.from_location, s.to_location))
    return cables


MAX_SNAP_M = 0.10  # a terminal further than this from its routable node is reported (about one mesh spacing)


@dataclass(frozen=True)
class ScenarioIssue:
    """One finding of `validate_scenario`; errors make the scenario unusable, warnings are reported."""

    severity: str  # "error" | "warning"
    message: str


def validate_scenario(
    graph: RoutingGraph,
    specs: list[CableSpec],
    cables: list[Cable],
    radius: float,
    separation_fn: Callable[[str, str], float],
    max_snap: float = MAX_SNAP_M,
) -> list[ScenarioIssue]:
    """Check the snapped terminals: snap distance, shared nodes and terminal separation.

    - error  : a cable whose two terminals snap to the same node, or an unknown EMC class,
    - warning: a terminal moved by more than `max_snap` to reach a routable node, a node shared by
      terminals of different cables, or terminals of different classes closer than their required
      separation (no EMC-clean solution can exist then).
    """
    issues: list[ScenarioIssue] = []
    terminals: list[tuple[str, str, str, int]] = []  # (cable, label, class, node)
    for spec, cable in zip(specs, cables):
        if spec.emc_class not in EMC_CLASSES:
            issues.append(ScenarioIssue("error", f"{spec.name}: bilinmeyen EMC sınıfı {spec.emc_class!r}"))
        if cable.start == cable.end:
            issues.append(ScenarioIssue("error", f"{spec.name}: iki uç aynı düğüme düşüyor ({cable.start})"))
        for end, point, node in zip(("başlangıç", "bitiş"), spec.points(radius), (cable.start, cable.end)):
            moved = float(np.linalg.norm(graph.positions[node] - point))
            if moved > max_snap:
                issues.append(ScenarioIssue(
                    "warning", f"{spec.name} {end} ucu en yakın rotalanabilir düğüme {moved:.3f} m taşındı "
                               f"(sınır {max_snap:.2f} m)"))
            terminals.append((spec.name, f"{spec.name} {end}", spec.emc_class, node))
    for i, (cable_a, la, ca, na) in enumerate(terminals):
        for cable_b, lb, cb, nb in terminals[i + 1:]:
            if cable_a == cable_b:
                continue
            if na == nb:
                issues.append(ScenarioIssue("warning", f"{la} ve {lb} aynı düğümü paylaşıyor ({na})"))
            elif ca in EMC_CLASSES and cb in EMC_CLASSES and (sep := separation_fn(ca, cb)) > 0.0:
                d = float(np.linalg.norm(graph.positions[na] - graph.positions[nb]))
                if d < sep:
                    issues.append(ScenarioIssue(
                        "warning", f"{la} ({ca}) ile {lb} ({cb}) arası {d:.3f} m < gereken {sep:.2f} m: "
                                   "EMC açısından temiz bir çözüm olamaz"))
    return issues


def perturb_specs(
    specs: list[CableSpec], rng: np.random.Generator, dx: float = 0.05, dtheta: float = 1.5,
    length: float = np.inf,
) -> list[CableSpec]:
    """Shift every terminal by up to +-dx [m] axially and +-dtheta [deg] around the arc.

    The shifted terminal is kept on the skin: x in [0, length], theta in [0, 180] deg (the same
    ranges a scenario file allows), so a terminal on an edge of the section is never moved off it.
    For the example scenario the bounds are small enough that neighbouring terminals of different
    classes stay further apart than their required separation, so EMC violations remain avoidable.
    """
    def move(xt: tuple[float, float] | None) -> tuple[float, float] | None:
        if xt is None:
            return None
        x = float(np.clip(xt[0] + rng.uniform(-dx, dx), 0.0, length))
        return (x, float(np.clip(xt[1] + rng.uniform(-dtheta, dtheta), 0.0, 180.0)))

    def shift(xyz: tuple[float, float, float] | None) -> tuple[float, float, float] | None:
        # 3D terminals (mesh files) move by up to +-dx in every direction; snapping puts them back on the skin.
        if xyz is None:
            return None
        x, y, z = (float(v + rng.uniform(-dx, dx)) for v in xyz)
        return (x, y, z)

    return [replace(s, start_xt=move(s.start_xt), end_xt=move(s.end_xt),
                    start_xyz=shift(s.start_xyz), end_xyz=shift(s.end_xyz)) for s in specs]
