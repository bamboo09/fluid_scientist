"""Explicit workflow transitions with approval and idempotency guards."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.domain.models import Approval, AuditEvent


class TransitionError(RuntimeError):
    """Raised when an action would violate workflow invariants."""


class WorkflowSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    approvals: dict[str, Approval] = Field(default_factory=dict)
    external_jobs: dict[str, str] = Field(default_factory=dict)
    audit_events: tuple[AuditEvent, ...] = ()
    counters: dict[str, int] = Field(default_factory=dict)


class ResearchWorkflow:
    _transitions: dict[tuple[str, str], tuple[str, str | None]] = {
        ("CREATED", "INTERPRET"): ("SPEC_READY", None),
        ("SPEC_READY", "RETRIEVE_EVIDENCE"): ("EVIDENCE_READY", "GATE_1"),
        ("EVIDENCE_READY", "DESIGN_PILOT"): ("PILOT_READY", None),
        ("PILOT_READY", "SUBMIT_PILOT"): ("PILOT_RUNNING", "GATE_2"),
        ("PILOT_RUNNING", "VERIFY_PILOT"): ("PILOT_VERIFIED", None),
        ("PILOT_VERIFIED", "DESIGN_FULL"): ("FULL_READY", None),
        ("FULL_READY", "SUBMIT_FULL"): ("FULL_RUNNING", None),
        ("FULL_RUNNING", "ANALYZE"): ("ANALYZED", None),
        ("ANALYZED", "REVIEW"): ("REVIEW_READY", None),
        ("REVIEW_READY", "PUBLISH_REPORT"): ("REPORTED", "GATE_3"),
    }
    _known_states = {"CREATED", "REPORTED"} | {
        state
        for transition in _transitions.items()
        for state in (transition[0][0], transition[1][0])
    }

    def __init__(self, project_id: str) -> None:
        self.state = WorkflowSnapshot(project_id=project_id, name="CREATED")

    @classmethod
    def at_state(cls, *, project_id: str, state: str) -> "ResearchWorkflow":
        if state not in cls._known_states:
            raise ValueError(f"unknown workflow state: {state}")
        workflow = cls(project_id)
        workflow.state.name = state
        return workflow

    @classmethod
    def from_json(cls, payload: str) -> "ResearchWorkflow":
        snapshot = WorkflowSnapshot.model_validate_json(payload)
        if snapshot.name not in cls._known_states:
            raise ValueError(f"unknown workflow state: {snapshot.name}")
        workflow = cls(snapshot.project_id)
        workflow.state = snapshot
        return workflow

    def to_json(self) -> str:
        return self.state.model_dump_json()

    def approve(self, gate: str, *, approved_by: str, subject_version: int) -> Approval:
        if gate not in {"GATE_1", "GATE_2", "GATE_3"}:
            raise ValueError(f"unknown approval gate: {gate}")
        approval = Approval(
            gate=gate,
            approved_by=approved_by,
            approved_at=datetime.now(UTC),
            subject_version=subject_version,
        )
        self.state.approvals[gate] = approval
        self._audit(
            "APPROVAL_GRANTED",
            approved_by,
            {"gate": gate, "subject_version": subject_version},
        )
        return approval

    def transition(
        self, action: str, *, actor: str = "system", payload: dict[str, Any] | None = None
    ) -> str:
        transition = self._transitions.get((self.state.name, action))
        if transition is None:
            raise TransitionError(f"action {action} is not allowed from {self.state.name}")
        destination, required_gate = transition
        if required_gate is not None and required_gate not in self.state.approvals:
            raise TransitionError(f"{required_gate} approval is required before {action}")
        origin = self.state.name
        self.state.name = destination
        event_payload = {"from": origin, "to": destination, "action": action}
        event_payload.update(payload or {})
        self._audit("STATE_TRANSITION", actor, event_payload)
        return destination

    def record_external_job(self, case_id: str, job_id: str) -> str:
        if not case_id or not job_id:
            raise ValueError("case_id and job_id are required")
        existing = self.state.external_jobs.get(case_id)
        if existing is not None:
            if existing != job_id:
                raise TransitionError(f"{case_id} is already bound to external job {existing}")
            return existing
        self.state.external_jobs[case_id] = job_id
        self._audit("EXTERNAL_JOB_BOUND", "system", {"case_id": case_id, "job_id": job_id})
        return job_id

    def increment_counter(self, name: str, *, maximum: int) -> int:
        value = self.state.counters.get(name, 0) + 1
        if value > maximum:
            raise TransitionError(f"{name} exceeded maximum {maximum}")
        self.state.counters[name] = value
        return value

    def _audit(self, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        event = AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            actor=actor,
            payload=payload,
        )
        self.state.audit_events = (*self.state.audit_events, event)
