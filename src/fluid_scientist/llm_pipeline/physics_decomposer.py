"""Pass 5: Physics Decomposer -- determine the physics configuration.

The :class:`PhysicsDecomposer` takes the extracted facts and normalized
concepts from earlier passes and determines the full physics
configuration: governing equations, compressibility regime, time mode,
turbulence model, heat transfer, multiphase, moving mesh, external
forces, multi-region coupling, and the recommended solver module.

The logic is rule-based and follows the principle of *only deriving
what the user stated* -- no hidden defaults beyond the safe baselines
(incompressible, transient, laminar).
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.llm_pipeline.models import (
    ExtractedFact,
    NormalizedConcept,
    PhysicsDecomposition,
)


class PhysicsDecomposer:
    """Decompose physics configuration from facts and normalized concepts.

    The decomposer inspects the facts and concepts for physics-related
    signals and builds a :class:`PhysicsDecomposition` that captures the
    governing equations, regime, and recommended solver module.

    Defaults (applied only when the user did not state otherwise):

    * compressibility: incompressible
    * time_mode: transient
    * turbulence: laminar
    * heat_transfer: False
    * multiphase: False
    * moving_mesh: False
    """

    def decompose(
        self,
        facts: list[ExtractedFact],
        concepts: list[NormalizedConcept],
    ) -> PhysicsDecomposition:
        """Decompose the physics from facts and concepts.

        Args:
            facts: The list of facts extracted in Pass 1.
            concepts: The list of normalized concepts from Pass 3.

        Returns:
            A :class:`PhysicsDecomposition` with the determined physics.
        """
        # Collect all text signals from both facts and concepts.
        all_values: list[str] = []
        all_texts: list[str] = []
        for f in facts:
            if f.value is not None:
                all_values.append(str(f.value))
            all_texts.append(f.raw_text.lower())
        for c in concepts:
            if c.normalized_concept:
                all_values.append(c.normalized_concept)
            all_texts.append(c.raw_text.lower())

        combined_values = [v.lower() for v in all_values]
        combined_texts = " ".join(all_texts)

        # --- Determine compressibility ---
        compressible = self._is_compressible(combined_values, combined_texts)

        # --- Determine time mode ---
        time_mode = self._determine_time_mode(combined_values, facts)

        # --- Determine turbulence ---
        turbulence, turbulence_model = self._determine_turbulence(
            combined_values, combined_texts
        )

        # --- Determine heat transfer ---
        heat_transfer = self._determine_heat_transfer(combined_values, combined_texts)

        # --- Determine multiphase ---
        multiphase = self._determine_multiphase(combined_values, combined_texts)

        # --- Determine moving mesh ---
        moving_mesh = self._determine_moving_mesh(combined_values, combined_texts)

        # --- Determine external forces ---
        external_forces = self._determine_external_forces(
            combined_values, combined_texts
        )

        # --- Determine material models ---
        material_models = self._determine_material_models(facts)

        # --- Determine multi-region coupling ---
        multi_region_coupling = self._determine_multi_region_coupling(facts, concepts)

        # --- Build equations list ---
        equations = self._build_equations(
            compressible, heat_transfer, multiphase, moving_mesh, external_forces
        )

        # --- Determine recommended solver module ---
        recommended_solver = self._determine_solver_module(
            compressible, heat_transfer, multiphase, moving_mesh
        )

        return PhysicsDecomposition(
            equations=equations,
            compressibility="compressible" if compressible else "incompressible",
            time_mode=time_mode,
            turbulence=turbulence,
            heat_transfer=heat_transfer,
            multiphase=multiphase,
            moving_mesh=moving_mesh,
            material_models=material_models,
            external_forces=external_forces,
            multi_region_coupling=multi_region_coupling,
            recommended_solver_module=recommended_solver,
        )

    # ------------------------------------------------------------------
    # Individual physics determinations
    # ------------------------------------------------------------------

    def _is_compressible(
        self, values: list[str], combined_text: str
    ) -> bool:
        """Check if the simulation is compressible."""
        compressible_signals = {"compressible_flow", "compressible", "可压缩"}
        if any(v in compressible_signals for v in values):
            return True
        return any(signal in combined_text for signal in compressible_signals)

    def _determine_time_mode(
        self, values: list[str], facts: list[ExtractedFact]
    ) -> str:
        """Determine steady or transient mode."""
        # Check facts first (explicit user statement).
        for f in facts:
            if f.category == "constraint":
                if f.value == "steady":
                    return "steady"
                if f.value == "transient":
                    return "transient"
        # Check normalized concepts.
        if "steady_time_mode" in values:
            return "steady"
        if "transient_time_mode" in values:
            return "transient"
        # Default: transient (safe default).
        return "transient"

    def _determine_turbulence(
        self, values: list[str], combined_text: str
    ) -> tuple[str, str]:
        """Determine the turbulence model.

        Returns:
            A tuple of (turbulence_approach, turbulence_model_name).
        """
        # Check for specific RANS models first.
        specific_models = ["komegasst", "spalartallmaras", "kepsilon"]
        for model in specific_models:
            for v in values:
                if v == model:
                    return "RANS", model

        # Check for LES.
        les_signals = {"large_eddy_simulation", "les"}
        for v in values:
            if v in les_signals:
                return "LES", ""

        # Check for RANS (generic).
        rans_signals = {"reynolds_averaged_navier_stokes", "rans"}
        for v in values:
            if v in rans_signals:
                return "RANS", ""

        # Check for DNS.
        dns_signals = {"dns", "direct_numerical_simulation"}
        for v in values:
            if v in dns_signals:
                return "DNS", ""

        # Default: laminar.
        return "laminar", ""

    def _determine_heat_transfer(
        self, values: list[str], combined_text: str
    ) -> bool:
        """Determine if heat transfer is active."""
        # Heat flux observable implies heat transfer.
        heat_signals = {"wall_heat_flux", "heat flux", "热流"}
        for v in values:
            if v in heat_signals:
                return True
        for signal in heat_signals:
            if signal in combined_text:
                return True
        # Temperature parameter implies heat transfer.
        return any("temperature" in v or "温度" in v for v in values)

    def _determine_multiphase(
        self, values: list[str], combined_text: str
    ) -> bool:
        """Determine if multiphase flow is active."""
        multiphase_signals = {
            "multiphase_flow", "multiphase", "多相", "two-phase", "两相",
        }
        if any(v in multiphase_signals for v in values):
            return True
        return any(signal in combined_text for signal in multiphase_signals)

    def _determine_moving_mesh(
        self, values: list[str], combined_text: str
    ) -> bool:
        """Determine if moving mesh is active."""
        moving_signals = {"moving_mesh", "moving", "动网格", "移动"}
        if any(v in moving_signals for v in values):
            return True
        return any(signal in combined_text for signal in moving_signals)

    def _determine_external_forces(
        self, values: list[str], combined_text: str
    ) -> list[str]:
        """Determine external body forces."""
        forces: list[str] = []
        if "gravity_body_force" in values or "gravity" in combined_text or "重力" in combined_text:
            forces.append("gravity")
        if "buoyancy_force" in values or "buoyancy" in combined_text or "浮力" in combined_text:
            forces.append("buoyancy")
        # Also check for buoyancy candidates (ambiguous).
        for v in values:
            if ("boussinesq" in v or "full_buoyancy" in v) and "buoyancy" not in forces:
                forces.append("buoyancy")
        return forces

    def _determine_material_models(
        self, facts: list[ExtractedFact]
    ) -> list[dict[str, Any]]:
        """Determine material models from facts."""
        models: list[dict[str, Any]] = []
        # Check for density parameter.
        rho: Any = None
        nu: Any = None
        for f in facts:
            if f.category == "parameter":
                raw_lower = f.raw_text.lower()
                if "rho" in raw_lower or "密度" in raw_lower:
                    rho = f.value
                if "nu" in raw_lower or "ν" in raw_lower or "黏度" in raw_lower:
                    nu = f.value

        if rho is not None or nu is not None:
            model: dict[str, Any] = {"kind": "newtonian_fluid"}
            if rho is not None:
                model["density"] = rho
            if nu is not None:
                model["kinematic_viscosity"] = nu
            models.append(model)

        # If no explicit material parameters, note default.
        if not models:
            models.append({"kind": "newtonian_fluid", "source": "default"})

        return models

    def _determine_multi_region_coupling(
        self,
        facts: list[ExtractedFact],
        concepts: list[NormalizedConcept],
    ) -> list[dict[str, Any]]:
        """Determine multi-region coupling requirements."""
        coupling: list[dict[str, Any]] = []
        # Check for conjugate heat transfer signals.
        for c in concepts:
            if "conjugate" in c.normalized_concept.lower():
                coupling.append({
                    "type": "conjugate_heat_transfer",
                    "regions": ["fluid", "solid"],
                })
        # Check for multiple entities that might imply multi-region.
        entity_count = sum(1 for f in facts if f.category == "entity")
        if entity_count > 1:
            coupling.append({
                "type": "multi_entity_domain",
                "entity_count": entity_count,
            })
        return coupling

    def _build_equations(
        self,
        compressible: bool,
        heat_transfer: bool,
        multiphase: bool,
        moving_mesh: bool,
        external_forces: list[str],
    ) -> list[str]:
        """Build the list of governing equations."""
        equations: list[str] = []
        if compressible:
            equations.append("compressible_navier_stokes")
        else:
            equations.append("incompressible_navier_stokes")
        if heat_transfer:
            equations.append("energy_equation")
        if multiphase:
            equations.append("volume_fraction_transport")
        if moving_mesh:
            equations.append("ale_mesh_motion")
        if "gravity" in external_forces or "buoyancy" in external_forces:
            equations.append("body_force_term")
        return equations

    def _determine_solver_module(
        self,
        compressible: bool,
        heat_transfer: bool,
        multiphase: bool,
        moving_mesh: bool,
    ) -> str:
        """Determine the recommended OpenFOAM solver module.

        Mapping rules:
        * Compressible → ``"fluid"``
        * Incompressible + heat transfer → ``"isothermalFluid"``
        * Incompressible + multiphase → ``"multiphaseEulerFluid"``
        * Incompressible + moving mesh → ``"overRigidBodyFluid"``
        * Default (incompressible, isothermal, single-phase) →
          ``"incompressibleFluid"``
        """
        if compressible:
            return "fluid"
        if multiphase:
            return "multiphaseEulerFluid"
        if heat_transfer:
            return "isothermalFluid"
        if moving_mesh:
            return "overRigidBodyFluid"
        return "incompressibleFluid"


__all__ = ["PhysicsDecomposer"]
