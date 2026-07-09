"""Case plan generator.

The :class:`CasePlanGenerator` converts a confirmed
:class:`~fluid_scientist.draft.models.ExperimentDraft` into a
:class:`CasePlan` that the
:class:`~fluid_scientist.case_plan.compiler.NativeCaseCompiler` can
compile into an OpenFOAM case structure.

The generator is deterministic: the same draft always yields the same
case plan shape (only the generated ``case_plan_id`` is non-deterministic).
It performs solver auto-selection, maps requested outputs to
functionObjects, and runs a capability check to determine whether the
case can be compiled natively.
"""

from __future__ import annotations

import uuid

from fluid_scientist.case_plan.models import (
    CasePlan,
    FunctionObjectSpec,
    MeasurementPlanSpec,
    MissingCapability,
)
from fluid_scientist.draft.models import DraftStatus, ExperimentDraft
from fluid_scientist.study_decomposition.capability_checker import (
    CapabilityCheckResult,
    CapabilityPreChecker,
)
from fluid_scientist.study_decomposition.models import ObservableSpec, StudyIntent

# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

# Geometry type -> canonical case type.
_CASE_TYPE_MAP: dict[str, str] = {
    "cylinder": "cylinder_cross_flow",
    "backward_facing_step": "backward_facing_step",
    "cavity": "lid_driven_cavity",
    "pipe": "pipe_flow",
}

# Output observable_id -> functionObject mapping metadata.
# Each entry is (function_object_type, patches, fields, configuration).
_OUTPUT_FO_MAP: dict[str, tuple[str, list[str], list[str], dict]] = {
    "drag": ("forces", ["cylinder"], ["U", "p"], {"rho": "rhoInf", "rhoInf": 1.0}),
    "drag_coefficient": (
        "forces",
        ["cylinder"],
        ["U", "p"],
        {"rho": "rhoInf", "rhoInf": 1.0},
    ),
    "cd": ("forces", ["cylinder"], ["U", "p"], {"rho": "rhoInf", "rhoInf": 1.0}),
    "lift": (
        "forceCoeffs",
        ["cylinder"],
        ["U", "p"],
        {
            "rho": "rhoInf",
            "rhoInf": 1.0,
            "liftDir": [0, 1, 0],
            "dragDir": [1, 0, 0],
            "pitchAxis": [0, 0, 1],
        },
    ),
    "lift_coefficient": (
        "forceCoeffs",
        ["cylinder"],
        ["U", "p"],
        {
            "rho": "rhoInf",
            "rhoInf": 1.0,
            "liftDir": [0, 1, 0],
            "dragDir": [1, 0, 0],
            "pitchAxis": [0, 0, 1],
        },
    ),
    "cl": (
        "forceCoeffs",
        ["cylinder"],
        ["U", "p"],
        {
            "rho": "rhoInf",
            "rhoInf": 1.0,
            "liftDir": [0, 1, 0],
            "dragDir": [1, 0, 0],
            "pitchAxis": [0, 0, 1],
        },
    ),
    "pressure": ("probes", [], ["p"], {"probeLocations": []}),
    "velocity_profile": ("probes", [], ["U"], {"probeLocations": []}),
    "strouhal": ("probes", [], ["U"], {"probeLocations": []}),
    "st": ("probes", [], ["U"], {"probeLocations": []}),
}

# Valid ObservableSpec categories (used when reconstructing from draft dicts).
_VALID_OBSERVABLE_CATEGORIES = frozenset(
    {
        "force",
        "pressure",
        "heat_flux",
        "vortex_structure",
        "wake_deflection",
        "reattachment",
        "spectral",
        "turbulence_statistics",
        "internal_wave",
        "mixing",
        "custom",
    }
)


