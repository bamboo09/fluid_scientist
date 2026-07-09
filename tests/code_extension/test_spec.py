"""Tests for CodeExtensionSpec creation, defaults, and safety constraints."""

from __future__ import annotations

from datetime import datetime

import pytest

from fluid_scientist.code_extension.spec import (
    DEFAULT_SAFETY_CONSTRAINTS,
    CodeExtensionSpec,
)


def _make_spec(**overrides) -> CodeExtensionSpec:
    """Build a minimal CodeExtensionSpec with optional overrides."""
    defaults = {
        "extension_id": "ext-001",
        "session_id": "session-001",
        "extension_type": "metric_operator",
        "missing_capability_id": "cap-001",
        "description": "Calculate custom metric",
    }
    defaults.update(overrides)
    return CodeExtensionSpec(**defaults)


# ---------------------------------------------------------------------------
# Creation tests
# ---------------------------------------------------------------------------


class TestCodeExtensionSpecCreation:
    def test_create_minimal_spec(self):
        spec = _make_spec()
        assert spec.extension_id == "ext-001"
        assert spec.session_id == "session-001"
        assert spec.draft_id is None
        assert spec.extension_type == "metric_operator"
        assert spec.missing_capability_id == "cap-001"
        assert spec.description == "Calculate custom metric"
        assert spec.status == "spec_draft"
        assert spec.generated_code is None
        assert spec.test_results is None
        assert spec.review_notes == ""

    def test_create_with_draft_id(self):
        spec = _make_spec(draft_id="draft-001")
        assert spec.draft_id == "draft-001"

    def test_default_empty_collections(self):
        spec = _make_spec()
        assert spec.target_interfaces == []
        assert spec.inputs == []
        assert spec.outputs == []
        assert spec.acceptance_tests == []

    def test_timestamps_are_datetime(self):
        spec = _make_spec()
        assert isinstance(spec.created_at, datetime)
        assert isinstance(spec.updated_at, datetime)

    def test_timestamps_are_utc(self):
        spec = _make_spec()
        assert spec.created_at.tzinfo is not None
        assert spec.updated_at.tzinfo is not None

    def test_all_extension_types_accepted(self):
        extension_types = [
            "analysis_plugin",
            "metric_operator",
            "boundary_condition",
            "geometry_generator",
            "physical_model_writer",
            "postprocess_metric",
            "mesh_generator",
            "parameter_definition",
        ]
        for etype in extension_types:
            spec = _make_spec(extension_type=etype)
            assert spec.extension_type == etype

    def test_invalid_extension_type_rejected(self):
        with pytest.raises(ValueError):
            _make_spec(extension_type="unknown_type")


# ---------------------------------------------------------------------------
# Safety constraint tests
# ---------------------------------------------------------------------------


class TestSafetyConstraints:
    def test_default_safety_constraints_present(self):
        spec = _make_spec()
        assert len(spec.safety_constraints) == 4

    def test_file_system_constraint(self):
        spec = _make_spec()
        fs = next(
            c for c in spec.safety_constraints if c["type"] == "file_system"
        )
        assert "read" in fs["description"].lower()
        assert "write" in fs["description"].lower()
        assert fs["read_paths"] == ["case/**"]
        assert fs["write_paths"] == ["output/**"]

    def test_shell_execution_constraint(self):
        spec = _make_spec()
        shell = next(
            c for c in spec.safety_constraints if c["type"] == "shell_execution"
        )
        assert "arbitrary" in shell["description"].lower()
        assert shell["allowlisted_commands"] == []

    def test_execution_timeout_constraint(self):
        spec = _make_spec()
        timeout = next(
            c for c in spec.safety_constraints if c["type"] == "execution_timeout"
        )
        assert timeout["max_seconds"] == 300

    def test_numerical_safety_constraint(self):
        spec = _make_spec()
        numerical = next(
            c for c in spec.safety_constraints if c["type"] == "numerical_safety"
        )
        assert "nan" in numerical["description"].lower()
        assert "inf" in numerical["description"].lower()
        assert "nan_detection" in numerical["checks"]
        assert "inf_detection" in numerical["checks"]
        assert "bounded_output" in numerical["checks"]

    def test_safety_constraints_are_independent_copies(self):
        """Each spec instance gets its own deep copy of the defaults."""
        spec1 = _make_spec()
        spec2 = _make_spec()
        spec1.safety_constraints[0]["description"] = "modified"
        assert spec2.safety_constraints[0]["description"] != "modified"

    def test_custom_safety_constraints_respected(self):
        custom = [{"constraint_id": "custom", "type": "custom", "description": "Custom"}]
        spec = _make_spec(safety_constraints=custom)
        assert len(spec.safety_constraints) == 1
        assert spec.safety_constraints[0]["constraint_id"] == "custom"

    def test_default_safety_constraints_constant_matches(self):
        spec = _make_spec()
        types_in_spec = [c["type"] for c in spec.safety_constraints]
        types_in_const = [c["type"] for c in DEFAULT_SAFETY_CONSTRAINTS]
        assert types_in_spec == types_in_const


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_spec_draft_can_transition_to_reviewed(self):
        spec = _make_spec()
        assert spec.can_transition_to("spec_reviewed")

    def test_spec_draft_can_transition_to_rejected(self):
        spec = _make_spec()
        assert spec.can_transition_to("rejected")

    def test_spec_draft_cannot_transition_to_generating(self):
        spec = _make_spec()
        assert not spec.can_transition_to("generating")

    def test_transition_to_returns_new_instance(self):
        spec = _make_spec()
        updated = spec.transition_to("spec_reviewed")
        assert updated.status == "spec_reviewed"
        assert spec.status == "spec_draft"  # original unchanged

    def test_transition_updates_timestamp(self):
        spec = _make_spec()
        original_updated = spec.updated_at
        updated = spec.transition_to("spec_reviewed")
        assert updated.updated_at >= original_updated

    def test_invalid_transition_raises(self):
        spec = _make_spec()
        with pytest.raises(ValueError, match="Invalid transition"):
            spec.transition_to("approved")

    def test_full_valid_transition_chain(self):
        spec = _make_spec()
        for target in [
            "spec_reviewed",
            "generating",
            "generated",
            "testing",
            "tested",
            "approved",
            "registered",
        ]:
            assert spec.can_transition_to(target), (
                f"Should be able to transition to {target} from {spec.status}"
            )
            spec = spec.transition_to(target)
        assert spec.status == "registered"

    def test_rejected_is_terminal(self):
        spec = _make_spec(status="rejected")
        assert not spec.can_transition_to("spec_draft")
        assert not spec.can_transition_to("approved")
        assert not spec.can_transition_to("registered")

    def test_registered_is_terminal(self):
        spec = _make_spec(status="registered")
        assert not spec.can_transition_to("approved")
        assert not spec.can_transition_to("rejected")

    def test_any_non_terminal_state_can_reject(self):
        non_terminal_states = [
            "spec_draft",
            "spec_reviewed",
            "generating",
            "generated",
            "testing",
            "tested",
            "approved",
        ]
        for state in non_terminal_states:
            spec = _make_spec(status=state)
            assert spec.can_transition_to("rejected"), (
                f"Should be able to reject from {state}"
            )
