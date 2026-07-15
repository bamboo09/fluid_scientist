"""Repair context builder — constructs the context for LLM error diagnosis.

Collects:
- Classified error details
- Current CaseSpec (relevant fields only)
- Current OpenFOAM file contents (failing files)
- Previous repair attempts
- User original input (for semantic context)
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.repair.error_classifier import ClassifiedError


class RepairContextBuilder:
    """Builds context for LLM-based error diagnosis."""

    def build_context(
        self,
        error: ClassifiedError,
        stage: str,
        spec: Any | None = None,
        file_contents: dict[str, str] | None = None,
        user_text: str = "",
        previous_attempts: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Build the full context for LLM diagnosis.

        Args:
            error: The classified error
            stage: Which stage failed (mesh, smoke, full_run)
            spec: The current CaseSpec (optional)
            file_contents: Map of filename to content for relevant files
            user_text: Original user input
            previous_attempts: List of previous repair attempt records

        Returns:
            Context dictionary for the LLM diagnoser
        """
        context: dict[str, Any] = {
            "error": error.to_dict(),
            "stage": stage,
            "user_original_input": user_text[:500],
        }

        # Add relevant spec fields based on error category
        if spec is not None:
            context["spec_summary"] = self._summarize_spec(spec, error)

        # Add file contents
        if file_contents:
            # Limit each file to reasonable size
            context["files"] = {
                name: content[:2000] if len(content) > 2000 else content
                for name, content in file_contents.items()
            }

        # Add previous attempts to avoid repeating the same fix
        if previous_attempts:
            context["previous_attempts"] = previous_attempts[-3:]  # Last 3 attempts only

        return context

    def _summarize_spec(self, spec: Any, error: ClassifiedError) -> dict[str, Any]:
        """Summarize spec fields relevant to the error."""
        summary: dict[str, Any] = {}

        # Always include domain
        if hasattr(spec, "domain"):
            summary["domain"] = {
                "length": spec.domain.length_m.value if spec.domain.length_m.is_resolved() else None,
                "height": spec.domain.height_m.value if spec.domain.height_m.is_resolved() else None,
            }

        # Include cylinder info
        if hasattr(spec, "cylinder") and spec.has_cylinder:
            summary["cylinder"] = {
                "radius": spec.cylinder.radius_m.value if spec.cylinder.radius_m.is_resolved() else None,
                "center_x": spec.cylinder.center_x_m.value if spec.cylinder.center_x_m.is_resolved() else None,
                "center_y": spec.cylinder.center_y_m.value if spec.cylinder.center_y_m.is_resolved() else None,
            }

        # Include obstacles based on error
        if error.category.value in ("mesh_error", "boundary_condition_error"):
            if hasattr(spec, "triangle") and spec.has_triangle:
                summary["triangle"] = {
                    "base_width": spec.triangle.base_width_m.value if spec.triangle.base_width_m.is_resolved() else None,
                    "height": spec.triangle.height_m.value if spec.triangle.height_m.is_resolved() else None,
                    "center_x": spec.triangle.center_x_m.value if spec.triangle.center_x_m.is_resolved() else None,
                }
            if hasattr(spec, "rectangle") and spec.has_rectangle:
                summary["rectangle"] = {
                    "width": spec.rectangle.width_m.value if spec.rectangle.width_m.is_resolved() else None,
                    "height": spec.rectangle.height_m.value if spec.rectangle.height_m.is_resolved() else None,
                }
            if hasattr(spec, "bottom_profile") and spec.has_bottom_profile:
                summary["bottom_profile"] = {
                    "type": spec.bottom_profile.profile_type.value if spec.bottom_profile.profile_type else None,
                    "height": spec.bottom_profile.height_m.value if spec.bottom_profile.height_m.is_resolved() else None,
                    "width": spec.bottom_profile.width_m.value if spec.bottom_profile.width_m.is_resolved() else None,
                }

        # Include boundaries
        if hasattr(spec, "boundaries"):
            bc = spec.boundaries
            summary["boundaries"] = {
                "left": bc.left.semantic_type.value if bc.left.semantic_type else None,
                "right": bc.right.semantic_type.value if bc.right.semantic_type else None,
                "top": bc.top.semantic_type.value if bc.top.semantic_type else None,
                "bottom": bc.bottom.semantic_type.value if bc.bottom.semantic_type else None,
            }

        # Include simulation params for physics errors
        if error.category.value in ("physics_error", "solver_error"):
            if hasattr(spec, "simulation"):
                summary["simulation"] = {
                    "delta_t": spec.simulation.delta_t_s,
                    "end_time": spec.simulation.end_time_s,
                    "max_courant": getattr(spec.simulation, "max_courant", None),
                }
            if hasattr(spec, "fluid"):
                summary["fluid"] = {
                    "nu": spec.fluid.kinematic_viscosity_m2_s.value if spec.fluid.kinematic_viscosity_m2_s.is_resolved() else None,
                }

        return summary