class CasePlanGenerator:
    """Generate a :class:`CasePlan` from a confirmed :class:`ExperimentDraft`.

    The generator performs a deterministic mapping from draft fields to
    case plan fields, auto-selects a solver when one is not explicitly
    provided, maps requested outputs to functionObjects, and runs a
    capability check to determine whether the case can be compiled.
    """

    def __init__(self, capability_checker: CapabilityPreChecker | None = None):
        self._checker = capability_checker or CapabilityPreChecker()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, draft: ExperimentDraft) -> CasePlan:
        """Generate a :class:`CasePlan` from a confirmed ``ExperimentDraft``.

        Raises:
            ValueError: if ``draft.status`` is not ``confirmed``.
        """
        if draft.status != DraftStatus.CONFIRMED:
            raise ValueError(
                f"draft must be 'confirmed' to generate a case plan, "
                f"got '{draft.status}'"
            )

        # 2-4. Determine case type, solver, and dimensions.
        case_type = self._determine_case_type(draft)
        solver = self._determine_solver(draft)
        dimensions = self._determine_dimensions(draft)

        # 5-10. Generate the various plans.
        geometry_plan = self._generate_geometry_plan(draft)
        mesh_plan = self._generate_mesh_plan(draft, dimensions)
        boundary_condition_plan = self._generate_boundary_condition_plan(draft)
        initial_condition_plan = self._generate_initial_condition_plan(draft)
        physical_model_plan = self._generate_physical_model_plan(draft)
        numerics_plan = self._generate_numerics_plan(draft, solver)

        # 11. Generate measurement plan from requested outputs.
        measurement_plan = self._generate_measurement_plan(draft)

        # 12. Check capabilities.
        missing, can_compile, blocking_reasons = self._check_capabilities(draft)

        # Build the required capabilities list.
        required_capabilities = self._build_required_capabilities(
            solver, draft, measurement_plan
        )

        # 13. Return CasePlan.
        return CasePlan(
            case_plan_id=str(uuid.uuid4()),
            draft_id=draft.draft_id,
            draft_version=draft.version,
            case_type=case_type,
            solver=solver,
            dimensions=dimensions,
            geometry_plan=geometry_plan,
            mesh_plan=mesh_plan,
            boundary_condition_plan=boundary_condition_plan,
            initial_condition_plan=initial_condition_plan,
            physical_model_plan=physical_model_plan,
            numerics_plan=numerics_plan,
            measurement_plan=measurement_plan,
            postprocess_plan=dict(draft.postprocess_plan),
            required_capabilities=required_capabilities,
            missing_capabilities=missing,
            can_compile=can_compile,
            blocking_reasons=blocking_reasons,
        )

    # ------------------------------------------------------------------
    # Case type / solver / dimensions
    # ------------------------------------------------------------------

    def _determine_case_type(self, draft: ExperimentDraft) -> str:
        """Determine the canonical case type from study_type and geometry."""
        geo_type = draft.geometry.get("type", "")
        if geo_type in _CASE_TYPE_MAP:
            return _CASE_TYPE_MAP[geo_type]
        if geo_type:
            return geo_type
        return draft.study_type or "unknown"

    def _determine_solver(self, draft: ExperimentDraft) -> str:
        """Determine the solver, using an explicit value or auto-selecting."""
        solver_dict = draft.solver
        if solver_dict:
            for key in ("name", "solver", "application", "id"):
                val = solver_dict.get(key)
                if val:
                    return str(val)
        return self._auto_select_solver(draft.physics_models)

    def _auto_select_solver(self, physics: dict) -> str:
        """Auto-select a solver based on physics models.

        Selection logic (in priority order):
        - buoyancy present        -> buoyantPimpleFoam
        - turbulent + transient   -> pimpleFoam
        - turbulent + steady      -> simpleFoam
        - laminar + transient     -> pisoFoam
        - laminar + steady        -> simpleFoam
        - default                 -> pimpleFoam

        ``turbulent`` and ``temporal`` must be *explicitly* present in the
        physics dict for the specific rules to apply.  When either key is
        absent the system falls back to the default (pimpleFoam).
        """
        if physics.get("buoyancy", False):
            return "buoyantPimpleFoam"

        turbulent = physics.get("turbulent")
        temporal = physics.get("temporal")

        if turbulent is True:
            if temporal == "transient":
                return "pimpleFoam"
            if temporal == "steady":
                return "simpleFoam"
            return "pimpleFoam"

        if turbulent is False:
            if temporal == "transient":
                return "pisoFoam"
            if temporal == "steady":
                return "simpleFoam"
            return "pimpleFoam"

        return "pimpleFoam"

    def _determine_dimensions(self, draft: ExperimentDraft) -> str:
        """Determine 2D or 3D from geometry or physics models."""
        dim = draft.geometry.get(
            "dimension", draft.physics_models.get("dimension", "")
        )
        if str(dim).upper() == "2D":
            return "2D"
        return "3D"

    # ------------------------------------------------------------------
    # Plan generation
    # ------------------------------------------------------------------

    def _generate_geometry_plan(self, draft: ExperimentDraft) -> dict:
        """Generate the geometry plan from draft.geometry."""
        return dict(draft.geometry)

    def _generate_mesh_plan(self, draft: ExperimentDraft, dimensions: str) -> dict:
        """Generate the mesh plan from draft.mesh or defaults."""
        if draft.mesh:
            return dict(draft.mesh)
        if dimensions == "2D":
            return {"cells_x": 100, "cells_y": 50, "cells_z": 1}
        return {"cells_x": 100, "cells_y": 50, "cells_z": 10}

    def _generate_boundary_condition_plan(self, draft: ExperimentDraft) -> dict:
        """Generate the boundary condition plan from draft.boundary_conditions."""
        return {k: dict(v) for k, v in draft.boundary_conditions.items()}

    def _generate_initial_condition_plan(self, draft: ExperimentDraft) -> dict:
        """Generate the initial condition plan from draft.initial_conditions."""
        return {k: dict(v) for k, v in draft.initial_conditions.items()}

    def _generate_physical_model_plan(self, draft: ExperimentDraft) -> dict:
        """Generate the physical model plan from draft.physics_models."""
        return dict(draft.physics_models)

    def _generate_numerics_plan(
        self, draft: ExperimentDraft, solver: str
    ) -> dict:
        """Generate the numerics plan from draft.numerics or defaults."""
        if draft.numerics:
            plan = dict(draft.numerics)
        else:
            steady = "simple" in solver.lower()
            if steady:
                plan = {
                    "endTime": 1000,
                    "deltaT": 1.0,
                    "writeControl": "timeStep",
                    "writeInterval": 100,
                }
            else:
                plan = {
                    "endTime": 1000,
                    "deltaT": 0.01,
                    "writeControl": "timeStep",
                    "writeInterval": 100,
                }
        # Record steady flag for the compiler.
        plan.setdefault("steady", "simple" in solver.lower())
        return plan

    def _generate_measurement_plan(self, draft: ExperimentDraft) -> MeasurementPlanSpec:
        """Generate the measurement plan from draft.requested_outputs."""
        function_objects: list[FunctionObjectSpec] = []
        for output in draft.requested_outputs:
            obs_id = str(
                output.get("observable_id", output.get("name", ""))
            )
            fo = self._map_output_to_function_object(obs_id)
            if fo is not None:
                function_objects.append(fo)

        write_interval = draft.numerics.get("writeInterval", 100)
        return MeasurementPlanSpec(
            function_objects=function_objects,
            write_interval=write_interval,
        )

    def _map_output_to_function_object(
        self, obs_id: str
    ) -> FunctionObjectSpec | None:
        """Map a requested output observable_id to a FunctionObjectSpec."""
        entry = _OUTPUT_FO_MAP.get(obs_id.lower())
        if entry is None:
            return None
        fo_type, patches, fields, config = entry
        return FunctionObjectSpec(
            function_object_id=f"{fo_type}_{obs_id}",
            function_object_type=fo_type,
            fields=list(fields),
            patches=list(patches),
            configuration=dict(config),
        )

    # ------------------------------------------------------------------
    # Capability checking
    # ------------------------------------------------------------------

    def _check_capabilities(
        self, draft: ExperimentDraft
    ) -> tuple[list[MissingCapability], bool, list[str]]:
        """Check capabilities by building a StudyIntent and running the checker."""
        intent = self._build_intent_from_draft(draft)
        result: CapabilityCheckResult = self._checker.check(intent)

        missing = [
            MissingCapability(
                capability_id=m["capability_id"],
                capability_type=m["capability_type"],
                reason=m["reason"],
                severity=m.get("severity", "blocking"),
            )
            for m in result.missing_capabilities
        ]
        return missing, result.can_compile, list(result.blocking_reasons)

    def _build_intent_from_draft(self, draft: ExperimentDraft) -> StudyIntent:
        """Construct a minimal StudyIntent from an ExperimentDraft.

        This is needed because :class:`CapabilityPreChecker.check` expects a
        :class:`StudyIntent`.  Only the fields the checker actually inspects
        are populated.
        """
        observables: list[ObservableSpec] = []
        for output in draft.requested_outputs:
            observables.append(self._to_observable(output))

        return StudyIntent(
            study_id=draft.study_id or draft.draft_id,
            title=draft.objective or draft.study_type,
            raw_text=draft.objective,
            study_type=draft.study_type,
            research_objective=draft.objective,
            geometry=dict(draft.geometry),
            physical_models=dict(draft.physics_models),
            boundary_conditions=list(draft.boundary_conditions.values()),
            observables=observables,
        )

    @staticmethod
    def _to_observable(output: dict) -> ObservableSpec:
        """Reconstruct an :class:`ObservableSpec` from a draft output dict."""
        obs_id = str(output.get("observable_id", output.get("name", "")))
        category = output.get("category", "custom")
        if category not in _VALID_OBSERVABLE_CATEGORIES:
            category = "custom"
        return ObservableSpec(
            observable_id=obs_id,
            display_name=str(output.get("display_name", obs_id)),
            category=category,  # type: ignore[arg-type]
            required_fields=list(output.get("required_fields", [])),
        )

    # ------------------------------------------------------------------
    # Required capabilities
    # ------------------------------------------------------------------

    @staticmethod
    def _build_required_capabilities(
        solver: str,
        draft: ExperimentDraft,
        measurement_plan: MeasurementPlanSpec,
    ) -> list[str]:
        """Build the list of capabilities required by the case plan."""
        required: list[str] = [f"solver:{solver}"]

        geo_type = draft.geometry.get("type", "")
        if geo_type:
            required.append(f"geometry_generator:{geo_type}")

        for bc_data in draft.boundary_conditions.values():
            bc_type = bc_data.get("type", "")
            if bc_type:
                required.append(f"boundary_condition_writer:{bc_type}")

        for fo in measurement_plan.function_objects:
            required.append(f"function_object_writer:{fo.function_object_type}")

        return required


__all__ = ["CasePlanGenerator"]
