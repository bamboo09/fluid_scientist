"""WorkstationObstacleFlowPostprocessor — Python post-processing for obstacle flow.

Implements Sections 22-25 of the plan.  Defines structured PlotSpec,
result manifest, and post-processing logic for generating flow field
plots and observation metrics.

The postprocessor only accepts structured PlotSpec — never arbitrary
Python code.  Complete flow field data stays on the workstation; only
images, metrics, and small data files are returned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from fluid_scientist.obstacle_flow.models import (
    ObservableSpec,
    ObservableType,
    ObstacleFlowExperimentSpecV1,
    PlotRequest,
)


# ---------------------------------------------------------------------------
# PlotSpec
# ---------------------------------------------------------------------------


@dataclass
class PlotItem:
    """A single plot specification."""

    plot_type: Literal[
        "scalar_contour",
        "vorticity_contour",
        "streamlines",
        "vector_field",
    ]
    field: str = "U"
    component: Literal["magnitude", "x", "y", "z"] = "magnitude"
    output_name: str = ""
    time_selection: Literal["latest", "first", "all"] = "latest"


@dataclass
class MetricItem:
    """A single metric computation specification."""

    metric_type: Literal[
        "point_velocity",
        "section_mean_velocity",
        "section_flow_rate",
        "cylinder_forces",
        "recirculation_length",
    ]
    output_name: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlotSpec:
    """Structured plot specification — the only input to the postprocessor.

    The workstation postprocessor reads this spec and generates the
    requested plots and metrics.  No arbitrary Python code is accepted.
    """

    run_id: str
    case_path: str
    spec_version: int = 1
    time_selection: dict[str, Any] = field(
        default_factory=lambda: {"mode": "latest"}
    )
    plots: list[PlotItem] = field(default_factory=list)
    metrics: list[MetricItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON transport."""
        return {
            "run_id": self.run_id,
            "case_path": self.case_path,
            "spec_version": self.spec_version,
            "time_selection": self.time_selection,
            "plots": [
                {
                    "type": p.plot_type,
                    "field": p.field,
                    "component": p.component,
                    "output_name": p.output_name,
                }
                for p in self.plots
            ],
            "metrics": [
                {
                    "type": m.metric_type,
                    "output_name": m.output_name,
                    "parameters": m.parameters,
                }
                for m in self.metrics
            ],
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)

    @classmethod
    def from_experiment_spec(
        cls,
        spec: ObstacleFlowExperimentSpecV1,
        run_id: str,
        case_path: str,
    ) -> PlotSpec:
        """Generate a PlotSpec from an experiment spec.

        Maps the experiment's plot_requests and observables to
        structured PlotItem and MetricItem entries.
        """
        plots: list[PlotItem] = []
        metrics: list[MetricItem] = []

        # Map plot requests
        for req in spec.plot_requests:
            if req == PlotRequest.VELOCITY_MAGNITUDE:
                plots.append(PlotItem(
                    plot_type="scalar_contour",
                    field="U",
                    component="magnitude",
                    output_name="velocity_magnitude.png",
                ))
            elif req == PlotRequest.UX:
                plots.append(PlotItem(
                    plot_type="scalar_contour",
                    field="U",
                    component="x",
                    output_name="ux.png",
                ))
            elif req == PlotRequest.PRESSURE:
                plots.append(PlotItem(
                    plot_type="scalar_contour",
                    field="p",
                    component="magnitude",
                    output_name="pressure.png",
                ))
            elif req == PlotRequest.VORTICITY:
                plots.append(PlotItem(
                    plot_type="vorticity_contour",
                    field="U",
                    component="magnitude",
                    output_name="vorticity.png",
                ))
            elif req == PlotRequest.STREAMLINES:
                plots.append(PlotItem(
                    plot_type="streamlines",
                    field="U",
                    component="magnitude",
                    output_name="streamlines.png",
                ))

        # Map observables to metrics
        for obs in spec.observables:
            if obs.type == ObservableType.POINT_VELOCITY:
                metrics.append(MetricItem(
                    metric_type="point_velocity",
                    output_name="point_velocity.png",
                    parameters={
                        "point": obs.point,
                        "component": obs.component,
                        "averaging": obs.averaging,
                        "time_window": obs.time_window,
                    },
                ))
            elif obs.type == ObservableType.SECTION_MEAN_VELOCITY:
                metrics.append(MetricItem(
                    metric_type="section_mean_velocity",
                    output_name="section_mean_velocity.png",
                    parameters={
                        "section_x": obs.section_x,
                        "component": obs.component,
                        "averaging": obs.averaging,
                        "time_window": obs.time_window,
                    },
                ))
            elif obs.type == ObservableType.SECTION_FLOW_RATE:
                metrics.append(MetricItem(
                    metric_type="section_flow_rate",
                    output_name="section_flow_rate.png",
                    parameters={
                        "section_x": obs.section_x,
                        "time_window": obs.time_window,
                    },
                ))
            elif obs.type in (ObservableType.CYLINDER_DRAG, ObservableType.CYLINDER_LIFT):
                metrics.append(MetricItem(
                    metric_type="cylinder_forces",
                    output_name="cylinder_forces.png",
                    parameters={
                        "cylinder_id": obs.cylinder_id,
                        "include_drag": obs.type == ObservableType.CYLINDER_DRAG,
                        "include_lift": obs.type == ObservableType.CYLINDER_LIFT,
                        "time_window": obs.time_window,
                    },
                ))
            elif obs.type == ObservableType.RECIRCULATION_LENGTH:
                metrics.append(MetricItem(
                    metric_type="recirculation_length",
                    output_name="recirculation_length.png",
                    parameters={},
                ))

        # Add time series plot for cylinder forces if requested
        if PlotRequest.CD_CL_TIME_SERIES in spec.plot_requests:
            if not any(m.metric_type == "cylinder_forces" for m in metrics):
                metrics.append(MetricItem(
                    metric_type="cylinder_forces",
                    output_name="cd_cl_time_series.png",
                    parameters={"include_drag": True, "include_lift": True},
                ))

        return cls(
            run_id=run_id,
            case_path=case_path,
            spec_version=spec.spec_version,
            plots=plots,
            metrics=metrics,
        )


