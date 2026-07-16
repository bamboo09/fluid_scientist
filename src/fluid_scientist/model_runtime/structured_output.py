"""Structured (JSON schema) output validation.

Provides :class:`StructuredOutputValidator`, which validates parsed model
output against a JSON schema and parses raw model responses into typed
dicts.  On validation failure the validator returns an explicit
:class:`~fluid_scientist.model_runtime.errors.ModelInvocationError`
(code ``MODEL_OUTPUT_INVALID`` for unparseable responses,
``MODEL_SCHEMA_MISMATCH`` for schema-conformant failures).

The validator **never** silently coerces missing fields to defaults or
falls back to regex/template extraction: a response that does not parse
and conform is a hard failure.
"""
from __future__ import annotations

import json
from typing import Any

from fluid_scientist.model_runtime.errors import ModelInvocationError

__all__ = ["StructuredOutputValidator"]


class StructuredOutputValidator:
    """Validate model output against a JSON schema.

    The validator implements a pragmatic subset of JSON Schema (the
    keywords used by the platform's prompts): ``type``, ``required``,
    ``properties``, ``additionalProperties``, ``enum``, ``items``,
    ``minimum``/``maximum``, ``minLength``/``maxLength`` and
    ``minItems``/``maxItems``.  If the optional :mod:`jsonschema` package
    is installed it is used for full-spec validation; otherwise the
    built-in subset validator is used so the module has no hard external
    dependency.
    """

    def validate(self, output: dict, schema: dict) -> bool:
        """Return ``True`` iff *output* conforms to *schema*."""
        if not isinstance(output, dict) or not isinstance(schema, dict):
            return False
        try:
            import jsonschema  # type: ignore[import-not-found]
        except ImportError:
            return self._check(output, schema)
        try:
            jsonschema.validate(instance=output, schema=schema)
        except Exception:  # noqa: BLE001 - any validation error is a failure
            return False
        return True

    def parse(
        self,
        raw_response: str,
        schema: dict,
    ) -> tuple[dict | None, ModelInvocationError | None]:
        """Parse *raw_response* and validate it against *schema*.

        Returns ``(parsed_dict, None)`` on success, or
        ``(None, error)`` on failure.  Failures are *never* swallowed:
        the caller receives an explicit
        :class:`ModelInvocationError` describing the problem.
        """
        if not isinstance(raw_response, str) or not raw_response.strip():
            return None, ModelInvocationError(
                code="MODEL_OUTPUT_INVALID",
                provider="unknown",
                configured_model="unknown",
                message="model returned an empty response",
            )

        text = self._strip_code_fence(raw_response.strip())
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, ModelInvocationError(
                code="MODEL_OUTPUT_INVALID",
                provider="unknown",
                configured_model="unknown",
                message=f"model response is not valid JSON: {exc.msg}",
            )

        if not isinstance(parsed, dict):
            return None, ModelInvocationError(
                code="MODEL_SCHEMA_MISMATCH",
                provider="unknown",
                configured_model="unknown",
                message="model JSON root must be an object",
            )

        if not self.validate(parsed, schema):
            return None, ModelInvocationError(
                code="MODEL_SCHEMA_MISMATCH",
                provider="unknown",
                configured_model="unknown",
                message="model output does not conform to the expected schema",
            )

        return parsed, None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """Remove a surrounding ```...``` code fence if present."""
        if text.startswith("```"):
            # Drop the opening fence (and an optional language tag).
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1 :]
            else:
                text = text[3:]
            if text.endswith("```"):
                text = text[: -3]
        return text.strip()

    # ------------------------------------------------------------------
    # Built-in JSON-schema subset validator
    # ------------------------------------------------------------------
    def _check(self, instance: Any, schema: dict) -> bool:
        if not isinstance(schema, dict):
            return True  # nothing to check

        if "type" in schema and not self._check_type(instance, schema["type"]):
            return False

        if "enum" in schema and instance not in schema["enum"]:
            return False

        # Object-level constraints.
        if isinstance(instance, dict):
            required = schema.get("required", [])
            if isinstance(required, list):
                for key in required:
                    if key not in instance:
                        return False
            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                for key, value in instance.items():
                    if key in properties:
                        if not self._check(value, properties[key]):
                            return False
                    else:
                        additional = schema.get("additionalProperties", True)
                        if additional is False:
                            return False
                        if isinstance(additional, dict):
                            if not self._check(value, additional):
                                return False

        # Array-level constraints.
        if isinstance(instance, list):
            min_items = schema.get("minItems")
            if isinstance(min_items, int) and len(instance) < min_items:
                return False
            max_items = schema.get("maxItems")
            if isinstance(max_items, int) and len(instance) > max_items:
                return False
            items = schema.get("items")
            if isinstance(items, dict):
                for element in instance:
                    if not self._check(element, items):
                        return False

        # String-level constraints.
        if isinstance(instance, str):
            min_len = schema.get("minLength")
            if isinstance(min_len, int) and len(instance) < min_len:
                return False
            max_len = schema.get("maxLength")
            if isinstance(max_len, int) and len(instance) > max_len:
                return False

        # Numeric-level constraints (booleans excluded).
        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            minimum = schema.get("minimum")
            if isinstance(minimum, (int, float)) and instance < minimum:
                return False
            maximum = schema.get("maximum")
            if isinstance(maximum, (int, float)) and instance > maximum:
                return False

        return True

    @staticmethod
    def _check_type(instance: Any, typ: Any) -> bool:
        if isinstance(typ, list):
            return any(StructuredOutputValidator._check_type(instance, t) for t in typ)
        if typ == "object":
            return isinstance(instance, dict)
        if typ == "array":
            return isinstance(instance, list)
        if typ == "string":
            return isinstance(instance, str)
        if typ == "integer":
            return isinstance(instance, int) and not isinstance(instance, bool)
        if typ == "number":
            return isinstance(instance, (int, float)) and not isinstance(instance, bool)
        if typ == "boolean":
            return isinstance(instance, bool)
        if typ == "null":
            return instance is None
        # Unknown type keyword -> do not fail on type grounds.
        return True
