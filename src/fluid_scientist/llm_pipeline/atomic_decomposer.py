"""Pass 7: Atomic Requirement Decomposer -- break requirements to atoms.

The :class:`AtomicRequirementDecomposer` takes all outputs from the
previous passes and breaks them down into minimal, individually
implementable capabilities (atomic requirements).  Each atomic
requirement maps to a single, testable capability.

It also builds a dependency graph (:class:`RequirementDependencyEdge`)
that captures how requirements relate to each other (REQUIRES,
MEASURED_BY, IMPLEMENTED_BY, etc.).
"""

from __future__ import annotations

from fluid_scientist.llm_pipeline.models import (
    AmbiguityDetection,
    AtomicRequirement,
    EntityGraph,
    ExtractedFact,
    NormalizedConcept,
    ObservableDecomposition,
    PhysicsDecomposition,
    RequirementDependencyEdge,
)


class AtomicRequirementDecomposer:
    """Break requirements into minimal implementable capabilities.

    The decomposer generates atomic requirements from:

    * **Physics** -- each physics aspect (flow regime, time mode,
      turbulence model, heat transfer) becomes a separate requirement.
    * **Entities** -- each geometric entity and its placement.
    * **Boundaries** -- each boundary condition type.
    * **Mesh** -- refinement zones derived from observables.
    * **Observables** -- each measurement target and its analysis.

    Dependency edges connect requirements that depend on each other.
    """

    def decompose(
        self,
        facts: list[ExtractedFact],
        concepts: list[NormalizedConcept],
        entity_graph: EntityGraph,
        physics: PhysicsDecomposition,
        observables: ObservableDecomposition,
        ambiguity: AmbiguityDetection,
    ) -> tuple[list[AtomicRequirement], list[RequirementDependencyEdge]]:
        """Decompose all previous outputs into atomic requirements.

        Args:
            facts: Facts from Pass 1.
            concepts: Normalized concepts from Pass 3.
            entity_graph: Entity graph from Pass 4.
            physics: Physics decomposition from Pass 5.
            observables: Observable decomposition from Pass 6.
            ambiguity: Ambiguity detection from Pass 2.

        Returns:
            A tuple of (atomic_requirements, dependency_edges).
        """
        reqs: list[AtomicRequirement] = []
        edges: list[RequirementDependencyEdge] = []
        req_counter = 0

        # --- Physics requirements ---
        req_counter = self._add_physics_requirements(
            physics, reqs, req_counter
        )

        # --- Geometry requirements ---
        req_counter = self._add_geometry_requirements(
            facts, entity_graph, reqs, req_counter
        )

        # --- Boundary requirements ---
        req_counter = self._add_boundary_requirements(
            facts, concepts, reqs, req_counter
        )

        # --- Initial condition requirements ---
        req_counter = self._add_initial_condition_requirements(
            facts, concepts, reqs, req_counter
        )

        # --- Mesh requirements ---
        req_counter = self._add_mesh_requirements(
            facts, observables, reqs, req_counter
        )

        # --- Observable requirements ---
        req_counter = self._add_observable_requirements(
            observables, reqs, req_counter
        )

        # --- Build dependency edges ---
        self._build_dependency_edges(reqs, physics, observables, edges)

        return reqs, edges

    # ------------------------------------------------------------------
    # Requirement generators
    # ------------------------------------------------------------------

    def _add_physics_requirements(
        self,
        physics: PhysicsDecomposition,
        reqs: list[AtomicRequirement],
        counter: int,
    ) -> int:
        """Add physics-related atomic requirements."""
        # Flow regime (compressibility).
        counter += 1
        flow_req_id = f"REQ_{counter:03d}"
        reqs.append(AtomicRequirement(
            requirement_id=flow_req_id,
            category="physics",
            description=f"{physics.compressibility} flow regime",
            capability_type="solver_module",
            keywords=[physics.compressibility, "navier_stokes"],
            mandatory=True,
        ))

        # Time mode.
        counter += 1
        time_req_id = f"REQ_{counter:03d}"
        reqs.append(AtomicRequirement(
            requirement_id=time_req_id,
            category="physics",
            description=f"{physics.time_mode} time integration",
            capability_type="time_scheme",
            keywords=[physics.time_mode],
            mandatory=True,
        ))

        # Turbulence model.
        counter += 1
        turb_req_id = f"REQ_{counter:03d}"
        turb_desc = f"turbulence model: {physics.turbulence}"
        reqs.append(AtomicRequirement(
            requirement_id=turb_req_id,
            category="physics",
            description=turb_desc,
            capability_type="turbulence_model",
            keywords=[physics.turbulence.lower()],
            mandatory=True,
        ))

        # Heat transfer.
        if physics.heat_transfer:
            counter += 1
            heat_req_id = f"REQ_{counter:03d}"
            reqs.append(AtomicRequirement(
                requirement_id=heat_req_id,
                category="physics",
                description="energy equation for heat transfer",
                capability_type="energy_equation",
                keywords=["heat_transfer", "energy"],
                mandatory=True,
            ))

        # Multiphase.
        if physics.multiphase:
            counter += 1
            multi_req_id = f"REQ_{counter:03d}"
            reqs.append(AtomicRequirement(
                requirement_id=multi_req_id,
                category="physics",
                description="multiphase flow model",
                capability_type="multiphase_model",
                keywords=["multiphase", "vof"],
                mandatory=True,
            ))

        # Moving mesh.
        if physics.moving_mesh:
            counter += 1
            mesh_req_id = f"REQ_{counter:03d}"
            reqs.append(AtomicRequirement(
                requirement_id=mesh_req_id,
                category="physics",
                description="moving mesh / dynamic mesh",
                capability_type="dynamic_mesh",
                keywords=["moving_mesh", "dynamic_fv_mesh"],
                mandatory=True,
            ))

        # External forces.
        for force in physics.external_forces:
            counter += 1
            reqs.append(AtomicRequirement(
                requirement_id=f"REQ_{counter:03d}",
                category="physics",
                description=f"external force: {force}",
                capability_type="body_force",
                keywords=[force],
                mandatory=True,
            ))

        # Solver module.
        counter += 1
        solver_req_id = f"REQ_{counter:03d}"
        reqs.append(AtomicRequirement(
            requirement_id=solver_req_id,
            category="solver",
            description=f"recommended solver module: {physics.recommended_solver_module}",
            capability_type="solver_module",
            keywords=[physics.recommended_solver_module],
            mandatory=True,
            depends_on=[flow_req_id, time_req_id],
        ))

        return counter

    def _add_geometry_requirements(
        self,
        facts: list[ExtractedFact],
        entity_graph: EntityGraph,
        reqs: list[AtomicRequirement],
        counter: int,
    ) -> int:
        """Add geometry-related atomic requirements."""
        for entity in entity_graph.entities:
            counter += 1
            req_id = f"REQ_{counter:03d}"
            entity_kind = entity.get("kind", "unknown")
            reqs.append(AtomicRequirement(
                requirement_id=req_id,
                category="geometry",
                description=f"{entity_kind} geometry definition",
                capability_type="geometry_component",
                keywords=[entity_kind, "geometry"],
                mandatory=True,
            ))

            # Near-wall placement if entity is near a wall.
            if entity.get("near_wall"):
                counter += 1
                reqs.append(AtomicRequirement(
                    requirement_id=f"REQ_{counter:03d}",
                    category="geometry",
                    description=f"near-wall placement for {entity_kind}",
                    capability_type="geometry_placement",
                    keywords=["near_wall", "placement"],
                    mandatory=True,
                    depends_on=[req_id],
                ))

            # Rotation if entity rotates.
            if entity.get("motion") == "rotates_about":
                counter += 1
                reqs.append(AtomicRequirement(
                    requirement_id=f"REQ_{counter:03d}",
                    category="geometry",
                    description=f"rotation capability for {entity_kind}",
                    capability_type="geometry_rotation",
                    keywords=["rotation", "rotating"],
                    mandatory=True,
                    depends_on=[req_id],
                ))

        # If no entities in graph, derive from facts.
        if not entity_graph.entities:
            entity_facts = [f for f in facts if f.category == "entity"]
            for fact in entity_facts:
                counter += 1
                req_id = f"REQ_{counter:03d}"
                entity_kind = str(fact.value) if fact.value else "unknown"
                reqs.append(AtomicRequirement(
                    requirement_id=req_id,
                    category="geometry",
                    description=f"{entity_kind} geometry definition",
                    capability_type="geometry_component",
                    keywords=[entity_kind, "geometry"],
                    mandatory=True,
                ))

        return counter

    def _add_boundary_requirements(
        self,
        facts: list[ExtractedFact],
        concepts: list[NormalizedConcept],
        reqs: list[AtomicRequirement],
        counter: int,
    ) -> int:
        """Add boundary condition atomic requirements."""
        seen_boundaries: set[str] = set()

        for concept in concepts:
            boundary_concepts = {
                "no_slip_wall_intent": ("no_slip_wall", "no-slip wall boundary"),
                "uniform_velocity_inlet": ("uniform_velocity_inlet", "uniform velocity inlet"),
                "advective_outlet": ("advective_outlet", "advective/convective outlet"),
                "non_reflecting_outlet": ("non_reflecting_outlet", "non-reflecting outlet"),
                "pressure_outlet_intent": ("pressure_outlet", "pressure outlet"),
                "periodic_boundary_pair": ("periodic_pair", "periodic boundary pair"),
                "symmetry_plane": ("symmetry", "symmetry plane boundary"),
            }
            if concept.normalized_concept in boundary_concepts:
                bc_type, desc = boundary_concepts[concept.normalized_concept]
                if bc_type in seen_boundaries:
                    continue
                seen_boundaries.add(bc_type)
                counter += 1
                reqs.append(AtomicRequirement(
                    requirement_id=f"REQ_{counter:03d}",
                    category="boundary",
                    description=desc,
                    capability_type="boundary_condition",
                    keywords=[bc_type],
                    mandatory=True,
                ))

        # Also check facts for boundaries not covered by concepts.
        for fact in facts:
            if fact.category != "boundary":
                continue
            bc_value = str(fact.value) if fact.value else ""
            if bc_value in seen_boundaries or bc_value == "outlet":
                continue
            seen_boundaries.add(bc_value)
            counter += 1
            reqs.append(AtomicRequirement(
                requirement_id=f"REQ_{counter:03d}",
                category="boundary",
                description=f"boundary condition: {bc_value}",
                capability_type="boundary_condition",
                keywords=[bc_value],
                mandatory=True,
            ))

        # Handle generic outlet (ambiguous).
        has_generic_outlet = any(
            f.category == "boundary" and f.value == "outlet" for f in facts
        )
        has_specific_outlet = any(
            f.category == "boundary"
            and f.value in ("advective_outlet", "pressure_outlet")
            for f in facts
        )
        if has_generic_outlet and not has_specific_outlet:
            counter += 1
            reqs.append(AtomicRequirement(
                requirement_id=f"REQ_{counter:03d}",
                category="boundary",
                description="outlet boundary condition (type unresolved: advective or pressure)",
                capability_type="boundary_condition",
                keywords=["outlet", "advective_outlet", "pressure_outlet"],
                mandatory=True,
            ))

        return counter

    def _add_initial_condition_requirements(
        self,
        facts: list[ExtractedFact],
        concepts: list[NormalizedConcept],
        reqs: list[AtomicRequirement],
        counter: int,
    ) -> int:
        """Add initial condition atomic requirements."""
        seen_ics: set[str] = set()

        for concept in concepts:
            ic_concepts = {
                "quiescent_initial_velocity_field": (
                    "quiescent", "quiescent initial velocity field",
                ),
                "developed_pipe_inlet": (
                    "developed", "fully developed inlet profile",
                ),
            }
            if concept.normalized_concept in ic_concepts:
                ic_type, desc = ic_concepts[concept.normalized_concept]
                if ic_type in seen_ics:
                    continue
                seen_ics.add(ic_type)
                counter += 1
                reqs.append(AtomicRequirement(
                    requirement_id=f"REQ_{counter:03d}",
                    category="initial_condition",
                    description=desc,
                    capability_type="initial_condition",
                    keywords=[ic_type],
                    mandatory=True,
                ))

        return counter

    def _add_mesh_requirements(
        self,
        facts: list[ExtractedFact],
        observables: ObservableDecomposition,
        reqs: list[AtomicRequirement],
        counter: int,
    ) -> int:
        """Add mesh-related atomic requirements."""
        # Wake refinement if wake-related observables are present.
        wake_observables = {"frequency_spectrum", "vortex_shedding", "wake_analysis"}
        has_wake = any(
            obs.get("semantic_type") in wake_observables
            for obs in observables.observables
        )
        if has_wake:
            counter += 1
            reqs.append(AtomicRequirement(
                requirement_id=f"REQ_{counter:03d}",
                category="mesh",
                description="wake region mesh refinement",
                capability_type="mesh_refinement",
                keywords=["wake_refinement", "refinement_zone"],
                mandatory=True,
            ))

        # Near-wall refinement if wall observables are present.
        wall_observables = {"wall_heat_flux", "wall_shear_stress", "pressure_coefficient"}
        has_wall = any(
            obs.get("semantic_type") in wall_observables
            for obs in observables.observables
        )
        has_no_slip = any(
            f.category == "boundary" and f.value == "no_slip_wall"
            for f in facts
        )
        if has_wall or has_no_slip:
            counter += 1
            reqs.append(AtomicRequirement(
                requirement_id=f"REQ_{counter:03d}",
                category="mesh",
                description="near-wall mesh refinement for boundary layer resolution",
                capability_type="mesh_refinement",
                keywords=["near_wall_refinement", "boundary_layer"],
                mandatory=True,
            ))

        return counter

    def _add_observable_requirements(
        self,
        observables: ObservableDecomposition,
        reqs: list[AtomicRequirement],
        counter: int,
    ) -> int:
        """Add observable-related atomic requirements."""
        for obs in observables.observables:
            obs_type = obs.get("semantic_type", "unknown")
            capability_status = obs.get("capability_status", "SUPPORTED")

            # Sampling requirement.
            counter += 1
            sampling_req_id = f"REQ_{counter:03d}"
            sampling_info = obs.get("sampling", {})
            sampling_type = (
                sampling_info.get("type", "unknown")
                if isinstance(sampling_info, dict)
                else "unknown"
            )
            reqs.append(AtomicRequirement(
                requirement_id=sampling_req_id,
                category="observable",
                description=f"sample data for {obs_type} ({sampling_type})",
                capability_type="sampling_capability",
                keywords=[obs_type, "sampling", sampling_type],
                mandatory=True,
            ))

            # Analysis requirement.
            counter += 1
            analysis_req_id = f"REQ_{counter:03d}"
            analysis_info = obs.get("analysis", {})
            analysis_method = (
                analysis_info.get("method", "unknown")
                if isinstance(analysis_info, dict)
                else "unknown"
            )
            reqs.append(AtomicRequirement(
                requirement_id=analysis_req_id,
                category="observable",
                description=f"analyze {obs_type} using {analysis_method}",
                capability_type="analysis_capability",
                keywords=[obs_type, "analysis", analysis_method],
                mandatory=True,
                depends_on=[sampling_req_id],
            ))

            # Mark non-supported observables.
            if capability_status == "REQUIRES_NEW_PHYSICS":
                reqs[-1].mandatory = True
                # The analysis requirement itself needs new physics.
                counter += 1
                reqs.append(AtomicRequirement(
                    requirement_id=f"REQ_{counter:03d}",
                    category="observable",
                    description=f"new physics required for {obs_type}",
                    capability_type="new_physics",
                    keywords=[obs_type, "extension"],
                    mandatory=True,
                    depends_on=[analysis_req_id],
                ))

        return counter

    # ------------------------------------------------------------------
    # Dependency edge builder
    # ------------------------------------------------------------------

    def _build_dependency_edges(
        self,
        reqs: list[AtomicRequirement],
        physics: PhysicsDecomposition,
        observables: ObservableDecomposition,
        edges: list[RequirementDependencyEdge],
    ) -> None:
        """Build dependency edges between atomic requirements."""
        # Index requirements by category for quick lookup.
        by_category: dict[str, list[AtomicRequirement]] = {}
        for r in reqs:
            by_category.setdefault(r.category, []).append(r)

        # Solver depends on physics requirements.
        solver_reqs = by_category.get("solver", [])
        physics_reqs = by_category.get("physics", [])
        for solver in solver_reqs:
            for phys in physics_reqs:
                if phys.requirement_id != solver.requirement_id:
                    edges.append(RequirementDependencyEdge(
                        source=solver.requirement_id,
                        target=phys.requirement_id,
                        edge_type="REQUIRES",
                    ))

        # Geometry requirements are required by boundary and mesh.
        geometry_reqs = by_category.get("geometry", [])
        boundary_reqs = by_category.get("boundary", [])
        mesh_reqs = by_category.get("mesh", [])
        for boundary in boundary_reqs:
            for geom in geometry_reqs:
                edges.append(RequirementDependencyEdge(
                    source=boundary.requirement_id,
                    target=geom.requirement_id,
                    edge_type="REQUIRES",
                ))
        for mesh in mesh_reqs:
            for geom in geometry_reqs:
                edges.append(RequirementDependencyEdge(
                    source=mesh.requirement_id,
                    target=geom.requirement_id,
                    edge_type="REQUIRES",
                ))

        # Observable sampling is implemented by mesh.
        observable_reqs = by_category.get("observable", [])
        sampling_reqs = [r for r in observable_reqs if "sampling" in r.description]
        for sampling in sampling_reqs:
            for mesh in mesh_reqs:
                edges.append(RequirementDependencyEdge(
                    source=sampling.requirement_id,
                    target=mesh.requirement_id,
                    edge_type="IMPLEMENTED_BY",
                ))

        # Observable analysis is measured by the observable itself.
        analysis_reqs = [r for r in observable_reqs if "analyze" in r.description]
        for analysis in analysis_reqs:
            # Find the corresponding sampling requirement.
            for sampling in sampling_reqs:
                # Match by observable type in keywords.
                common_keywords = set(analysis.keywords) & set(sampling.keywords)
                if common_keywords:
                    edges.append(RequirementDependencyEdge(
                        source=analysis.requirement_id,
                        target=sampling.requirement_id,
                        edge_type="MEASURED_BY",
                    ))

        # Mesh requirements are validated by observable sampling.
        for mesh in mesh_reqs:
            for sampling in sampling_reqs:
                edges.append(RequirementDependencyEdge(
                    source=mesh.requirement_id,
                    target=sampling.requirement_id,
                    edge_type="VALIDATED_BY",
                ))

        # Physics requirements are implemented by solver.
        for phys in physics_reqs:
            for solver in solver_reqs:
                if phys.requirement_id != solver.requirement_id:
                    edges.append(RequirementDependencyEdge(
                        source=phys.requirement_id,
                        target=solver.requirement_id,
                        edge_type="IMPLEMENTED_BY",
                    ))


__all__ = ["AtomicRequirementDecomposer"]