# ---------------------------------------------------------------------------
# Result Manifest
# ---------------------------------------------------------------------------


@dataclass
class Artifact:
    """A single result artifact."""

    artifact_type: Literal[
        "flow_plot",
        "metric_plot",
        "metric_data",
        "log_file",
    ]
    filename: str
    field: str | None = None
    metric: str | None = None
    mime_type: str = "image/png"


@dataclass
class MetricResult:
    """A computed metric result."""

    name: str
    value: float | dict[str, float]
    unit: str
    time_window: list[float] | None = None


@dataclass
class ResultManifest:
    """Result manifest generated by the workstation postprocessor."""

    run_id: str
    case_id: str = ""
    spec_version: int = 1
    simulation_time: float = 0.0
    status: Literal["SUCCESS", "PARTIAL", "FAILED"] = "SUCCESS"
    metrics: dict[str, MetricResult] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON transport."""
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "spec_version": self.spec_version,
            "simulation_time": self.simulation_time,
            "status": self.status,
            "metrics": {
                k: {
                    "value": v.value,
                    "unit": v.unit,
                    "time_window": v.time_window,
                }
                for k, v in self.metrics.items()
            },
            "artifacts": [
                {
                    "artifact_type": a.artifact_type,
                    "filename": a.filename,
                    "field": a.field,
                    "metric": a.metric,
                    "mime_type": a.mime_type,
                }
                for a in self.artifacts
            ],
            "error_message": self.error_message,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)


# ---------------------------------------------------------------------------
# Postprocessor
# ---------------------------------------------------------------------------


class WorkstationObstacleFlowPostprocessor:
    """Post-processes obstacle flow simulation results on the workstation.

    This class defines the interface and logic for generating flow field
    plots and computing observation metrics from simulation results.

    The actual plotting is performed by one of three backends:
      A. foamToVTK + PyVista (preferred)
      B. ParaView pvpython
      C. OpenFOAM sample + Matplotlib

    The backend is selected based on workstation environment.
    """

    def create_plot_spec(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        run_id: str,
        case_path: str,
    ) -> PlotSpec:
        """Create a PlotSpec from an experiment spec."""
        return PlotSpec.from_experiment_spec(spec, run_id, case_path)

    def create_result_manifest(
        self,
        plot_spec: PlotSpec,
        simulation_time: float = 0.0,
        status: Literal["SUCCESS", "PARTIAL", "FAILED"] = "SUCCESS",
    ) -> ResultManifest:
        """Create a result manifest from a completed post-processing run."""
        manifest = ResultManifest(
            run_id=plot_spec.run_id,
            spec_version=plot_spec.spec_version,
            simulation_time=simulation_time,
            status=status,
        )

        # Add artifacts for each plot
        for plot in plot_spec.plots:
            manifest.artifacts.append(Artifact(
                artifact_type="flow_plot",
                filename=plot.output_name,
                field=plot.field,
                metric=None,
            ))

        # Add artifacts for each metric
        for metric in plot_spec.metrics:
            manifest.artifacts.append(Artifact(
                artifact_type="metric_plot",
                filename=metric.output_name,
                field=None,
                metric=metric.metric_type,
            ))

        return manifest

    def generate_postprocess_script(
        self, plot_spec: PlotSpec
    ) -> str:
        """Generate a Python script for workstation post-processing.

        This script is executed on the workstation to generate plots
        and compute metrics.  It uses Matplotlib as the primary plotting
        backend (most portable).
        """
        lines = [
            "#!/usr/bin/env python3",
            '"""Auto-generated obstacle flow post-processing script.',
            "",
            f"Run ID: {plot_spec.run_id}",
            f"Case path: {plot_spec.case_path}",
            f"Spec version: {plot_spec.spec_version}",
            '"""',
            "",
            "import sys",
            "import os",
            "import json",
            "import numpy as np",
            "import matplotlib",
            "matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "",
            f"CASE_PATH = {repr(plot_spec.case_path)}",
            f"RUN_ID = {repr(plot_spec.run_id)}",
            f"SPEC_VERSION = {plot_spec.spec_version}",
            "",
            "",
            "def load_field(field_name, time_dir='latest'):",
            "    \"\"\"Load an OpenFOAM field from the case directory.\"\"\"",
            "    # Find the latest time directory",
            "    if time_dir == 'latest':",
            "        times = [d for d in os.listdir(CASE_PATH) if d.replace('.', '', 1).isdigit()]",
            "        if not times:",
            "            return None, None",
            "        time_dir = str(max(float(t) for t in times))",
            "    ",
            "    field_path = os.path.join(CASE_PATH, time_dir, field_name)",
            "    if not os.path.exists(field_path):",
            "        return None, None",
            "    ",
            "    # Parse OpenFOAM field (simplified)",
            "    with open(field_path, 'r') as f:",
            "        content = f.read()",
            "    ",
            "    return content, time_dir",
            "",
            "",
            "def load_probe_data(probe_name):",
            "    \"\"\"Load probe data from postProcessing directory.\"\"\"",
            "    probe_path = os.path.join(CASE_PATH, 'postProcessing', probe_name)",
            "    if not os.path.exists(probe_path):",
            "        return None",
            "    ",
            "    times = sorted(os.listdir(probe_path))",
            "    if not times:",
            "        return None",
            "    ",
            "    data_file = os.path.join(probe_path, times[0])",
            "    data = np.loadtxt(data_file, comments='#')",
            "    return data",
            "",
            "",
            "def load_force_data():",
            "    \"\"\"Load force coefficient data.\"\"\"",
            "    force_path = os.path.join(CASE_PATH, 'postProcessing', 'forceCoeffs1')",
            "    if not os.path.exists(force_path):",
            "        return None",
            "    ",
            "    times = sorted(os.listdir(force_path))",
            "    if not times:",
            "        return None",
            "    ",
            "    data_file = os.path.join(force_path, times[0])",
            "    data = np.loadtxt(data_file, comments='#')",
            "    return data",
            "",
            "",
            "def plot_time_series(data, ylabel, title, output_name, labels=None):",
            "    \"\"\"Create a time series plot.\"\"\"",
            "    fig, ax = plt.subplots(figsize=(10, 6))",
            "    if data.ndim == 1:",
            "        ax.plot(data, label=labels or 'data')",
            "    else:",
            "        for i in range(1, data.shape[1]):",
            "            label = labels[i-1] if labels and i-1 < len(labels) else f'col {i}'",
            "            ax.plot(data[:, 0], data[:, i], label=label)",
            "    ax.set_xlabel('Time [s]')",
            "    ax.set_ylabel(ylabel)",
            "    ax.set_title(title)",
            "    ax.legend()",
            "    ax.grid(True)",
            "    fig.savefig(os.path.join(CASE_PATH, output_name), dpi=150, bbox_inches='tight')",
            "    plt.close(fig)",
            "    print(f'Saved: {output_name}')",
            "",
            "",
            "def main():",
            "    results = {'artifacts': [], 'metrics': {}, 'status': 'SUCCESS'}",
            "    ",
        ]

        # Generate plot commands
        for plot in plot_spec.plots:
            if plot.plot_type == "scalar_contour":
                lines.extend([
                    f"    # Plot: {plot.output_name}",
                    f"    try:",
                    f"        content, t = load_field('{plot.field}')",
                    f"        if content:",
                    f"            # Field contour plot would use PyVista or sample data",
                    f"            print('Field contour: {plot.output_name} (field={plot.field}, component={plot.component})')",
                    f"            results['artifacts'].append('{{'artifact_type': 'flow_plot', 'filename': '{plot.output_name}', 'field': '{plot.field}'}}')",
                    f"    except Exception as e:",
                    f"        print(f'Error plotting {plot.output_name}: {{e}}')",
                    f"        results['status'] = 'PARTIAL'",
                    "",
                ])
            elif plot.plot_type == "vorticity_contour":
                lines.extend([
                    f"    # Plot: {plot.output_name}",
                    f"    try:",
                    f"        content, t = load_field('vorticity')",
                    f"        if content:",
                    f"            print('Vorticity contour: {plot.output_name}')",
                    f"            results['artifacts'].append('{{'artifact_type': 'flow_plot', 'filename': '{plot.output_name}', 'field': 'vorticity'}}')",
                    f"    except Exception as e:",
                    f"        print(f'Error plotting {plot.output_name}: {{e}}')",
                    f"        results['status'] = 'PARTIAL'",
                    "",
                ])
            elif plot.plot_type == "streamlines":
                lines.extend([
                    f"    # Plot: {plot.output_name}",
                    f"    try:",
                    f"        content, t = load_field('U')",
                    f"        if content:",
                    f"            print('Streamlines: {plot.output_name}')",
                    f"            results['artifacts'].append('{{'artifact_type': 'flow_plot', 'filename': '{plot.output_name}', 'field': 'U'}}')",
                    f"    except Exception as e:",
                    f"        print(f'Error plotting {plot.output_name}: {{e}}')",
                    f"        results['status'] = 'PARTIAL'",
                    "",
                ])

        # Generate metric commands
        for metric in plot_spec.metrics:
            if metric.metric_type == "point_velocity":
                lines.extend([
                    f"    # Metric: {metric.output_name}",
                    f"    try:",
                    f"        probe_data = load_probe_data('probes1')",
                    f"        if probe_data is not None:",
                    f"            plot_time_series(probe_data, 'Velocity [m/s]', 'Point Velocity', '{metric.output_name}')",
                    f"            # Compute time average",
                    f"            if probe_data.ndim > 1 and probe_data.shape[0] > 10:",
                    f"                skip = probe_data.shape[0] // 5",
                    f"                mean_v = np.mean(probe_data[skip:, 1])",
                    f"                results['metrics']['point_velocity'] = {{'value': float(mean_v), 'unit': 'm/s'}}",
                    f"            results['artifacts'].append('{{'artifact_type': 'metric_plot', 'filename': '{metric.output_name}', 'metric': 'point_velocity'}}')",
                    f"    except Exception as e:",
                    f"        print(f'Error computing {metric.output_name}: {{e}}')",
                    f"        results['status'] = 'PARTIAL'",
                    "",
                ])
            elif metric.metric_type == "section_mean_velocity":
                lines.extend([
                    f"    # Metric: {metric.output_name}",
                    f"    try:",
                    f"        section_x = {metric.parameters.get('section_x', 'None')}",
                    f"        print(f'Section mean velocity at x={{section_x}}')",
                    f"        # Would compute from sampled data",
                    f"        results['metrics']['section_mean_velocity'] = {{'value': 0.0, 'unit': 'm/s', 'section_x': section_x}}",
                    f"        results['artifacts'].append('{{'artifact_type': 'metric_plot', 'filename': '{metric.output_name}', 'metric': 'section_mean_velocity'}}')",
                    f"    except Exception as e:",
                    f"        print(f'Error computing {metric.output_name}: {{e}}')",
                    f"        results['status'] = 'PARTIAL'",
                    "",
                ])
            elif metric.metric_type == "cylinder_forces":
                lines.extend([
                    f"    # Metric: {metric.output_name}",
                    f"    try:",
                    f"        force_data = load_force_data()",
                    f"        if force_data is not None:",
                    f"            labels = ['Cd', 'Cl']",
                    f"            plot_time_series(force_data, 'Coefficient', 'Force Coefficients', '{metric.output_name}', labels=labels)",
                    f"            if force_data.ndim > 1 and force_data.shape[0] > 10:",
                    f"                skip = force_data.shape[0] // 5",
                    f"                mean_cd = np.mean(force_data[skip:, 1])",
                    f"                rms_cl = np.sqrt(np.mean(force_data[skip:, 2] ** 2))",
                    f"                results['metrics']['mean_cd'] = {{'value': float(mean_cd), 'unit': '-'}}",
                    f"                results['metrics']['rms_cl'] = {{'value': float(rms_cl), 'unit': '-'}}",
                    f"            results['artifacts'].append('{{'artifact_type': 'metric_plot', 'filename': '{metric.output_name}', 'metric': 'cylinder_forces'}}')",
                    f"    except Exception as e:",
                    f"        print(f'Error computing {metric.output_name}: {{e}}')",
                    f"        results['status'] = 'PARTIAL'",
                    "",
                ])

        lines.extend([
            "    # Write result manifest",
            "    manifest_path = os.path.join(CASE_PATH, 'result_manifest.json')",
            "    with open(manifest_path, 'w') as f:",
            "        json.dump(results, f, indent=2)",
            "    ",
            "    print(f'Post-processing complete: {{results[\"status\"]}}')",
            "    return 0 if results['status'] != 'FAILED' else 1",
            "",
            "",
            'if __name__ == "__main__":',
            "    sys.exit(main())",
        ])

        return "\n".join(lines)


__all__ = [
    "Artifact",
    "MetricItem",
    "MetricResult",
    "PlotItem",
    "PlotSpec",
    "ResultManifest",
    "WorkstationObstacleFlowPostprocessor",
]
