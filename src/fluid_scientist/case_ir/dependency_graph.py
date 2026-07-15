"""Requirement Dependency Graph -- tracks relationships between atomic requirements.

This module implements the dependency graph described in section 17 of
the refactor plan.  Atomic requirements are not independent; they may
require other requirements, conflict with them, be derived from user
facts, or be validated by specific capabilities.  The
:class:`RequirementDependencyGraph` captures these relationships as a
directed graph and provides query utilities (dependency lookup, conflict
detection, topological sort).

Typical usage::

    from fluid_scientist.case_ir.dependency_graph import (
        DependencyEdge,
        RequirementDependencyGraph,
    )

    graph = RequirementDependencyGraph()
    graph.add_edge(DependencyEdge(
        source="REQ-001",
        target="REQ-002",
        edge_type="REQUIRES",
        reason="Mesh is needed before boundary conditions",
    ))
    order = graph.topological_sort()
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Edge types
# ---------------------------------------------------------------------------

DependencyEdgeType = Literal[
    "REQUIRES",
    "CONFLICTS_WITH",
    "DERIVED_FROM",
    "MEASURED_BY",
    "IMPLEMENTED_BY",
    "VALIDATED_BY",
]

# ---------------------------------------------------------------------------
# DependencyEdge
# ---------------------------------------------------------------------------


class DependencyEdge(BaseModel):
    """A directed edge between two nodes in the dependency graph.

    Attributes:
        source: The source requirement ID (or capability ID).
        target: The target requirement ID (or capability ID).
        edge_type: The semantic type of the dependency relationship.
        reason: A human-readable explanation of why this edge exists.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    edge_type: DependencyEdgeType
    reason: str = ""


# ---------------------------------------------------------------------------
# RequirementDependencyGraph
# ---------------------------------------------------------------------------


class RequirementDependencyGraph(BaseModel):
    """A directed graph tracking relationships between atomic requirements.

    Nodes are requirement or capability IDs (strings).  Edges are
    :class:`DependencyEdge` objects that carry a semantic type and an
    optional reason.

    The graph supports:

    - **Dependency lookup**: :meth:`get_dependencies` returns all edges
      originating from a node.
    - **Dependent lookup**: :meth:`get_dependents` returns all edges
      pointing to a node.
    - **Conflict detection**: :meth:`get_conflicts` returns all nodes
      that conflict with a given node.
    - **Topological sort**: :meth:`topological_sort` returns nodes in
      dependency order using Kahn's algorithm (only ``REQUIRES`` edges
      are treated as ordering constraints).
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[str] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)

    def add_node(self, node_id: str) -> None:
        """Add a node if it does not already exist."""
        if node_id not in self.nodes:
            self.nodes.append(node_id)

    def add_edge(self, edge: DependencyEdge) -> None:
        """Add an edge, ensuring both endpoints exist as nodes."""
        self.add_node(edge.source)
        self.add_node(edge.target)
        self.edges.append(edge)

    def get_dependencies(self, node_id: str) -> list[DependencyEdge]:
        """Get all edges where *node_id* is the source."""
        return [e for e in self.edges if e.source == node_id]

    def get_dependents(self, node_id: str) -> list[DependencyEdge]:
        """Get all edges where *node_id* is the target."""
        return [e for e in self.edges if e.target == node_id]

    def get_conflicts(self, node_id: str) -> list[str]:
        """Get all nodes that conflict with *node_id*."""
        return [
            e.target
            for e in self.edges
            if e.source == node_id and e.edge_type == "CONFLICTS_WITH"
        ]

    def get_sources(self, node_id: str) -> list[DependencyEdge]:
        """Get all ``DERIVED_FROM`` edges pointing to *node_id*.

        This reveals which user facts or upstream requirements a given
        requirement was derived from.
        """
        return [
            e
            for e in self.edges
            if e.target == node_id and e.edge_type == "DERIVED_FROM"
        ]

    def get_implementations(self, node_id: str) -> list[DependencyEdge]:
        """Get all ``IMPLEMENTED_BY`` edges originating from *node_id*."""
        return [
            e
            for e in self.edges
            if e.source == node_id and e.edge_type == "IMPLEMENTED_BY"
        ]

    def has_cycle(self) -> bool:
        """Detect whether the ``REQUIRES`` sub-graph contains a cycle.

        Uses a depth-first traversal with a recursion stack.
        """
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for edge in self.edges:
            if edge.edge_type == "REQUIRES":
                adj.setdefault(edge.source, []).append(edge.target)

        white, gray, black = 0, 1, 2
        color: dict[str, int] = {n: white for n in self.nodes}

        def _dfs(node: str) -> bool:
            color[node] = gray
            for neighbor in adj.get(node, []):
                if color.get(neighbor, white) == gray:
                    return True
                if color.get(neighbor, white) == white:
                    if _dfs(neighbor):
                        return True
            color[node] = black
            return False

        for node in self.nodes:
            if color.get(node, white) == white:
                if _dfs(node):
                    return True
        return False

    def topological_sort(self) -> list[str]:
        """Return nodes in dependency order (topological sort).

        Only ``REQUIRES`` edges are treated as ordering constraints.
        Uses Kahn's algorithm.  If the graph contains a cycle, the
        remaining nodes (involved in the cycle) are appended in
        arbitrary order at the end.
        """
        # Build adjacency and in-degree maps from REQUIRES edges only.
        in_degree: dict[str, int] = {n: 0 for n in self.nodes}
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for edge in self.edges:
            if edge.edge_type == "REQUIRES":
                adj.setdefault(edge.source, []).append(edge.target)
                in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

        queue = [n for n, d in in_degree.items() if d == 0]
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Append any nodes that were not processed (cycle participants).
        remaining = [n for n in self.nodes if n not in result]
        result.extend(remaining)

        return result

    def to_dict(self) -> dict[str, Any]:
        """Serialise the graph to a plain dictionary."""
        return {
            "nodes": list(self.nodes),
            "edges": [e.model_dump() for e in self.edges],
        }


__all__ = [
    "DependencyEdge",
    "DependencyEdgeType",
    "RequirementDependencyGraph",
]
