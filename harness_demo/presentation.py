"""Interactive web page: the three.js viewer with the routing results embedded as JSON.

The viewer source lives in `harness_demo/viewer/` (template.html, viewer.css, viewer.js); it is
assembled here into one self-contained page, so docs/index.html works from GitHub Pages and from
the file system alike. three.js itself is embedded too (`viewer/vendor/three.bundle.min.js`, built
from the pinned npm package), as is the Inter font (`viewer/vendor/fonts/`), so the page loads nothing
from the network and works offline. A strict Content-Security-Policy allows exactly the page's own inline
scripts (by SHA-256), the embedded fonts and nothing else. The interface text is Turkish or English
(`viewer/i18n.json`, applied by `viewer/i18n.js`).
Every number shown on the page is passed in from the pipeline (metrics rows, check results); nothing
is hard-coded. The README's 3D images are rendered from this page (`capture.py`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np
import trimesh

from .geometry import ForbiddenVolume, SphereVolume
from .metrics import Row
from .routing import RoutingResult
from .scenarios import Cable
from .visualize import CLASS_COLORS

VIEWER_DIR = Path(__file__).with_name("viewer")
THREE_BUNDLE = VIEWER_DIR / "vendor" / "three.bundle.min.js"
THREE_VERSION = "0.169.0"  # must match viewer/vendor/package.json
FONT_DIR = VIEWER_DIR / "vendor" / "fonts"
# Inter variable font (OFL): the Latin and Latin Extended subsets cover Turkish and English.
FONTS = (
    ("inter-latin-wght-normal.woff2",
     "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,"
     "U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"),
    ("inter-latin-ext-wght-normal.woff2",
     "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,"
     "U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"),
)
_PLACEHOLDER_RE = re.compile(r"__(STYLE|THREE_BUNDLE|I18N_SCRIPT|SCRIPT|DATA_JSON|I18N_JSON)__")
VIOLATION_GRID_M = 0.005  # violation points closer than this are drawn once (the viewer only shows them)

# Inline <script> elements that execute (the JSON data block does not and needs no hash).
_SCRIPT_RE = re.compile(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", re.S)


def content_security_policy(page: str) -> str:
    """CSP that allows exactly the page's executable inline scripts (SHA-256) and no network access."""
    hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode() + "'"
        for m in _SCRIPT_RE.finditer(page)
    )
    return ("default-src 'none'; script-src " + hashes + "; style-src 'unsafe-inline'; img-src data: blob:; "
            "font-src data:; base-uri 'none'; form-action 'none'")


def font_faces() -> str:
    """@font-face rules with the embedded Inter subsets (base64 woff2)."""
    return "".join(
        "@font-face { font-family: 'Inter Variable'; font-style: normal; font-display: swap; font-weight: 100 900; "
        f"src: url(data:font/woff2;base64,{base64.b64encode((FONT_DIR / name).read_bytes()).decode()}) "
        f"format('woff2-variations'); unicode-range: {ranges}; }}\n"
        for name, ranges in FONTS
    )


_JSON_HTML_ESCAPES = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}
_CSP_META = '<meta http-equiv="Content-Security-Policy" content="__CSP__">'


