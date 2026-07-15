"""Case IR validation suite.

This package provides a comprehensive set of validators for
:class:`~fluid_scientist.case_ir.models.RequestedCaseIR`:

- :class:`SchemaValidator` -- checks schema integrity (required fields,
  valid enum values, confidence ranges, etc.).
- :class:`ReferenceValidator` -- checks that all cross-references
  (entity, region, material, observable, patch) point to objects that
  exist.
- :class:`ScientificConsistencyValidator` -- detects scientifically
  contradictory or implausible combinations (e.g. LES + steady,
  isothermal + heat flux).
- :class:`CapabilityFeasibilityValidator` -- checks that the
  capabilities implied by the Case IR are registered and verified in the
  capability registry.
- :class:`DimensionalConsistencyValidator` -- checks that all physical
  parameters have correct and consistent units.

The :class:`CaseIRValidationReport` aggregates results from all five
validators.

Typical usage::

    from fluid_scientist.case_ir.validators import (
        SchemaValidator,
        ReferenceValidator,
        ScientificConsistencyValidator,
        CapabilityFeasibilityValidator,
        DimensionalConsistencyValidator,
        CaseIRValidationReport,
    )

    report = CaseIRValidationReport(
        schema_issues=SchemaValidator().validate(case_ir),
        reference_issues=ReferenceValidator().validate(case_ir),
        consistency_issues=ScientificConsistencyValidator().validate(case_ir),
        capability_issues=CapabilityFeasibilityValidator().validate(case_ir),
        dimensional_issues=DimensionalConsistencyValidator().validate(case_ir),
    )
    if not report.passed:
        print(report.summary())
"""

from __future__ import annotations

from fluid_scientist.case_ir.validators.capability_feasibility_validator import (
    CapabilityFeasibilityValidator,
)
from fluid_scientist.case_ir.validators.dimensional_consistency_validator import (
    DimensionalConsistencyValidator,
)
from fluid_scientist.case_ir.validators.reference_validator import (
    ReferenceValidator,
)
from fluid_scientist.case_ir.validators.report import CaseIRValidationReport
from fluid_scientist.case_ir.validators.schema_validator import (
    SchemaValidator,
    ValidationIssue,
)
from fluid_scientist.case_ir.validators.scientific_consistency_validator import (
    ScientificConsistencyValidator,
)

__all__ = [
    "CapabilityFeasibilityValidator",
    "CaseIRValidationReport",
    "DimensionalConsistencyValidator",
    "ReferenceValidator",
    "SchemaValidator",
    "ScientificConsistencyValidator",
    "ValidationIssue",
]
