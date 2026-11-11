"""Routing algorithms.

a) baseline        : independent Dijkstra per cable                              (core.py)
b) bundled         : sequential routing; reused edges get a cost discount        (sequential.py)
c) emc_aware       : (b) + penalty on edges close to routes of other EMC classes,
                     followed by rip-up-and-reroute of cables that still conflict (sequential.py)
d) lagrangian      : edge capacity (<= K cables) relaxed with Lagrange multipliers,
                     subgradient updates, lower/upper bounds tracked per iteration (lagrangian.py)
e) integrated      : (c) on a turn-aware graph (no turn sharper than the minimum bend
                     radius can be taken) with a capacity penalty on full edges   (sequential.py, turn_graph.py)
f) branched        : branched harness: a Steiner tree per EMC class gives trunks and branches,
                     then (e) with each cable's tree path as a cheap corridor        (sequential.py)

Options of (e) and (f): area capacity (cable cross-sections), a coupling penalty near foreign
routes, and the bundle bend rule (turn radius >= factor x diameter of the bundle bending there).
"""

from .core import SAMPLE_STEP_M, NoPathError, RoutingResult, circumradii, densify, route_baseline, shortest_path
from .lagrangian import route_lagrangian
from .sequential import (
    ORDER_STRATEGIES,
    cable_corridors,
    class_trees,
    order_cables,
    route_branched,
    route_bundled,
    route_conflicts,
    route_emc_aware,
    route_integrated,
)
from .turn_graph import TurnGraph, build_turn_graph, length_lower_bound, turn_aware_path

__all__ = [
    "ORDER_STRATEGIES",
    "SAMPLE_STEP_M",
    "NoPathError",
    "RoutingResult",
    "TurnGraph",
    "build_turn_graph",
    "cable_corridors",
    "circumradii",
    "class_trees",
    "densify",
    "length_lower_bound",
    "order_cables",
    "route_baseline",
    "route_branched",
    "route_bundled",
    "route_conflicts",
    "route_emc_aware",
    "route_integrated",
    "route_lagrangian",
    "shortest_path",
    "turn_aware_path",
]
