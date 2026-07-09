"""Tests for the CodeExtensionWorkflow closed-loop lifecycle."""

from __future__ import annotations

import pytest

from fluid_scientist.capabilities.models import CapabilityRegistry, MissingCapability
from fluid_scientist.code_extension.spec import CodeExtensionSpec, CodeExtensionWorkflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_missing_capability(**kwargs) -> MissingCapability:
    """Build a MissingCapability with sensible defaults."""
    defaults = {
        "capability_id": "cap_metric_custom",
        "capability_type": "metric_operator",
        "requested_behavior": "Calculate custom Reynolds stress metric",
        "reason": "Metric not in registry",
        "severity": "blocking",
        "code_extension_allowed": True,
        "required_inputs": ["velocity_field", "density"],
        "expected_outputs": ["reynolds_stress"],
        "suggested_extension_type": "metric_operator",
        "source_module": "metric_planner",
    }
    defaults.update(kwargs)
    return MissingCapability(**defaults)


def _make_spec(**overrides) -> CodeExtensionSpec:
    """Build a CodeExtensionSpec with minimal defaults."""
    defaults = {
        "extension_id": "ext-001",
        "session_id": "session-001",
        "extension_type": "analysis_plugin",
        "missing_capability_id": "cap-001",
        "description": "Test extension",
    }
    defaults.update(overrides)
    return CodeExtensionSpec(**defaults)


# ---------------------------------------------------------------------------
# create_spec tests
# ---------------------------------------------------------------------------


class TestCreateSpec:
    def test_create_from_missing_capability_object(self):
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()
        spec = wf.create_spec(cap, session_id="session-001")

        assert spec.session_id == "session-001"
        assert spec.draft_id is None
        assert spec.status == "spec_draft"
        assert spec.missing_capability_id == "cap_metric_custom"
        assert spec.extension_type == "metric_operator"
        assert "Reynolds stress" in spec.description
        assert len(spec.inputs) == 2
        assert spec.inputs[0]["name"] == "velocity_field"
        assert spec.inputs[1]["name"] == "density"
        assert len(spec.outputs) == 1
        assert spec.outputs[0]["name"] == "reynolds_stress"
        assert len(spec.safety_constraints) == 4

    def test_create_from_dict(self):
        wf = CodeExtensionWorkflow()
        cap_dict = {
            "capability_id": "cap_bc_custom",
            "capability_type": "boundary_condition",
            "requested_behavior": "Custom wall function BC",
            "reason": "No suitable BC available",
            "required_inputs": ["wall_shear_stress"],
            "expected_outputs": ["wall_velocity"],
            "suggested_extension_type": "boundary_condition",
        }
        spec = wf.create_spec(cap_dict, session_id="session-002", draft_id="draft-001")

        assert spec.session_id == "session-002"
        assert spec.draft_id == "draft-001"
        assert spec.extension_type == "boundary_condition"
        assert spec.missing_capability_id == "cap_bc_custom"
        assert spec.description == "Custom wall function BC"

    def test_create_with_draft_id(self):
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()
        spec = wf.create_spec(cap, session_id="s", draft_id="d-001")
        assert spec.draft_id == "d-001"

    def test_create_maps_solver_extension_type(self):
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability(
            capability_type="solver_extension",
            suggested_extension_type=None,
        )
        spec = wf.create_spec(cap, session_id="s")
        assert spec.extension_type == "physical_model_writer"

    def test_create_maps_post_processor_type(self):
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability(
            capability_type="post_processor",
            suggested_extension_type=None,
        )
        spec = wf.create_spec(cap, session_id="s")
        assert spec.extension_type == "postprocess_metric"

    def test_create_defaults_to_analysis_plugin(self):
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability(
            capability_type="unknown_type",
            suggested_extension_type=None,
        )
        spec = wf.create_spec(cap, session_id="s")
        assert spec.extension_type == "analysis_plugin"

    def test_create_generates_unique_extension_ids(self):
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()
        spec1 = wf.create_spec(cap, session_id="s")
        spec2 = wf.create_spec(cap, session_id="s")
        assert spec1.extension_id != spec2.extension_id

    def test_create_missing_capability_id_raises(self):
        wf = CodeExtensionWorkflow()
        with pytest.raises(ValueError, match="capability_id"):
            wf.create_spec({}, session_id="s")

    def test_create_invalid_type_raises(self):
        wf = CodeExtensionWorkflow()
        with pytest.raises(TypeError):
            wf.create_spec("not a dict", session_id="s")  # type: ignore[arg-type]

    def test_create_description_falls_back_to_reason(self):
        wf = CodeExtensionWorkflow()
        cap_dict = {
            "capability_id": "cap-x",
            "capability_type": "analysis_plugin",
            "reason": "Fallback reason",
        }
        spec = wf.create_spec(cap_dict, session_id="s")
        assert spec.description == "Fallback reason"


