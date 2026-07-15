"""Scientific consistency validation for RequestedCaseIR.

This validator detects scientifically contradictory or implausible
combinations in the Case IR.  Unlike schema and reference validators
(which check *structure*), this validator checks *physics*: it knows
that LES requires a transient simulation, that steady-state frequency
spectra are meaningless, and that isothermal conditions are incompatible
with wall heat flux boundary conditions.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.case_ir.models import (
    BoundaryIntent,
    Observable,
    OperatingStage,
    ParameterValue,
    RequestedCaseIR,
)
from fluid_scientist.case_ir.validators.schema_validator import ValidationIssue


class ScientificConsistencyValidator:
    """Validates scientific consistency of the Case IR.

    Checks performed:

    - ``steady`` + ``frequency_spectrum`` observable -> CONFLICT.
    - 2D simulation + ``spanwise_flip`` -> CONFLICT.
    - ``isothermal`` (heat_transfer=False) + ``wall_heat_flux`` BC -> CONFLICT.
    - ``LES`` / ``DES`` / ``DNS`` + ``steady`` -> CONFLICT.
    - Reynolds number relation consistency (``Re = U*L/nu``).
    - Periodic direction vs geometry.
    - Multiphase observable without a phase fraction field.
    - ``forceCoeffs`` observable without ``Aref`` / ``lRef``.
    - Sampling window outside operating stage time ranges.
    - Compressible flow without density / temperature fields.
    - Moving mesh without motion entity.
    - Porous media region without porous material.
    """

    def validate(self, case_ir: RequestedCaseIR) -> list[ValidationIssue]:
        """Run all scientific consistency checks."""
        issues: list[ValidationIssue] = []

        issues.extend(self._check_steady_frequency_spectrum(case_ir))
        issues.extend(self._check_2d_spanwise_flip(case_ir))
        issues.extend(self._check_isothermal_heat_flux(case_ir))
        issues.extend(self._check_les_steady(case_ir))
        issues.extend(self._check_reynolds_consistency(case_ir))
        issues.extend(self._check_periodic_vs_geometry(case_ir))
        issues.extend(self._check_multiphase_fields(case_ir))
        issues.extend(self._check_force_coeffs_refs(case_ir))
        issues.extend(self._check_sampling_window(case_ir))
        issues.extend(self._check_compressible_fields(case_ir))
        issues.extend(self._check_moving_mesh(case_ir))
        issues.extend(self._check_porous_consistency(case_ir))

        return issues

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_steady_frequency_spectrum(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Steady-state simulations cannot produce frequency spectra."""
        issues: list[ValidationIssue] = []
        if case_ir.physics.time_mode != "steady":
            return issues
        for i, obs in enumerate(case_ir.observables):
            if obs.semantic_type in {
                "frequency_spectrum",
                "power_spectral_density",
                "psd",
                "spectral_analysis",
            }:
                issues.append(
                    ValidationIssue(
                        code="STEADY_FREQUENCY_SPECTRUM_CONFLICT",
                        path=f"observables[{i}].semantic_type",
                        message=(
                            f"Observable '{obs.id}' requests a frequency "
                            f"spectrum but the simulation is steady-state. "
                            f"Frequency analysis requires transient data."
                        ),
                    )
                )
        return issues

    def _check_2d_spanwise_flip(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """2D simulations with spanwise_flip are contradictory.

        A 2D case is detected when a boundary intent with semantic_role
        containing ``"empty"`` or ``"2d"`` exists, or when the mesh
        strategy is ``block_mesh`` and an ``empty_2d`` boundary is present.
        """
        issues: list[ValidationIssue] = []
        is_2d = self._detect_2d(case_ir)
        if not is_2d:
            return issues

        for i, obs in enumerate(case_ir.observables):
            if obs.semantic_type in {"spanwise_flip", "wake_flip", "flip"}:
                issues.append(
                    ValidationIssue(
                        code="TWO_D_SPANWISE_FLIP_CONFLICT",
                        path=f"observables[{i}].semantic_type",
                        message=(
                            f"Observable '{obs.id}' requests spanwise flip "
                            f"detection but the simulation is 2D. Spanwise "
                            f"flip is a 3D phenomenon."
                        ),
                    )
                )
            if "spanwise" in obs.semantic_type.lower():
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="TWO_D_SPANWISE_CONFLICT",
                        path=f"observables[{i}].semantic_type",
                        message=(
                            f"Observable '{obs.id}' references spanwise "
                            f"behaviour in a 2D simulation."
                        ),
                    )
                )

        # Also check additional_physics
        for phys in case_ir.physics.additional_physics:
            if "spanwise" in phys.lower() or "flip" in phys.lower():
                issues.append(
                    ValidationIssue(
                        code="TWO_D_SPANWISE_FLIP_CONFLICT",
                        path="physics.additional_physics",
                        message=(
                            f"Additional physics '{phys}' references "
                            f"spanwise behaviour in a 2D simulation."
                        ),
                    )
                )
        return issues

    def _check_isothermal_heat_flux(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Isothermal (heat_transfer=False) + wall heat flux -> CONFLICT."""
        issues: list[ValidationIssue] = []
        if case_ir.physics.heat_transfer:
            return issues

        for i, bc in enumerate(case_ir.boundary_intents):
            role_lower = bc.semantic_role.lower()
            if "heat_flux" in role_lower or "wall_heat_flux" in role_lower:
                issues.append(
                    ValidationIssue(
                        code="ISOTHERMAL_HEAT_FLUX_CONFLICT",
                        path=f"boundary_intents[{i}].semantic_role",
                        message=(
                            f"Boundary intent '{bc.id}' specifies heat flux "
                            f"but physics.heat_transfer is False (isothermal)."
                        ),
                    )
                )
            # Check parameters for heat flux values
            for pname, pval in bc.parameters.items():
                if "heat" in pname.lower() and "flux" in pname.lower():
                    issues.append(
                        ValidationIssue(
                            code="ISOTHERMAL_HEAT_FLUX_CONFLICT",
                            path=f"boundary_intents[{i}].parameters.{pname}",
                            message=(
                                f"Boundary intent '{bc.id}' has a heat flux "
                                f"parameter '{pname}' but "
                                f"physics.heat_transfer is False."
                            ),
                        )
                    )
        return issues

    def _check_les_steady(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """LES, DES, and DNS require transient simulations."""
        issues: list[ValidationIssue] = []
        turb = case_ir.physics.turbulence
        if turb in {"LES", "DES", "DNS"} and case_ir.physics.time_mode == "steady":
            issues.append(
                ValidationIssue(
                    code="LES_STEADY_CONFLICT",
                    path="physics",
                    message=(
                        f"Turbulence model '{turb}' requires a transient "
                        f"simulation, but physics.time_mode is 'steady'. "
                        f"LES/DES/DNS are inherently unsteady approaches."
                    ),
                )
            )
        return issues

    def _check_reynolds_consistency(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Verify Reynolds number derived constraints are consistent.

        If a derived constraint expresses ``Re = U * L / nu`` (or a
        rearrangement), we attempt to extract the numerical values of
        ``U``, ``L``, and ``nu`` from the Case IR parameters and verify
        that the computed ``Re`` matches the stored value.
        """
        issues: list[ValidationIssue] = []
        for i, dc in enumerate(case_ir.derived_constraints):
            expr_lower = dc.expression.lower().replace(" ", "")
            if "re" not in expr_lower:
                continue
            if not any(
                keyword in expr_lower
                for keyword in ["u*l/nu", "u*l/nu", "u*l/ν"]
            ):
                continue

            # Try to extract values
            values = self._extract_param_values(case_ir)
            u_val = values.get("U") or values.get("U_ref") or values.get("velocity")
            l_val = values.get("L") or values.get("L_ref") or values.get("diameter") or values.get("length")
            nu_val = values.get("nu") or values.get("kinematic_viscosity") or values.get("viscosity")
            re_val = values.get("Re") or values.get("reynolds_number")

            if u_val is not None and l_val is not None and nu_val is not None:
                if nu_val == 0:
                    issues.append(
                        ValidationIssue(
                            code="ZERO_VISCOSITY",
                            path=f"derived_constraints[{i}].expression",
                            message=(
                                f"Derived constraint '{dc.id}' divides by "
                                f"nu=0 (zero kinematic viscosity)."
                            ),
                        )
                    )
                else:
                    computed_re = u_val * l_val / nu_val
                    if re_val is not None:
                        relative_error = abs(computed_re - re_val) / max(abs(re_val), 1e-30)
                        if relative_error > 0.05:  # 5% tolerance
                            issues.append(
                                ValidationIssue(
                                    code="REYNOLDS_MISMATCH",
                                    path=f"derived_constraints[{i}].expression",
                                    message=(
                                        f"Derived constraint '{dc.id}': "
                                        f"Re = U*L/nu = {u_val}*{l_val}/"
                                        f"{nu_val} = {computed_re:.2f}, "
                                        f"but stored Re = {re_val} "
                                        f"(relative error: {relative_error:.1%})."
                                    ),
                                )
                            )
        return issues

    def _check_periodic_vs_geometry(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Periodic boundary conditions require a periodic geometry direction.

        If a boundary intent uses a periodic/cyclic semantic role, the
        geometry should support it (e.g., the mesh should have matching
        periodic faces).  We check that at least two periodic boundaries
        are defined (periodic boundaries come in pairs).
        """
        issues: list[ValidationIssue] = []
        periodic_bcs = [
            (i, bc)
            for i, bc in enumerate(case_ir.boundary_intents)
            if "periodic" in bc.semantic_role.lower()
            or "cyclic" in bc.semantic_role.lower()
        ]
        if len(periodic_bcs) == 1:
            issues.append(
                ValidationIssue(
                    code="UNPAIRED_PERIODIC_BC",
                    path=f"boundary_intents[{periodic_bcs[0][0]}]",
                    message=(
                        f"Periodic boundary intent "
                        f"'{periodic_bcs[0][1].id}' has no matching pair. "
                        f"Periodic boundaries must come in pairs."
                    ),
                )
            )

        # Check that periodic direction is compatible with 2D
        if periodic_bcs and self._detect_2d(case_ir):
            for i, bc in periodic_bcs:
                if "spanwise" in bc.semantic_role.lower() or "z" in bc.target_patch.lower():
                    issues.append(
                        ValidationIssue(
                            code="PERIODIC_2D_SPANWISE_CONFLICT",
                            path=f"boundary_intents[{i}]",
                            message=(
                                f"Periodic boundary '{bc.id}' references the "
                                f"spanwise (z) direction in a 2D simulation."
                            ),
                        )
                    )
        return issues

    def _check_multiphase_fields(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Multiphase observables require a phase fraction field.

        If ``physics.multiphase`` is True or an observable references
        multiphase quantities, a phase fraction field (``alpha``,
        ``alpha.phase``, etc.) should be present in ``fields``.
        """
        issues: list[ValidationIssue] = []
        field_names = {f.name for f in case_ir.fields}
        has_phase_fraction = any(
            "alpha" in fname.lower() or "phase" in fname.lower()
            for fname in field_names
        )

        multiphase_observables = [
            (i, obs)
            for i, obs in enumerate(case_ir.observables)
            if any(
                kw in obs.semantic_type.lower()
                for kw in ("phase", "interface", "volume_fraction", "vof")
            )
        ]

        if case_ir.physics.multiphase and not has_phase_fraction:
            issues.append(
                ValidationIssue(
                    code="MULTIPHASE_MISSING_PHASE_FIELD",
                    path="fields",
                    message=(
                        "physics.multiphase is True but no phase fraction "
                        "field (alpha.*) is defined in fields."
                    ),
                )
            )

        for i, obs in multiphase_observables:
            if not has_phase_fraction:
                issues.append(
                    ValidationIssue(
                        code="MULTIPHASE_OBSERVABLE_MISSING_FIELD",
                        path=f"observables[{i}].semantic_type",
                        message=(
                            f"Observable '{obs.id}' references multiphase "
                            f"behaviour but no phase fraction field is "
                            f"defined."
                        ),
                    )
                )
        return issues

    def _check_force_coeffs_refs(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """forceCoeffs observables require Aref and lRef parameters."""
        issues: list[ValidationIssue] = []
        for i, obs in enumerate(case_ir.observables):
            if obs.semantic_type not in {
                "drag_coefficient",
                "lift_coefficient",
                "force_coefficients",
                "force_coeffs",
                "cl_cd",
            }:
                continue

            # Check for Aref / lRef in observable analysis or sampling
            analysis_str = str(obs.analysis).lower()
            sampling_str = str(obs.sampling).lower()
            combined = analysis_str + " " + sampling_str

            has_aref = "aref" in combined
            has_lref = "lref" in combined

            # Also check boundary intent parameters
            for bc in case_ir.boundary_intents:
                for pname in bc.parameters:
                    if "aref" in pname.lower():
                        has_aref = True
                    if "lref" in pname.lower():
                        has_lref = True

            # Also check entity parameters
            for entity in case_ir.entities:
                for pname in entity.parameters:
                    if "aref" in pname.lower():
                        has_aref = True
                    if "lref" in pname.lower():
                        has_lref = True

            if not has_aref:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="FORCE_COEFFS_MISSING_AREF",
                        path=f"observables[{i}].semantic_type",
                        message=(
                            f"Observable '{obs.id}' computes force "
                            f"coefficients but no reference area (Aref) "
                            f"is specified."
                        ),
                    )
                )
            if not has_lref:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="FORCE_COEFFS_MISSING_LREF",
                        path=f"observables[{i}].semantic_type",
                        message=(
                            f"Observable '{obs.id}' computes force "
                            f"coefficients but no reference length (lRef) "
                            f"is specified."
                        ),
                    )
                )
        return issues

    def _check_sampling_window(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Sampling windows in observables must be within operating stages."""
        issues: list[ValidationIssue] = []
        if not case_ir.operating_stages:
            return issues

        # Compute the total time range from operating stages
        all_starts: list[float] = []
        all_ends: list[float] = []
        for stage in case_ir.operating_stages:
            if stage.time_range and len(stage.time_range) == 2:
                all_starts.append(stage.time_range[0])
                all_ends.append(stage.time_range[1])

        if not all_starts:
            return issues

        global_start = min(all_starts)
        global_end = max(all_ends)

        for i, obs in enumerate(case_ir.observables):
            sampling = obs.sampling
            if not sampling:
                continue
            start = sampling.get("start_time") or sampling.get("start")
            end = sampling.get("end_time") or sampling.get("end")
            if start is not None and isinstance(start, (int, float)):
                if start < global_start or start > global_end:
                    issues.append(
                        ValidationIssue(
                            code="SAMPLING_WINDOW_OUTSIDE_STAGES",
                            path=f"observables[{i}].sampling.start_time",
                            message=(
                                f"Observable '{obs.id}' sampling start "
                                f"({start}) is outside the operating stage "
                                f"time range [{global_start}, {global_end}]."
                            ),
                        )
                    )
            if end is not None and isinstance(end, (int, float)):
                if end < global_start or end > global_end:
                    issues.append(
                        ValidationIssue(
                            code="SAMPLING_WINDOW_OUTSIDE_STAGES",
                            path=f"observables[{i}].sampling.end_time",
                            message=(
                                f"Observable '{obs.id}' sampling end "
                                f"({end}) is outside the operating stage "
                                f"time range [{global_start}, {global_end}]."
                            ),
                        )
                    )
        return issues

    def _check_compressible_fields(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Compressible flow requires density and temperature fields."""
        issues: list[ValidationIssue] = []
        if case_ir.physics.flow_regime != "compressible":
            return issues

        field_names = {f.name for f in case_ir.fields}
        if field_names and "T" not in field_names and "temperature" not in field_names:
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="COMPRESSIBLE_MISSING_TEMPERATURE",
                    path="fields",
                    message=(
                        "Compressible flow simulation has no temperature "
                        "field (T) defined."
                    ),
                )
            )
        if field_names and "rho" not in field_names and "density" not in field_names:
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="COMPRESSIBLE_MISSING_DENSITY",
                    path="fields",
                    message=(
                        "Compressible flow simulation has no density "
                        "field (rho) defined."
                    ),
                )
            )
        return issues

    def _check_moving_mesh(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Moving mesh requires at least one entity with motion."""
        issues: list[ValidationIssue] = []
        if not case_ir.physics.moving_mesh:
            return issues

        has_motion = any(entity.motion for entity in case_ir.entities)
        if not has_motion:
            issues.append(
                ValidationIssue(
                    code="MOVING_MESH_NO_MOTION_ENTITY",
                    path="entities",
                    message=(
                        "physics.moving_mesh is True but no entity has a "
                        "motion reference defined."
                    ),
                )
            )
        return issues

    def _check_porous_consistency(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Porous media region should have a porous material."""
        issues: list[ValidationIssue] = []
        if not case_ir.physics.porous_media:
            return issues

        porous_regions = [
            (i, r) for i, r in enumerate(case_ir.regions) if r.kind == "porous"
        ]
        if not porous_regions:
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="POROUS_MEDIA_NO_POROUS_REGION",
                    path="regions",
                    message=(
                        "physics.porous_media is True but no region has "
                        "kind='porous'."
                    ),
                )
            )

        for i, region in porous_regions:
            if region.material_ref:
                material = next(
                    (m for m in case_ir.materials if m.id == region.material_ref),
                    None,
                )
                if material and material.kind != "porous":
                    issues.append(
                        ValidationIssue(
                            code="POROUS_REGION_NON_POROUS_MATERIAL",
                            path=f"regions[{i}].material_ref",
                            message=(
                                f"Region '{region.id}' is porous but its "
                                f"material '{region.material_ref}' has "
                                f"kind='{material.kind}' (expected 'porous')."
                            ),
                        )
                    )
        return issues

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_2d(self, case_ir: RequestedCaseIR) -> bool:
        """Detect whether the simulation is 2D."""
        for bc in case_ir.boundary_intents:
            role = bc.semantic_role.lower()
            if "empty" in role or "2d" in role:
                return True
        # Check mesh strategy + entity dimensions
        for entity in case_ir.entities:
            for pname, pval in entity.parameters.items():
                if "thickness" in pname.lower() and isinstance(pval.value, (int, float)):
                    if pval.value == 0:
                        return True
        return False

    def _extract_param_values(
        self, case_ir: RequestedCaseIR
    ) -> dict[str, float]:
        """Extract numeric parameter values from the Case IR.

        Returns a flat dictionary mapping common parameter names to their
        numeric values, searching entities, materials, boundary intents,
        and numerical intent.
        """
        values: dict[str, float] = {}

        def _try_add(name: str, val: Any) -> None:
            if isinstance(val, (int, float)):
                values[name] = float(val)

        # Entity parameters
        for entity in case_ir.entities:
            for pname, pval in entity.parameters.items():
                _try_add(pname, pval.value)

        # Material properties
        for material in case_ir.materials:
            for pname, pval in material.properties.items():
                _try_add(pname, pval.value)

        # Boundary intent parameters
        for bc in case_ir.boundary_intents:
            for pname, pval in bc.parameters.items():
                _try_add(pname, pval.value)

        # Numerical intent
        if case_ir.numerical_intent.max_courant_number:
            _try_add("Courant", case_ir.numerical_intent.max_courant_number.value)

        return values


__all__ = ["ScientificConsistencyValidator"]
