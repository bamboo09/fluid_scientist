"""Credential-free vertical slice of the research workflow."""

from pydantic import BaseModel, ConfigDict

from fluid_scientist.analysis.core import summarize_metric
from fluid_scientist.domain.models import (
    AnalysisResult,
    ResearchReport,
    ValidationResult,
)
from fluid_scientist.orchestration.workflow import ResearchWorkflow
from fluid_scientist.ports import EvidenceRetriever, JobScheduler, LLMProvider, SimulatorAdapter
from fluid_scientist.validation.core import (
    grid_convergence_index,
    mass_imbalance_percent,
    monitor_stable,
    residuals_converged,
)


class DemoResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    workflow_state: str
    external_jobs: dict[str, str]
    audit_event_count: int
    validation: ValidationResult
    analysis: AnalysisResult
    report: ResearchReport


class ResearchService:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        evidence: EvidenceRetriever,
        simulator: SimulatorAdapter,
        scheduler: JobScheduler,
        project_id: str = "demo-project",
    ) -> None:
        self._llm = llm
        self._evidence = evidence
        self._simulator = simulator
        self._scheduler = scheduler
        self._project_id = project_id

    def run_approved_demo(self, question: str) -> DemoResearchResult:
        workflow = ResearchWorkflow(self._project_id)
        spec = self._llm.interpret(question)
        workflow.transition("INTERPRET", payload={"question": spec.question})

        workflow.approve("GATE_1", approved_by="demo-researcher", subject_version=1)
        evidence = self._evidence.retrieve(spec)
        workflow.transition(
            "RETRIEVE_EVIDENCE", payload={"evidence_count": len(evidence.items)}
        )

        plan = self._simulator.design_pilot(self._project_id, spec)
        cases = self._simulator.render_cases(self._project_id, spec, plan)
        workflow.transition("DESIGN_PILOT", payload={"case_count": len(cases)})
        workflow.approve("GATE_2", approved_by="demo-researcher", subject_version=1)
        workflow.transition("SUBMIT_PILOT")

        simulations = []
        for case in cases:
            job_id = self._scheduler.submit(case)
            workflow.record_external_job(case.case_id, job_id)
            simulations.append(self._scheduler.result(job_id))
        simulation_tuple = tuple(simulations)

        validation = self._validate(simulation_tuple)
        workflow.transition("VERIFY_PILOT", payload={"passed": True})
        workflow.transition("DESIGN_FULL")
        workflow.transition("SUBMIT_FULL")

        base_analysis = summarize_metric(
            project_id=self._project_id,
            metric_name="pressure_drop_pa",
            samples=[result.pressure_drop_pa for result in simulation_tuple],
            artifact_ids=tuple(result.artifact_id for result in simulation_tuple),
        )
        analysis = AnalysisResult(
            project_id=base_analysis.project_id,
            sample_count=base_analysis.sample_count,
            metrics={
                **base_analysis.metrics,
                "fine_grid_gci_percent": 100.0 * (1.0 - validation.mesh_independence),
                "mass_imbalance_percent": validation.mass_imbalance_percent,
            },
            observations=base_analysis.observations,
            artifact_ids=base_analysis.artifact_ids,
        )
        workflow.transition("ANALYZE")

        claims = self._llm.analyze(analysis, evidence, simulation_tuple)
        report = ResearchReport(
            project_id=self._project_id,
            title="90-degree bend Pilot: experiment results analysis and report",
            scope=(
                "Within the supplied incompressible, steady bend geometry and Fake-mode "
                "mesh sequence."
            ),
            claims=claims,
            limitations=(
                "Fake-mode values demonstrate workflow behavior and are not publishable CFD data.",
            ),
        )
        workflow.transition("REVIEW")
        if not self._llm.review(report, validation):
            raise RuntimeError("scientific review rejected the report")
        workflow.approve("GATE_3", approved_by="demo-reviewer", subject_version=1)
        workflow.transition("PUBLISH_REPORT")

        return DemoResearchResult(
            project_id=self._project_id,
            workflow_state=workflow.state.name,
            external_jobs=workflow.state.external_jobs,
            audit_event_count=len(workflow.state.audit_events),
            validation=validation,
            analysis=analysis,
            report=report,
        )

    @staticmethod
    def _validate(simulations: tuple) -> ValidationResult:
        if len(simulations) != 3:
            raise ValueError("Pilot validation requires three grid results")
        for result in simulations:
            if not residuals_converged(result.residuals, 1e-5):
                raise ValueError(f"{result.case_id} residuals did not converge")
            if not monitor_stable(result.monitor_values, relative_band=0.001):
                raise ValueError(f"{result.case_id} monitor was not stable")
        imbalances = [
            mass_imbalance_percent(item.inlet_mass_flow, item.outlet_mass_flow)
            for item in simulations
        ]
        gci = grid_convergence_index(
            [item.grid_size for item in simulations],
            [item.pressure_drop_pa for item in simulations],
        )
        worst_imbalance = max(imbalances)
        return ValidationResult(
            case_id=simulations[-1].case_id,
            iterative_convergence=1.0,
            mass_imbalance_percent=worst_imbalance,
            mass_conservation_passed=worst_imbalance <= 0.1,
            mesh_independence=max(0.0, 1.0 - gci.fine_gci_percent / 100.0),
            benchmark_agreement=0.95,
        )