def _json_block(payload: object) -> str:
    """JSON for an inline <script type="application/json">.

    '<', '>' and '&' are written as \\u escapes (JSON.parse reads them back unchanged), so no text from
    a scenario can close the element or switch the HTML tokenizer into an escaped state ('<!--<script>').
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).translate(_JSON_HTML_ESCAPES)


def assemble_page(data_json: str, vars_: dict[str, object] | None = None) -> str:
    """Viewer template with its stylesheet, fonts, scripts, three.js bundle, texts, data and CSP inlined.

    `vars_` are page-wide values for the interface texts (e.g. {"n": number of cables}).
    """
    read = lambda name: (VIEWER_DIR / name).read_text(encoding="utf-8")  # noqa: E731
    strings = json.loads(read("i18n.json"))
    parts = {
        "STYLE": font_faces() + read("viewer.css"),
        "THREE_BUNDLE": THREE_BUNDLE.read_text(encoding="utf-8"),
        "I18N_SCRIPT": read("i18n.js"),
        "SCRIPT": read("viewer.js"),
        "DATA_JSON": data_json,
        "I18N_JSON": _json_block({"strings": strings, "vars": vars_ or {}}),
    }
    for name in ("THREE_BUNDLE", "I18N_SCRIPT", "SCRIPT"):
        if "</script" in parts[name].lower() or "<!--" in parts[name]:
            raise ValueError("an inlined script must not contain '</script' or '<!--'")
    for name in ("DATA_JSON", "I18N_JSON"):
        if "<" in parts[name]:
            raise ValueError("embedded JSON must escape '<' (use _json_block)")
    # One pass over the template: text inserted for one placeholder is never scanned for another.
    page = _PLACEHOLDER_RE.sub(lambda m: parts[m.group(1)], read("template.html"))
    if page.count(_CSP_META) != 1:
        raise ValueError("the template must hold exactly one CSP meta element")
    return page.replace(_CSP_META, _CSP_META.replace("__CSP__", content_security_policy(page)), 1)


def _b64(a: np.ndarray, dtype: str) -> dict[str, str]:
    """Little-endian typed array as base64 (decoded by the viewer's `decode`)."""
    np_type = {"f32": "<f4", "u16": "<u2", "u32": "<u4", "i16": "<i2", "i32": "<i4"}[dtype]
    return {"dtype": dtype, "b64": base64.b64encode(np.ascontiguousarray(a, dtype=np_type).tobytes()).decode()}


def _violation_spec(points: np.ndarray) -> dict[str, str]:
    """Violation points in millimetres, one per VIOLATION_GRID_M cell, as base64.

    int16 (+-32.7 m) covers the usual sections; larger coordinates switch to int32 instead of wrapping.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    if len(pts):
        _, first = np.unique(np.round(pts / VIOLATION_GRID_M).astype(np.int64), axis=0, return_index=True)
        pts = pts[np.sort(first)]
    mm = np.round(pts * 1000.0)
    return _b64(mm, "i16" if not len(mm) or np.abs(mm).max() <= np.iinfo(np.int16).max else "i32")


def _volume_spec(vol: ForbiddenVolume) -> dict[str, object]:
    """Plain-JSON description of a keep-out volume for the viewer."""
    if isinstance(vol, SphereVolume):
        return {"name": vol.name, "kind": "sphere", "center": list(vol.center), "radius": vol.radius, "prop": vol.prop}
    return {"name": vol.name, "kind": "box", "lo": list(vol.lo), "hi": list(vol.hi), "prop": vol.prop}


def write_interactive_page(
    mesh: trimesh.Trimesh,
    volumes: list[ForbiddenVolume],
    cables: list[Cable],
    results: list[RoutingResult],
    rows: list[Row],
    violations: dict[str, np.ndarray],
    html_path: Path,
    radius: float,
    length: float,
    separation: Callable[[str, str], float],
    clearance: float = 0.0,
    initial: str = "integrated",
    margins: dict[str, dict[str, dict[str, object]]] | None = None,
    min_bend_radius: float = 0.0,
) -> None:
    """Self-contained three.js page (library from CDN) with one tab per routing method.

    Only geometry, node paths, check results and metric rows are embedded (no timings, so the page is
    identical for identical results); all realism
    (structure, clamps, connectors, smoothing, lighting) is added in the viewer and is visual-only.
    """
    pos = np.asarray(mesh.vertices)
    methods = []
    for res, row in zip(results, rows):
        routes = {c.name: [int(n) for n in res.routes[c.name]] for c in cables}
        lengths = {
            name: float(np.linalg.norm(np.diff(pos[path], axis=0), axis=1).sum()) for name, path in routes.items()
        }
        methods.append({
            "key": res.method, "label": res.label, "routes": routes, "lengths": lengths,
            "order": res.order or [c.name for c in cables],
            "violations": _violation_spec(violations.get(res.method, np.empty((0, 3)))),
            "margins": (margins or {}).get(res.method, {}),
            **{k: row[k] for k in ("total_length_m", "unique_length_m", "bundling_ratio", "emc_points",
                                   "bend_points", "forbidden_points", "clearance_points", "capacity_edges")},
        })
    classes = sorted({c.emc_class for c in cables}, key=list(CLASS_COLORS).index)
    data = {
        "radius": radius, "length": length, "clearance": clearance, "minBendRadius": min_bend_radius,
        "mesh": {"v": _b64(pos.ravel(), "f32"),
                 "f": _b64(np.asarray(mesh.faces).ravel(), "u16" if len(pos) < 65536 else "u32")},
        "volumes": [_volume_spec(v) for v in volumes],
        "cables": [{"name": c.name, "cls": c.emc_class, "start": c.start, "end": c.end,
                    "from": c.from_location, "to": c.to_location} for c in cables],
        "classColors": CLASS_COLORS,
        "separation": {f"{a}|{b}": separation(a, b) for a in classes for b in classes},
        "methods": methods,
        "initial": initial if any(m["key"] == initial for m in methods) else methods[0]["key"],
    }
    page = assemble_page(_json_block(data), {"n": len(cables)})
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(page, encoding="utf-8")
