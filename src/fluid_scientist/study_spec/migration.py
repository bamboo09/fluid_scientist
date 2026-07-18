"""Legacy migration: convert ``CylinderFlow2DExperimentSpecV1`` to
:class:`SimulationStudySpec`.

This module provides a :class:`LegacyMigrator` that performs a **read-only**
migration from the legacy cylinder-flow spec dict to the canonical
:class:`SimulationStudySpec`.  No data is lost: any legacy field that does
not have a direct counterpart in the new schema is preserved in the
``extensions`` dict under ``legacy_preservation``.

The mapping strategy is:

* Legacy ``ProvenanceField`` (value + source + status + confidence + reason)
  → :class:`SourcedValue` (value + unit + status + confidence).
* Legacy ``FieldSource`` enum → new ``SourcedValue.status`` literal.
* Legacy ``BoundaryConfig`` (left/right/top/bottom/front/back) → list of
  :class:`BoundaryCondition`.
* Legacy ``SimulationSpec`` → :class:`TimeControl`.
* Legacy ``ObservableSpec`` → :class:`ObservationTarget`.
* Legacy domain / fluid / cylinder → new geometry / physics blocks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .boundaries import BoundaryCondition, BoundaryDefinition
from .geometry import (
    DomainSpec as GeometryDomainSpec,
)
from .geometry import (
    GeometryDefinition,
    GeometryEntity,
    PlacementSpec,
)
from .models import (
    ExecutionDefinition,
    MeshDefinition,
    PhysicsDefinition,
    SimulationStudySpec,
    StudyDefinition,
    ValidationDefinition,
)
from .numerics import NumericsDefinition
from .observations import ObservationDefinition, ObservationTarget
from .provenance import SpecProvenance
from .quantities import Quantity, SourcedValue, TimeControl

__all__ = ["LegacyMigrator"]

# ---------------------------------------------------------------------------
# Source-status mapping: legacy FieldSource -> new SourcedValue.status
# ---------------------------------------------------------------------------

_LEGACY_SOURCE_MAP: dict[str, str] = {
    "USER_CONFIRMED": "user_confirmed",
    "USER_EXPLICIT": "user_explicit",
    "FORMULA_DERIVED": "derived",
    "SYSTEM_DERIVED": "derived",
    "MODEL_RECOMMENDED": "model_recommended",
    "SYSTEM_DEFAULT": "default_pending",
}

# ---------------------------------------------------------------------------
# Semantic boundary type -> role mapping
# ---------------------------------------------------------------------------

_BC_TYPE_MAP: dict[str, tuple[str, str]] = {
    # semantic_type -> (role, bc_type)
    "uniform_velocity_inlet": ("inlet", "velocityInlet"),
    "time_varying_velocity_inlet": ("inlet", "velocityInlet"),
    "spatial_nonuniform_velocity_inlet": ("inlet", "velocityInlet"),
    "pressure_inlet": ("inlet", "pressureInlet"),
    "pressure_outlet": ("outlet", "pressureOutlet"),
    "open_outlet": ("outlet", "pressureOutlet"),
    "advective_outlet": ("outlet", "advectiveOutlet"),
    "no_slip_wall": ("wall", "noSlipWall"),
    "slip_wall": ("wall", "slipWall"),
    "moving_wall": ("wall", "movingWall"),
    "shear_stress": ("wall", "shearStress"),
    "symmetry": ("symmetry", "symmetry"),
    "freestream": ("freestream", "freestream"),
    "open_boundary": ("freestream", "freestream"),
    "periodic": ("cyclic", "cyclic"),
    "empty": ("empty", "empty"),
    "pressure_boundary": ("outlet", "pressureOutlet"),
}


class LegacyMigrator:
    """Migrate legacy ``CylinderFlow2DExperimentSpecV1`` dicts to the new
    :class:`SimulationStudySpec`.

    Usage::

        migrator = LegacyMigrator()
        new_spec = migrator.migrate_from_cylinder_flow_spec(legacy_dict)
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _get(d: dict[str, Any], path: str, default: Any = None) -> Any:
        """Retrieve a nested value using dotted-path notation."""
        parts = path.split(".")
        current: Any = d
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    @classmethod
    def _sourced_value(
        cls,
        pf: dict[str, Any] | None,
        unit: str | None = None,
    ) -> SourcedValue:
        """Convert a legacy ``ProvenanceField`` dict to a ``SourcedValue``."""
        if pf is None:
            return SourcedValue(value=None, unit=unit, status="unknown")
        raw_source = pf.get("source", "SYSTEM_DEFAULT")
        status = _LEGACY_SOURCE_MAP.get(raw_source, "unknown")
        return SourcedValue(
            value=pf.get("value"),
            unit=unit,
            status=status,
            confidence=pf.get("confidence"),
        )

    @classmethod
    def _quantity_from_pf(
        cls,
        pf: dict[str, Any] | None,
        unit: str | None = None,
    ) -> Quantity | None:
        """Convert a legacy ``ProvenanceField`` to a ``Quantity``."""
        if pf is None or pf.get("value") is None:
            return None
        return Quantity(value=pf.get("value"), unit=unit)

    # ------------------------------------------------------------------
    # Sub-block migration methods
    # ------------------------------------------------------------------

    def _migrate_study(self, legacy: dict[str, Any]) -> StudyDefinition:
        return StudyDefinition(
            title=legacy.get("title") or "Migrated Cylinder Flow Study",
            objective=legacy.get("objective") or "Migrated from legacy spec",
            research_questions=[
                g.get("description", "")
                for g in legacy.get("analysis_goals", [])
                if g.get("description")
            ],
        )

    def _migrate_physics(self, legacy: dict[str, Any]) -> PhysicsDefinition:
        fluid = legacy.get("fluid", {})
        cylinder = legacy.get("cylinder", {})
        sim = legacy.get("simulation", {})

        material = self._sourced_value(fluid.get("type"))
        density = self._sourced_value(fluid.get("density_kg_m3"), unit="kg/m^3")
        nu = self._sourced_value(
            fluid.get("kinematic_viscosity_m2_s"), unit="m^2/s"
        )

        # Characteristic length from cylinder.
        char_dim_pf = cylinder.get("characteristic_dimension_m")
        if char_dim_pf is None or char_dim_pf.get("value") is None:
            char_dim_pf = cylinder.get("diameter_m")
        char_length = self._sourced_value(char_dim_pf, unit="m")

        # Reynolds number: try estimate_reynolds from the legacy spec.
        re_val = self._estimate_reynolds(legacy)
        if re_val is not None:
            reynolds = SourcedValue(
                value=re_val, status="derived", unit=None,
            )
        else:
            reynolds = None

        # Velocity: from left boundary or inlet profile.
        velocity_val = self._extract_inlet_velocity(legacy)
        if velocity_val is not None:
            velocity = SourcedValue(
                value=velocity_val, unit="m/s", status="derived",
            )
        else:
            velocity = None

        return PhysicsDefinition(
            material=material,
            density=density,
            kinematic_viscosity=nu,
            reynolds_number=reynolds,
            velocity=velocity,
            characteristic_length=char_length,
        )

    def _estimate_reynolds(self, legacy: dict[str, Any]) -> float | None:
        """Estimate Reynolds number from the legacy spec fields."""
        fluid = legacy.get("fluid", {})
        nu_pf = fluid.get("kinematic_viscosity_m2_s", {})
        nu = nu_pf.get("value")
        if nu is None or nu <= 0:
            return None

        cylinder = legacy.get("cylinder", {})
        char_dim = None
        cd_pf = cylinder.get("characteristic_dimension_m")
        if cd_pf and cd_pf.get("value"):
            char_dim = cd_pf["value"]
        else:
            d_pf = cylinder.get("diameter_m")
            if d_pf and d_pf.get("value"):
                char_dim = d_pf["value"]
            else:
                r_pf = cylinder.get("radius_m")
                if r_pf and r_pf.get("value"):
                    char_dim = r_pf["value"] * 2.0
        if not char_dim:
            char_dim = legacy.get("domain", {}).get("height_m", {}).get("value")
        if not char_dim:
            return None

        vel = self._extract_inlet_velocity(legacy)
        if vel is None or vel <= 0:
            return None
        return vel * char_dim / nu

    def _extract_inlet_velocity(self, legacy: dict[str, Any]) -> float | None:
        """Extract the characteristic inlet velocity from the legacy spec."""
        boundaries = legacy.get("boundaries", {})
        left = boundaries.get("left", {})
        st = left.get("semantic_type")
        if st and st.startswith("uniform_velocity") or st == "time_varying_velocity_inlet" or st == "spatial_nonuniform_velocity_inlet":
            return left.get("inlet_velocity")

        inlet_profile = legacy.get("inlet_profile", {})
        if inlet_profile.get("enabled"):
            params = inlet_profile.get("parameters", {})
            ttype = inlet_profile.get("temporal_type", "constant")
            if ttype == "constant":
                return params.get("velocity") or params.get("max_velocity")
            if ttype == "sinusoidal":
                return params.get("mean_velocity")
            if ttype == "ramp":
                return params.get("end_velocity")
        return None

    def _migrate_geometry(self, legacy: dict[str, Any]) -> GeometryDefinition:
        domain = legacy.get("domain", {})
        dimensions = "2d" if domain.get("dimensionality", "2D").upper() == "2D" else "3d"
        new_domain = GeometryDomainSpec(
            length=self._sourced_value(domain.get("length_m"), unit="m"),
            width=self._sourced_value(domain.get("height_m"), unit="m"),
            height=None,
            dimensions=dimensions,
        )

        entities: dict[str, GeometryEntity] = {}

        # Cylinder entity.
        cylinder = legacy.get("cylinder", {})
        if cylinder:
            radius_pf = cylinder.get("radius_m", {})
            diameter_pf = cylinder.get("diameter_m", {})
            cx_pf = cylinder.get("center_x_m", {})
            cy_pf = cylinder.get("center_y_m", {})
            placement: PlacementSpec | None = None
            if cx_pf.get("value") is not None or cy_pf.get("value") is not None:
                placement = PlacementSpec(
                    x=self._sourced_value(cx_pf, unit="m"),
                    y=self._sourced_value(cy_pf, unit="m"),
                )
            entities["cylinder"] = GeometryEntity(
                entity_id="cylinder",
                semantic_type="cylinder_2d",
                primitive={
                    "type": "circle",
                    "radius": radius_pf.get("value"),
                    "diameter": diameter_pf.get("value"),
                },
                original_user_semantics="cylinder",
                placement=placement,
            )

        # Rectangle entity.
        rect = legacy.get("rectangle", {})
        if rect.get("enabled"):
            entities["rectangle"] = GeometryEntity(
                entity_id="rectangle",
                semantic_type="rectangle_2d",
                primitive={
                    "type": "rectangle",
                    "width": rect.get("width_m", {}).get("value"),
                    "height": rect.get("height_m", {}).get("value"),
                },
                original_user_semantics="rectangle",
                placement=PlacementSpec(
                    x=self._sourced_value(rect.get("center_x_m"), unit="m"),
                    y=self._sourced_value(rect.get("center_y_m"), unit="m"),
                    attachment=rect.get("relation_to_cylinder"),
                ),
            )

        # Triangle entity.
        tri = legacy.get("triangle", {})
        if tri.get("enabled"):
            entities["triangle"] = GeometryEntity(
                entity_id="triangle",
                semantic_type="triangle_2d",
                primitive={
                    "type": "polygon",
                    "n_vertices": 3,
                    "base_width": tri.get("base_width_m", {}).get("value"),
                    "height": tri.get("height_m", {}).get("value"),
                    "apex_direction": tri.get("apex_direction", "up"),
                },
                original_user_semantics="triangle",
                placement=PlacementSpec(
                    x=self._sourced_value(tri.get("center_x_m"), unit="m"),
                    y=self._sourced_value(tri.get("center_y_m"), unit="m"),
                    attachment=tri.get("attached_boundary")
                    or tri.get("relation_to_cylinder"),
                ),
            )

        # Trapezoid entity.
        trap = legacy.get("trapezoid", {})
        if trap.get("enabled"):
            entities["trapezoid"] = GeometryEntity(
                entity_id="trapezoid",
                semantic_type="trapezoid_2d",
                primitive={
                    "type": "polygon",
                    "n_vertices": 4,
                    "top_width": trap.get("top_width_m", {}).get("value"),
                    "bottom_width": trap.get("bottom_width_m", {}).get("value"),
                    "height": trap.get("height_m", {}).get("value"),
                    "apex_direction": trap.get("apex_direction", "up"),
                },
                original_user_semantics="trapezoid",
                placement=PlacementSpec(
                    x=self._sourced_value(trap.get("center_x_m"), unit="m"),
                    y=self._sourced_value(trap.get("center_y_m"), unit="m"),
                    attachment=trap.get("attached_boundary")
                    or trap.get("relation_to_cylinder"),
                ),
            )

        return GeometryDefinition(
            domain=new_domain,
            entities=entities,
            relations=[],
        )

    def _migrate_boundaries(self, legacy: dict[str, Any]) -> BoundaryDefinition:
        bc = legacy.get("boundaries", {})
        conditions: list[BoundaryCondition] = []

        # Map each named boundary side.  The patch_name is derived from the
        # semantic role: inlet/outlet patches get canonical names, while
        # wall/empty/freestream patches keep the domain side name.
        side_map = {
            "left": "left",
            "right": "right",
            "top": "top",
            "bottom_flat": "bottom",
            "bottom_profile_surface": "bottom_profile_surface",
            "front": "front",
            "back": "back",
        }
        for legacy_side, fallback_name in side_map.items():
            b = bc.get(legacy_side, {})
            sem_type = b.get("semantic_type")
            if sem_type is None:
                continue
            role, bc_type = _BC_TYPE_MAP.get(sem_type, ("custom", "custom"))

            # Derive canonical patch name from role.
            if role == "inlet":
                patch_name = "inlet"
            elif role == "outlet":
                patch_name = "outlet"
            else:
                patch_name = fallback_name

            params: dict[str, Any] = {}
            if b.get("inlet_velocity") is not None:
                params["velocity"] = b["inlet_velocity"]
            if b.get("pressure_value") is not None:
                params["pressure"] = b["pressure_value"]
            if b.get("velocity_vector") is not None:
                params["velocity_vector"] = b["velocity_vector"]
            if b.get("freestream_velocity") is not None:
                params["freestream_velocity"] = b["freestream_velocity"]

            raw_source = b.get("source", "SYSTEM_DEFAULT")
            source_status = _LEGACY_SOURCE_MAP.get(raw_source, "unknown")
            conditions.append(
                BoundaryCondition(
                    patch_name=patch_name,
                    role=role,
                    bc_type=bc_type,
                    parameters=params,
                    source_status=source_status,
                )
            )

        # Add the cylinder wall as a no-slip boundary if a cylinder exists.
        cylinder = legacy.get("cylinder", {})
        if cylinder and (
            cylinder.get("radius_m", {}).get("value") is not None
            or cylinder.get("diameter_m", {}).get("value") is not None
        ):
            conditions.append(
                BoundaryCondition(
                    patch_name="cylinder",
                    role="wall",
                    bc_type="noSlipWall",
                    parameters={},
                    source_status="derived",
                )
            )

        return BoundaryDefinition(conditions=conditions)

    def _migrate_numerics(self, legacy: dict[str, Any]) -> NumericsDefinition:
        sim = legacy.get("simulation", {})
        legacy_mode = sim.get("time_mode", "auto")

        # Determine mode: auto -> transient for cylinder flow.
        if legacy_mode == "steady":
            mode = "steady"
        elif legacy_mode == "transient":
            mode = "transient"
        else:  # auto
            mode = "transient"  # cylinder flow is typically transient

        end_time_q = self._quantity_from_pf(
            {"value": sim.get("end_time")}, unit="s"
        )
        delta_t_q = self._quantity_from_pf(
            {"value": sim.get("delta_t")}, unit="s"
        ) if sim.get("delta_t") else None

        max_courant = sim.get("max_courant_number", 0.5)

        time_control = TimeControl(
            mode=mode,
            start_time=Quantity(value=0.0, unit="s"),
            end_time=end_time_q,
            delta_t=delta_t_q,
            adaptive=False,
            max_courant=max_courant,
        )

        # Turbulence model from flow regime.
        regime = sim.get("flow_regime", "auto")
        if regime == "laminar":
            turbulence_model = "laminar"
        elif regime == "turbulent":
            turbulence_model = "RANS_kOmegaSST"
        else:  # auto
            re_val = self._estimate_reynolds(legacy)
            if re_val is not None and re_val < 2300:
                turbulence_model = "laminar"
            else:
                turbulence_model = "RANS_kOmegaSST"

        return NumericsDefinition(
            time=time_control,
            solver="icoFoam" if turbulence_model == "laminar" else "pimpleFoam",
            discretization={
                "ddtSchemes": {"ddtScheme": "backward" if mode == "transient" else "steadyState"},
                "gradSchemes": {"gradScheme": "Gauss linear"},
                "divSchemes": {"divScheme": "Gauss linear"},
                "laplacianSchemes": {"laplacianScheme": "Gauss linear corrected"},
            },
            turbulence_model=turbulence_model,
        )

    def _migrate_observations(self, legacy: dict[str, Any]) -> ObservationDefinition:
        targets: list[ObservationTarget] = []
        metric_map = {
            "cylinder_drag": ("cd", "forceCoeffs"),
            "cylinder_lift": ("cl", "forceCoeffs"),
            "point_velocity": ("point_velocity", "probes"),
            "section_mean_velocity": ("section_mean_velocity", "surfaceFieldValue"),
            "wall_shear_stress": ("wall_shear", "wallShearStress"),
            "velocity_magnitude_field": ("velocity_field", "fieldAverage"),
            "pressure_field": ("pressure_field", "fieldAverage"),
            "vorticity_field": ("vorticity", "vorticity"),
            "wake_shedding_frequency": ("strouhal", "probes"),
        }
        for obs in legacy.get("observables", []):
            legacy_type = obs.get("type")
            metric, fo_type = metric_map.get(legacy_type, ("custom", None))
            params: dict[str, Any] = {}
            if obs.get("point"):
                params["point"] = obs["point"]
            if obs.get("section_x") is not None:
                params["section_x"] = obs["section_x"]
            if obs.get("component"):
                params["component"] = obs["component"]
            if obs.get("label"):
                params["label"] = obs["label"]
            targets.append(
                ObservationTarget(
                    target_id=obs.get("label", legacy_type or "unknown"),
                    metric=metric,
                    parameters=params,
                    function_object_type=fo_type,
                )
            )

        return ObservationDefinition(
            targets=targets,
            probes=[],
            postprocessing=[],
        )

    def _migrate_mesh(self, legacy: dict[str, Any]) -> MeshDefinition:
        # The legacy spec does not have a dedicated mesh block; derive from
        # domain and cylinder.
        domain = legacy.get("domain", {})
        length = domain.get("length_m", {}).get("value", 1.0)
        height = domain.get("height_m", {}).get("value", 1.0)
        # Heuristic resolution: ~100 cells per unit length.
        approx_cells = max(1, int(length * 100)) if isinstance(length, int | float) else 100
        return MeshDefinition(
            resolution=SourcedValue(
                value=approx_cells, unit="cells", status="derived",
            ),
            mesh_type="blockMesh",
            refinement_regions=[],
        )

    def _migrate_execution(self, legacy: dict[str, Any]) -> ExecutionDefinition:
        return ExecutionDefinition(
            target_id="workstation",
            parallel=False,
            cores=None,
        )

    def _migrate_validation(self, legacy: dict[str, Any]) -> ValidationDefinition:
        checks = ["courant_number"]
        if legacy.get("simulation", {}).get("time_mode") in ("transient", "auto"):
            checks.append("mass_balance")
        return ValidationDefinition(checks=checks)

    def _migrate_provenance(self, legacy: dict[str, Any]) -> SpecProvenance:
        return SpecProvenance(
            created_at=self._now(),
            created_by="legacy_migrator",
            parent_version=legacy.get("spec_version"),
            creation_turn_id=None,
            modification_history=[
                {
                    "patch_id": "legacy_migration",
                    "timestamp": self._now(),
                    "summary": (
                        f"Migrated from legacy "
                        f"{legacy.get('case_family', 'cylinder_flow_2d')} "
                        f"spec v{legacy.get('spec_version', 1)}"
                    ),
                }
            ],
        )

    def _build_extensions(self, legacy: dict[str, Any]) -> dict[str, Any]:
        """Preserve all legacy fields that have no direct counterpart.

        This guarantees no data loss.  The full legacy dict is stored
        under ``extensions.legacy_preservation``.
        """
        return {
            "legacy_preservation": {
                "original_schema": "CylinderFlow2DExperimentSpecV1",
                "case_family": legacy.get("case_family"),
                "pipeline_id": legacy.get("pipeline_id"),
                "pipeline_stage": legacy.get("pipeline_stage"),
                "raw_spec": legacy,
            },
            "legacy_bottom_profile": legacy.get("bottom_profile"),
            "legacy_forcing": legacy.get("forcing"),
            "legacy_inlet_profile": legacy.get("inlet_profile"),
            "legacy_initial_conditions": legacy.get("initial_conditions"),
            "legacy_assumptions": legacy.get("assumptions"),
            "legacy_ambiguities": legacy.get("ambiguities"),
            "legacy_unresolved_fields": legacy.get("unresolved_fields"),
            "legacy_blocking_issues": legacy.get("blocking_issues"),
            "legacy_recommendations": legacy.get("recommendations"),
            "legacy_decision_summary": legacy.get("decision_summary"),
            "legacy_draft_status": legacy.get("draft_status"),
            "legacy_user_input_text": legacy.get("user_input_text"),
            "legacy_flow_topology": legacy.get("flow_topology"),
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def migrate_from_cylinder_flow_spec(
        self,
        legacy_spec: dict[str, Any],
    ) -> SimulationStudySpec:
        """Convert a legacy ``CylinderFlow2DExperimentSpecV1`` dict to a
        :class:`SimulationStudySpec`.

        This migration is **read-only**: the input dict is never modified,
        and all legacy data that has no direct counterpart in the new schema
        is preserved in ``spec.extensions.legacy_preservation``.

        Parameters
        ----------
        legacy_spec:
            A dict representation of the legacy
            ``CylinderFlow2DExperimentSpecV1``.

        Returns
        -------
        SimulationStudySpec
            The migrated, canonical spec.
        """
        spec_id = legacy_spec.get("experiment_id") or legacy_spec.get("spec_id") or "migrated_spec"
        session_id = legacy_spec.get("spec_id") or spec_id

        return SimulationStudySpec(
            schema_version="1.0",
            spec_id=spec_id,
            session_id=session_id,
            version=1,
            parent_version=None,
            study=self._migrate_study(legacy_spec),
            physics=self._migrate_physics(legacy_spec),
            geometry=self._migrate_geometry(legacy_spec),
            boundaries=self._migrate_boundaries(legacy_spec),
            initial_conditions=[],
            numerics=self._migrate_numerics(legacy_spec),
            mesh=self._migrate_mesh(legacy_spec),
            observations=self._migrate_observations(legacy_spec),
            execution=self._migrate_execution(legacy_spec),
            validation=self._migrate_validation(legacy_spec),
            extensions=self._build_extensions(legacy_spec),
            provenance=self._migrate_provenance(legacy_spec),
        )
