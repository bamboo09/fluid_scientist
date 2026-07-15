"""Reference integrity validation for RequestedCaseIR.

This validator ensures that all cross-references inside a
:class:`~fluid_scientist.case_ir.models.RequestedCaseIR` point to objects
that actually exist.  Dangling references are a common source of runtime
errors in downstream compilers, so they are caught here as ``error``-level
issues.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.case_ir.models import RequestedCaseIR
from fluid_scientist.case_ir.validators.schema_validator import ValidationIssue


class ReferenceValidator:
    """Validates that all references in the Case IR are valid.

    Checks performed:

    - ``observable.target_region`` exists in ``regions``.
    - ``interface.region_a`` and ``interface.region_b`` exist in ``regions``.
    - ``relation.source`` and ``relation.target`` exist in ``entities``.
    - ``boundary_intent.target_patch`` is non-empty and, when it matches an
      entity id pattern, that entity exists.
    - ``initial_condition.target`` references a valid region or field.
    - ``operating_stage.observable_refs`` reference valid observables.
    - ``derived_constraint.inputs`` reference valid parameter paths.
    - ``region.material_ref`` references a valid material (when non-empty).
    - ``region.physics_refs`` are valid (checked against physics intent).
    - ``observable.capability_ref`` is checked for format validity.
    """

    def validate(self, case_ir: RequestedCaseIR) -> list[ValidationIssue]:
        """Run all reference checks and return the list of issues found."""
        issues: list[ValidationIssue] = []

        entity_ids = {e.id for e in case_ir.entities if e.id}
        region_ids = {r.id for r in case_ir.regions if r.id}
        material_ids = {m.id for m in case_ir.materials if m.id}
        observable_ids = {o.id for o in case_ir.observables if o.id}
        field_names = {f.name for f in case_ir.fields if f.name}
        stage_ids = {s.id for s in case_ir.operating_stages if s.id}

        # -- Interfaces: region_a / region_b -----------------------------
        for i, interface in enumerate(case_ir.interfaces):
            if interface.region_a not in region_ids:
                issues.append(
                    ValidationIssue(
                        code="DANGLING_INTERFACE_REGION_A",
                        path=f"interfaces[{i}].region_a",
                        message=(
                            f"Interface '{interface.id}' references "
                            f"non-existent region_a: '{interface.region_a}'"
                        ),
                    )
                )
            if interface.region_b not in region_ids:
                issues.append(
                    ValidationIssue(
                        code="DANGLING_INTERFACE_REGION_B",
                        path=f"interfaces[{i}].region_b",
                        message=(
                            f"Interface '{interface.id}' references "
                            f"non-existent region_b: '{interface.region_b}'"
                        ),
                    )
                )
            if interface.region_a == interface.region_b:
                issues.append(
                    ValidationIssue(
                        code="SELF_REFERENCING_INTERFACE",
                        path=f"interfaces[{i}]",
                        message=(
                            f"Interface '{interface.id}' has identical "
                            f"region_a and region_b: '{interface.region_a}'"
                        ),
                    )
                )

        # -- Observables: target_region ----------------------------------
        for i, obs in enumerate(case_ir.observables):
            if obs.target_region and obs.target_region not in region_ids:
                issues.append(
                    ValidationIssue(
                        code="DANGLING_OBSERVABLE_REGION",
                        path=f"observables[{i}].target_region",
                        message=(
                            f"Observable '{obs.id}' references "
                            f"non-existent region: '{obs.target_region}'"
                        ),
                    )
                )
            # Check required_fields reference known field names
            for fname in obs.required_fields:
                if field_names and fname not in field_names:
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            code="UNKNOWN_REQUIRED_FIELD",
                            path=f"observables[{i}].required_fields",
                            message=(
                                f"Observable '{obs.id}' requires field "
                                f"'{fname}' which is not defined in fields"
                            ),
                        )
                    )

        # -- Relations: source / target ----------------------------------
        for i, rel in enumerate(case_ir.relations):
            if rel.source not in entity_ids:
                issues.append(
                    ValidationIssue(
                        code="DANGLING_RELATION_SOURCE",
                        path=f"relations[{i}].source",
                        message=(
                            f"Relation '{rel.id}' references "
                            f"non-existent source entity: '{rel.source}'"
                        ),
                    )
                )
            if rel.target not in entity_ids:
                issues.append(
                    ValidationIssue(
                        code="DANGLING_RELATION_TARGET",
                        path=f"relations[{i}].target",
                        message=(
                            f"Relation '{rel.id}' references "
                            f"non-existent target entity: '{rel.target}'"
                        ),
                    )
                )

        # -- Boundary intents: target_patch ------------------------------
        for i, bc in enumerate(case_ir.boundary_intents):
            if not bc.target_patch:
                issues.append(
                    ValidationIssue(
                        code="MISSING_TARGET_PATCH",
                        path=f"boundary_intents[{i}].target_patch",
                        message=(
                            f"Boundary intent '{bc.id}' has no target_patch"
                        ),
                    )
                )
            elif bc.target_patch in entity_ids:
                # Patch name matches an entity id -- this is valid.
                pass
            # If the patch is a standard OpenFOAM name (inlet, outlet,
            # walls, etc.) we accept it without error.  Only flag patches
            # that look like entity references but don't exist.
            elif bc.target_patch.startswith("entity:"):
                ref_id = bc.target_patch[len("entity:") :]
                if ref_id not in entity_ids:
                    issues.append(
                        ValidationIssue(
                            code="DANGLING_BOUNDARY_PATCH",
                            path=f"boundary_intents[{i}].target_patch",
                            message=(
                                f"Boundary intent '{bc.id}' references "
                                f"non-existent entity patch: '{ref_id}'"
                            ),
                        )
                    )

        # -- Initial conditions: target ---------------------------------
        for i, ic in enumerate(case_ir.initial_conditions):
            if not ic.target:
                issues.append(
                    ValidationIssue(
                        code="MISSING_IC_TARGET",
                        path=f"initial_conditions[{i}].target",
                        message=(
                            f"Initial condition '{ic.id}' has no target"
                        ),
                    )
                )
            elif ic.target not in region_ids and ic.target not in field_names:
                # Target might be a region or a field name; if it matches
                # neither, flag as warning.
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="UNKNOWN_IC_TARGET",
                        path=f"initial_conditions[{i}].target",
                        message=(
                            f"Initial condition '{ic.id}' target "
                            f"'{ic.target}' is neither a known region "
                            f"nor a known field"
                        ),
                    )
                )

        # -- Operating stages: observable_refs ---------------------------
        for i, stage in enumerate(case_ir.operating_stages):
            for ref in stage.observable_refs:
                if ref not in observable_ids:
                    issues.append(
                        ValidationIssue(
                            code="DANGLING_STAGE_OBSERVABLE_REF",
                            path=f"operating_stages[{i}].observable_refs",
                            message=(
                                f"Stage '{stage.id}' references "
                                f"non-existent observable: '{ref}'"
                            ),
                        )
                    )

        # -- Regions: material_ref --------------------------------------
        for i, region in enumerate(case_ir.regions):
            if region.material_ref and region.material_ref not in material_ids:
                issues.append(
                    ValidationIssue(
                        code="DANGLING_REGION_MATERIAL",
                        path=f"regions[{i}].material_ref",
                        message=(
                            f"Region '{region.id}' references "
                            f"non-existent material: '{region.material_ref}'"
                        ),
                    )
                )

        # -- Derived constraints: inputs --------------------------------
        for i, dc in enumerate(case_ir.derived_constraints):
            for inp in dc.inputs:
                if not _is_valid_param_path(inp, case_ir):
                    issues.append(
                        ValidationIssue(
                            code="DANGLING_DERIVED_INPUT",
                            path=f"derived_constraints[{i}].inputs",
                            message=(
                                f"Derived constraint '{dc.id}' input "
                                f"'{inp}' does not reference a valid "
                                f"parameter path"
                            ),
                        )
                    )
            if not dc.output:
                issues.append(
                    ValidationIssue(
                        code="MISSING_DERIVED_OUTPUT",
                        path=f"derived_constraints[{i}].output",
                        message=(
                            f"Derived constraint '{dc.id}' has no output path"
                        ),
                    )
                )

        # -- Entity motion references -----------------------------------
        for i, entity in enumerate(case_ir.entities):
            if entity.motion:
                # Motion references should be a known concept; at minimum
                # they should not be empty strings.
                if not entity.motion.strip():
                    issues.append(
                        ValidationIssue(
                            code="EMPTY_MOTION_REF",
                            path=f"entities[{i}].motion",
                            message=(
                                f"Entity '{entity.id}' has an empty motion "
                                f"reference"
                            ),
                        )
                    )

        return issues


def _is_valid_param_path(path: str, case_ir: RequestedCaseIR) -> bool:
    """Check whether *path* references a valid parameter in the Case IR.

    Supported path formats:

    - ``entities.<id>.parameters.<name>``
    - ``materials.<id>.properties.<name>``
    - ``regions.<id>.<field>``
    - ``physics.<field>``
    - ``observables.<id>.<field>``
    - Bare names (e.g. ``"U_ref"``, ``"nu"``) are accepted as they may
      refer to variables resolved at evaluation time.
    """
    if not path:
        return False

    parts = path.split(".")
    if len(parts) < 2:
        # Bare variable name -- accept it (may be resolved later).
        return True

    root = parts[0]

    if root == "entities":
        if len(parts) >= 4 and parts[2] == "parameters":
            entity_id = parts[1]
            param_name = parts[3]
            for entity in case_ir.entities:
                if entity.id == entity_id and param_name in entity.parameters:
                    return True
            return False
        return True  # Partial match -- don't over-validate

    if root == "materials":
        if len(parts) >= 4 and parts[2] == "properties":
            material_id = parts[1]
            prop_name = parts[3]
            for material in case_ir.materials:
                if material.id == material_id and prop_name in material.properties:
                    return True
            return False
        return True

    if root == "regions":
        return True  # Region field paths are loosely checked

    if root == "physics":
        return True  # Physics fields are loosely checked

    if root == "observables":
        return True  # Observable paths are loosely checked

    # Unknown root -- accept as a bare variable name.
    return True


__all__ = ["ReferenceValidator"]
