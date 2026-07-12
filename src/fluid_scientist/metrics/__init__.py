"""Metric compilation: AnalysisGoal -> executable MetricDefinition chain.

This module compiles scientific intent (AnalysisGoal) into fully
executable metric definitions, each carrying:

* required raw fields
* required surfaces / probes
* OpenFOAM functionObject configurations
* sampling strategy
* post-processing implementation (capability_id)
* expected output artifacts
* interpretation / acceptance rules

The catalog is generic (force coefficients, pressure drop, spectra,
vortex identification, heat transfer, conservation, convergence, etc.)
rather than hard-coded to a single case type.  LLM is used upstream to
map unfamiliar goal language onto these generic measurement primitives.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# MetricDefinition  -- fully executable metric
# ---------------------------------------------------------------------------


class MetricDefinition(BaseModel):
    """A single, fully executable metric."""

    metric_id: str
    category: Literal[
        "scientific",
        "boundary_verification",
        "numerical_credibility",
        "comparison",
    ]
    definition: str
    source_goal_id: str = ""
    required_fields: list[str] = Field(default_factory=list)
    required_regions: list[str] = Field(default_factory=list)
    required_patches: list[str] = Field(default_factory=list)
    required_function_objects: list[dict[str, Any]] = Field(default_factory=list)
    required_sampling: dict[str, Any] = Field(default_factory=dict)
    postprocessor_capability_id: str = ""
    output_artifacts: list[str] = Field(default_factory=list)
    unit: str = ""
    interpretation_rule: str = ""
    implementation_status: Literal[
        "native",
        "extension_required",
        "pending",
    ] = "native"


from typing import Literal  # noqa: E402


# ---------------------------------------------------------------------------
# FunctionObject configurations  (deterministic fragments)
# ---------------------------------------------------------------------------


def _fo_residuals() -> dict[str, Any]:
    return {
        "name": "residuals",
        "type": "residuals",
        "libs": ['"libutilityFunctionObjects.so"'],
        "fields": ["p", "U"],
        "configuration": {},
    }


def _fo_force_coeffs(patches: list[str], rho_ref: float = 1.0, A_ref: float = 1.0, l_ref: float = 1.0) -> dict[str, Any]:
    return {
        "name": "forceCoeffs1",
        "type": "forceCoeffs",
        "libs": ['"libforces.so"'],
        "patches": patches,
        "fields": [],
        "configuration": {
            "rho": "rhoInf",
            "rhoInf": rho_ref,
            "Aref": A_ref,
            "lRef": l_ref,
            "magUInf": 1.0,
            "dragDir": [1, 0, 0],
            "liftDir": [0, 1, 0],
            "CofR": [0, 0, 0],
        },
    }


def _fo_forces(patches: list[str], rho_ref: float = 1.0) -> dict[str, Any]:
    return {
        "name": "forces1",
        "type": "forces",
        "libs": ['"libforces.so"'],
        "patches": patches,
        "fields": [],
        "configuration": {
            "rho": "rhoInf",
            "rhoInf": rho_ref,
            "CofR": [0, 0, 0],
        },
    }


def _fo_probes(locations: list[list[float]], fields: list[str]) -> dict[str, Any]:
    return {
        "name": "probes1",
        "type": "probes",
        "libs": ['"libsampling.so"'],
        "patches": [],
        "fields": fields,
        "configuration": {
            "probeLocations": locations,
        },
    }


def _fo_surface_sample(name: str, patches: list[str], fields: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "type": "surfaces",
        "libs": ['"libsampling.so"'],
        "patches": patches,
        "fields": fields,
        "configuration": {
            "surfaces": {
                name: {
                    "type": "patchInternalField",
                    "patches": patches,
                    "interpolate": True,
                }
            },
        },
    }


def _fo_field_average(fields: list[str]) -> dict[str, Any]:
    return {
        "name": "fieldAverage1",
        "type": "fieldAverage",
        "libs": ['"libfieldFunctionObjects.so"'],
        "patches": [],
        "fields": fields,
        "configuration": {
            "fields": [{
                "field": f,
                "mean": True,
                "prime2Mean": True,
                "base": "time",
            } for f in fields],
        },
    }


def _fo_courant() -> dict[str, Any]:
    return {
        "name": "Co",
        "type": "CourantNo",
        "libs": ['"libfieldFunctionObjects.so"'],
        "patches": [],
        "fields": [],
        "configuration": {},
    }


def _fo_y_plus(patches: list[str]) -> dict[str, Any]:
    return {
        "name": "yPlus1",
        "type": "yPlus",
        "libs": ['"libfieldFunctionObjects.so"'],
        "patches": patches,
        "fields": [],
        "configuration": {},
    }


def _fo_q_criterion() -> dict[str, Any]:
    return {
        "name": "Q",
        "type": "Q",
        "libs": ['"libfieldFunctionObjects.so"'],
        "patches": [],
        "fields": ["U"],
        "configuration": {},
    }


# ---------------------------------------------------------------------------
# Generic measurement primitives catalog
# ---------------------------------------------------------------------------


class MetricCatalog:
    """Catalog of generic measurement primitives.

    Each primitive knows how to emit the required functionObjects,
    probes, surfaces, postprocessor capabilities, and output artifacts.
    """

    def scientific_force_coefficients(self, goal_id: str, wall_patches: list[str]) -> list[MetricDefinition]:
        fos = [_fo_force_coeffs(wall_patches), _fo_residuals()]
        metrics = [
            MetricDefinition(
                metric_id="drag_coefficient",
                category="scientific",
                definition="Time-averaged drag coefficient Cd",
                source_goal_id=goal_id,
                required_fields=["U", "p"],
                required_patches=wall_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.force_spectrum",
                output_artifacts=["forceCoeffs.dat", "Cd_mean.txt", "Cd_rms.txt"],
                unit="",
                interpretation_rule="Cd = mean(forceCoeffs:Cd) after statistical stationarity",
            ),
            MetricDefinition(
                metric_id="lift_coefficient",
                category="scientific",
                definition="Time-averaged and RMS lift coefficient Cl",
                source_goal_id=goal_id,
                required_fields=["U", "p"],
                required_patches=wall_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.force_spectrum",
                output_artifacts=["forceCoeffs.dat", "Cl_mean.txt", "Cl_rms.txt"],
                unit="",
                interpretation_rule="Cl = mean(forceCoeffs:Cl)",
            ),
            MetricDefinition(
                metric_id="force_psd",
                category="scientific",
                definition="Power spectral density of lift coefficient",
                source_goal_id=goal_id,
                required_fields=["U", "p"],
                required_patches=wall_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.force_spectrum",
                output_artifacts=["forceCoeffs.dat", "Cl_psd.txt", "St_peak.txt"],
                unit="",
                interpretation_rule="Dominant peak in Cl PSD gives Strouhal number",
            ),
            MetricDefinition(
                metric_id="strouhal_number",
                category="scientific",
                definition="Strouhal number St = f*D/U from vortex shedding",
                source_goal_id=goal_id,
                required_fields=["U", "p"],
                required_patches=wall_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.force_spectrum",
                output_artifacts=["St_peak.txt"],
                unit="",
                interpretation_rule="St = f_dom * L_ref / U_ref",
            ),
        ]
        return metrics

    def scientific_pressure_drop(self, goal_id: str, inlet_patch: str, outlet_patch: str) -> list[MetricDefinition]:
        fos = [
            _fo_surface_sample("inlet_surface", [inlet_patch], ["p"]),
            _fo_surface_sample("outlet_surface", [outlet_patch], ["p"]),
            _fo_residuals(),
        ]
        return [
            MetricDefinition(
                metric_id="pressure_drop",
                category="scientific",
                definition="Static pressure drop between inlet and outlet",
                source_goal_id=goal_id,
                required_fields=["p"],
                required_patches=[inlet_patch, outlet_patch],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.pressure_drop",
                output_artifacts=["p_inlet_mean.txt", "p_outlet_mean.txt", "delta_p.txt"],
                unit="Pa",
                interpretation_rule="Delta p = area-average(p, inlet) - area-average(p, outlet)",
            ),
            MetricDefinition(
                metric_id="flow_rate",
                category="scientific",
                definition="Volumetric flow rate at inlet",
                source_goal_id=goal_id,
                required_fields=["U", "phi"],
                required_patches=[inlet_patch],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.flow_rate",
                output_artifacts=["flow_rate.txt"],
                unit="m^3/s",
                interpretation_rule="Q = surfaceIntegral(phi) at inlet",
            ),
        ]

    def scientific_velocity_profile(self, goal_id: str, sample_positions: list[list[float]], line_sample: bool = True) -> list[MetricDefinition]:
        probe_locations = sample_positions if sample_positions else [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [5.0, 0.0, 0.0]]
        fos = [
            _fo_probes(probe_locations, ["U", "p"]),
            _fo_field_average(["U"]),
            _fo_residuals(),
        ]
        return [
            MetricDefinition(
                metric_id="velocity_profiles",
                category="scientific",
                definition="Mean velocity profiles at streamwise stations",
                source_goal_id=goal_id,
                required_fields=["U"],
                required_regions=["wake", "boundary_layer"],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.velocity_profile",
                output_artifacts=["probes1_U.xy", "UMean.xy", "U_profile_comparison.png"],
                unit="m/s",
                interpretation_rule="Time/spanwise averaged U profiles at specified x-stations",
            ),
        ]

    def scientific_vortex_identification(self, goal_id: str) -> list[MetricDefinition]:
        fos = [
            _fo_q_criterion(),
            _fo_field_average(["U"]),
            _fo_residuals(),
        ]
        return [
            MetricDefinition(
                metric_id="q_criterion",
                category="scientific",
                definition="Q-criterion isosurfaces for vortex visualization",
                source_goal_id=goal_id,
                required_fields=["U"],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.vortex_tracking",
                output_artifacts=["Q_iso.vtk", "vortex_core_tracks.csv"],
                unit="1/s^2",
                interpretation_rule="Q > 0 identifies vortical regions; iso-surface at positive threshold",
            ),
        ]

    def scientific_wake_analysis(self, goal_id: str, sample_positions: list[list[float]] | None = None) -> list[MetricDefinition]:
        positions = sample_positions or [[2.0, 0.0, 0.0], [5.0, 0.0, 0.0], [10.0, 0.0, 0.0]]
        fos = [
            _fo_probes(positions, ["U", "p"]),
            _fo_field_average(["U"]),
            _fo_residuals(),
        ]
        return [
            MetricDefinition(
                metric_id="wake_centerline",
                category="scientific",
                definition="Wake centerline velocity deficit and recovery",
                source_goal_id=goal_id,
                required_fields=["U"],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.wake_centerline",
                output_artifacts=["wake_centerline_U.txt", "wake_deflection.txt", "wake_recovery_rate.txt"],
                unit="m/s",
                interpretation_rule="Wake center = point of minimum velocity deficit; deflection = cross-stream offset",
            ),
        ]

    def scientific_spectral_analysis(self, goal_id: str, probe_locations: list[list[float]] | None = None) -> list[MetricDefinition]:
        positions = probe_locations or [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]
        fos = [
            _fo_probes(positions, ["U", "p"]),
            _fo_residuals(),
        ]
        return [
            MetricDefinition(
                metric_id="velocity_spectra",
                category="scientific",
                definition="Power spectral density of velocity fluctuations at probes",
                source_goal_id=goal_id,
                required_fields=["U"],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.force_spectrum",
                output_artifacts=["probes1_U.xy", "U_spectra.txt", "dominant_frequencies.txt"],
                unit="Hz",
                interpretation_rule="Welch PSD of U' at each probe; identify dominant peaks",
            ),
        ]

    def scientific_heat_transfer(self, goal_id: str, wall_patches: list[str]) -> list[MetricDefinition]:
        fos = [
            _fo_surface_sample("wall_surface", wall_patches, ["T", "wallHeatFlux"]),
            _fo_residuals(),
        ]
        return [
            MetricDefinition(
                metric_id="nusselt_number",
                category="scientific",
                definition="Surface Nusselt number distribution",
                source_goal_id=goal_id,
                required_fields=["T", "wallHeatFlux"],
                required_patches=wall_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.statistics",
                output_artifacts=["wall_surface_T.vtk", "Nu_distribution.csv", "Nu_mean.txt"],
                unit="",
                interpretation_rule="Nu = q_w * L_ref / (k * delta_T)",
            ),
        ]

    def boundary_verification_metrics(self, wall_patches: list[str], inlet_patches: list[str], outlet_patches: list[str]) -> list[MetricDefinition]:
        fos = [
            _fo_forces(wall_patches),
            _fo_surface_sample("inlet_v", inlet_patches, ["U"]),
            _fo_surface_sample("outlet_v", outlet_patches, ["U"]),
            _fo_residuals(),
            _fo_courant(),
        ]
        return [
            MetricDefinition(
                metric_id="inlet_profile_error",
                category="boundary_verification",
                definition="Deviation of actual inlet profile from target",
                required_fields=["U"],
                required_patches=inlet_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.conservation",
                output_artifacts=["inlet_profile_error.txt"],
                unit="",
                interpretation_rule="L2 error between sampled and target inlet U profile < 1%",
            ),
            MetricDefinition(
                metric_id="mass_conservation_error",
                category="boundary_verification",
                definition="Global mass flux imbalance between inlet and outlet",
                required_fields=["phi", "U"],
                required_patches=inlet_patches + outlet_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.conservation",
                output_artifacts=["mass_balance.txt"],
                unit="kg/s",
                interpretation_rule="|inlet_mass_flow - outlet_mass_flow| / inlet_mass_flow < 0.1%",
            ),
            MetricDefinition(
                metric_id="wall_no_slip_error",
                category="boundary_verification",
                definition="Velocity magnitude at no-slip walls (should be ~0)",
                required_fields=["U"],
                required_patches=wall_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.conservation",
                output_artifacts=["wall_velocity_error.txt"],
                unit="m/s",
                interpretation_rule="max(|U_wall|) / U_ref < 1e-4",
            ),
            MetricDefinition(
                metric_id="outlet_backflow",
                category="boundary_verification",
                definition="Fraction of outlet with inward-pointing velocity",
                required_fields=["U"],
                required_patches=outlet_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.conservation",
                output_artifacts=["outlet_backflow_fraction.txt"],
                unit="",
                interpretation_rule="backflow_fraction < 5%",
            ),
        ]

    def credibility_metrics(self, wall_patches: list[str]) -> list[MetricDefinition]:
        fos = [
            _fo_residuals(),
            _fo_courant(),
            _fo_y_plus(wall_patches) if wall_patches else _fo_courant(),
            _fo_field_average(["U"]),
        ]
        return [
            MetricDefinition(
                metric_id="residual_convergence",
                category="numerical_credibility",
                definition="Solver residual levels at convergence",
                required_fields=["p", "U"],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.residual_analysis",
                output_artifacts=["residuals.txt", "convergence_summary.txt"],
                unit="",
                interpretation_rule="p_rms_residual < 1e-6 (steady) or stationary (transient)",
            ),
            MetricDefinition(
                metric_id="courant_number_max",
                category="numerical_credibility",
                definition="Maximum Courant number during run",
                required_fields=["Co"],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.mesh_check",
                output_artifacts=["Co_max.txt"],
                unit="",
                interpretation_rule="max(Co) stays below target Co_max",
            ),
            MetricDefinition(
                metric_id="y_plus_distribution",
                category="numerical_credibility",
                definition="Wall y+ distribution on no-slip patches",
                required_fields=["yPlus"],
                required_patches=wall_patches,
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.mesh_check",
                output_artifacts=["yPlus1.dat", "yplus_stats.txt"],
                unit="",
                interpretation_rule="y+ in target range for chosen wall treatment",
            ),
            MetricDefinition(
                metric_id="statistical_stationarity",
                category="numerical_credibility",
                definition="Stationarity of monitored quantities",
                required_fields=["U"],
                required_function_objects=fos,
                postprocessor_capability_id="postprocess.statistics",
                output_artifacts=["stationarity_report.txt"],
                unit="",
                interpretation_rule="Moving average of forces/velocity stabilizes within 1%",
            ),
        ]


# ---------------------------------------------------------------------------
# GoalToMetricCompiler
# ---------------------------------------------------------------------------


class GoalToMetricCompiler:
    """Compile scientific analysis goals into executable MetricDefinitions.

    Uses the generic :class:`MetricCatalog` primitives; LLM maps arbitrary
    natural-language goals to these primitives upstream.
    """

    def __init__(self) -> None:
        self._catalog = MetricCatalog()

    def compile(
        self,
        analysis_goals: list[dict[str, Any]],
        boundary_patches: dict[str, list[str]] | None = None,
    ) -> dict[str, list[MetricDefinition]]:
        """Compile a list of analysis goals into metric layers.

        ``boundary_patches`` can specify groups such as:
        * ``walls``: list of no-slip wall patch names
        * ``inlets``: list of inlet patches
        * ``outlets``: list of outlet patches
        """
        bp = boundary_patches or {}
        wall_patches = bp.get("walls", ["body", "wall", "cylinder"])
        fo_wall_patches = bp.get("fo_walls", wall_patches)
        inlet_patches = bp.get("inlets", ["inlet"])
        outlet_patches = bp.get("outlets", ["outlet"])

        scientific: list[MetricDefinition] = []
        seen_ids: set[str] = set()

        for goal in analysis_goals:
            # Coerce string items to dicts (analysis_goals may be list[str]).
            if isinstance(goal, str):
                goal = {"goal_id": goal, "phenomenon": goal, "target_quantity": ""}
            if not isinstance(goal, dict):
                continue
            goal_id = goal.get("goal_id", "")
            phenomenon = goal.get("phenomenon", "").lower()
            target_qty = goal.get("target_quantity", "").lower()

            # Map to generic primitives by phenomenon/target
            new_metrics: list[MetricDefinition] = []

            if any(kw in phenomenon + " " + target_qty for kw in ("force", "drag", "lift", "阻力", "升力", "载荷")):
                new_metrics = self._catalog.scientific_force_coefficients(goal_id, fo_wall_patches)
            elif any(kw in phenomenon + " " + target_qty for kw in ("pressure drop", "flow rate", "压降", "流量")):
                new_metrics = self._catalog.scientific_pressure_drop(goal_id, inlet_patches[0], outlet_patches[0])
            elif any(kw in phenomenon + " " + target_qty for kw in ("velocity profile", "wake", "尾迹", "剖面", "速度分布")):
                new_metrics = self._catalog.scientific_velocity_profile(goal_id, [])
                if "wake" in phenomenon or "尾迹" in phenomenon:
                    new_metrics.extend(self._catalog.scientific_wake_analysis(goal_id))
            elif any(kw in phenomenon + " " + target_qty for kw in ("vortex", "q-criterion", "vortical", "涡")):
                new_metrics = self._catalog.scientific_vortex_identification(goal_id)
            elif any(kw in phenomenon + " " + target_qty for kw in ("spectrum", "spectral", "frequency", "psf", "频谱", "频率")):
                new_metrics = self._catalog.scientific_spectral_analysis(goal_id)
            elif any(kw in phenomenon + " " + target_qty for kw in ("heat", "nusselt", "传热", "热")):
                new_metrics = self._catalog.scientific_heat_transfer(goal_id, fo_wall_patches)
            else:
                # Generic baseline
                new_metrics = self._catalog.scientific_velocity_profile(goal_id, [])

            for m in new_metrics:
                if m.metric_id not in seen_ids:
                    scientific.append(m)
                    seen_ids.add(m.metric_id)

        boundary_v = self._catalog.boundary_verification_metrics(fo_wall_patches, inlet_patches, outlet_patches)
        credibility = self._catalog.credibility_metrics(fo_wall_patches)

        return {
            "scientific": scientific,
            "boundary_verification": boundary_v,
            "numerical_credibility": credibility,
        }

    def compile_all_to_dicts(
        self,
        analysis_goals: list[dict[str, Any]],
        boundary_patches: dict[str, list[str]] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Same as compile() but returns plain dicts for JSON serialization."""
        result = self.compile(analysis_goals, boundary_patches)
        return {
            key: [m.model_dump() for m in metrics]
            for key, metrics in result.items()
        }


__all__ = [
    "GoalToMetricCompiler",
    "MetricCatalog",
    "MetricDefinition",
]
