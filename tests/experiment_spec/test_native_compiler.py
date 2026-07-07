"""Tests for the native ExperimentSpec compiler registry and compilers."""

from __future__ import annotations

import warnings
from unittest.mock import patch

import pytest

from fluid_scientist.experiment_planning.compilers import CompiledCase
from fluid_scientist.experiment_spec.compilation import (
    COMPILER_ID,
    CompilationManifest,
    SpecNotConfirmedError,
    compile_confirmed_spec,
    compile_spec,
)
from fluid_scientist.experiment_spec.models import (
    Criticality,
    ExperimentSpec,
    ExperimentStatus,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)
from fluid_scientist.experiment_spec.native_compiler import (
    CavityFlowCompiler,
    CompilerRegistry,
    CylinderFlowCompiler,
    ExperimentCompiler,
    PipeFlowCompiler,
    compile_spec_native,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _param(
    pid: str,
    value,
    *,
    data_type: str = "float",
    criticality: str = "medium",
) -> ParameterSpec:
    return ParameterSpec(
        parameter_id=pid,
        display_name=pid,
        category="test",
        value=value,
        data_type=data_type,
        source=ParameterSourceInfo(type=ParameterSource.TEMPLATE_DEFAULT),
        criticality=Criticality(criticality),
    )


def _make_confirmed_pipe_spec(
    status: ExperimentStatus = ExperimentStatus.CONFIRMED,
) -> ExperimentSpec:
    """A confirmed pipe experiment spec with valid laminar parameters."""
    return ExperimentSpec(
        experiment_id="pipe-native-001",
        status=status,
        research=ResearchSpec(title="Pipe Test", objective="Test pipe flow"),
        parameters=[
            _param("diameter", 0.05, criticality="critical"),
            _param("length", 1.0),
            _param("mean_velocity", 0.02, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("reynolds_number", 1000.0, criticality="critical"),
            _param("axial_cells", 80, data_type="integer"),
            _param("radial_cells", 10, data_type="integer"),
        ],
    )


def _make_confirmed_cylinder_spec(
    status: ExperimentStatus = ExperimentStatus.CONFIRMED,
) -> ExperimentSpec:
    """A confirmed cylinder experiment spec with valid low-Re parameters."""
    return ExperimentSpec(
        experiment_id="cyl-native-001",
        status=status,
        research=ResearchSpec(title="Cylinder Test", objective="Test cylinder flow"),
        parameters=[
            _param("reynolds_number", 100.0, criticality="critical"),
            _param("diameter", 0.1, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("cells_radial", 40, data_type="integer"),
            _param("cells_wake", 120, data_type="integer"),
            _param("end_time", 10.0),
            _param("max_courant", 0.5),
        ],
    )


def _make_confirmed_cavity_spec(
    status: ExperimentStatus = ExperimentStatus.CONFIRMED,
) -> ExperimentSpec:
    """A confirmed cavity experiment spec."""
    return ExperimentSpec(
        experiment_id="cav-native-001",
        status=status,
        research=ResearchSpec(title="Cavity Test", objective="Test cavity flow"),
        parameters=[
            _param("side_length", 0.1, criticality="critical"),
            _param("lid_velocity", 1.0, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("cells_per_side", 64, data_type="integer"),
            _param("end_time", 10.0),
        ],
    )


def _make_unknown_spec(
    status: ExperimentStatus = ExperimentStatus.CONFIRMED,
) -> ExperimentSpec:
    """A confirmed spec that no native compiler can handle."""
    return ExperimentSpec(
        experiment_id="unknown-native-001",
        status=status,
        research=ResearchSpec(title="Unknown", objective="Test unknown"),
        parameters=[
            _param("foo", 1.0),
            _param("bar", 2.0),
        ],
    )


# ---------------------------------------------------------------------------
# 1-2. PipeFlowCompiler.can_compile
# ---------------------------------------------------------------------------


class TestPipeFlowCompilerCanCompile:
    def test_can_compile_returns_true_for_pipe_spec(self):
        """PipeFlowCompiler.can_compile() returns True for specs with
        'length' and 'axial_cells' parameters."""
        spec = _make_confirmed_pipe_spec()
        compiler = PipeFlowCompiler()
        assert compiler.can_compile(spec) is True

    def test_can_compile_returns_false_for_non_pipe_spec(self):
        """PipeFlowCompiler.can_compile() returns False for specs without
        'length' and 'axial_cells' parameters."""
        spec = _make_confirmed_cavity_spec()
        compiler = PipeFlowCompiler()
        assert compiler.can_compile(spec) is False


# ---------------------------------------------------------------------------
# 3. PipeFlowCompiler.compile
# ---------------------------------------------------------------------------


class TestPipeFlowCompilerCompile:
    def test_compile_returns_compiled_case_with_correct_type(self):
        """PipeFlowCompiler.compile() returns a CompiledCase with
        experiment_type == 'laminar_pipe'."""
        spec = _make_confirmed_pipe_spec()
        compiler = PipeFlowCompiler()
        compiled = compiler.compile(spec)
        assert isinstance(compiled, CompiledCase)
        assert compiled.experiment_type == "laminar_pipe"
        assert len(compiled.archive) > 0
        assert compiled.archive_sha256.startswith("sha256:")


# ---------------------------------------------------------------------------
# 4. CylinderFlowCompiler.can_compile
# ---------------------------------------------------------------------------


class TestCylinderFlowCompilerCanCompile:
    def test_can_compile_returns_true_for_cylinder_spec(self):
        """CylinderFlowCompiler.can_compile() returns True for specs with
        'cells_wake' and 'reynolds_number'."""
        spec = _make_confirmed_cylinder_spec()
        compiler = CylinderFlowCompiler()
        assert compiler.can_compile(spec) is True

    def test_can_compile_returns_false_for_non_cylinder_spec(self):
        spec = _make_confirmed_pipe_spec()
        compiler = CylinderFlowCompiler()
        assert compiler.can_compile(spec) is False


# ---------------------------------------------------------------------------
# 5. CavityFlowCompiler.can_compile
# ---------------------------------------------------------------------------


class TestCavityFlowCompilerCanCompile:
    def test_can_compile_returns_true_for_cavity_spec(self):
        """CavityFlowCompiler.can_compile() returns True for specs with
        'side_length' and 'lid_velocity'."""
        spec = _make_confirmed_cavity_spec()
        compiler = CavityFlowCompiler()
        assert compiler.can_compile(spec) is True

    def test_can_compile_returns_false_for_non_cavity_spec(self):
        spec = _make_confirmed_pipe_spec()
        compiler = CavityFlowCompiler()
        assert compiler.can_compile(spec) is False


# ---------------------------------------------------------------------------
# 6-7. CompilerRegistry.resolve
# ---------------------------------------------------------------------------


class TestCompilerRegistryResolve:
    def test_resolve_returns_pipe_compiler_for_pipe_spec(self):
        """CompilerRegistry.resolve() returns the correct compiler for a
        pipe spec."""
        spec = _make_confirmed_pipe_spec()
        registry = CompilerRegistry()
        compiler = registry.resolve(spec)
        assert compiler is not None
        assert isinstance(compiler, PipeFlowCompiler)

    def test_resolve_returns_cylinder_compiler_for_cylinder_spec(self):
        spec = _make_confirmed_cylinder_spec()
        registry = CompilerRegistry()
        compiler = registry.resolve(spec)
        assert compiler is not None
        assert isinstance(compiler, CylinderFlowCompiler)

    def test_resolve_returns_cavity_compiler_for_cavity_spec(self):
        spec = _make_confirmed_cavity_spec()
        registry = CompilerRegistry()
        compiler = registry.resolve(spec)
        assert compiler is not None
        assert isinstance(compiler, CavityFlowCompiler)

    def test_resolve_returns_none_for_unknown_spec(self):
        """CompilerRegistry.resolve() returns None for an unknown spec type."""
        spec = _make_unknown_spec()
        registry = CompilerRegistry()
        assert registry.resolve(spec) is None


# ---------------------------------------------------------------------------
# 8. CompilerRegistry.available_compilers
# ---------------------------------------------------------------------------


class TestCompilerRegistryAvailableCompilers:
    def test_available_compilers_returns_all_three_ids(self):
        """CompilerRegistry.available_compilers() returns all three
        compiler IDs."""
        registry = CompilerRegistry()
        ids = registry.available_compilers()
        assert "fluid_scientist.native.pipe_flow" in ids
        assert "fluid_scientist.native.cylinder_flow" in ids
        assert "fluid_scientist.native.cavity_flow" in ids
        assert len(ids) == 3


# ---------------------------------------------------------------------------
# 9. compile_spec_native returns (CompiledCase, CompilationManifest)
# ---------------------------------------------------------------------------


class TestCompileSpecNativeReturns:
    def test_returns_tuple_for_confirmed_pipe_spec(self):
        """compile_spec_native() returns (CompiledCase, CompilationManifest)
        for a confirmed pipe spec."""
        spec = _make_confirmed_pipe_spec()
        compiled, manifest = compile_spec_native(spec)
        assert isinstance(compiled, CompiledCase)
        assert isinstance(manifest, CompilationManifest)
        assert compiled.experiment_type == "laminar_pipe"
        assert manifest.experiment_id == spec.experiment_id
        assert manifest.experiment_version == spec.experiment_version
        assert len(manifest.spec_hash) == 16
        assert len(manifest.case_hash) == 16

    def test_returns_tuple_for_confirmed_cylinder_spec(self):
        spec = _make_confirmed_cylinder_spec()
        compiled, manifest = compile_spec_native(spec)
        assert isinstance(compiled, CompiledCase)
        assert isinstance(manifest, CompilationManifest)
        assert compiled.experiment_type == "cylinder_flow"

    def test_returns_tuple_for_confirmed_cavity_spec(self):
        spec = _make_confirmed_cavity_spec()
        compiled, manifest = compile_spec_native(spec)
        assert isinstance(compiled, CompiledCase)
        assert isinstance(manifest, CompilationManifest)
        assert compiled.experiment_type == "lid_driven_cavity"


# ---------------------------------------------------------------------------
# 10. compile_spec_native raises SpecNotConfirmedError for non-confirmed
# ---------------------------------------------------------------------------


class TestCompileSpecNativeNotConfirmed:
    def test_raises_for_draft_spec(self):
        """compile_spec_native() raises SpecNotConfirmedError for
        non-confirmed spec."""
        spec = _make_confirmed_pipe_spec(status=ExperimentStatus.DRAFT)
        with pytest.raises(SpecNotConfirmedError, match="confirmed"):
            compile_spec_native(spec)

    def test_raises_for_ready_spec(self):
        spec = _make_confirmed_cylinder_spec(status=ExperimentStatus.READY)
        with pytest.raises(SpecNotConfirmedError, match="confirmed"):
            compile_spec_native(spec)


# ---------------------------------------------------------------------------
# 11. compile_spec_native raises ValueError for unknown experiment type
# ---------------------------------------------------------------------------


class TestCompileSpecNativeUnknownType:
    def test_raises_value_error_for_unknown_type(self):
        """compile_spec_native() raises ValueError for unknown experiment
        type.

        With the parameter hard gate, validate_required_parameters() runs
        before compiler resolution and raises 'cannot detect experiment
        type' for specs whose parameter IDs don't match any known type.
        """
        spec = _make_unknown_spec()
        with pytest.raises(ValueError, match="cannot detect"):
            compile_spec_native(spec)


# ---------------------------------------------------------------------------
# 12. compile_spec_native does NOT call compile_plan
# ---------------------------------------------------------------------------


class TestCompileSpecNativeNoCompilePlan:
    def test_does_not_call_compile_plan(self):
        """Native compilation must NOT call compile_plan."""
        spec = _make_confirmed_pipe_spec()
        registry = CompilerRegistry()

        with patch(
            "fluid_scientist.experiment_planning.compilers.compile_plan"
        ) as spy:
            compiled, manifest = compile_spec_native(spec, registry)
            assert spy.call_count == 0, (
                "compile_plan must not be called in native path"
            )

        assert compiled is not None
        assert manifest.spec_hash is not None

    def test_does_not_call_compile_plan_for_cavity(self):
        spec = _make_confirmed_cavity_spec()
        registry = CompilerRegistry()

        with patch(
            "fluid_scientist.experiment_planning.compilers.compile_plan"
        ) as spy:
            compiled, manifest = compile_spec_native(spec, registry)
            assert spy.call_count == 0

        assert compiled.experiment_type == "lid_driven_cavity"


# ---------------------------------------------------------------------------
# 13. compile_spec uses native path (does NOT call compile_confirmed_spec)
# ---------------------------------------------------------------------------


class TestCompileSpecUsesNativePath:
    def test_does_not_call_compile_confirmed_spec_for_pipe(self):
        """compile_spec() uses native path when available and does NOT
        call compile_confirmed_spec."""
        spec = _make_confirmed_pipe_spec()

        with patch(
            "fluid_scientist.experiment_spec.compilation.compile_confirmed_spec"
        ) as spy:
            compiled, manifest = compile_spec(spec)
            assert spy.call_count == 0, (
                "compile_confirmed_spec must not be called when a native "
                "compiler is available"
            )

        assert isinstance(compiled, CompiledCase)
        assert isinstance(manifest, CompilationManifest)
        assert compiled.experiment_type == "laminar_pipe"

    def test_does_not_call_compile_confirmed_spec_for_cavity(self):
        spec = _make_confirmed_cavity_spec()

        with patch(
            "fluid_scientist.experiment_spec.compilation.compile_confirmed_spec"
        ) as spy:
            compiled, manifest = compile_spec(spec)
            assert spy.call_count == 0

        assert compiled.experiment_type == "lid_driven_cavity"

    def test_does_not_call_compile_plan_for_pipe(self):
        """compile_spec() with native path must NOT call compile_plan."""
        spec = _make_confirmed_pipe_spec()

        with patch(
            "fluid_scientist.experiment_planning.compilers.compile_plan"
        ) as spy:
            compiled, manifest = compile_spec(spec)
            assert spy.call_count == 0

        assert compiled.experiment_type == "laminar_pipe"


# ---------------------------------------------------------------------------
# 14. compile_confirmed_spec emits DeprecationWarning
# ---------------------------------------------------------------------------


class TestCompileConfirmedSpecDeprecation:
    def test_emits_deprecation_warning(self):
        """compile_confirmed_spec() emits DeprecationWarning."""
        spec = _make_confirmed_pipe_spec()
        with pytest.warns(DeprecationWarning, match="deprecated"):
            compiled = compile_confirmed_spec(spec)
        assert isinstance(compiled, CompiledCase)
        assert compiled.experiment_type == "laminar_pipe"

    def test_deprecation_warning_emitted_for_cavity(self):
        spec = _make_confirmed_cavity_spec()
        with pytest.warns(DeprecationWarning):
            compiled = compile_confirmed_spec(spec)
        assert compiled.experiment_type == "lid_driven_cavity"


# ---------------------------------------------------------------------------
# 15. CompilationManifest has correct compiler_id from native compiler
# ---------------------------------------------------------------------------


class TestNativeManifestCompilerId:
    def test_pipe_manifest_has_native_compiler_id(self):
        """CompilationManifest has correct compiler_id from the native
        compiler (not the old COMPILER_ID)."""
        spec = _make_confirmed_pipe_spec()
        compiled, manifest = compile_spec_native(spec)
        assert manifest.compiler_id == "fluid_scientist.native.pipe_flow"
        assert manifest.compiler_id != COMPILER_ID
        assert manifest.compiler_version == "1.0.0"

    def test_cylinder_manifest_has_native_compiler_id(self):
        spec = _make_confirmed_cylinder_spec()
        _, manifest = compile_spec_native(spec)
        assert manifest.compiler_id == "fluid_scientist.native.cylinder_flow"
        assert manifest.compiler_id != COMPILER_ID

    def test_cavity_manifest_has_native_compiler_id(self):
        spec = _make_confirmed_cavity_spec()
        _, manifest = compile_spec_native(spec)
        assert manifest.compiler_id == "fluid_scientist.native.cavity_flow"
        assert manifest.compiler_id != COMPILER_ID

    def test_compile_spec_manifest_has_native_compiler_id(self):
        """compile_spec() manifest also uses native compiler_id when a
        native compiler is available."""
        spec = _make_confirmed_pipe_spec()
        _, manifest = compile_spec(spec)
        assert manifest.compiler_id == "fluid_scientist.native.pipe_flow"
        assert manifest.compiler_id != COMPILER_ID


# ---------------------------------------------------------------------------
# Extra: Protocol and registry registration
# ---------------------------------------------------------------------------


class TestCompilerProtocol:
    def test_compilers_satisfy_protocol(self):
        """All three compilers satisfy the ExperimentCompiler protocol."""
        for compiler in (PipeFlowCompiler(), CylinderFlowCompiler(),
                         CavityFlowCompiler()):
            assert hasattr(compiler, "compiler_id")
            assert hasattr(compiler, "compiler_version")
            assert hasattr(compiler, "can_compile")
            assert hasattr(compiler, "compile")

    def test_registry_can_register_custom_compiler(self):
        """CompilerRegistry.register() adds a custom compiler."""

        class CustomCompiler:
            compiler_id = "custom.test"
            compiler_version = "0.1.0"

            def can_compile(self, spec: ExperimentSpec) -> bool:
                return False

            def compile(self, spec: ExperimentSpec) -> CompiledCase:
                raise NotImplementedError

        registry = CompilerRegistry()
        custom = CustomCompiler()
        registry.register(custom)
        assert "custom.test" in registry.available_compilers()
        assert len(registry.available_compilers()) == 4
