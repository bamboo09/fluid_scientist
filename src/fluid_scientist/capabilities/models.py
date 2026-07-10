"""Unified MissingCapability and CodeExtension data structures.

This module re-exports the canonical model classes from their v5 homes:

- :class:`MissingCapability` lives in :mod:`fluid_scientist.case_plan.models`
- :class:`CodeExtensionSpec` lives in :mod:`fluid_scientist.code_extension.spec`

Legacy constants and helper classes (``StrictModel``, ``CapabilityType``,
``CompilerCapability``, ``CapabilityRegistry``) remain defined here for
backward compatibility.

Both ``MissingCapability`` and ``CodeExtensionSpec`` are imported lazily via
module-level ``__getattr__`` (PEP 562) to avoid circular imports:

* ``code_extension.spec`` imports ``CapabilityRegistry`` from this module.
* ``case_plan.__init__`` (triggered by importing ``case_plan.models``)
  eventually imports ``CapabilityRegistry`` from this module through
  ``study_decomposition.capability_checker``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from fluid_scientist.case_plan.models import MissingCapability as _MissingCapability
    from fluid_scientist.code_extension.spec import CodeExtensionSpec as _CodeExtensionSpec

    MissingCapability = _MissingCapability
    CodeExtensionSpec = _CodeExtensionSpec

# ---------------------------------------------------------------------------
# StrictModel
# ---------------------------------------------------------------------------


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


# ---------------------------------------------------------------------------
# CapabilityType constants (aligned with v5 ExtensionType)
# ---------------------------------------------------------------------------


class CapabilityType:
    """Capability type constants.

    Updated to align with the v5 ``ExtensionType`` literal in
    :mod:`fluid_scientist.code_extension.spec`.  Legacy constant names are
    preserved as aliases so older call sites continue to work.
    """

    # v5-aligned types
    METRIC_OPERATOR = "metric_operator"
    BOUNDARY_CONDITION = "boundary_condition"
    BOUNDARY_CONDITION_WRITER = "boundary_condition_writer"
    SOLVER_EXTENSION = "solver_extension"
    POST_PROCESSOR = "post_processor"
    POSTPROCESS_METRIC = "postprocess_metric"
    MESH_GENERATOR = "mesh_generator"
    MESH_GENERATOR_PLUGIN = "mesh_generator_plugin"
    GEOMETRY_GENERATOR = "geometry_generator"
    GEOMETRY_GENERATOR_PLUGIN = "geometry_generator_plugin"
    ANALYSIS_PLUGIN = "analysis_plugin"
    PYTHON_METRIC = "python_metric"
    CASE_COMPILER_PLUGIN = "case_compiler_plugin"
    INITIAL_CONDITION_GENERATOR = "initial_condition_generator"
    INITIAL_CONDITION_WRITER = "initial_condition_writer"
    PHYSICAL_MODEL_WRITER = "physical_model_writer"
    FUNCTION_OBJECT_WRITER = "function_object_writer"
    PARAMETER_DEFINITION = "parameter_definition"
    OPENFOAM_FUNCTION_OBJECT_WRITER = "openfoam_function_object_writer"
    SOLVER = "solver"


# ---------------------------------------------------------------------------
# CompilerCapability (unchanged)
# ---------------------------------------------------------------------------


class CompilerCapability(StrictModel):
    """A capability declaration for the native case compiler.

    Describes a single compiler capability (solver writer, geometry
    generator, boundary condition writer, etc.) along with the specific
    values it supports, the inputs it requires, the files it produces,
    and any known limitations.  The compiler uses these declarations to
    determine whether a :class:`~fluid_scientist.case_plan.models.CasePlan`
    can be compiled natively.
    """

    capability_id: str
    capability_type: Literal[
        "solver",
        "geometry_generator",
        "mesh_generator",
        "boundary_condition_writer",
        "initial_condition_writer",
        "physical_model_writer",
        "function_object_writer",
        "postprocess_metric",
    ]
    supported_values: list[str] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CapabilityRegistry — updated to work with the unified v5 CodeExtensionSpec
# ---------------------------------------------------------------------------


class CapabilityRegistry:
    """Registry of registered capabilities (code extensions that have been approved)."""

    # States considered "registered/approved" for both legacy and v5 lifecycles.
    _APPROVED_STATES: ClassVar[frozenset[str]] = frozenset(
        {"approved", "conditionally_approved", "registered"}
    )

    def __init__(self) -> None:
        self._capabilities: dict[str, dict[str, Any]] = {}

    def register(self, extension: Any) -> None:
        """Register an approved extension as an available capability.

        Accepts either:
        - The unified v5 :class:`~fluid_scientist.code_extension.spec.CodeExtensionSpec`
        - The legacy ``CodeExtensionSpec`` (duck-typed with attribute access)
        - Any duck-typed object with the expected attributes

        The extension is indexed both by its ``extension_id`` and by its
        ``related_capability_id`` (when present) so that
        :meth:`has_capability` can detect whether a missing capability has
        been satisfied by a registered extension.
        """
        # Resolve status/state: prefer ``status`` (v5), fall back to ``state`` (legacy).
        status = getattr(extension, "status", None) or getattr(
            extension, "state", None
        )
        if status not in self._APPROVED_STATES:
            raise ValueError(
                f"Cannot register extension in state '{status}'"
            )

        # Helper: read an attribute, falling back to alternative names and a default.
        def _attr(obj: Any, *names: str, default: Any = None) -> Any:
            for name in names:
                val = getattr(obj, name, None)
                if val is not None:
                    return val
            return default

        # Map v5 fields to legacy dict keys for storage.
        description = _attr(extension, "description", "requirement", default="")
        extension_name = _attr(extension, "extension_name", default="")
        if not extension_name:
            # v5 fallback: derive from requirement
            req = _attr(extension, "requirement", "description", default="")
            extension_name = (req[:200] if req else "") or _attr(
                extension, "extension_id", default=""
            )

        # For required_inputs / expected_outputs: the v5 model stores
        # list[dict] in ``inputs``/``outputs`` and also has plain
        # list[str] fields ``required_inputs``/``expected_outputs``
        # (legacy).  Prefer the list[str] legacy fields if populated;
        # otherwise derive from the dict lists.
        required_inputs = _attr(extension, "required_inputs", default=None)
        if not required_inputs:
            raw_inputs = _attr(extension, "inputs", default=[]) or []
            required_inputs = [
                i.get("name", str(i)) if isinstance(i, dict) else str(i)
                for i in raw_inputs
            ]

        expected_outputs = _attr(extension, "expected_outputs", default=None)
        if not expected_outputs:
            raw_outputs = _attr(extension, "outputs", default=[]) or []
            expected_outputs = [
                o.get("name", str(o)) if isinstance(o, dict) else str(o)
                for o in raw_outputs
            ]

        related_cap_id = _attr(
            extension, "related_capability_id", "missing_capability_id", default=None
        )

        data = {
            "extension_id": _attr(extension, "extension_id", default=""),
            "extension_name": extension_name,
            "extension_type": _attr(extension, "extension_type", default=""),
            "description": description,
            "required_inputs": required_inputs,
            "expected_outputs": expected_outputs,
            "generated_code": _attr(extension, "generated_code", default=None),
            "related_capability_id": related_cap_id,
        }
        ext_id = data["extension_id"]
        self._capabilities[ext_id] = data
        # Also index by related_capability_id so that resolve() can filter
        # capabilities that have been satisfied by a registered extension.
        if related_cap_id:
            self._capabilities[related_cap_id] = data

    def has_capability(self, capability_id: str) -> bool:
        return capability_id in self._capabilities

    def get_capability(self, capability_id: str) -> dict[str, Any] | None:
        return self._capabilities.get(capability_id)

    def list_capabilities(self) -> list[dict[str, Any]]:
        return list(self._capabilities.values())


# ---------------------------------------------------------------------------
# Lazy re-exports to break circular imports.
#
# code_extension/spec.py imports CapabilityRegistry from this module.
# case_plan/__init__.py eventually imports CapabilityRegistry through
# study_decomposition.capability_checker.  If we imported MissingCapability
# or CodeExtensionSpec at module level we'd create cycles:
#
#   capabilities.models → case_plan.models → case_plan (package __init__)
#     → case_plan.generator → study_decomposition.capability_checker
#     → capabilities.models  (cycle)
#
#   capabilities.models → code_extension.spec → capabilities.models  (cycle)
#
# Instead we use module-level __getattr__ (PEP 562) so that
# ``from fluid_scientist.capabilities.models import MissingCapability``
# and ``from fluid_scientist.capabilities.models import CodeExtensionSpec``
# work but the imports are deferred until the names are actually accessed.
# ---------------------------------------------------------------------------


def __getattr__(name: str) -> Any:
    if name == "CodeExtensionSpec":
        from fluid_scientist.code_extension.spec import CodeExtensionSpec as _CES

        return _CES
    if name == "MissingCapability":
        from fluid_scientist.case_plan.models import MissingCapability as _MC

        return _MC
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CapabilityRegistry",
    "CapabilityType",
    "CodeExtensionSpec",
    "CompilerCapability",
    "MissingCapability",
    "StrictModel",
]
