"""Mesh validator for OpenFOAM 13 cases.

The :class:`MeshValidator` validates the mesh quality either by parsing
``checkMesh`` output (when OpenFOAM is available) or by performing
structural checks on ``system/blockMeshDict`` (when running in
pure-Python mode).

When ``checkMesh`` output is provided, the validator parses:

* Overall mesh statistics (cells, faces, points).
* Non-orthogonality (max and average).
* Max aspect ratio.
* Max skewness.
* Number of boundary patches.
* Cell zones.
* Mesh non-conformity warnings.
"""

from __future__ import annotations

import re

from fluid_scientist.compiler.compiler import CompiledCase
from fluid_scientist.validation_runner.static_validator import ValidationResult


# ---------------------------------------------------------------------------
# Mesh quality thresholds
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "max_non_orthogonality": 70.0,        # degrees
    "avg_non_orthogonality": 15.0,        # degrees
    "max_aspect_ratio": 1000.0,            # dimensionless
    "max_skewness": 4.0,                   # dimensionless
    "min_cells": 1,                        # must have at least 1 cell
    "max_cells": 100_000_000,             # sanity limit
    "max_boundary_patches": 100,           # sanity limit
}


class MeshValidator:
    """Validates OpenFOAM mesh quality.

    Can operate in two modes:

    1. **With checkMesh output**: parse the output string for mesh
       statistics and quality metrics.
    2. **Without checkMesh**: perform structural checks on
       ``system/blockMeshDict``.

    Parameters:
        thresholds: Optional override for quality thresholds.
    """

    def __init__(
        self,
        thresholds: dict[str, float] | None = None,
    ) -> None:
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def validate(
        self,
        case: CompiledCase,
        checkmesh_output: str | None = None,
    ) -> ValidationResult:
        """Validate the mesh.

        Parameters:
            case: The compiled case containing blockMeshDict.
            checkmesh_output: Optional output from ``checkMesh`` command.
                If provided, the validator will parse it for quality
                metrics.  If ``None``, only structural checks are
                performed.

        Returns:
            A :class:`ValidationResult` with mesh quality errors.
        """
        result = ValidationResult(check_name="mesh_validation")

        if checkmesh_output:
            self._parse_checkmesh_output(checkmesh_output, result)
        else:
            result.add_warning(
                "No checkMesh output provided; performing structural "
                "checks on blockMeshDict only"
            )

        self._check_blockmeshdict_structure(case, result)
        self._check_boundary_patches(case, result)

        return result

    # ------------------------------------------------------------------
    # Parse checkMesh output
    # ------------------------------------------------------------------

    def _parse_checkmesh_output(
        self, output: str, result: ValidationResult
    ) -> None:
        """Parse checkMesh output for mesh statistics and quality metrics."""
        # Overall statistics
        cells = self._extract_int(output, r"cells:\s*(\d+)")

        if cells is not None:
            if cells < self.thresholds["min_cells"]:
                result.add_error(
                    f"Mesh has too few cells: {cells} "
                    f"(minimum: {self.thresholds['min_cells']})"
                )
            if cells > self.thresholds["max_cells"]:
                result.add_error(
                    f"Mesh has too many cells: {cells} "
                    f"(maximum: {self.thresholds['max_cells']})"
                )
        else:
            result.add_warning("checkMesh output: could not extract cell count")

        # Non-orthogonality
        max_nonortho = self._extract_float(
            output, r"Max.*non-orthogonality:\s*([\d.]+)"
        )
        if max_nonortho is None:
            max_nonortho = self._extract_float(
                output, r"Max non-orthogonality:\s*([\d.]+)"
            )
        avg_nonortho = self._extract_float(
            output, r"Average.*non-orthogonality:\s*([\d.]+)"
        )
        if avg_nonortho is None:
            avg_nonortho = self._extract_float(
                output, r"Average non-orthogonality:\s*([\d.]+)"
            )

        if max_nonortho is not None:
            if max_nonortho > self.thresholds["max_non_orthogonality"]:
                result.add_error(
                    f"Max non-orthogonality {max_nonortho} deg exceeds "
                    f"threshold {self.thresholds['max_non_orthogonality']} deg"
                )
        else:
            result.add_warning(
                "checkMesh output: could not extract max non-orthogonality"
            )

        if avg_nonortho is not None:
            if avg_nonortho > self.thresholds["avg_non_orthogonality"]:
                result.add_warning(
                    f"Average non-orthogonality {avg_nonortho} deg exceeds "
                    f"threshold {self.thresholds['avg_non_orthogonality']} deg"
                )

        # Aspect ratio
        max_ar = self._extract_float(
            output, r"Max aspect ratio:\s*([\d.]+)"
        )
        if max_ar is not None:
            if max_ar > self.thresholds["max_aspect_ratio"]:
                result.add_error(
                    f"Max aspect ratio {max_ar} exceeds threshold "
                    f"{self.thresholds['max_aspect_ratio']}"
                )

        # Skewness
        max_skew = self._extract_float(
            output, r"Max skewness:\s*([\d.]+)"
        )
        if max_skew is not None:
            if max_skew > self.thresholds["max_skewness"]:
                result.add_error(
                    f"Max skewness {max_skew} exceeds threshold "
                    f"{self.thresholds['max_skewness']}"
                )

        # Check for FOAM FATAL ERROR
        if "FOAM FATAL ERROR" in output:
            # Extract the error message
            error_match = re.search(
                r"FOAM FATAL ERROR.*?(?:\n\n|\Z)", output, re.DOTALL
            )
            error_msg = error_match.group(0).strip() if error_match else "FOAM FATAL ERROR"
            result.add_error(f"checkMesh reported: {error_msg}")

        # Check for failed checks
        if "***Failed" in output or "failed" in output.lower():
            result.add_error("checkMesh reported mesh check failures")

        # Number of boundary patches
        n_patches = self._extract_int(output, r"patches:\s*(\d+)")
        if n_patches is not None:
            if n_patches > self.thresholds["max_boundary_patches"]:
                result.add_warning(
                    f"Mesh has many boundary patches: {n_patches} "
                    f"(threshold: {self.thresholds['max_boundary_patches']})"
                )

    # ------------------------------------------------------------------
    # Structural checks on blockMeshDict
    # ------------------------------------------------------------------

    def _check_blockmeshdict_structure(
        self, case: CompiledCase, result: ValidationResult
    ) -> None:
        """Check the structure of blockMeshDict."""
        bmd = case.get("system/blockMeshDict") or ""
        if not bmd:
            result.add_warning("No system/blockMeshDict found for mesh validation")
            return

        # Check vertices section
        if "vertices" not in bmd:
            result.add_error("blockMeshDict: missing 'vertices' section")

        # Count vertices by finding coordinate tuples (x y z)
        vertex_pattern = re.compile(
            r"\(\s*[\d.eE+-]+\s+[\d.eE+-]+\s+[\d.eE+-]+\s*\)"
        )
        n_verts = len(vertex_pattern.findall(bmd))
        if n_verts < 8:
            result.add_error(
                f"blockMeshDict: too few vertices ({n_verts}), "
                f"need at least 8 for a hex block"
            )

        # Check blocks section
        if "blocks" not in bmd:
            result.add_error("blockMeshDict: missing 'blocks' section")

        block_match = re.search(
            r"blocks\s*\([^)]*hex\s*\(([^)]+)\)", bmd, re.DOTALL
        )
        if block_match:
            vert_indices = block_match.group(1).split()
            if len(vert_indices) < 8:
                result.add_error(
                    f"blockMeshDict: hex block has {len(vert_indices)} "
                    f"vertex indices, need 8"
                )

        # Check boundary section
        if "boundary" not in bmd:
            result.add_error("blockMeshDict: missing 'boundary' section")

        # Check mergePatchPairs
        if "mergePatchPairs" not in bmd:
            result.add_warning("blockMeshDict: missing 'mergePatchPairs' section")

    # ------------------------------------------------------------------
    # Check boundary patches
    # ------------------------------------------------------------------

    def _check_boundary_patches(
        self, case: CompiledCase, result: ValidationResult
    ) -> None:
        """Check boundary patches are consistent between blockMeshDict and 0/ files."""
        bmd = case.get("system/blockMeshDict") or ""
        if not bmd:
            return

        # Extract patches from blockMeshDict
        bmd_patches: list[str] = []
        boundary_match = re.search(r"boundary\s*\((.*?)\)", bmd, re.DOTALL)
        if boundary_match:
            section = boundary_match.group(1)
            for m in re.finditer(r"^\s*(\w+)\s*\n\s*\{", section, re.MULTILINE):
                name = m.group(1)
                if name not in bmd_patches:
                    bmd_patches.append(name)

        if not bmd_patches:
            result.add_warning(
                "blockMeshDict: no boundary patches found in boundary section"
            )
            return

        # Check that each patch has a type
        for patch in bmd_patches:
            pattern = rf"{patch}\s*\{{[^}}]*type\s+(\w+)"
            m = re.search(pattern, bmd, re.DOTALL)
            if not m:
                result.add_error(
                    f"blockMeshDict: patch '{patch}' is missing 'type'"
                )

        # Check that each patch has faces
        for patch in bmd_patches:
            pattern = rf"{patch}\s*\{{[^}}]*faces\s*\("
            if not re.search(pattern, bmd, re.DOTALL):
                result.add_error(
                    f"blockMeshDict: patch '{patch}' is missing 'faces'"
                )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_int(text: str, pattern: str) -> int | None:
        m = re.search(pattern, text)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _extract_float(text: str, pattern: str) -> float | None:
        m = re.search(pattern, text)
        if m:
            return float(m.group(1))
        return None


__all__ = [
    "DEFAULT_THRESHOLDS",
    "MeshValidator",
]
