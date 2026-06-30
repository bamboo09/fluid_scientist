"""Persistent project and human-approval application service."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from fluid_scientist.domain.models import Approval
from fluid_scientist.orchestration.workflow import ResearchWorkflow, TransitionError
from fluid_scientist.ports import WorkflowRepository


class ProjectView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    workflow_state: str
    version: int
    approvals: tuple[Approval, ...]
    external_jobs: dict[str, str]
    audit_event_count: int


class ProjectService:
    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def create(self, question: str, *, actor: str = "researcher") -> ProjectView:
        project_id = str(uuid4())
        workflow = ResearchWorkflow(project_id)
        workflow.transition("INTERPRET", actor=actor, payload={"question": question})
        version = self._repository.save_snapshot(project_id, workflow.to_json(), expected_version=0)
        for event in workflow.state.audit_events:
            self._repository.append_audit_event(project_id, event)
        return self._view(workflow, version)

    def get(self, project_id: str) -> ProjectView:
        workflow, version = self._load(project_id)
        return self._view(workflow, version)

    def decide(
        self,
        project_id: str,
        *,
        gate: str,
        decision: Literal["approve", "reject"],
        actor: str,
        subject_version: int,
        reason: str | None = None,
    ) -> ProjectView:
        workflow, version = self._load(project_id)
        event_count = len(workflow.state.audit_events)
        if decision == "approve":
            approval = workflow.approve(gate, approved_by=actor, subject_version=subject_version)
            self._repository.record_approval(project_id, approval)
        else:
            workflow.reject(
                gate,
                rejected_by=actor,
                subject_version=subject_version,
                reason=reason or "",
            )
        new_version = self._repository.save_snapshot(
            project_id, workflow.to_json(), expected_version=version
        )
        self._persist_new_events(project_id, workflow, event_count)
        return self._view(workflow, new_version)

    def act(self, project_id: str, action: str, *, actor: str = "system") -> ProjectView:
        workflow, version = self._load(project_id)
        event_count = len(workflow.state.audit_events)
        workflow.transition(action, actor=actor)
        new_version = self._repository.save_snapshot(
            project_id, workflow.to_json(), expected_version=version
        )
        self._persist_new_events(project_id, workflow, event_count)
        return self._view(workflow, new_version)

    def prepare_pilot_submission(self, project_id: str, case_id: str) -> str | None:
        workflow, _ = self._load(project_id)
        existing = workflow.state.external_jobs.get(case_id)
        if existing is not None:
            return existing
        if workflow.state.name != "PILOT_READY":
            raise TransitionError(f"pilot submission is not allowed from {workflow.state.name}")
        if "GATE_2" not in workflow.state.approvals:
            raise TransitionError("GATE_2 approval is required before pilot submission")
        return None

    def record_pilot_submission(
        self,
        project_id: str,
        *,
        case_id: str,
        job_id: str,
        target_id: str,
        actor: str,
    ) -> ProjectView:
        workflow, version = self._load(project_id)
        event_count = len(workflow.state.audit_events)
        existing = workflow.state.external_jobs.get(case_id)
        if existing is not None:
            if existing != job_id:
                raise TransitionError(f"{case_id} is already bound to external job {existing}")
            return self._view(workflow, version)
        workflow.transition(
            "SUBMIT_PILOT",
            actor=actor,
            payload={"target_id": target_id, "case_id": case_id, "job_id": job_id},
        )
        workflow.record_external_job(case_id, job_id)
        self._repository.bind_external_job(project_id, case_id, job_id)
        new_version = self._repository.save_snapshot(
            project_id, workflow.to_json(), expected_version=version
        )
        self._persist_new_events(project_id, workflow, event_count)
        return self._view(workflow, new_version)

    def verify_pilot(
        self,
        project_id: str,
        *,
        case_id: str,
        validation: dict[str, object],
        actor: str = "validator",
    ) -> ProjectView:
        workflow, version = self._load(project_id)
        if workflow.state.name == "PILOT_VERIFIED":
            return self._view(workflow, version)
        if case_id not in workflow.state.external_jobs:
            raise TransitionError(f"no external job is bound for {case_id}")
        event_count = len(workflow.state.audit_events)
        workflow.transition(
            "VERIFY_PILOT",
            actor=actor,
            payload={"case_id": case_id, "validation": validation},
        )
        new_version = self._repository.save_snapshot(
            project_id, workflow.to_json(), expected_version=version
        )
        self._persist_new_events(project_id, workflow, event_count)
        return self._view(workflow, new_version)

    def _load(self, project_id: str) -> tuple[ResearchWorkflow, int]:
        stored = self._repository.load_snapshot(project_id)
        if stored is None:
            raise KeyError(f"project not found: {project_id}")
        return ResearchWorkflow.from_json(stored.snapshot), stored.version

    def _persist_new_events(
        self, project_id: str, workflow: ResearchWorkflow, previous_count: int
    ) -> None:
        for event in workflow.state.audit_events[previous_count:]:
            self._repository.append_audit_event(project_id, event)

    @staticmethod
    def _view(workflow: ResearchWorkflow, version: int) -> ProjectView:
        return ProjectView(
            project_id=workflow.state.project_id,
            workflow_state=workflow.state.name,
            version=version,
            approvals=tuple(workflow.state.approvals.values()),
            external_jobs=workflow.state.external_jobs,
            audit_event_count=len(workflow.state.audit_events),
        )
