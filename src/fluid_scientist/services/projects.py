"""Persistent project and human-approval application service."""

import hashlib
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from fluid_scientist.domain.models import Approval, ApprovedArtifact
from fluid_scientist.orchestration.workflow import ResearchWorkflow, TransitionError
from fluid_scientist.ports import (
    StoredCompiledExperiment,
    StoredExperimentPlan,
    WorkflowRepository,
)


class ProjectView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    question: str | None = None
    workflow_state: str
    version: int
    approvals: tuple[Approval, ...]
    approved_artifacts: dict[str, ApprovedArtifact]
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

    def recent(self) -> ProjectView:
        project_id = self._repository.latest_project_id()
        if project_id is None:
            raise KeyError("no projects exist")
        return self.get(project_id)

    def decide(
        self,
        project_id: str,
        *,
        gate: str,
        decision: Literal["approve", "reject"],
        actor: str,
        subject_version: int,
        reason: str | None = None,
        plan_id: str | None = None,
        plan_version: int | None = None,
        archive_sha256: str | None = None,
    ) -> ProjectView:
        workflow, version = self._load(project_id)
        event_count = len(workflow.state.audit_events)
        if decision == "approve":
            if gate == "GATE_2" and plan_id is not None:
                if plan_version is None or archive_sha256 is None:
                    raise ValueError("Gate 2 artifact binding is incomplete")
                self._validate_compiled_binding(
                    project_id,
                    plan_id=plan_id,
                    plan_version=plan_version,
                    archive_sha256=archive_sha256,
                )
                workflow.bind_approved_artifact(
                    plan_id,
                    plan_version=plan_version,
                    archive_sha256=archive_sha256,
                    actor=actor,
                )
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

    def store_experiment_plan(self, plan: StoredExperimentPlan) -> StoredExperimentPlan:
        return self._repository.store_experiment_plan(plan)

    def load_experiment_plan(self, plan_id: str) -> StoredExperimentPlan:
        plan = self._repository.load_experiment_plan(plan_id)
        if plan is None:
            raise KeyError(f"experiment plan not found: {plan_id}")
        return plan

    def store_compiled_experiment(
        self, compiled: StoredCompiledExperiment
    ) -> StoredCompiledExperiment:
        return self._repository.store_compiled_experiment(compiled)

    def load_compiled_experiment(
        self, experiment_id: str, plan_version: int
    ) -> StoredCompiledExperiment:
        compiled = self._repository.load_compiled_experiment(experiment_id, plan_version)
        if compiled is None:
            raise KeyError(f"compiled experiment not found: {experiment_id} version {plan_version}")
        return compiled

    def prepare_bound_experiment_submission(
        self,
        project_id: str,
        *,
        plan_id: str,
        case_id: str,
        archive_sha256: str,
    ) -> tuple[str | None, StoredExperimentPlan, StoredCompiledExperiment]:
        workflow, _ = self._load(project_id)
        existing = workflow.state.external_jobs.get(case_id)
        if existing is None:
            if workflow.state.name != "PILOT_READY":
                raise TransitionError(
                    f"pilot submission is not allowed from {workflow.state.name}"
                )
            if "GATE_2" not in workflow.state.approvals:
                raise TransitionError("GATE_2 approval is required before pilot submission")
        binding = workflow.state.approved_artifacts.get(plan_id)
        if binding is None:
            raise TransitionError(f"plan {plan_id} has no Gate 2 artifact binding")
        if binding.archive_sha256 != archive_sha256:
            raise TransitionError("submitted digest does not match the Gate 2 approved digest")
        plan = self.load_experiment_plan(plan_id)
        if plan.project_id != project_id:
            raise TransitionError("experiment plan belongs to a different project")
        compiled = self.load_compiled_experiment(plan_id, binding.plan_version)
        if compiled.archive_sha256 != binding.archive_sha256:
            raise TransitionError("stored compiled digest does not match the Gate 2 binding")
        actual_digest = f"sha256:{hashlib.sha256(compiled.archive).hexdigest()}"
        if actual_digest != binding.archive_sha256:
            raise TransitionError("stored archive bytes do not match the Gate 2 approved digest")
        return existing, plan, compiled

    def _validate_compiled_binding(
        self,
        project_id: str,
        *,
        plan_id: str,
        plan_version: int,
        archive_sha256: str,
    ) -> None:
        plan = self.load_experiment_plan(plan_id)
        if plan.project_id != project_id:
            raise TransitionError("experiment plan belongs to a different project")
        if plan.version != plan_version:
            raise TransitionError("plan version does not match the stored experiment plan")
        compiled = self.load_compiled_experiment(plan_id, plan_version)
        if compiled.archive_sha256 != archive_sha256:
            raise TransitionError("archive digest does not match the compiled experiment")

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
        question = next(
            (
                value
                for event in workflow.state.audit_events
                if isinstance((value := event.payload.get("question")), str)
            ),
            None,
        )
        return ProjectView(
            project_id=workflow.state.project_id,
            question=question,
            workflow_state=workflow.state.name,
            version=version,
            approvals=tuple(workflow.state.approvals.values()),
            approved_artifacts=workflow.state.approved_artifacts,
            external_jobs=workflow.state.external_jobs,
            audit_event_count=len(workflow.state.audit_events),
        )
