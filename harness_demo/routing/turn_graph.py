"""Bend-aware routing: the line graph of the routing graph with only the allowed turns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import csr_matrix

from ..graph import RoutingGraph
from ..scenarios import Cable
from .core import NoPathError, circumradii, shortest_path

_SINK_EPS = 1e-12  # tiny weight for arcs into the virtual sink (explicit zeros would be dropped)


@dataclass
class TurnGraph:
    """Line graph of the routing graph: one state per directed edge, arcs = allowed turns.

    A route u -> v -> w is only possible if w != u and the circle through u, v, w has a
    radius of at least `min_radius`, i.e. exactly the condition the bend check tests.
    """

    de_from: np.ndarray  # (2E,) tail node of each directed edge
    de_to: np.ndarray  # (2E,) head node
    de_edge: np.ndarray  # (2E,) undirected edge id
    arc_src: np.ndarray  # allowed transitions de_src -> de_dst
    arc_dst: np.ndarray
    min_radius: float
    arc_radius: np.ndarray | None = None  # bend radius of each allowed turn (circle through u, v, w)
    # CSR structure of the search graph, built once (see `_search_template`).
    _template: tuple[np.ndarray, np.ndarray, np.ndarray] | None = field(default=None, init=False, repr=False)
    _arc_keys: np.ndarray | None = field(default=None, init=False, repr=False)

    @property
    def n_states(self) -> int:
        """Number of directed edges."""
        return len(self.de_from)

    @property
    def key_base(self) -> int:
        """Node-id bound used to encode turn keys (largest node id + 1)."""
        return int(max(self.de_from.max(), self.de_to.max())) + 1 if self.n_states else 1

    def arc_keys(self) -> np.ndarray:
        """Undirected turn key of every arc: u->v->w and w->v->u share the key (see `turn_key`)."""
        if self._arc_keys is None:
            a, v, c = self.de_from[self.arc_src], self.de_to[self.arc_src], self.de_to[self.arc_dst]
            self._arc_keys = turn_key(a, v, c, self.key_base)
        return self._arc_keys

    def path_turn_keys(self, path: Sequence[int]) -> np.ndarray:
        """Turn keys of the interior vertices of a node path."""
        p = np.asarray(path, dtype=np.int64)
        return turn_key(p[:-2], p[1:-1], p[2:], self.key_base) if len(p) >= 3 else np.empty(0, dtype=np.int64)

    def _search_template(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(indices, indptr, perm) of the search graph: states + virtual source n + virtual sink n+1.

        Its structure never changes: every allowed turn, an arc from the source into *every* state
        and an arc from every state into the sink. Only the weights depend on the cable; arcs that
        do not exist for it get an infinite weight, which Dijkstra never relaxes. `perm` maps the
        entry order (turns, source arcs, sink arcs) to the CSR data order.
        """
        if self._template is None:
            n = self.n_states
            states = np.arange(n)
            rows = np.concatenate([self.arc_src, np.full(n, n), states])
            cols = np.concatenate([self.arc_dst, states, np.full(n, n + 1)])
            order = csr_matrix((np.arange(1, len(rows) + 1, dtype=float), (rows, cols)), shape=(n + 2, n + 2))
            order.sort_indices()
            self._template = (order.indices, order.indptr, order.data.astype(np.int64) - 1)
        return self._template


def turn_key(a: np.ndarray, v: np.ndarray, c: np.ndarray, n_nodes: int) -> np.ndarray:
    """One int64 per turn a-v-c, the same for both directions (requires n_nodes**3 < 2**63)."""
    a, v, c = (np.asarray(x, dtype=np.int64) for x in (a, v, c))
    n = np.int64(n_nodes)
    return (v * n + np.minimum(a, c)) * n + np.maximum(a, c)


def build_turn_graph(graph: RoutingGraph, min_radius: float) -> TurnGraph:
    """Enumerate every (u->v, v->w) transition and keep the ones with a large enough bend radius."""
    e = graph.edges
    de_from = np.concatenate([e[:, 0], e[:, 1]])
    de_to = np.concatenate([e[:, 1], e[:, 0]])
    de_edge = np.concatenate([np.arange(len(e)), np.arange(len(e))])

    # Out-going directed edges grouped by tail node -> contiguous ranges.
    order = np.argsort(de_from, kind="stable")
    counts_by_node = np.bincount(de_from, minlength=graph.n_nodes)
    start_by_node = np.concatenate([[0], np.cumsum(counts_by_node)[:-1]])

    counts = counts_by_node[de_to]  # successors of each directed edge
    arc_src = np.repeat(np.arange(len(de_from)), counts)
    within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    arc_dst = order[np.repeat(start_by_node[de_to], counts) + within]

    pos = graph.positions
    a, b, c = pos[de_from[arc_src]], pos[de_to[arc_src]], pos[de_to[arc_dst]]
    radius = circumradii(a, b, c)
    ok = de_to[arc_dst] != de_from[arc_src]  # no U-turns
    ok &= radius >= min_radius * (1.0 + 1e-9)
    return TurnGraph(de_from, de_to, de_edge, arc_src[ok], arc_dst[ok], min_radius, radius[ok])


def length_lower_bound(graph: RoutingGraph, cables: Sequence[Cable], turns: TurnGraph) -> float:
    """Sum over cables of the shortest bend-feasible route length.

    Every solution of the integrated method satisfies the bend constraint, so its total length can
    never be below this value (capacity and EMC separation only make routes longer).
    """
    return sum(graph.path_length(turn_aware_path(turns, graph.lengths, c.start, c.end)) for c in cables)


def path_cost(cost: np.ndarray, edge_ids: np.ndarray) -> float:
    """Cost of a route on the turn-aware search graph (every edge paid once, plus the sink arc)."""
    return float(cost[edge_ids].sum()) + _SINK_EPS


def turn_aware_path(turns: TurnGraph, cost: np.ndarray, source: int, target: int,
                    limit: float = np.inf, arc_extra: np.ndarray | None = None) -> list[int]:
    """Cheapest node path from source to target that only uses allowed turns.

    The cost of a state (directed edge) is paid when it is entered; a virtual source enters
    every edge leaving `source` and every edge reaching `target` leads to a virtual sink.
    `limit` (an upper bound on the optimal cost, e.g. the cost of a known feasible route) lets
    Dijkstra stop exploring beyond it; it never changes the result. `arc_extra` adds a cost per
    allowed turn (e.g. the bundle bend penalty).
    """
    n = turns.n_states
    indices, indptr, perm = turns._search_template()
    w_turn = cost[turns.de_edge[turns.arc_dst]]
    if arc_extra is not None:
        w_turn = w_turn + arc_extra
    w_src = np.where(turns.de_from == source, cost[turns.de_edge], np.inf)
    w_snk = np.where(turns.de_to == target, _SINK_EPS, np.inf)
    data = np.concatenate([w_turn, w_src, w_snk])[perm]
    adj = csr_matrix((data, indices, indptr), shape=(n + 2, n + 2))
    try:
        states = shortest_path(adj, n, n + 1, limit)[1:-1]
    except NoPathError:
        raise NoPathError(f"düğüm {source} -> {target}: en küçük bükülme yarıçapına ({turns.min_radius:g} m) uyan "
                          "bir yol yok (mesh bu yarıçap için fazla ince ya da düzenli olabilir)") from None
    return [int(turns.de_from[states[0]])] + [int(turns.de_to[s]) for s in states]
