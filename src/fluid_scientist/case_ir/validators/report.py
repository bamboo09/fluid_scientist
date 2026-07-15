"""Aggregated validation report for Case IR.

This module defines :class:`CaseIRValidationReport`, which collects the
results from all five validators (schema, reference, scientific
consistency, capability feasibility, and dimensional consistency) into
a single report with convenience properties for pass/fail decisions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from fluid_scientist.case_ir.validators.schema_validator import ValidationIssue


class CaseIRValidationReport(BaseModel):
    """Aggregated validation report for a :class:`~fluid_scientist.case_ir.models.RequestedCaseIR`.

    Collects issues from all five validators and provides convenience
    properties for determining overall pass/fail status, error counts,
    and warning counts.

    Attributes:
        schema_issues: Issues from :class:`SchemaValidator`.
        reference_issues: Issues from :class:`ReferenceValidator`.
        consistency_issues: Issues from
            :class:`ScientificConsistencyValidator`.
        capability_issues: Issues from
            :class:`CapabilityFeasibilityValidator`.
        dimensional_issues: Issues from
            :class:`DimensionalConsistencyValidator`.
    """

    schema_issues: list[ValidationIssue] = Field(default_factory=list)
    reference_issues: list[ValidationIssue] = Field(default_factory=list)
    consistency_issues: list[ValidationIssue] = Field(default_factory=list)
    capability_issues: list[ValidationIssue] = Field(default_factory=list)
    dimensional_issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if there are no error-level issues across all validators."""
        return not any(i.level == "error" for i in self.all_issues)

    @property
    def all_issues(self) -> list[ValidationIssue]:
        """All issues from all validators, concatenated."""
        return [
            *self.schema_issues,
            *self.reference_issues,
            *self.consistency_issues,
            *self.capability_issues,
            *self.dimensional_issues,
        ]

    @property
    def error_count(self) -> int:
        """Number of error-level issues."""
        return sum(1 for i in self.all_issues if i.level == "error")

    @property
    def warning_count(self) -> int:
        """Number of warning-level issues."""
        return sum(1 for i in self.all_issues if i.level == "warning")

    @property
    def info_count(self) -> int:
        """Number of info-level issues."""
        return sum(1 for i in self.all_issues if i.level == "info")

    @property
    def total_count(self) -> int:
        """Total number of issues (all levels)."""
        return len(self.all_issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        """Only error-level issues."""
        return [i for i in self.all_issues if i.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        """Only warning-level issues."""
        return [i for i in self.all_issues if i.level == "warning"]

    def summary(self) -> str:
        """Return a human-readable summary of the validation report."""
        lines = [
            f"Case IR Validation Report",
            f"  Passed: {self.passed}",
            f"  Total issues: {self.total_count}",
            f"  Errors: {self.error_count}",
            f"  Warnings: {self.warning_count}",
            f"  Info: {self.info_count}",
        ]
        if self.errors:
            lines.append("  Errors:")
            for issue in self.errors:
                lines.append(f"    [{issue.code}] {issue.path}: {issue.message}")
        if self.warnings:
            lines.append("  Warnings:")
            for issue in self.warnings:
                lines.append(f"    [{issue.code}] {issue.path}: {issue.message}")
        return "\n".join(lines)


__all__ = ["CaseIRValidationReport"]
