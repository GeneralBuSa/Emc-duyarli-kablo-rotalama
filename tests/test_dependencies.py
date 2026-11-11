"""Dependency pins: the requirements files, the hash-locked CI install and the npm lock agree."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "harness_demo" / "viewer" / "vendor"


def _name(raw: str) -> str:
    """Normalised package name (PEP 503): case and runs of '-', '_', '.' do not matter."""
    return re.sub(r"[-_.]+", "-", raw).lower()


def _pins(path: Path) -> dict[str, str]:
    """`name==version` lines of a requirements file, following `-r` includes."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().rstrip("\\").strip()
        if line.startswith("-r "):
            pins |= _pins(path.parent / line[3:].strip())
        elif line and not line.startswith("--"):
            name, sep, version = line.partition("==")
            assert sep and version, f"{path.name}: not pinned exactly: {line!r}"
            pins[_name(name)] = version.strip()
    return pins


def test_requirements_match_the_ci_lock():
    """Every pin of requirements*.txt is installed by CI (requirements-ci.lock) at the same version."""
    lock = _pins(ROOT / "requirements-ci.lock")
    wanted = _pins(ROOT / "requirements-dev.txt")  # includes requirements.txt
    assert wanted, "no pins found"
    mismatched = {n: (v, lock.get(n)) for n, v in wanted.items() if lock.get(n) != v}
    assert not mismatched, f"requirements vs requirements-ci.lock (wanted, locked): {mismatched}"
    text = (ROOT / "requirements-ci.lock").read_text(encoding="utf-8")
    for block in re.split(r"\n(?=\S)", text.strip()):
        if not block.startswith("#"):
            assert "--hash=sha256:" in block, f"no hash in the lock for: {block.splitlines()[0]}"


def test_npm_lock_matches_package_json():
    """The vendor build's npm dependencies are pinned exactly and locked with integrity hashes."""
    package = json.loads((VENDOR / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((VENDOR / "package-lock.json").read_text(encoding="utf-8"))
    for name, version in package["devDependencies"].items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name}: pin an exact version, not {version!r}"
        entry = lock["packages"][f"node_modules/{name}"]
        assert entry["version"] == version, name
    for path, entry in lock["packages"].items():
        if path:  # "" is the project itself
            assert entry.get("integrity", "").startswith("sha512-"), f"{path}: no sha512 integrity"
            assert entry.get("resolved", "").startswith("https://registry.npmjs.org/"), f"{path}: not from npm"
