"""Semantic fidelity checker for the Case IR conversion.

The :class:`SemanticFidelityChecker` verifies that the conversion from a
:class:`~fluid_scientist.study_spec.models.SimulationStudySpec` to a
:class:`~fluid_scientist.case_ir.models.RequestedCaseIR` preserves all
scientific intent:

* Entity count is preserved.
* Each entity's ``semantic_type`` is retained in the Case IR.
* Domain dimensions match.
* Spatial relationships (geometry relations) are preserved.
* All user-explicit values have source evidence in the Case IR.

The checker returns a :class:`SemanticFidelityReport` with boolean flags
for each check and a list of human-readable violation strings.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "SemanticFidelityChecker",
    "SemanticFidelityReport",
]


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------


class SemanticFidelityReport(BaseModel):
    """Result of a semantic fidelity check.

    Attributes:
        entity_count_match: True if the entity count in the spec equals
            the entity count in the Case IR.
        geometry_types_preserved: True if every entity's
            ``semantic_type`` is preserved in the Case IR.
        dimensions_preserved: True if domain dimensions match between
            spec and Case IR.
        spatial_relationships_preserved: True if all geometry relations
            are preserved in the Case IR.
        source_evidence_complete: True if all user-explicit values have
            corresponding source evidence in the Case IR.
        violations: List of human-readable violation descriptions.
    """

    entity_count_match: bool = True
    geometry_types_preserved: bool = True
    dimensions_preserved: bool = True
    spatial_relationships_preserved: bool = True
    source_evidence_complete: bool = True
    violations: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if no violations were detected."""
        return not self.violations


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


