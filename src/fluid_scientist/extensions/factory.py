"""ExtensionSpecFactory — translates a CapabilityResolutionPlan into concrete
extension specs.

The factory inspects each :class:`RequirementClassificationResult` produced by
:class:`~fluid_scientist.capabilities.gap_analyzer.CapabilityGapAnalyzer` and,
depending on the classification, emits one of three typed specs:

* ``EXTENDABLE`` with ``extension_type == "config"`` -> :class:`ConfigExtensionSpec`
* ``EXTENDABLE`` with ``extension_type == "code"``   -> :class:`CodeExtensionSpec`
* ``REQUIRES_NEW_PHYSICS``                           -> :class:`PhysicsExtensionSpec`

Requirements that are already supported, blocked by the environment, or need
clarification are intentionally **not** turned into extension specs — there is
nothing to generate for them.  The factory never fabricates specs for
requirements it cannot concretely address.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.capabilities.gap_analyzer import (
    CapabilityResolutionPlan,
    RequirementClassificationResult,
)
from fluid_scientist.capabilities.registry import (
    CapabilityRegistry,
    CapabilityRequirement,
)
from fluid_scientist.extensions.code_spec import CodeExtensionSpec, TestSpec
from fluid_scientist.extensions.config_spec import ConfigExtensionSpec
from fluid_scientist.extensions.physics_spec import (
    ConservationCheck,
    PhysicsExtensionSpec,
)

# Capability types whose extension is low-risk (pure dictionary work) and can
# therefore be validated statically.  Everything else routed through the
# ``EXTENDABLE`` path defaults to a code extension that needs runtime testing.
_STATIC_VALIDATION_TYPES: frozenset[str] = frozenset(
    {
        "parameter_definition",
        "openfoam_function_object_writer",
    }
)

# Map of physics-flavour keywords (matched against the scientific reason /
# description) to the Foundation 13 solver module, physical scope and the new
# constant / field files the physics extension must introduce.
_PHYSICS_FLAVOURS: tuple[dict[str, Any], ...] = (
    {
        "keywords": ("heat", "thermal", "temperature", "energy", "conjugate"),
        "physical_scope": "thermal_fluid",
        "solver_module": "isothermalFluid",
        "governing_equations": ["energy_transport"],
        "new_constant_files": ["constant/thermophysicalProperties"],
        "new_field_files": ["0/T"],
    },
    {
        "keywords": ("multiphase", "vof", "phase", "interface"),
        "physical_scope": "multiphase",
        "solver_module": "multiphaseEulerFoam",
        "governing_equations": ["volume_fraction_transport"],
        "new_constant_files": ["constant/physicalProperties"],
        "new_field_files": ["0/alpha"],
    },
    {
        "keywords": ("porous", "darcy", "permeability"),
        "physical_scope": "porous_media",
        "solver_module": "porousSimpleFoam",
        "governing_equations": ["darcy_forchheimer_momentum_source"],
        "new_constant_files": ["constant/porosityProperties"],
        "new_field_files": [],
    },
    {
        "keywords": ("compressible", "density", "mach", "shock"),
        "physical_scope": "compressible_fluid",
        "solver_module": "fluid",
        "governing_equations": ["compressible_navier_stokes"],
        "new_constant_files": ["constant/thermophysicalProperties"],
        "new_field_files": ["0/T"],
    },
)

# Default conservation checks attached to every physics extension.
_DEFAULT_CONSERVATION_CHECKS: tuple[ConservationCheck, ...] = (
    ConservationCheck(
        check_id="mass_conservation",
        quantity="mass",
        method="flux_balance",
        tolerance=1e-3,
    ),
    ConservationCheck(
        check_id="momentum_conservation",
        quantity="momentum",
        method="integral",
        tolerance=1e-2,
    ),
)

# A union alias for the three spec types the factory can emit.
ExtensionSpecUnion = ConfigExtensionSpec | CodeExtensionSpec | PhysicsExtensionSpec


class ExtensionSpecFactory:
    """Build extension specs from a :class:`CapabilityResolutionPlan`.

    Args:
        registry: Optional :class:`CapabilityRegistry`.  When supplied it is
            consulted to enrich the generated specs with information about the
            closest existing capability (used for dependency / conflict hints).
    """

    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._registry = registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_specs(self, plan: CapabilityResolutionPlan) -> list[ExtensionSpecUnion]:
        """Return one extension spec for every actionable requirement in *plan*.

        Actionable means the requirement is classified as ``EXTENDABLE`` or
        ``REQUIRES_NEW_PHYSICS``.  Optional requirements are included so the
        caller can decide whether to pursue them; the ``mandatory`` flag is
        preserved on the underlying requirement for downstream filtering.
        """
        specs: list[ExtensionSpecUnion] = []
        for result in plan.results:
            spec = self._build_for_result(result)
            if spec is not None:
                specs.append(spec)
        return specs

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _build_for_result(
        self, result: RequirementClassificationResult
    ) -> ExtensionSpecUnion | None:
        classification = result.classification
        if classification == "EXTENDABLE":
            if result.extension_type == "config":
                return self._build_config_spec(result)
            return self._build_code_spec(result)
        if classification == "REQUIRES_NEW_PHYSICS":
            return self._build_physics_spec(result)
        # EXACT_SUPPORTED, COMPOSABLE_SUPPORTED, NEEDS_CLARIFICATION, and
        # ENVIRONMENT_BLOCKED produce no extension spec.
        return None

    # ------------------------------------------------------------------
    # ConfigExtensionSpec
    # ------------------------------------------------------------------

    def _build_config_spec(self, result: RequirementClassificationResult) -> ConfigExtensionSpec:
        req = result.requirement
        semantic_role = self._derive_semantic_role(req)
        validation_method = (
            "static" if req.capability_type in _STATIC_VALIDATION_TYPES else "smoke_test"
        )
        return ConfigExtensionSpec(
            spec_id=f"cfgext-{req.requirement_id}",
            description=(
                req.description
                or req.scientific_reason
                or result.reason
                or f"Config extension for {req.capability_type}"
            ),
            target_capability_type=req.capability_type,
            semantic_role=semantic_role,
            parameter_schema=self._merge_schemas(req),
            foundation13_mapping=self._stringify_mapping(req.openfoam_mapping),
            dependencies=self._derive_dependencies(req),
            conflicts=self._derive_conflicts(req),
            validation_method=validation_method,
            fallback_behavior=self._derive_fallback_behavior(req),
        )

    # ------------------------------------------------------------------
    # CodeExtensionSpec
    # ------------------------------------------------------------------

    def _build_code_spec(self, result: RequirementClassificationResult) -> CodeExtensionSpec:
        req = result.requirement
        entrypoint = self._suggest_entrypoint(req)
        inputs = self._derive_inputs(req)
        outputs = self._derive_outputs(req)
        unit_tests = self._default_unit_tests(req, inputs, outputs)
        return CodeExtensionSpec(
            spec_id=f"codext-{req.requirement_id}",
            description=(
                req.description
                or req.scientific_reason
                or result.reason
                or f"Code extension for {req.capability_type}"
            ),
            target_capability_type=req.capability_type,
            language="python",
            inputs=inputs,
            outputs=outputs,
            dependencies=self._derive_dependencies(req),
            security_constraints=list(DEFAULT_SECURITY_CONSTRAINTS),
            fallback_behavior=self._derive_fallback_behavior(req),
            unit_tests=unit_tests,
            benchmark_tests=[],
            target_case_tests=[],
            implementation_code="",  # filled in by the orchestrator's generator
            implementation_entrypoint=entrypoint,
        )

    # ------------------------------------------------------------------
    # PhysicsExtensionSpec
    # ------------------------------------------------------------------

    def _build_physics_spec(self, result: RequirementClassificationResult) -> PhysicsExtensionSpec:
        req = result.requirement
        flavour = self._detect_physics_flavour(req)
        required_fields = flavour.get("new_field_files", []) or ["U", "p"]
        # Normalise field file paths to bare field names where possible.
        normalised_fields: list[str] = []
        for field_file in required_fields:
            name = field_file.rsplit("/", 1)[-1]
            if name and name not in normalised_fields:
                normalised_fields.append(name)
        for base_field in ("U", "p"):
            if base_field not in normalised_fields:
                normalised_fields.append(base_field)

        return PhysicsExtensionSpec(
            spec_id=f"physext-{req.requirement_id}",
            description=(
                req.description
                or req.scientific_reason
                or result.reason
                or f"Physics extension for {req.capability_type}"
            ),
            physical_scope=flavour["physical_scope"],
            governing_equations=list(flavour.get("governing_equations", [])),
            required_fields=normalised_fields,
            solver_module=flavour["solver_module"],
            boundary_requirements=self._derive_dependencies(req),
            validation_benchmark=req.capability_id or "",
            conservation_checks=list(_DEFAULT_CONSERVATION_CHECKS),
            applicability_limits=self._derive_applicability_limits(req),
            required_base_pack="foundation13-incompressible-laminar-transient",
            new_constant_files=list(flavour.get("new_constant_files", [])),
            new_field_files=list(flavour.get("new_field_files", [])),
        )

    # ------------------------------------------------------------------
    # Shared derivation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_semantic_role(req: CapabilityRequirement) -> str:
        if req.capability_id:
            return req.capability_id
        if req.keywords:
            return "_".join(req.keywords[:3])
        return f"{req.capability_type}_{req.requirement_id}"

    @staticmethod
    def _merge_schemas(req: CapabilityRequirement) -> dict[str, Any]:
        schema: dict[str, Any] = {}
        schema.update(req.required_input or {})
        schema.update(req.input_contract or {})
        schema.update(req.expected_output or {})
        schema.update(req.output_contract or {})
        return schema

    @staticmethod
    def _stringify_mapping(mapping: dict[str, Any]) -> dict[str, str]:
        return {str(k): str(v) for k, v in mapping.items()}

    def _derive_dependencies(self, req: CapabilityRequirement) -> list[str]:
        deps: list[str] = []
        deps.extend(req.required_by)
        for fb in req.fallback_options:
            if isinstance(fb, dict):
                label = fb.get("capability_id") or fb.get("name")
                if label and label not in deps:
                    deps.append(str(label))
        if req.capability_id:
            deps.append(req.capability_id)
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for dep in deps:
            if dep not in seen:
                seen.add(dep)
                unique.append(dep)
        return unique

    def _derive_conflicts(self, req: CapabilityRequirement) -> list[str]:
        conflicts: list[str] = []
        for fb in req.fallback_options:
            if isinstance(fb, dict) and fb.get("conflicts"):
                conflicts.append(str(fb["conflicts"]))
        return conflicts

    @staticmethod
    def _derive_fallback_behavior(req: CapabilityRequirement) -> str:
        if req.fallback_options:
            return "use_default"
        return "reject"

    @staticmethod
    def _derive_inputs(req: CapabilityRequirement) -> list[str]:
        keys = list((req.required_input or {}).keys())
        keys.extend((req.input_contract or {}).keys())
        # Deduplicate preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                unique.append(key)
        return unique

    @staticmethod
    def _derive_outputs(req: CapabilityRequirement) -> list[str]:
        keys = list((req.expected_output or {}).keys())
        keys.extend((req.output_contract or {}).keys())
        seen: set[str] = set()
        unique: list[str] = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                unique.append(key)
        return unique

    @staticmethod
    def _suggest_entrypoint(req: CapabilityRequirement) -> str:
        if req.capability_id:
            base = req.capability_id.replace(".", "_").replace("-", "_")
        elif req.keywords:
            base = "_".join(req.keywords[:2])
        else:
            base = req.capability_type or "extension"
        return f"{base}_entrypoint"

    @staticmethod
    def _default_unit_tests(
        req: CapabilityRequirement,
        inputs: list[str],
        outputs: list[str],
    ) -> list[TestSpec]:
        return [
            TestSpec(
                test_id=f"contract-{req.requirement_id}",
                test_type="unit",
                description=(
                    "Verify the generated entrypoint accepts the declared "
                    "input contract and returns the declared output contract."
                ),
                input_data={key: None for key in inputs},
                expected_output={key: None for key in outputs},
                tolerance=0.0,
            ),
        ]

    @staticmethod
    def _derive_applicability_limits(req: CapabilityRequirement) -> dict[str, Any]:
        limits: dict[str, Any] = {}
        for key in ("Re", "Mach", "Pr", "Ra"):
            if req.required_input and key in req.required_input:
                limits[key] = req.required_input[key]
        if req.openfoam_mapping:
            limits["openfoam_mapping"] = req.openfoam_mapping
        return limits

    @staticmethod
    def _detect_physics_flavour(req: CapabilityRequirement) -> dict[str, Any]:
        haystack = " ".join(
            part for part in (req.description, req.scientific_reason, *req.keywords) if part
        ).lower()
        for flavour in _PHYSICS_FLAVOURS:
            if any(kw in haystack for kw in flavour["keywords"]):
                return flavour
        # Default: isothermal / incompressible fluid (the Foundation 13 base).
        return {
            "physical_scope": "isothermal_fluid",
            "solver_module": "incompressibleFluid",
            "governing_equations": ["incompressible_navier_stokes"],
            "new_constant_files": [],
            "new_field_files": [],
        }


DEFAULT_SECURITY_CONSTRAINTS: tuple[str, ...] = (
    "no_subprocess",
    "no_filesystem_write_outside_workspace",
    "no_network_access",
    "no_dynamic_import",
    "no_codeStream",
)


__all__ = [
    "DEFAULT_SECURITY_CONSTRAINTS",
    "ExtensionSpecFactory",
    "ExtensionSpecUnion",
]
