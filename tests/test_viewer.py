"""The interactive viewer (harness_demo/viewer/): JS syntax, data contract, capture API, optional browser run.

The browser test loads the generated page in headless Chrome (three.js is embedded, so no internet
is needed); it only runs with HARNESS_BROWSER_TESTS=1.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import main
from harness_demo.presentation import VIEWER_DIR

VIEWER_JS = (VIEWER_DIR / "viewer.js").read_text(encoding="utf-8")
CAPTURE_PY = (Path(main.__file__).parent / "harness_demo" / "capture.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("viewer")
    main.main(["--no-images", "--trials", "0", "--workers", "1",
               "--out", str(tmp / "out"), "--docs", str(tmp / "docs")])
    return tmp / "docs" / "index.html"


def _embedded_data(page: Path) -> dict:
    html = page.read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_page_is_self_contained(page):
    html = page.read_text(encoding="utf-8")
    for placeholder in ("__STYLE__", "__SCRIPT__", "__DATA_JSON__", "__THREE_BUNDLE__", "__CSP__", "__I18N_JSON__",
                        "__I18N_SCRIPT__"):
        assert placeholder not in html
    assert (VIEWER_DIR / "viewer.css").read_text(encoding="utf-8").strip()[:40] in html
    assert "window.viewer = {" in html and "window.__i18n = {" in html  # the scripts were inlined
    assert html.count("url(data:font/woff2;base64,") == 2  # Inter (Latin + Latin Extended) is embedded
    assert "font-src data:" in html


I18N = json.loads((VIEWER_DIR / "i18n.json").read_text(encoding="utf-8"))
TEMPLATE = (VIEWER_DIR / "template.html").read_text(encoding="utf-8")


def test_both_languages_have_every_text():
    tr, en = set(I18N["tr"]), set(I18N["en"])
    assert tr - en == set()
    assert {k for k in en - tr if not k.startswith("method.")} == set()  # Turkish method labels come from the data
    for lang, strings in I18N.items():
        placeholders = {k: set(re.findall(r"\{(\w+)\}", v)) for k, v in strings.items()}
        for key, names in placeholders.items():
            if key in I18N["tr"]:
                assert names == set(re.findall(r"\{(\w+)\}", I18N["tr"][key])), f"{lang}: {key}"


def test_every_text_key_used_by_the_page_exists():
    used = set(re.findall(r'data-i18n(?:-html|-aria|-title)?="([^"]+)"', TEMPLATE))
    used |= set(re.findall(r'\bt\("([\w.]*\w)"', VIEWER_JS))  # literal keys; prefixes ("layer." + key) below
    used |= set(re.findall(r'"((?:check|unit)\.\w+)"', VIEWER_JS))  # CHECKS table
    toggles = VIEWER_JS[VIEWER_JS.index("const toggles = ["):VIEWER_JS.index("const chipEls")]
    used |= {f"layer.{k}" for k in re.findall(r'\["(\w+)", (?:true|false)', toggles)}
    views = VIEWER_JS[VIEWER_JS.index("const VIEWS = {"):VIEWER_JS.index("const viewsEl")]
    used |= {f"view.{k}" for k in re.findall(r"^\s{2}(\w+): \{", views, re.M)}
    tour = re.search(r"const TOUR = (\[.*?\]);", VIEWER_JS).group(1)
    used |= {f"tour.{k}.{part}" for k in re.findall(r'\["(\w+)", "', tour) for part in ("title", "body")}
    assert len(used) > 60
    assert used <= set(I18N["tr"]), f"missing texts: {sorted(used - set(I18N['tr']))}"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_viewer_js_syntax(tmp_path):
    module = tmp_path / "viewer.mjs"  # .mjs: parse as an ES module (it uses import statements)
    module.write_text(VIEWER_JS, encoding="utf-8")
    proc = subprocess.run(["node", "--check", str(module)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_viewer_reads_only_embedded_data_fields(page):
    """Every top-level `D.<field>` the viewer uses must be provided by presentation.py."""
    used = set(re.findall(r"\bD\.([A-Za-z_]\w*)", VIEWER_JS))
    provided = set(_embedded_data(page))
    assert used, "the viewer should read its data from D"
    assert used <= provided, f"missing in the embedded data: {sorted(used - provided)}"


def test_capture_uses_only_existing_viewer_api():
    """capture.py drives the page through window.viewer; every member it calls must exist."""
    api_block = VIEWER_JS[VIEWER_JS.index("window.viewer = {"):]
    api_block = api_block[: api_block.index("\n};")]
    defined = set(re.findall(r"^\s{2}(\w+)(?:\(|:)", api_block, re.M))
    called = set(re.findall(r"window\.viewer\.(\w+)", CAPTURE_PY))
    assert called, "capture.py should drive window.viewer"
    assert called <= defined, f"not defined in viewer.js: {sorted(called - defined)}"


@pytest.mark.skipif(os.environ.get("HARNESS_BROWSER_TESTS") != "1",
                    reason="set HARNESS_BROWSER_TESTS=1 (needs Chrome)")
def test_page_runs_without_errors_in_headless_chrome(page):
    from choreographer import Browser

    from harness_demo.capture import _open, browser_options

    async def run() -> tuple[list, int, str]:
        async with Browser(**browser_options()) as browser:
            p = await _open(browser, page)
            for key in _embedded_data(page)["methods"]:
                key = key["key"]
                await p.js(f"window.viewer.select({json.dumps(key)}); window.viewer.progress(3.5); "
                           "window.viewer.frame()")
            n = await p.js("window.viewer.methods.length")
            caption = await p.js("document.getElementById('caption').textContent")
            english = await p.js("window.viewer.lang('en'); [document.documentElement.lang, "
                                 "document.getElementById('tab-baseline').textContent, "
                                 "document.querySelector('#cabletable th').textContent, "
                                 "document.getElementById('tab-branched').textContent]")
            return await p.js("window.__viewerErrors"), n, caption, english

    errors, n_methods, caption, english = asyncio.run(run())
    assert errors == []
    assert n_methods == 6
    assert "f) Dallı demet" in caption  # last selected method (README images are Turkish)
    assert english == ["en", "a) Baseline (independent Dijkstra)", "Cable", "f) Branched harness (Steiner tree)"]


def test_three_bundle_matches_the_pinned_version():
    """The embedded three.js bundle is built from the npm version pinned in vendor/package.json."""
    from harness_demo.presentation import THREE_BUNDLE, THREE_VERSION

    package = json.loads((THREE_BUNDLE.parent / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((THREE_BUNDLE.parent / "package-lock.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["three"] == THREE_VERSION
    assert lock["packages"]["node_modules/three"]["version"] == THREE_VERSION
    assert lock["packages"]["node_modules/three"]["integrity"].startswith("sha512-")
    bundle = THREE_BUNDLE.read_text(encoding="utf-8")
    assert f'"{THREE_VERSION.split(".")[1]}"' in bundle  # three's REVISION string, e.g. "169"
    assert "window.__three" in bundle and "@license" in bundle


# SHA-256 of the embedded vendor files (bundle with LF line ends): a changed file must be a deliberate rebuild.
VENDOR_SHA256 = {
    "three.bundle.min.js": "ee5c154c2c35b490b1f987311fc233ace18637c951066d5ddcefc21e15b5188e",
    "fonts/inter-latin-wght-normal.woff2": "3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62",
    "fonts/inter-latin-ext-wght-normal.woff2": "34b9c504cab7a73e37b746343a449132e56cf7b5481af2cb81dc74dcff25c956",
}


def test_vendor_files_match_their_pinned_hashes():
    from harness_demo.presentation import THREE_BUNDLE

    for name, digest in VENDOR_SHA256.items():
        data = (THREE_BUNDLE.parent / name).read_bytes()
        if name.endswith(".js"):
            data = data.replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == digest, f"{name} changed: rebuild it and update VENDOR_SHA256"


def test_capture_chrome_runs_sandboxed_without_unsafe_flags(monkeypatch):
    """The README images are captured with Chrome's sandbox on and without choreographer's unsafe flags."""
    from choreographer import Browser
    from choreographer.browsers import ChromeNotFoundError

    from harness_demo.capture import UNSAFE_CHROME_FLAGS, browser_options

    monkeypatch.delenv("HARNESS_CHROME_NO_SANDBOX", raising=False)
    try:
        impl = Browser(**browser_options())._browser_impl
    except ChromeNotFoundError:
        pytest.skip("Chrome is not installed")
    impl.pre_open()
    try:
        cli = set(impl.get_cli())
    finally:
        impl.clean()
    assert not cli & UNSAFE_CHROME_FLAGS
    assert "--no-sandbox" not in cli and "--disable-extensions" in cli and "--headless" in cli
