"""Schema-level validation for RequestedCaseIR."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from fluid_scientist.case_ir.models import RequestedCaseIR


class ValidationIssue(BaseModel):
    """A single validation issue found during Case IR validation.

    Attributes:
        level: Severity level -- ``"error"``, ``"warning"``, or ``"info"``.
            Only ``"error"`` level issues cause :attr:`CaseIRValidationReport.passed`
            to return ``False``.
        code: A short machine-readable code identifying the issue class.
        path: Dotted path to the offending field in the Case IR.
        message: Human-readable description of the issue.
    """

    level: str = "error"  # error, warning, info
    code: str = ""
    path: str = ""
    message: str = ""


# Valid literal sets -- kept as plain sets (not Literal) so that the
# validator can report unknown values rather than having pydantic reject
# them at model construction time.
_VALID_ENTITY_KINDS: set[str] = {
    "cylinder",
    "sphere",
    "box",
    "pipe",
    "plane_wall",
    "nozzle",
    "imported_stl",
    "custom",
}

_VALID_REGION_KINDS: set[str] = {"fluid", "solid", "porous"}

_VALID_SOURCES: set[str] = {
    "USER_EXPLICIT",
    "USER_CONFIRMED",
    "MODEL_INFERRED",
    "MODEL_RECOMMENDED",
    "SYSTEM_DEFAULT",
    "FORMULA_DERIVED",
    "CAPABILITY_REQUIRED",
    "TEMPLATE_DERIVED",
    "LITERATURE_SUGGESTED",
}

_VALID_STATUSES: set[str] = {
    "CONFIRMED",
    "INFERRED",
    "RECOMMENDED",
    "ASSUMED",
    "UNRESOLVED",
    "AMBIGUOUS",
    "CONFLICTING",
}

_VALID_CAPABILITY_STATUSES: set[str] = {
    "UNRESOLVED",
    "SUPPORTED",
    "COMPOSABLE",
    "EXTENDABLE",
    "REQUIRES_NEW_PHYSICS",
}

_VALID_TURBULENCE: set[str] = {"laminar", "RANS", "LES", "DES", "DNS"}

_VALID_FLOW_REGIMES: set[str] = {"incompressible", "compressible"}

_VALID_TIME_MODES: set[str] = {"steady", "transient"}


class SchemaValidator:
    """Validates :class:`RequestedCaseIR` schema integrity.

    This validator checks structural / schema-level constraints that are
    not enforced by the pydantic model itself (or that are deliberately
    relaxed at construction time so that partially-built IRs can be
    validated incrementally).  It covers:

    - Required top-level identifiers (``study_id``, ``case_id``).
    - Entity kinds and ids.
    - :class:`ParameterValue` ``source``, ``confidence``, and ``status``
      fields.
    - Region kinds.
    - Observable ``semantic_type`` and ``capability_status``.
    - Operating-stage ``time_range`` length and ordering.
    - Physics-intent ``turbulence`` value.
    """

    def validate(self, case_ir: RequestedCaseIR) -> list[ValidationIssue]:
        """Run all schema checks and return the list of issues found."""
        issues: list[ValidationIssue] = []

        # -- Required top-level fields -----------------------------------
        if not case_ir.study_id:
            issues.append(
                ValidationIssue(
                    code="MISSING_STUDY_ID",
                    path="study_id",
                    message="study_id is required",
                )
            )
        if not case_ir.case_id:
            issues.append(
                ValidationIssue(
                    code="MISSING_CASE_ID",
                    path="case_id",
                    message="case_id is required",
                )
            )

        # -- Entities ----------------------------------------------------
        for i, entity in enumerate(case_ir.entities):
            if entity.kind not in _VALID_ENTITY_KINDS:
                issues.append(
                    ValidationIssue(
                        code="INVALID_ENTITY_KIND",
                        path=f"entities[{i}].kind",
                        message=f"Unknown entity kind: {entity.kind}",
                    )
                )
            if not entity.id:
                issues.append(
                    ValidationIssue(
                        code="MISSING_ENTITY_ID",
                        path=f"entities[{i}].id",
                        message=f"Entity at index {i} has no id",
                    )
                )

        # -- ParameterValue structures ----------------------------------
        for i, entity in enumerate(case_ir.entities):
            for param_name, param_val in entity.parameters.items():
                if param_val.source not in _VALID_SOURCES:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_SOURCE",
                            path=f"entities[{i}].parameters.{param_name}.source",
                            message=f"Invalid source: {param_val.source}",
                        )
                    )
                if not 0 <= param_val.confidence <= 1.0:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_CONFIDENCE",
                            path=f"entities[{i}].parameters.{param_name}.confidence",
                            message=f"confidence must be 0-1, got {param_val.confidence}",
                        )
                    )
                if param_val.status not in _VALID_STATUSES:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_STATUS",
                            path=f"entities[{i}].parameters.{param_name}.status",
                            message=f"Invalid status: {param_val.status}",
                        )
                    )

        # Also check material properties
        for i, material in enumerate(case_ir.materials):
            for param_name, param_val in material.properties.items():
                if param_val.source not in _VALID_SOURCES:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_SOURCE",
                            path=f"materials[{i}].properties.{param_name}.source",
                            message=f"Invalid source: {param_val.source}",
                        )
                    )
                if not 0 <= param_val.confidence <= 1.0:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_CONFIDENCE",
                            path=f"materials[{i}].properties.{param_name}.confidence",
                            message=f"confidence must be 0-1, got {param_val.confidence}",
                        )
                    )

        # -- Regions -----------------------------------------------------
        for i, region in enumerate(case_ir.regions):
            if region.kind not in _VALID_REGION_KINDS:
                issues.append(
                    ValidationIssue(
                        code="INVALID_REGION_KIND",
                        path=f"regions[{i}].kind",
                        message=f"Unknown region kind: {region.kind}",
                    )
                )
            if not region.id:
                issues.append(
                    ValidationIssue(
                        code="MISSING_REGION_ID",
                        path=f"regions[{i}].id",
                        message=f"Region at index {i} has no id",
                    )
                )

        # -- Observables -------------------------------------------------
        for i, obs in enumerate(case_ir.observables):
            if not obs.semantic_type:
                issues.append(
                    ValidationIssue(
                        code="MISSING_OBSERVABLE_TYPE",
                        path=f"observables[{i}].semantic_type",
                        message=f"Observable at index {i} has no semantic_type",
                    )
                )
            if obs.capability_status not in _VALID_CAPABILITY_STATUSES:
                issues.append(
                    ValidationIssue(
                        code="INVALID_CAPABILITY_STATUS",
                        path=f"observables[{i}].capability_status",
                        message=f"Invalid capability_status: {obs.capability_status}",
                    )
                )

        # -- Operating stages -------------------------------------------
        for i, stage in enumerate(case_ir.operating_stages):
            if stage.time_range and len(stage.time_range) != 2:
                issues.append(
                    ValidationIssue(
                        code="INVALID_TIME_RANGE",
                        path=f"operating_stages[{i}].time_range",
                        message="time_range must have exactly 2 elements",
                    )
                )
            if stage.time_range and stage.time_range[0] >= stage.time_range[1]:
                issues.append(
                    ValidationIssue(
                        code="INVALID_TIME_RANGE",
                        path=f"operating_stages[{i}].time_range",
                        message="time_range start must be < end",
                    )
                )

        # -- Physics intent ----------------------------------------------
        if case_ir.physics.turbulence not in _VALID_TURBULENCE:
            issues.append(
                ValidationIssue(
                    code="INVALID_TURBULENCE",
                    path="physics.turbulence",
                    message=f"Invalid turbulence: {case_ir.physics.turbulence}",
                )
            )
        if case_ir.physics.flow_regime not in _VALID_FLOW_REGIMES:
            issues.append(
                ValidationIssue(
                    code="INVALID_FLOW_REGIME",
                    path="physics.flow_regime",
                    message=f"Invalid flow_regime: {case_ir.physics.flow_regime}",
                )
            )
        if case_ir.physics.time_mode not in _VALID_TIME_MODES:
            issues.append(
                ValidationIssue(
                    code="INVALID_TIME_MODE",
                    path="physics.time_mode",
                    message=f"Invalid time_mode: {case_ir.physics.time_mode}",
                )
            )

        # -- Duplicate ids -----------------------------------------------
        entity_ids = [e.id for e in case_ir.entities if e.id]
        seen: set[str] = set()
        for eid in entity_ids:
            if eid in seen:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="DUPLICATE_ENTITY_ID",
                        path="entities",
                        message=f"Duplicate entity id: {eid}",
                    )
                )
            seen.add(eid)

        region_ids = [r.id for r in case_ir.regions if r.id]
        seen.clear()
        for rid in region_ids:
            if rid in seen:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_REGION_ID",
                        path="regions",
                        message=f"Duplicate region id: {rid}",
                    )
                )
            seen.add(rid)

        return issues


__all__ = ["SchemaValidator", "ValidationIssue"]