# ---------------------------------------------------------------------------
# Full closed-loop happy path
# ---------------------------------------------------------------------------


class TestFullClosedLoop:
    def test_happy_path(self):
        """Full closed loop: create -> review -> generate -> test -> approve -> register."""
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()

        # 1. Create spec
        spec = wf.create_spec(cap, session_id="session-001")
        assert spec.status == "spec_draft"

        # 2. Review spec
        spec = wf.review_spec(spec, notes="Spec looks good")
        assert spec.status == "spec_reviewed"
        assert "looks good" in spec.review_notes

        # 3. Submit for generation
        spec = wf.submit_for_generation(spec)
        assert spec.status == "generating"

        # 4. Submit code
        code = (
            "def calculate_reynolds_stress(velocity, density):\n"
            "    return velocity * density\n"
        )
        spec = wf.submit_code(spec, code=code)
        assert spec.status == "generated"
        assert spec.generated_code == code

        # 5. Run tests (no acceptance tests -> syntax check)
        spec = wf.run_tests(spec)
        assert spec.status == "tested"
        assert spec.test_results is not None
        assert spec.test_results["passed"] >= 1
        assert spec.test_results["failed"] == 0

        # 6. Approve
        spec = wf.approve(spec, review_notes="All tests passed")
        assert spec.status == "approved"
        assert "All tests passed" in spec.review_notes

        # 7. Register
        registry = CapabilityRegistry()
        spec = wf.register(spec, registry)
        assert spec.status == "registered"
        assert registry.has_capability("cap_metric_custom")
        assert registry.has_capability(spec.extension_id)

    def test_happy_path_with_acceptance_tests(self):
        """Full loop with explicit acceptance tests that pass."""
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()

        spec = wf.create_spec(cap, session_id="session-002")
        spec = wf.review_spec(spec)
        spec = wf.submit_for_generation(spec)

        code = "result = 42\n"
        spec = wf.submit_code(spec, code=code)

        # Add acceptance tests
        spec = spec.model_copy(
            update={
                "acceptance_tests": [
                    {
                        "test_id": "test_basic",
                        "test_name": "Basic Execution",
                        "test_code": "assert result == 42\n",
                        "timeout_seconds": 5.0,
                    },
                    {
                        "test_id": "test_type",
                        "test_name": "Type Check",
                        "test_code": "assert isinstance(result, int)\n",
                        "timeout_seconds": 5.0,
                    },
                ]
            }
        )

        spec = wf.run_tests(spec)
        assert spec.status == "tested"
        assert spec.test_results["total"] == 2
        assert spec.test_results["passed"] == 2
        assert spec.test_results["failed"] == 0

        spec = wf.approve(spec)
        registry = CapabilityRegistry()
        spec = wf.register(spec, registry)
        assert spec.status == "registered"

    def test_test_failure_still_transitions_to_tested(self):
        """Tests can fail but spec still transitions to tested (user decides)."""
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()

        spec = wf.create_spec(cap, session_id="s")
        spec = wf.review_spec(spec)
        spec = wf.submit_for_generation(spec)
        spec = wf.submit_code(spec, code="x = 1\n")

        spec = spec.model_copy(
            update={
                "acceptance_tests": [
                    {
                        "test_id": "fail_test",
                        "test_name": "Failing Test",
                        "test_code": "assert False, 'intentional failure'\n",
                    }
                ]
            }
        )

        spec = wf.run_tests(spec)
        assert spec.status == "tested"
        assert spec.test_results["failed"] == 1
        assert spec.test_results["passed"] == 0

    def test_syntax_error_in_generated_code(self):
        """Generated code with a syntax error fails the syntax check."""
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()

        spec = wf.create_spec(cap, session_id="s")
        spec = wf.review_spec(spec)
        spec = wf.submit_for_generation(spec)
        spec = wf.submit_code(spec, code="def broken(:\n    pass\n")

        spec = wf.run_tests(spec)
        assert spec.status == "tested"
        assert spec.test_results["passed"] == 0
        assert spec.test_results["failed"] == 1

    def test_dangerous_code_blocked_by_sandbox(self):
        """Code with dangerous imports is caught by the sandbox."""
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()

        spec = wf.create_spec(cap, session_id="s")
        spec = wf.review_spec(spec)
        spec = wf.submit_for_generation(spec)
        spec = wf.submit_code(spec, code="import subprocess\n")

        spec = spec.model_copy(
            update={
                "acceptance_tests": [
                    {
                        "test_id": "exec_test",
                        "test_name": "Exec Test",
                        "test_code": "print('hello')\n",
                    }
                ]
            }
        )

        spec = wf.run_tests(spec)
        assert spec.status == "tested"
        assert spec.test_results["failed"] == 1


