import pytest

from fluid_scientist.orchestration.workflow import ResearchWorkflow, TransitionError


def workflow_at(state: str) -> ResearchWorkflow:
    return ResearchWorkflow.at_state(project_id="project-1", state=state)


def test_pilot_cannot_submit_without_gate_two() -> None:
    workflow = workflow_at("PILOT_READY")

    with pytest.raises(TransitionError, match="GATE_2"):
        workflow.transition("SUBMIT_PILOT")


def test_gate_two_allows_pilot_submission_and_is_audited() -> None:
    workflow = workflow_at("PILOT_READY")
    workflow.approve("GATE_2", approved_by="researcher", subject_version=1)

    workflow.transition("SUBMIT_PILOT")

    assert workflow.state.name == "PILOT_RUNNING"
    assert [event.event_type for event in workflow.state.audit_events[-2:]] == [
        "APPROVAL_GRANTED",
        "STATE_TRANSITION",
    ]


def test_replayed_external_job_is_idempotent() -> None:
    workflow = workflow_at("PILOT_RUNNING")

    first = workflow.record_external_job("case-1", "123")
    second = workflow.record_external_job("case-1", "123")

    assert first == second == "123"
    assert workflow.state.external_jobs == {"case-1": "123"}


def test_external_job_id_mismatch_is_rejected() -> None:
    workflow = workflow_at("PILOT_RUNNING")
    workflow.record_external_job("case-1", "123")

    with pytest.raises(TransitionError, match="already bound"):
        workflow.record_external_job("case-1", "456")


def test_snapshot_round_trip_preserves_gate_and_jobs() -> None:
    workflow = workflow_at("PILOT_READY")
    workflow.approve("GATE_2", approved_by="researcher", subject_version=1)
    workflow.transition("SUBMIT_PILOT")
    workflow.record_external_job("case-1", "123")

    restored = ResearchWorkflow.from_json(workflow.to_json())

    assert restored.state.name == "PILOT_RUNNING"
    assert restored.state.approvals["GATE_2"].approved_by == "researcher"
    assert restored.state.external_jobs == {"case-1": "123"}
