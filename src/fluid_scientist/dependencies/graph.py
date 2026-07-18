"""Dependency graph for the CFD simulation spec.

The :class:`DependencyGraph` is a directed graph whose nodes are spec
paths (JSON pointers) and whose edges encode causal relationships
declared by :class:`~fluid_scientist.dependencies.rules.DependencyRule`
instances.

Edge direction
--------------
An edge ``A -> B`` means *"B depends on A"* (i.e. A is an input to B).
Therefore:

* ``get_dependents(A)`` returns the paths that *depend on* A
  (downstream, the targets of edges leaving A).
* ``get_dependencies(B)`` returns the paths that B *depends on*
  (upstream, the sources of edges entering B).

The graph is built at construction time from the default CFD rule set
but is mutable: :meth:`add_node` and :meth:`add_edge` allow extension
at runtime.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .rules import DependencyRule, RuleRegistry

__all__ = [
    "DependencyNode",
    "DependencyEdge",
    "DependencyGraph",
    "EdgeType",
]

#: The four edge types, corresponding 1:1 to the rule types.
EdgeType = Literal["derives", "constrains", "invalidates", "triggers_recompile"]

#: Mapping from :class:`DependencyRule.rule_type` to :class:`EdgeType`.
_RULE_TYPE_TO_EDGE_TYPE: dict[str, str] = {
    "derive": "derives",
    "constrain": "constrains",
    "invalidate": "invalidates",
    "recompile": "triggers_recompile",
}


class DependencyNode(BaseModel):
    """A node in the dependency graph — a single spec path.

    Parameters
    ----------
    path:
        JSON-pointer path, e.g. ``"/physics/velocity"``.
    value_type:
        Type descriptor for the value at this path, e.g. ``"float"``,
        ``"str"``, ``"dict"``.
    description:
        Optional human-readable description.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    value_type: str
    description: str | None = None


class DependencyEdge(BaseModel):
    """A directed edge in the dependency graph.

    Parameters
    ----------
    source_path:
        The path that the edge originates from (an input).
    target_path:
        The path that the edge points to (depends on *source_path*).
    rule_id:
        Identifier of the :class:`DependencyRule` that produced this edge.
    edge_type:
        One of ``"derives"``, ``"constrains"``, ``"invalidates"``,
        ``"triggers_recompile"``.
    """

    model_config = ConfigDict(extra="forbid")

    source_path: str
    target_path: str
    rule_id: str
    edge_type: EdgeType


# ---------------------------------------------------------------------------
# Pre-built nodes
# ---------------------------------------------------------------------------

#: Canonical set of dependency-graph nodes for a CFD spec.
_DEFAULT_NODES: list[DependencyNode] = [
    # Physics
    DependencyNode(path="/physics/velocity", value_type="float", description="Characteristic velocity"),
    DependencyNode(path="/physics/characteristic_length", value_type="float", description="Characteristic length (e.g. diameter)"),
    DependencyNode(path="/physics/kinematic_viscosity", value_type="float", description="Kinematic viscosity"),
    DependencyNode(path="/physics/dynamic_viscosity", value_type="float", description="Dynamic viscosity"),
    DependencyNode(path="/physics/density", value_type="float", description="Fluid density"),
    DependencyNode(path="/physics/reynolds_number", value_type="float", description="Reynolds number"),
    DependencyNode(path="/physics/material", value_type="str", description="Fluid material identifier"),
    # Numerics / time
    DependencyNode(path="/numerics/time/start_time", value_type="float", description="Simulation start time"),
    DependencyNode(path="/numerics/time/end_time", value_type="float", description="Simulation end time"),
    DependencyNode(path="/numerics/time/duration", value_type="float", description="Simulation duration"),
    DependencyNode(path="/numerics/time/delta_t", value_type="float", description="Time step"),
    DependencyNode(path="/numerics/time/write_interval", value_type="float", description="Write interval"),
    DependencyNode(path="/numerics/time/expected_output_count", value_type="int", description="Expected number of output time steps"),
    DependencyNode(path="/numerics/time/courant_number", value_type="float", description="Courant number estimate"),
    DependencyNode(path="/numerics/time/statistics_windows", value_type="list", description="Statistics time windows"),
    # Geometry / mesh / boundaries
    DependencyNode(path="/geometry", value_type="dict", description="Geometry definition"),
    DependencyNode(path="/mesh", value_type="dict", description="Mesh definition"),
    DependencyNode(path="/mesh/resolution", value_type="float", description="Mesh resolution / characteristic cell size"),
    DependencyNode(path="/boundaries", value_type="dict", description="Boundary conditions"),
    # Observations
    DependencyNode(path="/observations/targets", value_type="list", description="Observation targets"),
    DependencyNode(path="/observations/function_objects/forceCoeffs", value_type="dict", description="forceCoeffs function object"),
    DependencyNode(path="/observations/function_objects/probes", value_type="dict", description="probes function object"),
    DependencyNode(path="/observations/function_objects/surfaceFieldValue", value_type="dict", description="surfaceFieldValue function object"),
    DependencyNode(path="/observations/function_objects/fieldAverage", value_type="dict", description="fieldAverage function object"),
    # Study metadata
    DependencyNode(path="/study/title", value_type="str", description="Study title"),
]


