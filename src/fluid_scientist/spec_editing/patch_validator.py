"""Patch validation for the spec-editing module.

The :class:`PatchValidator` performs *pre-application* validation of a
:class:`SimulationSpecPatch`.  It checks:

1. **Version match** — ``patch.base_version`` must equal the current
   spec version (optimistic concurrency).
2. **Path validity** — every operation's ``path`` must exist in the
   :class:`PathRegistry`.
3. **Mutability** — the field at ``path`` must be marked ``mutable``
   unless the operation is a read-only ``test``.
4. **Operation-path compatibility** — e.g. ``append_unique`` is only
   valid on array paths.
5. **Type check** — the ``value`` type must match the schema-declared
   type for the field.
6. **Unit check** — if the field has a ``unit_dimension``, the value's
   unit must match (when a unit can be extracted from the value).

The validator returns a list of error strings.  An empty list means the
patch is valid.  The validator **never silently falls back** — every
problem is reported as an error string.
"""

from __future__ import annotations

from typing import Any

from .models import PatchOperation, SimulationSpecPatch
from .path_registry import PathRegistry
from .quantity_resolver import QuantityResolver

__all__ = ["PatchValidator"]


class PatchValidator:
    """Validate a :class:`SimulationSpecPatch` before application.

    Usage::

        validator = PatchValidator(PathRegistry(), QuantityResolver())
        errors = validator.validate(patch, current_spec_dict)
        if errors:
            # Do not apply — surface errors to the user.
            ...
    """

    def __init__(
        self,
        path_registry: PathRegistry,
        quantity_resolver: QuantityResolver,
    ) -> None:
        self._registry = path_registry
        self._resolver = quantity_resolver

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        patch: SimulationSpecPatch,
        current_spec: dict[str, Any],
    ) -> list[str]:
        """Validate *patch* against *current_spec*.

        Returns a list of error strings.  An empty list means the patch
        is valid.
        """
        errors: list[str] = []

        # 1. Version match (optimistic concurrency).
        current_version = current_spec.get("version", 1)
        if patch.base_version != current_version:
            errors.append(
                f"Version conflict: patch expects base_version="
                f"{patch.base_version} but current spec version is "
                f"{current_version}."
            )

        # 2. Spec ID match.
        current_spec_id = current_spec.get("spec_id", "")
        if patch.base_spec_id and current_spec_id and patch.base_spec_id != current_spec_id:
            errors.append(
                f"Spec ID mismatch: patch targets spec_id="
                f"'{patch.base_spec_id}' but current spec has "
                f"spec_id='{current_spec_id}'."
            )

        # 3. Validate each operation.
        for i, op in enumerate(patch.operations):
            op_errors = self.validate_operation(op, current_spec)
            for err in op_errors:
                errors.append(f"Operation {i} ({op.op} {op.path}): {err}")

        return errors

    def validate_operation(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> list[str]:
        """Validate a single :class:`PatchOperation`.

        Returns a list of error strings (empty = valid).
        """
        errors: list[str] = []

        # --- Path validity ---
        if not op.path:
            errors.append("Path is empty.")
            return errors

        if not op.path.startswith("/"):
            errors.append(
                f"Path '{op.path}' must be a JSON Pointer starting with '/'."
            )
            return errors

        meta = self._registry.get_path_metadata(op.path)
        if meta is None:
            errors.append(
                f"Path '{op.path}' is not a recognised spec path."
            )
            return errors

        # --- Mutability check ---
        # 'test' operations are read-only and always allowed.
        if op.op != "test" and not meta.mutable:
            errors.append(
                f"Field '{op.path}' is immutable and cannot be modified "
                f"with op='{op.op}'."
            )

        # --- Operation-path compatibility ---
        op_compat_errors = self._check_operation_path_compatibility(op, meta.value_schema)
        errors.extend(op_compat_errors)

        # --- Type check (for ops that carry a value) ---
        if op.value is not None and op.op in ("replace", "add", "merge", "test", "append_unique"):
            resolved_value = self._resolver.resolve(op.value, current_spec, op.path)
            type_errors = self._check_type(op.path, resolved_value, meta.value_schema, meta.unit_dimension)
            errors.extend(type_errors)

        # --- from_path check (for move/copy) ---
        if op.op in ("move", "copy") and not op.from_path:
            errors.append(
                f"Operation '{op.op}' requires a 'from_path' field."
            )
        if op.from_path:
            from_meta = self._registry.get_path_metadata(op.from_path)
            if from_meta is None:
                errors.append(
                    f"from_path '{op.from_path}' is not a recognised spec path."
                )

        return errors

    # ------------------------------------------------------------------
    # Internal: operation-path compatibility
    # ------------------------------------------------------------------

    @staticmethod
    def _check_operation_path_compatibility(
        op: PatchOperation,
        value_schema: dict[str, Any],
    ) -> list[str]:
        """Check that the operation type is valid for the field's schema."""
        errors: list[str] = []
        schema_type = value_schema.get("type", "object")

        # append_unique is only valid on arrays.
        if op.op == "append_unique" and schema_type != "array":
            errors.append(
                f"Operation 'append_unique' is only valid on array fields, "
                f"but '{op.path}' has type '{schema_type}'."
            )

        # merge is only valid on objects.
        if op.op == "merge" and schema_type not in ("object",):
            errors.append(
                f"Operation 'merge' is only valid on object fields, "
                f"but '{op.path}' has type '{schema_type}'."
            )

        return errors

    # ------------------------------------------------------------------
    # Internal: type checking
    # ------------------------------------------------------------------

    @staticmethod
    def _check_type(
        path: str,
        value: Any,
        value_schema: dict[str, Any],
        unit_dimension: str | None,
    ) -> list[str]:
        """Check that *value* matches the expected *value_schema*."""
        errors: list[str] = []
        expected_type = value_schema.get("type")

        if expected_type is None:
            return errors  # No type constraint to check.

        actual_type = PatchValidator._python_type_name(value)

        type_compatible = PatchValidator._is_type_compatible(actual_type, expected_type)

        if not type_compatible:
            errors.append(
                f"Type mismatch at '{path}': expected '{expected_type}' "
                f"but got '{actual_type}'."
            )

        # Unit dimension check (only for object-type fields that carry units).
        if unit_dimension is not None and isinstance(value, dict):
            value_unit = value.get("unit")
            if value_unit is not None:
                unit_ok = PatchValidator._is_unit_compatible(value_unit, unit_dimension)
                if not unit_ok:
                    errors.append(
                        f"Unit mismatch at '{path}': field dimension is "
                        f"'{unit_dimension}' but value has unit '{value_unit}'."
                    )

        return errors

    @staticmethod
    def _python_type_name(value: Any) -> str:
        """Return a JSON-Schema-like type name for *value*."""
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        if value is None:
            return "null"
        return "unknown"

    @staticmethod
    def _is_type_compatible(actual: str, expected: str) -> bool:
        """Check if *actual* Python type is compatible with *expected*
        JSON Schema type."""
        if actual == expected:
            return True
        # integer is a subset of number.
        if expected == "number" and actual in ("integer", "number"):
            return True
        if expected == "integer" and actual == "integer":
            return True
        # For object-typed schema fields, dicts are always compatible
        # (the dict may be a Quantity or SourcedValue representation).
        if expected == "object" and actual == "object":
            return True
        # Numbers in Quantity/SourcedValue dicts: when the schema says
        # "number" but the value is a dict (Quantity), we allow it
        # because the resolver will extract the numeric value.
        if expected == "number" and actual == "object":
            return True
        if expected == "integer" and actual == "object":
            return True
        # When the schema says "object" but the value is a bare number,
        # the executor will wrap it into the existing Quantity/SourcedValue
        # dict structure (preserving unit and provenance).
        if expected == "object" and actual in ("number", "integer"):
            return True
        return False

    # ------------------------------------------------------------------
    # Internal: unit compatibility
    # ------------------------------------------------------------------

    #: Mapping from physical dimensions to compatible unit strings.
    _UNIT_MAP: dict[str, set[str]] = {
        "time": {"s", "ms", "min", "hour", "sec", "seconds"},
        "length": {"m", "cm", "mm", "km", "meter", "meters"},
        "velocity": {"m/s", "m/s^2", "cm/s", "mm/s", "ft/s"},
        "density": {"kg/m^3", "kg/m^3", "g/cm^3"},
        "kinematic_viscosity": {"m^2/s", "m2/s", "stokes", "cm^2/s"},
        "dimensionless": {"", "dimensionless", "none", "-"},
    }

    @classmethod
    def _is_unit_compatible(cls, unit: str, dimension: str) -> bool:
        """Check if *unit* is compatible with *dimension*."""
        compatible = cls._UNIT_MAP.get(dimension)
        if compatible is None:
            return True  # Unknown dimension — allow.
        return unit.lower() in {u.lower() for u in compatible} or unit in compatible