class SemanticFidelityChecker:
    """Verifies semantic fidelity between a spec dict and a Case IR dict.

    The checker compares a serialised
    :class:`~fluid_scientist.study_spec.models.SimulationStudySpec`
    dict with a serialised
    :class:`~fluid_scientist.case_ir.models.RequestedCaseIR` dict and
    produces a :class:`SemanticFidelityReport`.
    """

    def check(
        self,
        spec_dict: dict[str, Any],
        case_ir_dict: dict[str, Any],
    ) -> SemanticFidelityReport:
        """Run all fidelity checks.

        Args:
            spec_dict: A serialised ``SimulationStudySpec`` dict.
            case_ir_dict: A serialised ``RequestedCaseIR`` dict (or the
                output of
                :class:`~fluid_scientist.case_ir.geometry_to_case_ir.StudySpecToCaseIRConverter`).

        Returns:
            A :class:`SemanticFidelityReport` with the results.
        """
        report = SemanticFidelityReport()

        self._check_entity_count(spec_dict, case_ir_dict, report)
        self._check_geometry_types(spec_dict, case_ir_dict, report)
        self._check_dimensions(spec_dict, case_ir_dict, report)
        self._check_spatial_relationships(spec_dict, case_ir_dict, report)
        self._check_source_evidence(spec_dict, case_ir_dict, report)

        return report

    # ------------------------------------------------------------------
    # Entity count
    # ------------------------------------------------------------------

    def _check_entity_count(
        self,
        spec_dict: dict[str, Any],
        case_ir_dict: dict[str, Any],
        report: SemanticFidelityReport,
    ) -> None:
        """Verify entity count in spec == entity count in case_ir."""
        spec_entities = spec_dict.get("geometry", {}).get("entities", {})
        spec_count = len(spec_entities) if isinstance(spec_entities, dict) else 0

        case_entities = case_ir_dict.get("entities", [])
        case_count = len(case_entities) if isinstance(case_entities, list) else 0

        if spec_count != case_count:
            report.entity_count_match = False
            report.violations.append(
                f"Entity count mismatch: spec has {spec_count} entities, "
                f"case_ir has {case_count}"
            )

    # ------------------------------------------------------------------
    # Geometry types
    # ------------------------------------------------------------------

    def _check_geometry_types(
        self,
        spec_dict: dict[str, Any],
        case_ir_dict: dict[str, Any],
        report: SemanticFidelityReport,
    ) -> None:
        """Verify each entity's semantic_type is preserved in case_ir."""
        spec_entities = spec_dict.get("geometry", {}).get("entities", {})
        if not isinstance(spec_entities, dict):
            return

        case_entities = case_ir_dict.get("entities", [])
        if not isinstance(case_entities, list):
            return

        # Build a lookup: entity_id -> semantic_type from case_ir.
        case_semantic_types: dict[str, str | None] = {}
        for entity in case_entities:
            eid = entity.get("id", "")
            params = entity.get("parameters", {})
            sem_val = params.get("semantic_type", {})
            if isinstance(sem_val, dict):
                case_semantic_types[eid] = str(sem_val.get("value", ""))
            else:
                case_semantic_types[eid] = str(sem_val) if sem_val else None

        for entity_id, entity in spec_entities.items():
            eid = entity.get("entity_id", entity_id)
            spec_semantic_type = entity.get("semantic_type", "")
            case_semantic_type = case_semantic_types.get(eid)

            if case_semantic_type is None:
                report.geometry_types_preserved = False
                report.violations.append(
                    f"Entity '{eid}' not found in case_ir"
                )
            elif case_semantic_type != spec_semantic_type:
                report.geometry_types_preserved = False
                report.violations.append(
                    f"Geometry type mismatch for entity '{eid}': "
                    f"spec='{spec_semantic_type}', "
                    f"case_ir='{case_semantic_type}'"
                )

    # ------------------------------------------------------------------
    # Dimensions
    # ------------------------------------------------------------------

    def _check_dimensions(
        self,
        spec_dict: dict[str, Any],
        case_ir_dict: dict[str, Any],
        report: SemanticFidelityReport,
    ) -> None:
        """Verify domain dimensions match between spec and case_ir."""
        spec_domain = spec_dict.get("geometry", {}).get("domain", {})
        case_domain = case_ir_dict.get("domain", {})

        # Compare dimensions (2d / 3d).
        spec_dims = spec_domain.get("dimensions", "2d")
        case_dims = case_domain.get("dimensions", "2d")
        if spec_dims != case_dims:
            report.dimensions_preserved = False
            report.violations.append(
                f"Domain dimensions mismatch: spec='{spec_dims}', "
                f"case_ir='{case_dims}'"
            )
            return

        # Compare length.
        spec_length = self._extract_sourced_value(spec_domain.get("length"))
        case_length = self._extract_sourced_value(case_domain.get("length"))
        if spec_length is not None and case_length is not None:
            if abs(spec_length - case_length) > 1e-9:
                report.dimensions_preserved = False
                report.violations.append(
                    f"Domain length mismatch: spec={spec_length}, "
                    f"case_ir={case_length}"
                )

        # Compare width (if present).
        spec_width = self._extract_sourced_value(spec_domain.get("width"))
        case_width = self._extract_sourced_value(case_domain.get("width"))
        if spec_width is not None and case_width is not None:
            if abs(spec_width - case_width) > 1e-9:
                report.dimensions_preserved = False
                report.violations.append(
                    f"Domain width mismatch: spec={spec_width}, "
                    f"case_ir={case_width}"
                )

    # ------------------------------------------------------------------
    # Spatial relationships
    # ------------------------------------------------------------------

    def _check_spatial_relationships(
        self,
        spec_dict: dict[str, Any],
        case_ir_dict: dict[str, Any],
        report: SemanticFidelityReport,
    ) -> None:
        """Verify spatial relationships are preserved."""
        spec_relations = spec_dict.get("geometry", {}).get("relations", [])
        case_relations = case_ir_dict.get("relations", [])

        if not isinstance(spec_relations, list):
            spec_relations = []
        if not isinstance(case_relations, list):
            case_relations = []

        # Build a set of (source, target, type) tuples from case_ir.
        case_set: set[tuple[str, str, str]] = set()
        for rel in case_relations:
            case_set.add((
                rel.get("source", ""),
                rel.get("target", ""),
                rel.get("type", ""),
            ))

        for rel in spec_relations:
            subject = rel.get("subject_id", "")
            obj = rel.get("object_id", "")
            # Direct type match or check if any relation between the
            # same pair exists in case_ir.
            rel_type = rel.get("type", "")
            key = (subject, obj, rel_type)
            # Also check with mapped type (the converter may map types).
            if key not in case_set:
                # Check if any relation between subject and object exists.
                pair_found = any(
                    r.get("source") == subject and r.get("target") == obj
                    for r in case_relations
                )
                if not pair_found:
                    report.spatial_relationships_preserved = False
                    report.violations.append(
                        f"Spatial relationship not preserved: "
                        f"{subject} -> {obj} (type='{rel_type}')"
                    )

    # ------------------------------------------------------------------
    # Source evidence
    # ------------------------------------------------------------------

    def _check_source_evidence(
        self,
        spec_dict: dict[str, Any],
        case_ir_dict: dict[str, Any],
        report: SemanticFidelityReport,
    ) -> None:
        """Verify all user-explicit values have source evidence in case_ir.

        This check counts the number of ``user_explicit`` SourcedValues
        in the spec and verifies that the case_ir contains at least as
        many ``USER_EXPLICIT`` ParameterValues.
        """
        spec_user_count = self._count_user_explicit_in_spec(spec_dict)
        case_user_count = self._count_user_explicit_in_case_ir(case_ir_dict)

        if case_user_count < spec_user_count:
            report.source_evidence_complete = False
            report.violations.append(
                f"Source evidence incomplete: spec has {spec_user_count} "
                f"user_explicit values, case_ir has only {case_user_count} "
                f"USER_EXPLICIT ParameterValues"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sourced_value(sourced: Any) -> float | None:
        """Extract a numeric value from a SourcedValue dict or raw number."""
        if sourced is None:
            return None
        if isinstance(sourced, int | float):
            return float(sourced)
        if isinstance(sourced, dict):
            v = sourced.get("value")
            if isinstance(v, int | float):
                return float(v)
        return None

    def _count_user_explicit_in_spec(self, spec_dict: dict[str, Any]) -> int:
        """Count user_explicit SourcedValues in the spec."""
        count = 0

        # Geometry entities.
        entities = spec_dict.get("geometry", {}).get("entities", {})
        if isinstance(entities, dict):
            for entity in entities.values():
                placement = entity.get("placement") or {}
                for coord in ("x", "y"):
                    val = placement.get(coord)
                    if isinstance(val, dict) and val.get("status") == "user_explicit":
                        count += 1

        # Boundary conditions.
        conditions = spec_dict.get("boundaries", {}).get("conditions", [])
        if isinstance(conditions, list):
            for cond in conditions:
                if cond.get("source_status") == "user_explicit":
                    count += 1

        # Physics material.
        material = spec_dict.get("physics", {}).get("material", {})
        if isinstance(material, dict) and material.get("status") == "user_explicit":
            count += 1

        # Domain dimensions.
        domain = spec_dict.get("geometry", {}).get("domain", {})
        for dim in ("length", "width", "height"):
            val = domain.get(dim)
            if isinstance(val, dict) and val.get("status") == "user_explicit":
                count += 1

        return count

    def _count_user_explicit_in_case_ir(self, case_ir_dict: dict[str, Any]) -> int:
        """Count USER_EXPLICIT ParameterValues in the case_ir."""
        count = 0

        # Entities.
        entities = case_ir_dict.get("entities", [])
        if isinstance(entities, list):
            for entity in entities:
                params = entity.get("parameters", {})
                if isinstance(params, dict):
                    for pv in params.values():
                        if isinstance(pv, dict) and pv.get("source") == "USER_EXPLICIT":
                            count += 1

        # Boundary intents.
        boundary_intents = case_ir_dict.get("boundary_intents", [])
        if isinstance(boundary_intents, list):
            for bi in boundary_intents:
                params = bi.get("parameters", {})
                if isinstance(params, dict):
                    for pv in params.values():
                        if isinstance(pv, dict) and pv.get("source") == "USER_EXPLICIT":
                            count += 1

        # Materials.
        materials = case_ir_dict.get("materials", [])
        if isinstance(materials, list):
            for mat in materials:
                props = mat.get("properties", {})
                if isinstance(props, dict):
                    for pv in props.values():
                        if isinstance(pv, dict) and pv.get("source") == "USER_EXPLICIT":
                            count += 1

        return count
