"""Performance metrics and table formatting."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from .checks import CheckReport
from .graph import RoutingGraph
from .routing import RoutingResult

Row = dict[str, Any]  # one metric row per method (heterogeneous values: label, floats, counts)

# (key, header, format)
COLUMNS: list[tuple[str, str, str]] = [
    ("label", "Yöntem", "{}"),
    ("total_length_m", "Toplam uzunluk [m]", "{:.2f}"),
    ("unique_length_m", "Benzersiz uzunluk [m]", "{:.2f}"),
    ("bundling_ratio", "Demetlenme oranı", "{:.3f}"),
    ("mass_kg", "Kablo kütlesi [kg]", "{:.2f}"),  # only when the scenario gives masses per metre
    ("emc_points", "EMC ihlali [nokta]", "{}"),
    ("bend_points", "Bükülme ihlali", "{}"),
    ("forbidden_points", "Yasak hacim ihlali", "{}"),
    ("clearance_points", "Boşluk payı ihlali [nokta]", "{}"),
    ("capacity_edges", "Kapasite ihlali [ayrıt]", "{}"),
    ("runtime_s", "Süre [s]", "{:.2f}"),
]


def compute_metrics(result: RoutingResult, graph: RoutingGraph, report: CheckReport,
                    mass_fn: Callable[[str], float] | None = None, classes: dict[str, str] | None = None) -> Row:
    """Length, bundling and violation metrics for one method; with `mass_fn` (kg/m by EMC class) and
    `classes` (EMC class by cable name) also the total cable mass."""
    edge_sets = [graph.path_edge_ids(p) for p in result.routes.values()]
    total = float(sum(graph.lengths[e].sum() for e in edge_sets))
    unique = float(graph.lengths[np.unique(np.concatenate(edge_sets))].sum())
    row: Row = {
        "method": result.method,
        "label": result.label,
        "total_length_m": total,
        "unique_length_m": unique,
        "bundling_ratio": 1.0 - unique / total,
    }
    if mass_fn is not None and classes is not None:
        row["mass_kg"] = sum(graph.path_length(p) * mass_fn(classes[n]) for n, p in result.routes.items())
    return {**row, **report.as_dict(), "runtime_s": result.runtime_s}


def _columns(rows: list[Row]) -> list[tuple[str, str, str]]:
    """The columns present in every row (optional ones such as the mass are left out otherwise)."""
    return [c for c in COLUMNS if all(c[0] in r for r in rows)]


def _cells(rows: list[Row]) -> list[list[str]]:
    """Formatted cell strings for every row."""
    return [[fmt.format(r[key]) for key, _, fmt in _columns(rows)] for r in rows]


def format_console_table(rows: list[Row]) -> str:
    """Fixed-width plain-text table."""
    headers = [h for _, h, _ in _columns(rows)]
    cells = _cells(rows)
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, h in enumerate(headers)]
    line = "-+-".join("-" * w for w in widths)
    out = [" | ".join(h.ljust(w) for h, w in zip(headers, widths)), line]
    for row in cells:
        out.append(" | ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths))))
    return "\n".join(out)


def format_markdown_table(rows: list[Row]) -> str:
    """GitHub-flavoured Markdown table."""
    headers = [h for _, h, _ in _columns(rows)]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join([":---"] + ["---:"] * (len(headers) - 1)) + "|"]
    out += ["| " + " | ".join(c) + " |" for c in _cells(rows)]
    return "\n".join(out)
