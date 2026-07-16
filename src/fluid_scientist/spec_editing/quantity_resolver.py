"""Quantity and expression resolution for the spec-editing module.

The :class:`QuantityResolver` resolves *relative* value expressions
that appear in patch operations.  Instead of the model having to
compute ``delta_t / 2`` itself and risk arithmetic errors, the model
can emit an expression dict like::

    {"operator": "multiply", "path": "/numerics/time/delta_t", "factor": 0.5}

and the resolver will read the current value from the spec, apply the
operation, and return the concrete result.

Supported operators: ``add``, ``subtract``, ``multiply``, ``divide``.

The resolver **never silently falls back** — if an expression cannot
be resolved (missing path, non-numeric current value, missing
operand), it raises :class:`ValueError` so that the patch validator
can surface the error to the user.
"""

from __future__ import annotations

from typing import Any

__all__ = ["QuantityResolver"]


class QuantityResolver:
    """Resolve relative-value expressions against the current spec.

    The resolver works on plain dicts (the ``model_dump()`` output of a
    :class:`SimulationStudySpec`).  It supports two kinds of input:

    1. **Direct values** — if ``value`` is not a dict (or is a dict
       without an ``"operator"`` key), it is returned as-is.
    2. **Expression dicts** — dicts with an ``"operator"`` key.  The
       resolver reads the current value at ``expression["path"]``,
       applies the operator with the given operand, and returns the
       result.

    Expression dict structure::

        {
            "operator": "multiply",   # add | subtract | multiply | divide
            "path": "/numerics/time/delta_t",
            "factor": 0.5,            # for multiply / divide
            "addend": 1.0,           # for add / subtract (also accepts "value")
        }
    """

    #: The set of recognised operators.
    _OPERATORS = frozenset({"add", "subtract", "multiply", "divide"})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        value: Any,
        current_spec: dict[str, Any],
        path: str,
    ) -> Any:
        """Resolve *value* in the context of *current_spec*.

        Parameters
        ----------
        value:
            The value to resolve.  If it is a dict with an ``"operator"``
            key, it is treated as a relative expression.  Otherwise it is
            returned as-is.
        current_spec:
            The current spec as a plain dict (e.g. from
            ``spec.model_dump()``).
        path:
            The JSON Pointer path of the field being resolved.  This is
            used for error messages.

        Returns
        -------
        The resolved value (a concrete number for expressions, or the
        original *value* for non-expression inputs).

        Raises
        ------
        ValueError
            If the expression cannot be resolved.
        """
        if not isinstance(value, dict):
            return value

        if "operator" not in value:
            # A plain dict value (e.g. a Quantity dict) — return as-is.
            return value

        return self._resolve_expression(value, current_spec, path)

    # ------------------------------------------------------------------
    # Internal: expression resolution
    # ------------------------------------------------------------------

    def _resolve_expression(
        self,
        expr: dict[str, Any],
        current_spec: dict[str, Any],
        target_path: str,
    ) -> float:
        """Resolve a single relative-expression dict."""
        operator = expr.get("operator")
        if operator not in self._OPERATORS:
            raise ValueError(
                f"Unknown operator '{operator}' in expression for path "
                f"'{target_path}'. Supported: {sorted(self._OPERATORS)}"
            )

        source_path = expr.get("path")
        if not source_path:
            raise ValueError(
                f"Expression for path '{target_path}' is missing the "
                f"'path' field that identifies the source value."
            )

        current_value = self._read_value_at_path(current_spec, source_path)
        if current_value is None:
            raise ValueError(
                f"Cannot resolve expression for '{target_path}': source "
                f"path '{source_path}' has no value in the current spec."
            )

        numeric_current = self._to_number(current_value, source_path)
        if numeric_current is None:
            raise ValueError(
                f"Cannot resolve expression for '{target_path}': source "
                f"path '{source_path}' contains a non-numeric value "
                f"({current_value!r})."
            )

        if operator in ("multiply", "divide"):
            operand = expr.get("factor", expr.get("value"))
        else:  # add, subtract
            operand = expr.get("addend", expr.get("value"))

        if operand is None:
            raise ValueError(
                f"Expression for path '{target_path}' with operator "
                f"'{operator}' is missing the operand (expected 'factor' "
                f"for multiply/divide or 'addend' for add/subtract)."
            )

        operand_num = self._to_number(operand, target_path)
        if operand_num is None:
            raise ValueError(
                f"Expression operand for path '{target_path}' is "
                f"non-numeric ({operand!r})."
            )

        if operator == "add":
            result = numeric_current + operand_num
        elif operator == "subtract":
            result = numeric_current - operand_num
        elif operator == "multiply":
            result = numeric_current * operand_num
        else:  # divide
            if operand_num == 0:
                raise ValueError(
                    f"Expression for path '{target_path}' attempts to "
                    f"divide by zero."
                )
            result = numeric_current / operand_num

        return result

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_value_at_path(spec: dict[str, Any], json_pointer: str) -> Any:
        """Read the value at a JSON Pointer path from a spec dict.

        Handles the ``-`` sentinel by ignoring it (treats it as
        "last element" — returns the last element of the array if
        present).
        """
        if not json_pointer or json_pointer == "/":
            return spec

        parts = json_pointer.lstrip("/").split("/")
        current: Any = spec
        for part in parts:
            if part == "-":
                if isinstance(current, list) and current:
                    current = current[-1]
                else:
                    return None
                continue
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            elif isinstance(current, list):
                try:
                    idx = int(part)
                except ValueError:
                    return None
                if idx < 0 or idx >= len(current):
                    return None
                current = current[idx]
            else:
                return None
        return current

    @staticmethod
    def _to_number(value: Any, context: str) -> float | None:
        """Coerce *value* to ``float`` if possible, else ``None``.

        Handles:
        - Raw ``int`` / ``float``.
        - ``Quantity`` dicts with a ``"value"`` key containing a number.
        - ``SourcedValue`` dicts with a ``"value"`` key containing a number.
        """
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, dict):
            inner = value.get("value")
            if isinstance(inner, bool):
                return None
            if isinstance(inner, int | float):
                return float(inner)
        return None
