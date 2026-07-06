"""Tests for the ExperimentSpec system."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.experiment_spec.dependency import (
    change_summary,
    propagate_change,
)
from fluid_scientist.experiment_spec.models import (
    Criticality,
    ExperimentSpec,
    ParameterConstraints,
    ParameterDependency,
    ParameterProvenance,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
    ResearchSpec,
)
from fluid_scientist.experiment_spec.state_machine import (
    TransitionError,
    assert_transition,
    is_editable,
    is_immutable,
    is_terminal,
)
from fluid_scientist.ports import StoredExperimentSpec


def repository(tmp_path):
    return SQLWorkflowRepository(f"sqlite:///{tmp_path / 'test.db'}")


def make_minimal_spec() -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="exp-001",
        research=ResearchSpec(
            title="Test Experiment",
            objective="Test objective for validation",
        ),
        parameters=[
            ParameterSpec(
                parameter_id="diameter",
                display_name="直径",
                category="geometry",
                value=0.1,
                unit="m",
                data_type="float",
                source=ParameterSourceInfo(type=ParameterSource.USER),
                status=ParameterStatus.ACCEPTED,
                criticality=Criticality.CRITICAL,
                impact_scope=["reynolds_number", "mesh"],
                constraints=ParameterConstraints(min=0, exclusive_min=True),
                dependencies=ParameterDependency(affects=["reynolds_number"]),
                provenance=ParameterProvenance(created_by="user"),
            ),
            ParameterSpec(
                parameter_id="reynolds_number",
                display_name="Reynolds数",
                category="physics",
                value=100.0,
                data_type="float",
                source=ParameterSourceInfo(
            type=ParameterSource.DERIVED,
            reference="diameter, velocity, viscosity",
        ),
                status=ParameterStatus.ACCEPTED,
                criticality=Criticality.CRITICAL,
                dependencies=ParameterDependency(depends_on=["diameter"]),
            ),
        ],
    )


# --- State machine tests ---


class TestStateMachine:
    def test_draft_to_ready(self):
        assert_transition("draft", "ready")

    def test_ready_to_confirmed(self):
        assert_transition("ready", "confirmed")

    def test_confirmed_to_compiling(self):
        assert_transition("confirmed", "compiling")

    def test_compiling_to_running(self):
        assert_transition("compiling", "running")

    def test_running_to_completed(self):
        assert_transition("running", "completed")

    def test_running_to_failed(self):
        assert_transition("running", "failed")

    def test_draft_to_rejected(self):
        assert_transition("draft", "rejected")

    def test_cannot_skip_ready(self):
        with pytest.raises(TransitionError):
            assert_transition("draft", "confirmed")

    def test_cannot_go_back_from_completed(self):
        with pytest.raises(TransitionError):
            assert_transition("completed", "draft")

    def test_completed_is_terminal(self):
        assert is_terminal("completed")

    def test_failed_can_go_to_draft(self):
        assert_transition("failed", "draft")

    def test_draft_is_editable(self):
        assert is_editable("draft")

    def test_confirmed_is_immutable(self):
        assert is_immutable("confirmed")


# --- ParameterSpec validation tests ---


class TestParameterSpec:
    def test_valid_parameter(self):
        p = ParameterSpec(
            parameter_id="vel",
            display_name="速度",
            category="bc",
            value=1.0,
            unit="m/s",
            source=ParameterSourceInfo(type=ParameterSource.USER),
        )
        assert p.value == 1.0

    def test_value_below_min_rejected(self):
        with pytest.raises(ValueError, match="below"):
            ParameterSpec(
                parameter_id="vel",
                display_name="速度",
                category="bc",
                value=-1.0,
                source=ParameterSourceInfo(type=ParameterSource.USER),
                constraints=ParameterConstraints(min=0, exclusive_min=True),
            )

    def test_enum_value_must_be_allowed(self):
        with pytest.raises(ValueError, match="allowed_values"):
            ParameterSpec(
                parameter_id="model",
                display_name="模型",
                category="physics",
                value="k-omega",
                data_type="enum",
                source=ParameterSourceInfo(type=ParameterSource.SYSTEM_RECOMMENDED),
                constraints=ParameterConstraints(allowed_values=["laminar", "k-epsilon"]),
            )

    def test_critical_param_must_have_value(self):
        with pytest.raises(ValueError, match="critical"):
            ParameterSpec(
                parameter_id="vel",
                display_name="速度",
                category="bc",
                value=None,
                source=ParameterSourceInfo(type=ParameterSource.USER),
                criticality=Criticality.CRITICAL,
            )


# --- ExperimentSpec tests ---


class TestExperimentSpec:
    def test_duplicate_parameter_ids_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            ExperimentSpec(
                experiment_id="exp-1",
                research=ResearchSpec(title="T", objective="O"),
                parameters=[
                    ParameterSpec(
                        parameter_id="x",
                        display_name="X",
                        category="c",
                        value=1.0,
                        source=ParameterSourceInfo(type=ParameterSource.USER),
                    ),
                    ParameterSpec(
                        parameter_id="x",
                        display_name="X2",
                        category="c",
                        value=2.0,
                        source=ParameterSourceInfo(type=ParameterSource.USER),
                    ),
                ],
            )

    def test_get_parameter(self):
        spec = make_minimal_spec()
        p = spec.get_parameter("diameter")
        assert p is not None
        assert p.value == 0.1

    def test_update_parameter(self):
        spec = make_minimal_spec()
        updated = spec.update_parameter("diameter", 0.2)
        assert updated.get_parameter("diameter").value == 0.2
        assert updated.get_parameter("diameter").status == ParameterStatus.MODIFIED

    def test_critical_unresolved_empty(self):
        spec = make_minimal_spec()
        assert len(spec.critical_unresolved()) == 0

    def test_critical_unresolved_with_unknown(self):
        spec = make_minimal_spec()
        # Add a critical param with unknown source
        params = list(spec.parameters)
        params.append(ParameterSpec(
            parameter_id="turbulence_model",
            display_name="湍流模型",
            category="physics",
            value=None,
            source=ParameterSourceInfo(type=ParameterSource.UNKNOWN),
            criticality=Criticality.CRITICAL,
        ))
        spec = spec.model_copy(update={"parameters": params})
        assert len(spec.critical_unresolved()) == 1

    def test_is_ready(self):
        spec = make_minimal_spec()
        assert spec.is_ready() is True


# --- Dependency propagation tests ---


class TestDependencyPropagation:
    def test_propagate_marks_dependent(self):
        spec = make_minimal_spec()
        updated, result = propagate_change(spec, "diameter", 0.2)
        assert result.directly_modified == "diameter"
        assert "reynolds_number" in result.auto_recomputed

    def test_propagate_stale_artifacts(self):
        spec = make_minimal_spec()
        _, result = propagate_change(spec, "diameter", 0.2)
        assert "mesh" in result.stale_artifacts

    def test_propagate_critical_warning(self):
        spec = make_minimal_spec()
        _, result = propagate_change(spec, "diameter", 0.2)
        assert any("Critical" in w for w in result.new_warnings)

    def test_change_summary(self):
        spec = make_minimal_spec()
        _, result = propagate_change(spec, "diameter", 0.2)
        summary = change_summary(result)
        assert "diameter" in summary
        assert "reynolds_number" in summary

    def test_non_editable_rejected(self):
        spec = make_minimal_spec()
        params = [
            p.model_copy(update={"editable": False})
            if p.parameter_id == "diameter"
            else p
            for p in spec.parameters
        ]
        spec = spec.model_copy(update={"parameters": params})
        with pytest.raises(ValueError, match="not editable"):
            propagate_change(spec, "diameter", 0.2)


# --- Repository tests ---


class TestRepository:
    def test_save_and_load(self, tmp_path):
        repo = repository(tmp_path)
        spec = make_minimal_spec()
        now = datetime.now(UTC).isoformat()
        stored = StoredExperimentSpec(
            experiment_id=spec.experiment_id,
            project_id=None,
            schema_version=spec.schema_version,
            experiment_version=1,
            status="draft",
            task_type="new_simulation",
            interaction_mode="standard",
            spec_json=spec.model_dump_json(),
            created_at=now,
            updated_at=now,
        )
        repo.save_experiment_spec(stored)
        loaded = repo.load_experiment_spec("exp-001")
        assert loaded is not None
        assert loaded.experiment_id == "exp-001"
        assert loaded.status == "draft"

    def test_load_nonexistent_returns_none(self, tmp_path):
        repo = repository(tmp_path)
        assert repo.load_experiment_spec("nonexistent") is None

    def test_list_by_project(self, tmp_path):
        repo = repository(tmp_path)
        spec = make_minimal_spec()
        now = datetime.now(UTC).isoformat()
        stored = StoredExperimentSpec(
            experiment_id="exp-001",
            project_id=None,
            schema_version="1.0.0",
            experiment_version=1,
            status="draft",
            task_type="new_simulation",
            interaction_mode="standard",
            spec_json=spec.model_dump_json(),
            created_at=now,
            updated_at=now,
        )
        repo.save_experiment_spec(stored)
        # project_id=None means "no filter" — returns all specs
        specs = repo.list_experiment_specs(project_id=None)
        assert len(specs) == 1
        # Filtering by a different project returns 0
        specs = repo.list_experiment_specs(project_id="other")
        assert len(specs) == 0

    def test_update_status(self, tmp_path):
        repo = repository(tmp_path)
        spec = make_minimal_spec()
        now = datetime.now(UTC).isoformat()
        stored = StoredExperimentSpec(
            experiment_id="exp-001",
            project_id=None,
            schema_version="1.0.0",
            experiment_version=1,
            status="draft",
            task_type="new_simulation",
            interaction_mode="standard",
            spec_json=spec.model_dump_json(),
            created_at=now,
            updated_at=now,
        )
        repo.save_experiment_spec(stored)
        updated = repo.update_experiment_spec_status(
            "exp-001", new_status="ready", updated_at=now
        )
        assert updated.status == "ready"

    def test_replace_spec(self, tmp_path):
        repo = repository(tmp_path)
        spec = make_minimal_spec()
        now = datetime.now(UTC).isoformat()
        stored = StoredExperimentSpec(
            experiment_id="exp-001",
            project_id=None,
            schema_version="1.0.0",
            experiment_version=1,
            status="draft",
            task_type="new_simulation",
            interaction_mode="standard",
            spec_json=spec.model_dump_json(),
            created_at=now,
            updated_at=now,
        )
        repo.save_experiment_spec(stored)
        updated_spec = spec.update_parameter("diameter", 0.3)
        result = repo.replace_experiment_spec(
            "exp-001",
            spec_json=updated_spec.model_dump_json(),
            experiment_version=2,
            status="draft",
            updated_at=now,
        )
        assert result.experiment_version == 2
        loaded = repo.load_experiment_spec("exp-001")
        json_data = loaded.spec_json.replace(" ", "")
        assert "\"value\":0.3" in json_data

    def test_duplicate_id_raises(self, tmp_path):
        repo = repository(tmp_path)
        spec = make_minimal_spec()
        now = datetime.now(UTC).isoformat()
        stored = StoredExperimentSpec(
            experiment_id="exp-001",
            project_id=None,
            schema_version="1.0.0",
            experiment_version=1,
            status="draft",
            task_type="new_simulation",
            interaction_mode="standard",
            spec_json=spec.model_dump_json(),
            created_at=now,
            updated_at=now,
        )
        repo.save_experiment_spec(stored)
        with pytest.raises(SAIntegrityError):
            repo.save_experiment_spec(stored)
