"""Dependency engine for the fluid-scientist simulation spec.

This package tracks how parameter changes cascade through the simulation
spec.  It comprises five cooperating modules:

* :mod:`.rules` — declarative dependency rules and the
  :class:`~.rules.RuleRegistry`.
* :mod:`.graph` — the :class:`~.graph.DependencyGraph` that indexes
  rules into a queryable directed graph.
* :mod:`.derived_values` — the :class:`~.derived_values.DerivedValueComputer`
  that evaluates derived values from source inputs.
* :mod:`.invalidation` — the :class:`~.invalidation.InvalidationEngine`
  that determines which artifacts (mesh, case, results, …) need
  regeneration.
* :mod:`.report` — the :class:`~.report.ReportBuilder` that ties
  everything together into a :class:`~.report.DependencyReport`.
"""

from __future__ import annotations

from .derived_values import DerivedValueComputer
from .graph import DependencyEdge, DependencyGraph, DependencyNode, EdgeType
from .invalidation import (
    ArtifactType,
    InvalidationEngine,
    InvalidationRule,
    InvalidationStatus,
)
from .report import DependencyReport, ReportBuilder
from .rules import DependencyRule, RuleRegistry, RuleType

__all__ = [
    # rules
    "DependencyRule",
    "RuleRegistry",
    "RuleType",
    # graph
    "DependencyNode",
    "DependencyEdge",
    "DependencyGraph",
    "EdgeType",
    # derived values
    "DerivedValueComputer",
    # invalidation
    "InvalidationStatus",
    "InvalidationRule",
    "InvalidationEngine",
    "ArtifactType",
    # report
    "DependencyReport",
    "ReportBuilder",
]
