"""Post-processing visualizer — generates plots and animations.

Uses matplotlib (Agg backend) to produce:
- Line charts: force coefficient history, residual convergence, Courant number
- Contour plots: pressure field, velocity magnitude, vorticity
- Animations: vorticity field evolution over time (GIF)
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import Normalize

from fluid_scientist.results.field_reader import CellMesh, FoamFieldReader
from fluid_scientist.results.models import SimulationData


# Chinese font support
plt.rcParams["font.sans-serif"] = [
    "SimHei",
    "Microsoft YaHei",
    "Noto Sans SC",
    "SimSun",
    "KaiTi",
    "FangSong",
    "WenQuanYi Micro Hei",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.family"] = "sans-serif"


class VisualizationArtifact:
    """A single visualization output."""

    def __init__(
        self,
        viz_type: str,
        field: str,
        fmt: str,
        data: bytes,
        title: str = "",
        time_step: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.type = viz_type  # "line_chart", "contour", "animation"
        self.field = field  # "force_coefficients", "pressure", "velocity", "vorticity", "residuals"
        self.format = fmt  # "svg", "png", "gif"
        self.data = data
        self.title = title
        self.time_step = time_step
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "field": self.field,
            "format": self.format,
            "title": self.title,
            "time_step": self.time_step,
            **self.metadata,
        }


class PostprocessVisualizer:
    """Generate visualization artifacts from simulation data and field files."""

    def __init__(
        self,
        output_dir: str | Path | None = None,
        max_contour_cells: int = 50000,
        max_anim_frames: int = 30,
        dpi: int = 150,
    ):
        self.output_dir = Path(output_dir) if output_dir else None
        self.max_contour_cells = max_contour_cells
        self.max_anim_frames = max_anim_frames
        self.dpi = dpi

    # ------------------------------------------------------------------
    # Line charts — from SimulationData (already collected)
    # ------------------------------------------------------------------

    def generate_force_coefficient_chart(
        self, sim_data: SimulationData
    ) -> VisualizationArtifact | None:
        """Generate force coefficient history line chart."""
        fc = sim_data.force_coefficients
        if not fc or not fc.get("Cd"):
            return None

        time_vals = sim_data.time_values.get("forceCoeffs", list(range(len(fc.get("Cd", [])))))

        fig, ax = plt.subplots(figsize=(8, 4))
        if "Cd" in fc and fc["Cd"]:
            ax.plot(time_vals[: len(fc["Cd"])], fc["Cd"], label="Cd (阻力系数)", linewidth=1.5)
        if "Cl" in fc and fc["Cl"]:
            ax.plot(time_vals[: len(fc["Cl"])], fc["Cl"], label="Cl (升力系数)", linewidth=1.5, alpha=0.8)
        if "Cm" in fc and fc["Cm"]:
            ax.plot(time_vals[: len(fc["Cm"])], fc["Cm"], label="Cm (力矩系数)", linewidth=1, alpha=0.6)

        ax.set_xlabel("时间 (s)")
        ax.set_ylabel("系数")
        ax.set_title("力系数历史")
        ax.legend(loc="best")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=self.dpi)
        plt.close(fig)
        buf.seek(0)
        return VisualizationArtifact(
            viz_type="line_chart",
            field="force_coefficients",
            fmt="png",
            data=buf.getvalue(),
            title="力系数历史",
        )

    def generate_residual_chart(
        self, sim_data: SimulationData
    ) -> VisualizationArtifact | None:
        """Generate residual convergence chart."""
        residuals = sim_data.residuals
        if not residuals:
            return None

        fig, ax = plt.subplots(figsize=(8, 4))
        for var, vals in residuals.items():
            if vals:
                ax.semilogy(range(len(vals)), vals, label=var, linewidth=1.2)

        ax.set_xlabel("迭代步")
        ax.set_ylabel("残差")
        ax.set_title("残差收敛曲线")
        ax.legend(loc="best")
        ax.grid(True, alpha=0.3, which="both")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=self.dpi)
        plt.close(fig)
        buf.seek(0)
        return VisualizationArtifact(
            viz_type="line_chart",
            field="residuals",
            fmt="png",
            data=buf.getvalue(),
            title="残差收敛曲线",
        )

    def generate_courant_chart(
        self, sim_data: SimulationData
    ) -> VisualizationArtifact | None:
        """Generate Courant number history chart."""
        co = sim_data.courant_numbers
        if not co:
            return None

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(range(len(co)), co, linewidth=1, color="steelblue")
        ax.axhline(y=1.0, color="r", linestyle="--", alpha=0.5, label="Co=1 (推荐上限)")
        ax.set_xlabel("时间步")
        ax.set_ylabel("Courant 数")
        ax.set_title("Courant 数历史")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=self.dpi)
        plt.close(fig)
        buf.seek(0)
        return VisualizationArtifact(
            viz_type="line_chart",
            field="courant",
            fmt="png",
            data=buf.getvalue(),
            title="Courant 数历史",
        )

    def generate_pressure_drop_chart(
        self, sim_data: SimulationData
    ) -> VisualizationArtifact | None:
        """Generate pressure drop over time chart."""
        sfv = sim_data.surface_field_values
        inlet_key = None
        outlet_key = None
        for k in sfv:
            if "inlet" in k.lower():
                inlet_key = k
            elif "outlet" in k.lower():
                outlet_key = k

        if not inlet_key or not outlet_key:
            return None

        inlet_vals = sfv[inlet_key]
        outlet_vals = sfv[outlet_key]
        n = min(len(inlet_vals), len(outlet_vals))
        if n == 0:
            return None

        dp = [inlet_vals[i] - outlet_vals[i] for i in range(n)]

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(range(n), dp, linewidth=1.5, color="darkgreen")
        ax.set_xlabel("时间步")
        ax.set_ylabel("压降 (Pa)")
        ax.set_title("压降历史")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=self.dpi)
        plt.close(fig)
        buf.seek(0)
        return VisualizationArtifact(
            viz_type="line_chart",
            field="pressure_drop",
            fmt="png",
            data=buf.getvalue(),
            title="压降历史",
        )

    # ------------------------------------------------------------------
    # Contour plots — from OpenFOAM field files
    # ------------------------------------------------------------------

    def generate_contour_plot(
        self,
        case_path: str | Path,
        time_dir: str,
        field_name: str,
        mesh: CellMesh | None = None,
    ) -> VisualizationArtifact | None:
        """Generate a 2D contour plot for a field at a given time.

        Args:
            case_path: Path to OpenFOAM case directory.
            time_dir: Time directory name (e.g. "5").
            field_name: "p", "U", or "vorticity".
            mesh: Pre-computed mesh (optional, will read if not provided).
        """
        reader = FoamFieldReader(case_path)

        if mesh is None or mesh.n_cells == 0:
            mesh = reader.read_mesh()

        if mesh.n_cells == 0:
            return None

        # Get field data
        if field_name == "vorticity":
            values = reader.compute_vorticity_z(time_dir, mesh)
            field_type = "scalar"
            title = f"涡量场 (t={time_dir}s)"
            cmap = "RdBu_r"
            units = "1/s"
        else:
            values, field_type = reader.read_field(time_dir, field_name)
            if field_type == "vector":
                # For velocity, compute magnitude
                values = [
                    math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
                    if isinstance(v, (tuple, list))
                    else 0.0
                    for v in values
                ]
                title = f"速度幅值 (t={time_dir}s)"
                cmap = "viridis"
                units = "m/s"
            else:
                title = f"压力场 (t={time_dir}s)"
                cmap = "coolwarm"
                units = "Pa"

        if not values or len(values) != mesh.n_cells:
            return None

        # Downsample if too many cells
        coords = mesh.cell_centers
        vals = values

        if len(coords) > self.max_contour_cells:
            step = len(coords) // self.max_contour_cells
            coords = coords[::step]
            vals = vals[::step]

        xs = np.array([c[0] for c in coords])
        ys = np.array([c[1] for c in coords])
        zs = np.array(vals)

        # Remove NaN/inf
        mask = np.isfinite(zs)
        xs, ys, zs = xs[mask], ys[mask], zs[mask]

        if len(zs) < 3:
            return None

        fig, ax = plt.subplots(figsize=(8, 5))

        # Use tricontourf for unstructured data
        try:
            tcf = ax.tricontourf(xs, ys, zs, levels=20, cmap=cmap)
            fig.colorbar(tcf, ax=ax, label=units)
        except Exception:
            # Fallback to scatter
            sc = ax.scatter(xs, ys, c=zs, cmap=cmap, s=1)
            fig.colorbar(sc, ax=ax, label=units)

        ax.set_aspect("equal")
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")
        ax.set_title(title)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=self.dpi)
        plt.close(fig)
        buf.seek(0)

        return VisualizationArtifact(
            viz_type="contour",
            field=field_name,
            fmt="png",
            data=buf.getvalue(),
            title=title,
            time_step=time_dir,
        )

    # ------------------------------------------------------------------
    # Animation — vorticity field over time
    # ------------------------------------------------------------------

    def generate_vorticity_animation(
        self,
        case_path: str | Path,
        mesh: CellMesh | None = None,
    ) -> VisualizationArtifact | None:
        """Generate a GIF animation of vorticity field evolution.

        Args:
            case_path: Path to OpenFOAM case directory.
            mesh: Pre-computed mesh (optional).
        """
        reader = FoamFieldReader(case_path)

        if mesh is None or mesh.n_cells == 0:
            mesh = reader.read_mesh()

        if mesh.n_cells == 0:
            return None

        time_dirs = reader.list_time_dirs()
        if len(time_dirs) < 2:
            return None

        # Limit number of frames
        if len(time_dirs) > self.max_anim_frames:
            step = len(time_dirs) // self.max_anim_frames
            time_dirs = time_dirs[::step][: self.max_anim_frames]

        # Pre-compute vorticity for all frames
        all_vorticity = []
        all_coords = mesh.cell_centers

        for td in time_dirs:
            vort = reader.compute_vorticity_z(td, mesh)
            if vort and len(vort) == mesh.n_cells:
                all_vorticity.append(vort)

        if len(all_vorticity) < 2:
            return None

        # Downsample if too many cells
        coords = all_coords
        if len(coords) > self.max_contour_cells:
            step = len(coords) // self.max_contour_cells
            coords = coords[::step]
            all_vorticity = [v[::step] for v in all_vorticity]

        xs = np.array([c[0] for c in coords])
        ys = np.array([c[1] for c in coords])

        # Determine global color range
        all_zs = np.concatenate([np.array(v) for v in all_vorticity])
        all_zs = all_zs[np.isfinite(all_zs)]
        if len(all_zs) == 0:
            return None

        vmax = np.percentile(np.abs(all_zs), 95)
        vmin = -vmax
        norm = Normalize(vmin=vmin, vmax=vmax)

        fig, ax = plt.subplots(figsize=(8, 5))

        def update(frame: int):
            ax.clear()
            zs = np.array(all_vorticity[frame])
            mask = np.isfinite(zs)
            try:
                tcf = ax.tricontourf(
                    xs[mask], ys[mask], zs[mask], levels=20, cmap="RdBu_r", norm=norm
                )
            except Exception:
                ax.scatter(xs[mask], ys[mask], c=zs[mask], cmap="RdBu_r", s=1, norm=norm)

            ax.set_aspect("equal")
            ax.set_xlabel("x (m)")
            ax.set_ylabel("y (m)")
            ax.set_title(f"涡量场 (t={time_dirs[frame]}s)")
            return []

        anim = animation.FuncAnimation(
            fig,
            update,
            frames=len(all_vorticity),
            interval=200,
            blit=False,
        )

        buf = io.BytesIO()
        try:
            anim.save(buf, format="gif", writer="pillow", fps=5, dpi=self.dpi)
        except Exception:
            plt.close(fig)
            return None

        plt.close(fig)
        buf.seek(0)

        return VisualizationArtifact(
            viz_type="animation",
            field="vorticity",
            fmt="gif",
            data=buf.getvalue(),
            title="涡量场动画",
            metadata={"n_frames": len(all_vorticity), "time_range": f"{time_dirs[0]}-{time_dirs[-1]}s"},
        )

    # ------------------------------------------------------------------
    # Batch generation — generate all applicable visualizations
    # ------------------------------------------------------------------

    def generate_all(
        self,
        sim_data: SimulationData,
        case_path: str | Path | None = None,
    ) -> list[VisualizationArtifact]:
        """Generate all applicable visualizations.

        Args:
            sim_data: Parsed simulation data.
            case_path: Path to OpenFOAM case (for contour/animation). Optional.

        Returns:
            List of visualization artifacts.
        """
        artifacts: list[VisualizationArtifact] = []

        # Line charts (from already-parsed data)
        for generator in [
            self.generate_force_coefficient_chart,
            self.generate_residual_chart,
            self.generate_courant_chart,
            self.generate_pressure_drop_chart,
        ]:
            try:
                art = generator(sim_data)
                if art:
                    artifacts.append(art)
            except Exception:
                pass

        # Contour plots (from field files)
        if case_path:
            reader = FoamFieldReader(case_path)
            mesh = reader.read_mesh()

            if mesh.n_cells > 0:
                time_dirs = reader.list_time_dirs()
                latest = time_dirs[-1] if time_dirs else "0"

                # Pressure contour
                try:
                    art = self.generate_contour_plot(case_path, latest, "p", mesh)
                    if art:
                        artifacts.append(art)
                except Exception:
                    pass

                # Velocity contour
                try:
                    art = self.generate_contour_plot(case_path, latest, "U", mesh)
                    if art:
                        artifacts.append(art)
                except Exception:
                    pass

                # Vorticity animation (only if multiple time steps)
                if len(time_dirs) >= 3:
                    try:
                        art = self.generate_vorticity_animation(case_path, mesh)
                        if art:
                            artifacts.append(art)
                    except Exception:
                        pass

        # Save to output dir if specified
        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            for i, art in enumerate(artifacts):
                ext = art.format
                filename = f"{art.type}_{art.field}_{i:03d}.{ext}"
                filepath = self.output_dir / filename
                filepath.write_bytes(art.data)
                art.metadata["filename"] = filename

        return artifacts


__all__ = ["PostprocessVisualizer", "VisualizationArtifact"]