class DependencyGraph:
    """Directed dependency graph for the simulation spec.

    The graph is populated with CFD-specific nodes and edges at
    construction time.  Nodes and edges can be added afterwards via
    :meth:`add_node` and :meth:`add_edge`.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, DependencyNode] = {}
        self._edges: list[DependencyEdge] = []
        # Adjacency: path -> paths that depend on it (downstream).
        self._dependents: dict[str, list[str]] = {}
        # Reverse adjacency: path -> paths it depends on (upstream).
        self._dependencies: dict[str, list[str]] = {}
        self._build_default_graph()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_default_graph(self) -> None:
        """Populate the graph with the pre-built CFD nodes and rules."""
        for node in _DEFAULT_NODES:
            self.add_node(node)

        registry = RuleRegistry()
        for rule in registry.list_all_rules():
            edge_type = _RULE_TYPE_TO_EDGE_TYPE.get(
                rule.rule_type, "constrains"
            )
            for source in rule.source_paths:
                edge = DependencyEdge(
                    source_path=source,
                    target_path=rule.target_path,
                    rule_id=rule.rule_id,
                    edge_type=edge_type,  # type: ignore[arg-type]
                )
                self.add_edge(edge)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, node: DependencyNode) -> None:
        """Add (or replace) a node in the graph."""
        self._nodes[node.path] = node
        # Ensure adjacency entries exist so lookups never KeyError.
        self._dependents.setdefault(node.path, [])
        self._dependencies.setdefault(node.path, [])

    def add_edge(self, edge: DependencyEdge) -> None:
        """Add a directed edge ``source -> target``.

        Auto-creates placeholder nodes for paths that do not yet exist
        so the adjacency lists stay consistent.
        """
        # Avoid duplicate edges (same source, target, and rule).
        for existing in self._edges:
            if (
                existing.source_path == edge.source_path
                and existing.target_path == edge.target_path
                and existing.rule_id == edge.rule_id
            ):
                return

        self._edges.append(edge)

        # Ensure both endpoints exist as nodes.
        for p in (edge.source_path, edge.target_path):
            if p not in self._nodes:
                self.add_node(
                    DependencyNode(path=p, value_type="unknown", description=None)
                )

        # Update forward adjacency (source -> target).
        deps = self._dependents.setdefault(edge.source_path, [])
        if edge.target_path not in deps:
            deps.append(edge.target_path)

        # Update reverse adjacency (target -> source).
        dior = self._dependencies.setdefault(edge.target_path, [])
        if edge.source_path not in dior:
            dior.append(edge.source_path)

    # ------------------------------------------------------------------
    # Queries — direct
    # ------------------------------------------------------------------

    def get_node(self, path: str) -> DependencyNode | None:
        """Return the node at *path*, or ``None``."""
        return self._nodes.get(path)

    def get_edges(self) -> list[DependencyEdge]:
        """Return a copy of all edges."""
        return list(self._edges)

    def get_nodes(self) -> list[DependencyNode]:
        """Return a copy of all nodes."""
        return list(self._nodes.values())

    def get_dependents(self, path: str) -> list[str]:
        """Return paths that *depend on* *path* (direct downstream)."""
        return list(self._dependents.get(path, []))

    def get_dependencies(self, path: str) -> list[str]:
        """Return paths that *path* depends on (direct upstream)."""
        return list(self._dependencies.get(path, []))

    # ------------------------------------------------------------------
    # Queries — transitive
    # ------------------------------------------------------------------

    def get_transitive_dependents(self, path: str) -> list[str]:
        """Return all paths transitively depending on *path*.

        Performs a breadth-first traversal following dependent edges.
        The *path* itself is never included in the result.
        """
        result: list[str] = []
        seen: set[str] = {path}
        queue: list[str] = [path]
        while queue:
            current = queue.pop(0)
            for dep in self._dependents.get(current, []):
                if dep not in seen:
                    seen.add(dep)
                    result.append(dep)
                    queue.append(dep)
        return result

    def get_transitive_dependencies(self, path: str) -> list[str]:
        """Return all paths that *path* transitively depends on.

        Performs a breadth-first traversal following dependency edges.
        The *path* itself is never included in the result.
        """
        result: list[str] = []
        seen: set[str] = {path}
        queue: list[str] = [path]
        while queue:
            current = queue.pop(0)
            for dep in self._dependencies.get(current, []):
                if dep not in seen:
                    seen.add(dep)
                    result.append(dep)
                    queue.append(dep)
        return result

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def has_cycle(self, path: str) -> bool:
        """Return ``True`` if *path* is part of a dependency cycle.

        A cycle exists when *path* is reachable from itself by following
        dependent (downstream) edges.
        """
        visited: set[str] = set()
        return self._detect_cycle(path, path, visited)

    def _detect_cycle(
        self, target: str, current: str, visited: set[str]
    ) -> bool:
        """DFS helper: return True if *target* is reachable from *current*."""
        for dep in self._dependents.get(current, []):
            if dep == target:
                return True
            if dep in visited:
                continue
            visited.add(dep)
            if self._detect_cycle(target, dep, visited):
                return True
        return False
