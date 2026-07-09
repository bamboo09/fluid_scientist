"""Draft validator — validates an :class:`ExperimentDraft`.

The :class:`DraftValidator` runs a series of deterministic checks against a
draft and returns a :class:`~fluid_scientist.draft.models.ValidationResult`
that categorises problems into *blocking issues* (structured dicts that
prevent the draft from advancing), *warnings* (non-blocking suggestions) and
*errors*.

Checks
------
1.  Critical parameters must have values (blocking).
2.  ``unknown_required`` parameters must NOT carry a value (blocking).
3.  Geometry must define a ``type`` and a characteristic dimension (blocking).
4.  Boundary conditions must include at least an inlet and an outlet
    (blocking).
5.  A solver must be specified (warning).
6.  A mesh strategy must be specified (warning).
7.  A measurement plan is required when requested outputs exist (warning).
8.  Analysis goals must not be empty (warning).
9.  The draft must not carry pre-existing blocking issues (blocking).
10. Rejected assumptions must not be present (blocking).
"""

from __future__ import annotations

from fluid_scientist.draft.models import (
    ExperimentDraft,
    ParameterSource,
    ValidationResult,
)

# Keys that may carry a characteristic (length) dimension inside ``geometry``.
_CHARACTERISTIC_DIMENSION_KEYS = frozenset(
    {"characteristic_length", "characteristic_dimension", "D", "diameter"}
)

# Boundary-condition types that must be present.
_REQUIRED_BOUNDARY_TYPES = ("inlet", "outlet")


def _has_boundary_type(boundary_conditions: dict, btype: str) -> bool:
    """Return ``True`` if ``btype`` is referenced by the boundary conditions.

    The boundary conditions are stored as a dict keyed by boundary name (the
    generator keys each entry by its ``type``).  We accept both the case
    where ``btype`` is a top-level key and the case where it appears as the
    ``type`` field of a nested entry, so the validator stays robust to
    different draft shapes.
    """
    if btype in boundary_conditions:
        return True
    return any(
        isinstance(value, dict) and value.get("type") == btype
        for value in boundary_conditions.values()
    )


def _is_rejected_assumption(assumption: dict) -> bool:
    """Return ``True`` if an assumption dict has been marked as rejected."""
    return assumption.get("status") == "rejected" or assumption.get(
        "rejected"
    ) is True


class DraftValidator:
    """Validate an :class:`ExperimentDraft` and report issues."""

    def validate(self, draft: ExperimentDraft) -> ValidationResult:
        """Validate a draft and return issues.

        Args:
            draft: The :class:`ExperimentDraft` to validate.

        Returns:
            A :class:`ValidationResult` with all detected issues
            categorised into ``blocking_issues`` (dicts), ``warnings``
            (strings) and ``errors`` (strings).  ``valid`` is ``True`` only
            when no blocking issues or errors were found.
        """
        blocking_issues: list[dict] = []
        warnings: list[str] = []

        # 1. Critical parameters must have values.
        #    A parameter is "critical" (must carry a value) when its source
        #    is anything other than ``unknown_required`` -- i.e. every value
        #    that is supposed to be known must actually be present.
        for param in draft.control_parameters:
            if param.source == ParameterSource.UNKNOWN_REQUIRED:
                continue
            if param.value is None:
                blocking_issues.append(
                    {
                        "check": "critical_parameter_missing_value",
                        "message": (
                            f"Critical parameter '{param.parameter_id}' "
                            f"({param.display_name}) is missing a value"
                        ),
                    }
                )

        # 2. unknown_required parameters must NOT carry a value.
        for param in draft.control_parameters:
            if (
                param.source == ParameterSource.UNKNOWN_REQUIRED
                and param.value is not None
            ):
                blocking_issues.append(
                    {
                        "check": "unknown_required_has_value",
                        "message": (
                            f"Parameter '{param.parameter_id}' has source "
                            f"'unknown_required' but a value is set"
                        ),
                    }
                )

        # 3. Geometry must define a type and a characteristic dimension.
        geometry = draft.geometry
        if not geometry:
            blocking_issues.append(
                {
                    "check": "geometry_empty",
                    "message": "Geometry is not specified",
                }
            )
        else:
            if "type" not in geometry:
                blocking_issues.append(
                    {
                        "check": "geometry_missing_type",
                        "message": "Geometry is missing required field 'type'",
                    }
                )
            if not any(
                key in geometry for key in _CHARACTERISTIC_DIMENSION_KEYS
            ):
                blocking_issues.append(
                    {
                        "check": "geometry_missing_characteristic_dimension",
                        "message": (
                            "Geometry is missing a characteristic dimension "
                            "(e.g. 'characteristic_length' or 'D')"
                        ),
                    }
                )

        # 4. Boundary conditions must include inlet and outlet.
        boundary_conditions = draft.boundary_conditions
        if not isinstance(boundary_conditions, dict) or not boundary_conditions:
            blocking_issues.append(
                {
                    "check": "boundary_conditions_empty",
                    "message": "Boundary conditions are not specified",
                }
            )
        else:
            for btype in _REQUIRED_BOUNDARY_TYPES:
                if not _has_boundary_type(boundary_conditions, btype):
                    blocking_issues.append(
                        {
                            "check": f"boundary_condition_missing_{btype}",
                            "message": (
                                f"Boundary conditions are missing '{btype}'"
                            ),
                        }
                    )

        # 5. A solver should be specified (warning).
        if not draft.solver:
            warnings.append("Solver is not specified")

        # 6. A mesh strategy should be specified (warning).
        if not draft.mesh:
            warnings.append("Mesh strategy is not specified")

        # 7. A measurement plan is required when outputs are requested.
        if draft.requested_outputs and not draft.measurement_plan:
            warnings.append(
                "Requested outputs exist but no measurement plan is defined"
            )

        # 8. Analysis goals should not be empty.
        if not draft.analysis_goals:
            warnings.append("Analysis goals are empty")

        # 9. The draft must not carry pre-existing blocking issues.
        for issue in draft.blocking_issues:
            message = (
                issue.get("issue")
                or issue.get("message")
                or str(issue)
                if isinstance(issue, dict)
                else str(issue)
            )
            blocking_issues.append(
                {
                    "check": "pre_existing_blocking_issue",
                    "message": f"Draft carries a blocking issue: {message}",
                }
            )

        # 10. Rejected assumptions must not be present.
        for assumption in draft.assumptions:
            if isinstance(assumption, dict) and _is_rejected_assumption(
                assumption
            ):
                field_name = assumption.get("field") or assumption.get(
                    "name", "unknown"
                )
                blocking_issues.append(
                    {
                        "check": "rejected_assumption_present",
                        "message": (
                            f"Rejected assumption '{field_name}' is present "
                            f"in the draft"
                        ),
                    }
                )

        # ``valid`` is True only when there are no blocking issues and no
        # errors.  ``errors`` is currently never populated by the checks
        # above but is part of the result contract.
        valid = not blocking_issues

        return ValidationResult(
            valid=valid,
            blocking_issues=blocking_issues,
            warnings=warnings,
            errors=[],
        )


__all__ = ["DraftValidator"]