# ---------------------------------------------------------------------------
# Invalid transition tests
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    def test_submit_for_generation_from_draft_raises(self):
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()
        spec = wf.create_spec(cap, session_id="s")
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.submit_for_generation(spec)

    def test_submit_code_from_draft_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec()
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.submit_code(spec, code="print('hello')")

    def test_run_tests_from_draft_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec()
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.run_tests(spec)

    def test_approve_from_draft_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec()
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.approve(spec)

    def test_register_from_tested_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="tested")
        registry = CapabilityRegistry()
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.register(spec, registry)

    def test_review_from_reviewed_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="spec_reviewed")
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.review_spec(spec)

    def test_approve_from_approved_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="approved")
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.approve(spec)

    def test_submit_code_from_generated_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="generated")
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.submit_code(spec, code="x = 1")


# ---------------------------------------------------------------------------
# Reject path tests
# ---------------------------------------------------------------------------


class TestRejectPath:
    def test_reject_from_spec_draft(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec()
        spec = wf.reject(spec, reason="Not needed")
        assert spec.status == "rejected"
        assert "Not needed" in spec.review_notes

    def test_reject_from_spec_reviewed(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="spec_reviewed")
        spec = wf.reject(spec, reason="Spec inadequate")
        assert spec.status == "rejected"
        assert "Spec inadequate" in spec.review_notes

    def test_reject_from_generating(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="generating")
        spec = wf.reject(spec, reason="Generation failed")
        assert spec.status == "rejected"

    def test_reject_from_generated(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="generated")
        spec = wf.reject(spec, reason="Code quality poor")
        assert spec.status == "rejected"

    def test_reject_from_testing(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="testing")
        spec = wf.reject(spec, reason="Tests hanging")
        assert spec.status == "rejected"

    def test_reject_from_tested(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="tested")
        spec = wf.reject(spec, reason="Tests showed issues")
        assert spec.status == "rejected"

    def test_reject_from_approved(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="approved")
        spec = wf.reject(spec, reason="Reconsidered")
        assert spec.status == "rejected"

    def test_reject_from_rejected_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="rejected")
        with pytest.raises(ValueError, match="terminal"):
            wf.reject(spec, reason="Already rejected")

    def test_reject_from_registered_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="registered")
        with pytest.raises(ValueError, match="terminal"):
            wf.reject(spec, reason="Already registered")


# ---------------------------------------------------------------------------
# Register tests
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_adds_to_registry(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(
            extension_id="ext-reg-001",
            missing_capability_id="cap_metric_x",
            status="approved",
            generated_code="result = 42\n",
        )
        registry = CapabilityRegistry()
        spec = wf.register(spec, registry)

        assert spec.status == "registered"
        assert registry.has_capability("cap_metric_x")
        assert registry.has_capability("ext-reg-001")

    def test_register_capability_data(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(
            extension_id="ext-reg-002",
            extension_type="boundary_condition",
            missing_capability_id="cap_bc_y",
            description="Custom BC",
            status="approved",
            generated_code="def bc():\n    pass\n",
        )
        spec = spec.model_copy(
            update={
                "inputs": [{"name": "velocity", "data_type": "float"}],
                "outputs": [{"name": "wall_shear", "data_type": "float"}],
            }
        )
        registry = CapabilityRegistry()
        wf.register(spec, registry)

        cap = registry.get_capability("cap_bc_y")
        assert cap is not None
        assert cap["extension_type"] == "boundary_condition"
        assert cap["generated_code"] == "def bc():\n    pass\n"
        assert cap["related_capability_id"] == "cap_bc_y"
        assert "velocity" in cap["required_inputs"]
        assert "wall_shear" in cap["expected_outputs"]

    def test_register_from_non_approved_raises(self):
        wf = CodeExtensionWorkflow()
        spec = _make_spec(status="tested")
        registry = CapabilityRegistry()
        with pytest.raises(ValueError, match="Invalid transition"):
            wf.register(spec, registry)

    def test_register_makes_capability_resolvable(self):
        """After registration, the capability is found by has_capability()."""
        wf = CodeExtensionWorkflow()
        cap = _make_missing_capability()
        spec = wf.create_spec(cap, session_id="s")
        spec = wf.review_spec(spec)
        spec = wf.submit_for_generation(spec)
        spec = wf.submit_code(spec, code="result = 42\n")
        spec = wf.run_tests(spec)
        spec = wf.approve(spec)

        registry = CapabilityRegistry()
        assert not registry.has_capability("cap_metric_custom")
        wf.register(spec, registry)
        assert registry.has_capability("cap_metric_custom")
