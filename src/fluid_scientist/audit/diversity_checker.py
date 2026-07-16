"""Anti-template diversity checker.

Verifies that different simulation specs produce different compiled
artifacts, preventing the "template 通吃" failure mode where all inputs
produce the same output regardless of user intent.
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ArtifactDiversityChecker",
    "DiversityReport",
    "DiversityViolation",
]


class DiversityViolation(BaseModel):
    """A single diversity violation found during checking."""

    model_config = ConfigDict(extra="forbid")

    violation_id: str
    check_name: str
    description: str
    spec_ids: list[str]
    artifact_keys: list[str]
    severity: Literal["warning", "error"]


class DiversityReport(BaseModel):
    """Overall diversity check report."""

    model_config = ConfigDict(extra="forbid")

    total_specs_checked: int
    total_artifacts_compared: int
    violations: list[DiversityViolation] = []
    passed: bool = True
    summary: str = ""

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if not self.violations:
            self.passed = True
            self.summary = (
                f"All {self.total_specs_checked} specs produce diverse artifacts "
                f"across {self.total_artifacts_compared} comparisons."
            )
        else:
            errors = [v for v in self.violations if v.severity == "error"]
            self.passed = len(errors) == 0
            self.summary = (
                f"{len(self.violations)} diversity violations found "
                f"({len(errors)} errors, {len(self.violations) - len(errors)} warnings) "
                f"across {self.total_specs_checked} specs."
            )


class ArtifactDiversityChecker:
    """Checks that different specs produce different compiled artifacts."""

    def check_compiled_cases(
        self,
        specs: list[dict],
        compiled_cases: list[dict],
    ) -> DiversityReport:
        """Compare compiled cases for diversity.

        Parameters
        ----------
        specs:
            List of SimulationStudySpec dicts.
        compiled_cases:
            List of CompiledCase dicts (same order as specs).
        """
        violations: list[DiversityViolation] = []

        violations.extend(self.check_archive_hash_diversity(compiled_cases))
        violations.extend(self.check_control_dict_diversity(specs, compiled_cases))
        violations.extend(self.check_geometry_diversity(specs, compiled_cases))
        violations.extend(self.check_boundary_diversity(specs, compiled_cases))
        violations.extend(self.check_transport_diversity(specs, compiled_cases))
        violations.extend(self.check_turbulence_diversity(specs, compiled_cases))
        violations.extend(self.check_function_object_diversity(specs, compiled_cases))

        total_comparisons = len(specs) * (len(specs) - 1) // 2 if len(specs) > 1 else 1

        return DiversityReport(
            total_specs_checked=len(specs),
            total_artifacts_compared=total_comparisons,
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_archive_hash_diversity(
        self, cases: list[dict]
    ) -> list[DiversityViolation]:
        """All archive_sha256 must be different for different specs."""
        violations: list[DiversityViolation] = []
        seen: dict[str, list[int]] = {}

        for i, case in enumerate(cases):
            sha = case.get("archive_sha256", "")
            if sha not in seen:
                seen[sha] = []
            seen[sha].append(i)

        for sha, indices in seen.items():
            if len(indices) > 1:
                spec_ids = [cases[i].get("spec_id", f"spec_{i}") for i in indices]
                violations.append(DiversityViolation(
                    violation_id=f"hash_collision_{sha[:8]}",
                    check_name="archive_hash_diversity",
                    description=(
                        f"Specs at indices {indices} produce identical "
                        f"archive_sha256 ({sha[:16]}...). "
                        f"Different specs must produce different compiled cases."
                    ),
                    spec_ids=spec_ids,
                    artifact_keys=["archive_sha256"],
                    severity="error",
                ))
        return violations

    def check_control_dict_diversity(
        self, specs: list[dict], cases: list[dict]
    ) -> list[DiversityViolation]:
        """Different end_time/deltaT must produce different controlDict."""
        violations: list[DiversityViolation] = []

        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                s1, s2 = specs[i], specs[j]
                c1, c2 = cases[i], cases[j]

                # Compare end_time
                et1 = self._get_nested(s1, "numerics", "time", "end_time", "value")
                et2 = self._get_nested(s2, "numerics", "time", "end_time", "value")

                if et1 is not None and et2 is not None and et1 != et2:
                    cd1 = c1.get("files", {}).get("system/controlDict", "")
                    cd2 = c2.get("files", {}).get("system/controlDict", "")
                    if cd1 == cd2:
                        violations.append(DiversityViolation(
                            violation_id=f"ctrl_dict_{i}_{j}",
                            check_name="control_dict_diversity",
                            description=(
                                f"Spec {i} has end_time={et1} and spec {j} has "
                                f"end_time={et2}, but controlDict files are identical."
                            ),
                            spec_ids=[
                                s1.get("spec_id", f"spec_{i}"),
                                s2.get("spec_id", f"spec_{j}"),
                            ],
                            artifact_keys=["system/controlDict"],
                            severity="error",
                        ))
        return violations

    def check_geometry_diversity(
        self, specs: list[dict], cases: list[dict]
    ) -> list[DiversityViolation]:
        """Different geometry must produce different compiled artifacts.

        Note: geometry changes may not affect 0/U field files directly
        (they affect mesh generation), so we check the overall archive hash
        instead of a specific file.
        """
        violations: list[DiversityViolation] = []

        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                s1, s2 = specs[i], specs[j]
                c1, c2 = cases[i], cases[j]

                g1 = self._get_nested(s1, "geometry", "entities", default={})
                g2 = self._get_nested(s2, "geometry", "entities", default={})

                if g1 != g2:
                    # Check that at least the archive hash differs
                    h1 = c1.get("archive_sha256", "")
                    h2 = c2.get("archive_sha256", "")
                    if h1 == h2:
                        violations.append(DiversityViolation(
                            violation_id=f"geom_{i}_{j}",
                            check_name="geometry_diversity",
                            description=(
                                f"Specs {i} and {j} have different geometry entities "
                                f"but identical archive_sha256."
                            ),
                            spec_ids=[
                                s1.get("spec_id", f"spec_{i}"),
                                s2.get("spec_id", f"spec_{j}"),
                            ],
                            artifact_keys=["archive_sha256"],
                            severity="error",
                        ))
        return violations

    def check_boundary_diversity(
        self, specs: list[dict], cases: list[dict]
    ) -> list[DiversityViolation]:
        """Different boundary conditions must produce different 0/U files."""
        violations: list[DiversityViolation] = []

        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                s1, s2 = specs[i], specs[j]
                c1, c2 = cases[i], cases[j]

                b1 = self._get_nested(s1, "boundaries", "conditions", default=[])
                b2 = self._get_nested(s2, "boundaries", "conditions", default=[])

                if b1 != b2:
                    u1 = c1.get("files", {}).get("0/U", "")
                    u2 = c2.get("files", {}).get("0/U", "")
                    if u1 == u2:
                        violations.append(DiversityViolation(
                            violation_id=f"bc_{i}_{j}",
                            check_name="boundary_diversity",
                            description=(
                                f"Specs {i} and {j} have different boundary conditions "
                                f"but identical 0/U field files."
                            ),
                            spec_ids=[
                                s1.get("spec_id", f"spec_{i}"),
                                s2.get("spec_id", f"spec_{j}"),
                            ],
                            artifact_keys=["0/U"],
                            severity="error",
                        ))
        return violations

    def check_transport_diversity(
        self, specs: list[dict], cases: list[dict]
    ) -> list[DiversityViolation]:
        """Different materials must produce different transportProperties."""
        violations: list[DiversityViolation] = []

        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                s1, s2 = specs[i], specs[j]
                c1, c2 = cases[i], cases[j]

                m1 = self._get_nested(s1, "physics", "material", "value")
                m2 = self._get_nested(s2, "physics", "material", "value")

                if m1 is not None and m2 is not None and m1 != m2:
                    tp1 = c1.get("files", {}).get("constant/transportProperties", "")
                    tp2 = c2.get("files", {}).get("constant/transportProperties", "")
                    if tp1 == tp2:
                        violations.append(DiversityViolation(
                            violation_id=f"transport_{i}_{j}",
                            check_name="transport_diversity",
                            description=(
                                f"Spec {i} has material='{m1}' and spec {j} has "
                                f"material='{m2}', but transportProperties are identical."
                            ),
                            spec_ids=[
                                s1.get("spec_id", f"spec_{i}"),
                                s2.get("spec_id", f"spec_{j}"),
                            ],
                            artifact_keys=["constant/transportProperties"],
                            severity="error",
                        ))
        return violations

    def check_turbulence_diversity(
        self, specs: list[dict], cases: list[dict]
    ) -> list[DiversityViolation]:
        """Different turbulence models must produce different turbulenceProperties."""
        violations: list[DiversityViolation] = []

        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                s1, s2 = specs[i], specs[j]
                c1, c2 = cases[i], cases[j]

                t1 = self._get_nested(s1, "numerics", "turbulence_model")
                t2 = self._get_nested(s2, "numerics", "turbulence_model")

                if t1 is not None and t2 is not None and t1 != t2:
                    tp1 = c1.get("files", {}).get("constant/turbulenceProperties", "")
                    tp2 = c2.get("files", {}).get("constant/turbulenceProperties", "")
                    if tp1 == tp2:
                        violations.append(DiversityViolation(
                            violation_id=f"turb_{i}_{j}",
                            check_name="turbulence_diversity",
                            description=(
                                f"Spec {i} has turbulence={t1} and spec {j} has "
                                f"turbulence={t2}, but turbulenceProperties are identical."
                            ),
                            spec_ids=[
                                s1.get("spec_id", f"spec_{i}"),
                                s2.get("spec_id", f"spec_{j}"),
                            ],
                            artifact_keys=["constant/turbulenceProperties"],
                            severity="error",
                        ))
        return violations

    def check_function_object_diversity(
        self, specs: list[dict], cases: list[dict]
    ) -> list[DiversityViolation]:
        """Different observations must produce different function objects in controlDict."""
        violations: list[DiversityViolation] = []

        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                s1, s2 = specs[i], specs[j]
                c1, c2 = cases[i], cases[j]

                o1 = self._get_nested(s1, "observations", "targets", default=[])
                o2 = self._get_nested(s2, "observations", "targets", default=[])

                if o1 != o2:
                    cd1 = c1.get("files", {}).get("system/controlDict", "")
                    cd2 = c2.get("files", {}).get("system/controlDict", "")
                    if cd1 == cd2:
                        violations.append(DiversityViolation(
                            violation_id=f"func_obj_{i}_{j}",
                            check_name="function_object_diversity",
                            description=(
                                f"Specs {i} and {j} have different observation targets "
                                f"but identical controlDict (function objects)."
                            ),
                            spec_ids=[
                                s1.get("spec_id", f"spec_{i}"),
                                s2.get("spec_id", f"spec_{j}"),
                            ],
                            artifact_keys=["system/controlDict"],
                            severity="error",
                        ))
        return violations

    def check_spec_to_artifact_traceability(
        self, specs: list[dict], cases: list[dict]
    ) -> list[DiversityViolation]:
        """Each spec change must be reflected in at least one artifact change."""
        violations: list[DiversityViolation] = []

        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                if specs[i] == specs[j]:
                    continue  # identical specs, skip

                c1_files = cases[i].get("files", {})
                c2_files = cases[j].get("files", {})

                # At least one file must differ
                all_same = True
                for key in set(list(c1_files.keys()) + list(c2_files.keys())):
                    if c1_files.get(key, "") != c2_files.get(key, ""):
                        all_same = False
                        break

                if all_same:
                    violations.append(DiversityViolation(
                        violation_id=f"traceability_{i}_{j}",
                        check_name="spec_to_artifact_traceability",
                        description=(
                            f"Specs {i} and {j} are different but ALL compiled "
                            f"artifacts are identical. At least one file must change."
                        ),
                        spec_ids=[
                            specs[i].get("spec_id", f"spec_{i}"),
                            specs[j].get("spec_id", f"spec_{j}"),
                        ],
                        artifact_keys=["all"],
                        severity="error",
                    ))
        return violations

    def generate_diversity_test_matrix(
        self, specs: list[dict]
    ) -> list[dict]:
        """Generate a matrix of spec pairs and expected artifact differences.

        Returns a list of dicts, each containing:
        - pair: (int, int) spec indices
        - spec_diff_fields: list of field paths that differ
        - expected_artifact_changes: list of file keys that should differ
        """
        matrix: list[dict] = []
        field_to_artifact = {
            "numerics.time.end_time": "system/controlDict",
            "numerics.time.delta_t": "system/controlDict",
            "numerics.turbulence_model": "constant/turbulenceProperties",
            "physics.material": "constant/transportProperties",
            "physics.kinematic_viscosity": "constant/transportProperties",
            "geometry.entities": "0/U",
            "boundaries.conditions": "0/U",
            "observations.targets": "system/controlDict",
        }

        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                s1, s2 = specs[i], specs[j]
                diff_fields: list[str] = []

                for field in field_to_artifact:
                    v1 = self._get_nested_by_path(s1, field)
                    v2 = self._get_nested_by_path(s2, field)
                    if v1 != v2:
                        diff_fields.append(field)

                expected_changes = list({
                    field_to_artifact[f] for f in diff_fields
                    if f in field_to_artifact
                })

                matrix.append({
                    "pair": (i, j),
                    "spec_diff_fields": diff_fields,
                    "expected_artifact_changes": expected_changes,
                })

        return matrix

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_nested(d: dict, *keys: str, default: Any = None) -> Any:
        """Safely traverse nested dict by keys."""
        current: Any = d
        for key in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(key, default)
            if current is None:
                return default
        return current

    @staticmethod
    def _get_nested_by_path(d: dict, dot_path: str) -> Any:
        """Traverse nested dict by dot-separated path."""
        keys = dot_path.split(".")
        return ArtifactDiversityChecker._get_nested(d, *keys)
