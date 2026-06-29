"""Deterministic fake adapters for CI, demos, and contract tests."""

from datetime import UTC, datetime

from fluid_scientist.domain.models import (
    AnalysisResult,
    CaseManifest,
    EvidenceItem,
    EvidenceLinkedClaim,
    EvidencePackage,
    ExperimentPlan,
    FluidSpec,
    GeometrySpec,
    ResearchReport,
    ResearchSpec,
    ValidationResult,
    VariableRange,
)
from fluid_scientist.ports import SimulationResult
from fluid_scientist.services.research import ResearchService


class FakeLLMProvider:
    def interpret(self, question: str) -> ResearchSpec:
        return ResearchSpec(
            question=question,
            geometry=GeometrySpec(type="bend_90", diameter_m=0.2, curvature_ratio=2.0),
            fluid=FluidSpec(),
            independent_variables=(
                VariableRange(
                    name="reynolds_number", minimum=10_000, maximum=100_000, scale="log"
                ),
                VariableRange(name="curvature_ratio", minimum=1.0, maximum=5.0),
            ),
            responses=("pressure_drop", "secondary_flow_intensity"),
        )

    def analyze(
        self,
        analysis: AnalysisResult,
        evidence: EvidencePackage,
        simulations: tuple[SimulationResult, ...],
    ) -> tuple[EvidenceLinkedClaim, ...]:
        mean_value = analysis.metrics["pressure_drop_pa_mean"]
        return (
            EvidenceLinkedClaim(
                text=f"The three-grid Pilot mean pressure drop is {mean_value:.1f} Pa.",
                evidence_ids=(
                    "analysis:pressure_drop_pa_mean",
                    *(result.artifact_id for result in simulations),
                ),
                level="statistical_inference",
            ),
            EvidenceLinkedClaim(
                text="The fine-grid result is supported by a traceable simulation artifact.",
                evidence_ids=("simulation:bend-fine", evidence.items[0].evidence_id),
                level="direct_observation",
            ),
        )

    def review(self, report: ResearchReport, validation: ValidationResult) -> bool:
        return validation.mass_conservation_passed and all(
            claim.evidence_ids for claim in report.claims
        )


class FakeEvidenceRetriever:
    def retrieve(self, spec: ResearchSpec) -> EvidencePackage:
        return EvidencePackage(
            query=f"90 degree bend validation {spec.fluid.name}",
            items=(
                EvidenceItem(
                    evidence_id="paper:benchmark:page-6",
                    source_id="benchmark-paper",
                    locator="page 6, velocity-profile figure",
                    excerpt="Benchmark evidence is represented by a reviewed Fake-mode fixture.",
                    confidence=0.99,
                    reviewed=True,
                ),
            ),
            coverage={"physical_mechanism": True, "validation_data": True},
        )


class FakeSimulatorAdapter:
    _values = {
        "bend-coarse": (0.1, 100.0),
        "bend-medium": (0.05, 110.0),
        "bend-fine": (0.025, 112.5),
    }

    def design_pilot(self, project_id: str, spec: ResearchSpec) -> ExperimentPlan:
        return ExperimentPlan(
            plan_id=f"{project_id}-pilot-v1",
            design_type="three_grid_pilot",
            pilot_case_ids=tuple(self._values),
            estimated_cpu_hours=18.0,
        )

    def render_cases(
        self, project_id: str, spec: ResearchSpec, plan: ExperimentPlan
    ) -> tuple[CaseManifest, ...]:
        return tuple(
            CaseManifest(
                case_id=case_id,
                project_id=project_id,
                version=1,
                template_id="openfoam-bend-v1",
                template_git_commit="abc1234",
                solver="simpleFoam",
                software_version="OpenFOAM-v2312",
                artifact_digest="sha256:" + index * 64,
                geometry=spec.geometry,
                physics={"reynolds_number": 50_000.0},
                resources={"cpus": 8, "memory_gb": 16, "walltime_min": 60},
                expected_outputs=("pressure_drop", "mass_balance"),
                created_at=datetime(2026, 6, 29, tzinfo=UTC),
            )
            for case_id, index in zip(plan.pilot_case_ids, ("a", "b", "c"), strict=True)
        )

    def run(self, case: CaseManifest) -> SimulationResult:
        grid_size, pressure_drop = self._values[case.case_id]
        return SimulationResult(
            case_id=case.case_id,
            grid_size=grid_size,
            pressure_drop_pa=pressure_drop,
            inlet_mass_flow=10.0,
            outlet_mass_flow=-9.995,
            residuals={"p": [1e-2, 1e-6], "U": [1e-2, 5e-6]},
            monitor_values=[pressure_drop, pressure_drop * 1.00005, pressure_drop * 0.99995],
            artifact_id=f"simulation:{case.case_id}",
        )


class FakeJobScheduler:
    def __init__(self, simulator: FakeSimulatorAdapter) -> None:
        self._simulator = simulator
        self._jobs: dict[str, CaseManifest] = {}

    def submit(self, case: CaseManifest) -> str:
        job_id = f"fake-slurm-{len(self._jobs) + 1:04d}"
        self._jobs[job_id] = case
        return job_id

    def result(self, job_id: str) -> SimulationResult:
        return self._simulator.run(self._jobs[job_id])


def build_demo_service() -> ResearchService:
    simulator = FakeSimulatorAdapter()
    return ResearchService(
        llm=FakeLLMProvider(),
        evidence=FakeEvidenceRetriever(),
        simulator=simulator,
        scheduler=FakeJobScheduler(simulator),
    )
