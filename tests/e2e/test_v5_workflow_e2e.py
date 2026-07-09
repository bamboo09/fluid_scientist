"""End-to-end integration test for the v5 study-decomposer draft workflow.

This test exercises the complete workflow without an HTTP server, using
the service components directly:

1. User sends batch research request → StudySplitter → StudyIntent list
2. CapabilityPreChecker + PriorityRanker → ranked studies
3. Select a study → DraftGenerator → ExperimentDraft
4. DraftValidator → validation result
5. DraftChangeAgent → ChangeProposal
6. ApplyProposalExecutor → new draft version
7. Draft confirm → frozen version
8. CasePlanGenerator → CasePlan
9. NativeCaseCompiler → OpenFOAM case structure
10. MissingCapability → CodeExtensionWorkflow → CodeExtensionSpec

This mirrors the Browser E2E test (commit 16) but at the service level.
"""

from __future__ import annotations

import pytest

from fluid_scientist.case_plan.compiler import NativeCaseCompiler
from fluid_scientist.case_plan.generator import CasePlanGenerator
from fluid_scientist.code_extension.spec import CodeExtensionWorkflow
from fluid_scientist.draft.apply_executor import ApplyProposalExecutor
from fluid_scientist.draft.change_agent import DraftChangeAgent
from fluid_scientist.draft.draft_generator import DraftGenerator
from fluid_scientist.draft.models import DraftStatus
from fluid_scientist.draft.validator import DraftValidator
from fluid_scientist.draft_session.input_router import InputRouter
from fluid_scientist.draft_session.models import DraftSession, DraftSessionStatus
from fluid_scientist.draft_session.state_machine import DraftSessionStateMachine
from fluid_scientist.study_decomposition.ambiguity_detector import AmbiguityDetector
from fluid_scientist.study_decomposition.capability_checker import (
    CapabilityPreChecker,
    PriorityRanker,
)
from fluid_scientist.study_decomposition.models import (
    ExtractedParameter,
    ObservableSpec,
    StudyIntent,
)
from fluid_scientist.study_decomposition.physics_extractor import PhysicsFrameExtractor
from fluid_scientist.study_decomposition.splitter import StudySplitter

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def services():
    """Create all service instances for the E2E test."""
    return {
        "splitter": StudySplitter(),
        "extractor": PhysicsFrameExtractor(),
        "detector": AmbiguityDetector(),
        "checker": CapabilityPreChecker(),
        "ranker": PriorityRanker(),
        "draft_generator": DraftGenerator(),
        "validator": DraftValidator(),
        "change_agent": DraftChangeAgent(),
        "apply_executor": ApplyProposalExecutor(),
        "case_plan_generator": CasePlanGenerator(),
        "compiler": NativeCaseCompiler(),
        "state_machine": DraftSessionStateMachine(),
        "input_router": InputRouter(),
        "extension_workflow": CodeExtensionWorkflow(),
    }


BATCH_INPUT = """1. 近壁倾斜圆柱 Re=3900 三维湍流尾迹
2. 倾斜圆射流 45 度冲击平壁 Re=23000
3. 倾斜椭圆柱 Re=5000 非定常绕流
4. 密度分层流体中水平圆柱垂向振荡 Re=500 Fr=0.2
5. 后台阶流动 Re=5000 分离再附"""


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


class TestV5WorkflowE2E:
    """Full workflow integration test from user input to case compilation."""

    def test_batch_input_to_ranked_studies(self, services):
        """Step 1-2: Batch input → split → extract → detect → check → rank."""
        splitter = services["splitter"]
        extractor = services["extractor"]
        detector = services["detector"]
        checker = services["checker"]
        ranker = services["ranker"]

        # Split batch input
        study_texts = splitter.split(BATCH_INPUT)
        assert len(study_texts) == 5

        # Extract physics and create StudyIntents
        studies = []
        for text in study_texts:
            frame = extractor.extract(text)
            params = extractor.extract_parameters(text)
            observables = extractor.extract_observables(text)
            ics, bcs = extractor.extract_conditions(text)
            goals = extractor.extract_analysis_goals(text)

            study = StudyIntent(
                study_id=f"study_{len(studies)+1}",
                title=text[:60],
                raw_text=text,
                study_type=frame.geometry_type or "unknown",
                research_objective=text,
                geometry={
                    "type": frame.geometry_type or "unknown",
                    "inclined": frame.is_inclined,
                    "near_wall": frame.near_wall,
                },
                physical_models={
                    "dimension": frame.dimension,
                    "temporal": frame.temporal_type,
                    "turbulent": frame.flow_regime == "turbulent",
                    "inclined": frame.is_inclined,
                    "moving_body": frame.is_moving_body,
                    "thermal": frame.has_thermal,
                    "buoyancy": frame.has_buoyancy,
                    "density_stratification": frame.has_density_stratification,
                },
                initial_conditions=ics,
                boundary_conditions=bcs,
                known_parameters=params,
                observables=observables,
                analysis_goals=goals,
            )
            ambiguities = detector.detect(study)
            study.ambiguity_report = ambiguities
            studies.append(study)

        # Check capabilities
        check_results = {s.study_id: checker.check(s) for s in studies}

        # Rank
        ranked = ranker.rank(studies, check_results)

        # BFS should be priority 1 (draftable)
        bfs = next(s for s in ranked if "后台阶" in s.title)
        assert bfs.recommended_priority == 1
        assert check_results[bfs.study_id].readiness_level == "draftable"

        # Stratified should be priority 5 (not_compilable_yet)
        strat = next(s for s in ranked if "密度分层" in s.title)
        assert strat.recommended_priority == 5

    def test_backward_step_full_workflow(self, services):
        """Step 3-8: BFS study → draft → validate → confirm → case plan → compile."""
        # Create a complete BFS study intent
        study = StudyIntent(
            study_id="bfs_study",
            title="Backward facing step Re=5000",
            raw_text="后台阶流动 Re=5000 分离再附",
            study_type="backward_facing_step",
            research_objective="研究后台阶分离再附流动",
            geometry={"type": "backward_facing_step", "expansion_ratio": 2},
            physical_models={
                "dimension": "3D",
                "temporal": "transient",
                "turbulent": True,
            },
            initial_conditions=[{"type": "uniform", "field": "U", "value": [1.0, 0, 0]}],
            boundary_conditions=[
                {"type": "inlet_velocity", "boundary": "inlet"},
                {"type": "outlet_pressure", "boundary": "outlet"},
                {"type": "no_slip", "boundary": "wall"},
            ],
            known_parameters=[
                ExtractedParameter(
                    canonical_id="reynolds_number",
                    display_name="Re",
                    value=5000,
                    dimensionless=True,
                    source_text="Re=5000",
                    source="user_provided",
                    affects=["turbulence_model"],
                    confidence=0.99,
                ),
                ExtractedParameter(
                    canonical_id="step_height",
                    display_name="Step Height H",
                    value=0.05,
                    unit="m",
                    source_text="H=0.05",
                    source="user_provided",
                    affects=["geometry", "mesh"],
                    confidence=0.95,
                ),
                ExtractedParameter(
                    canonical_id="inlet_velocity",
                    display_name="Inlet Velocity U",
                    value=1.0,
                    unit="m/s",
                    source_text="U=1.0",
                    source="user_provided",
                    affects=["boundary_conditions"],
                    confidence=0.9,
                ),
                ExtractedParameter(
                    canonical_id="nu",
                    display_name="Kinematic Viscosity",
                    value=1e-5,
                    unit="m2/s",
                    source_text="",
                    source="user_provided",
                    affects=["solver"],
                    confidence=0.9,
                ),
                ExtractedParameter(
                    canonical_id="domain_length",
                    display_name="Domain Length",
                    value=1.0,
                    unit="m",
                    source_text="",
                    source="user_provided",
                    affects=["geometry"],
                    confidence=0.8,
                ),
                ExtractedParameter(
                    canonical_id="domain_height",
                    display_name="Domain Height",
                    value=0.15,
                    unit="m",
                    source_text="",
                    source="user_provided",
                    affects=["geometry"],
                    confidence=0.8,
                ),
                ExtractedParameter(
                    canonical_id="endTime",
                    display_name="End Time",
                    value=10.0,
                    unit="s",
                    source_text="",
                    source="user_provided",
                    affects=["solver"],
                    confidence=0.7,
                ),
                ExtractedParameter(
                    canonical_id="deltaT",
                    display_name="Time Step",
                    value=0.001,
                    unit="s",
                    source_text="",
                    source="user_provided",
                    affects=["solver"],
                    confidence=0.7,
                ),
            ],
            observables=[
                ObservableSpec(
                    observable_id="reattachment_length",
                    display_name="Reattachment Length",
                    category="reattachment",
                ),
            ],
            analysis_goals=["研究分离再附机理"],
        )

        # Step 3: Generate draft
        draft = services["draft_generator"].generate(study)
        assert draft.objective == study.research_objective
        assert len(draft.control_parameters) > 0
        assert draft.status == DraftStatus.DRAFT

        # Step 4: Validate
        result = services["validator"].validate(draft)
        assert result is not None

        # Step 5: Confirm draft (freeze)
        confirmed = draft.confirm()
        assert confirmed.status == DraftStatus.CONFIRMED
        assert confirmed.locked is True
        assert confirmed.is_read_only()

        # Step 6: Generate case plan
        case_plan = services["case_plan_generator"].generate(confirmed)
        assert case_plan.solver != ""
        assert len(case_plan.measurement_plan.function_objects) > 0

        # Step 7: Compile to OpenFOAM case (if can_compile)
        if case_plan.can_compile:
            case_files = services["compiler"].compile(case_plan)
            assert "system" in case_files
            assert "controlDict" in case_files["system"]
            assert "constant" in case_files
            assert "0" in case_files

    def test_draft_change_and_apply_workflow(self, services):
        """Step 5-6: Draft → change proposal → apply → new version."""
        # Create a draft
        study = StudyIntent(
            study_id="test_study",
            title="Cylinder flow",
            raw_text="圆柱绕流 Re=100",
            study_type="cylinder",
            research_objective="Cylinder cross flow",
            geometry={"type": "cylinder"},
            physical_models={"dimension": "2D", "temporal": "transient"},
            boundary_conditions=[
                {"type": "inlet_velocity", "boundary": "inlet"},
                {"type": "outlet_pressure", "boundary": "outlet"},
            ],
            known_parameters=[
                ExtractedParameter(
                    canonical_id="reynolds_number",
                    display_name="Re",
                    value=100,
                    dimensionless=True,
                    source_text="Re=100",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="cylinder_diameter",
                    display_name="D",
                    value=0.1,
                    unit="m",
                    source_text="",
                    source="user_provided",
                ),
            ],
            observables=[
                ObservableSpec(
                    observable_id="drag",
                    display_name="Drag",
                    category="force",
                ),
            ],
        )

        draft = services["draft_generator"].generate(study)
        assert draft.version == 1

        # Generate change proposal
        proposal = services["change_agent"].generate(draft, "Re=200")
        assert len(proposal.changes) > 0
        assert proposal.base_draft_version == 1

        # Apply proposal
        new_draft, result = services["apply_executor"].apply(draft, proposal)
        assert new_draft.version == 2
        assert new_draft.draft_id != draft.draft_id

        # Check parameter was updated
        re_param = next(
            p for p in new_draft.control_parameters
            if p.parameter_id == "reynolds_number"
        )
        assert re_param.value == 200.0

        # Original draft unchanged
        original_re = next(
            p for p in draft.control_parameters
            if p.parameter_id == "reynolds_number"
        )
        assert original_re.value == 100

    def test_missing_capability_to_code_extension(self, services):
        """Step 10: Missing capability → CodeExtension spec."""
        missing_cap = {
            "capability_id": "buoyancy_model_writer",
            "capability_type": "physical_model_writer",
            "reason": "密度分层需要浮力模型写入器",
            "severity": "blocking",
        }

        spec = services["extension_workflow"].create_spec(
            missing_cap, "session_001", "draft_001"
        )
        assert spec.extension_type == "physical_model_writer"
        assert spec.missing_capability_id == "buoyancy_model_writer"
        assert spec.status == "spec_draft"
        assert len(spec.safety_constraints) >= 4

    def test_session_state_transitions(self, services):
        """Test session state machine transitions through the workflow."""
        sm = services["state_machine"]
        session = DraftSession(
            session_id="test_session",
            status=DraftSessionStatus.COLLECTING_INTENT,
        )

        # collecting_intent → batch_review
        session = sm.transition(session, DraftSessionStatus.BATCH_REVIEW)
        assert session.status == DraftSessionStatus.BATCH_REVIEW

        # batch_review → draft_ready
        session = sm.transition(session, DraftSessionStatus.DRAFT_READY)
        assert session.status == DraftSessionStatus.DRAFT_READY

        # draft_ready → ready
        session = sm.transition(session, DraftSessionStatus.READY)
        assert session.status == DraftSessionStatus.READY

        # ready → confirmed
        session = sm.transition(session, DraftSessionStatus.CONFIRMED)
        assert session.status == DraftSessionStatus.CONFIRMED

        # confirmed → case_planning
        session = sm.transition(session, DraftSessionStatus.CASE_PLANNING)
        assert session.status == DraftSessionStatus.CASE_PLANNING

    def test_input_routing_through_workflow(self, services):
        """Test input router correctly routes messages based on session state."""
        router = services["input_router"]

        # New session → research request
        session = DraftSession(
            session_id="test",
            status=DraftSessionStatus.COLLECTING_INTENT,
        )
        route = router.route("研究后台阶流动 Re=5000", session)
        assert route.input_type == "new_research_request"

        # Batch input
        route = router.route(BATCH_INPUT, session)
        assert route.input_type == "batch_research_request"

        # Batch review → study selection
        session.status = DraftSessionStatus.BATCH_REVIEW
        route = router.route("选择第5个后台阶任务", session)
        assert route.input_type == "study_selection"

        # Proposal pending → confirmation
        session.status = DraftSessionStatus.PROPOSAL_PENDING
        session.pending_proposal_id = "proposal_001"
        route = router.route("确认应用修改", session)
        assert route.input_type == "proposal_confirmation"

        # Proposal pending → cancel
        route = router.route("取消修改", session)
        assert route.input_type == "proposal_cancel"

    def test_complete_happy_path(self, services):
        """Complete happy path: input → draft → change → confirm → case plan."""
        # 1. Create study
        study = StudyIntent(
            study_id="happy_path",
            title="BFS Re=5000",
            raw_text="后台阶 Re=5000",
            study_type="backward_facing_step",
            research_objective="后台阶分离再附",
            geometry={"type": "backward_facing_step"},
            physical_models={"dimension": "3D", "temporal": "transient", "turbulent": True},
            boundary_conditions=[
                {"type": "inlet_velocity", "boundary": "inlet"},
                {"type": "outlet_pressure", "boundary": "outlet"},
                {"type": "no_slip", "boundary": "wall"},
            ],
            known_parameters=[
                ExtractedParameter(
                    canonical_id="reynolds_number",
                    display_name="Re",
                    value=5000,
                    dimensionless=True,
                    source_text="Re=5000",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="step_height",
                    display_name="H",
                    value=0.05,
                    unit="m",
                    source_text="",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="inlet_velocity",
                    display_name="U",
                    value=1.0,
                    unit="m/s",
                    source_text="",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="nu",
                    display_name="nu",
                    value=1e-5,
                    unit="m2/s",
                    source_text="",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="domain_length",
                    display_name="L",
                    value=1.0,
                    unit="m",
                    source_text="",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="domain_height",
                    display_name="H_total",
                    value=0.15,
                    unit="m",
                    source_text="",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="endTime",
                    display_name="endTime",
                    value=10.0,
                    unit="s",
                    source_text="",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="deltaT",
                    display_name="deltaT",
                    value=0.001,
                    unit="s",
                    source_text="",
                    source="user_provided",
                ),
            ],
            observables=[
                ObservableSpec(
                    observable_id="reattachment_length",
                    display_name="Reattachment",
                    category="reattachment",
                ),
            ],
        )

        # 2. Generate draft
        draft = services["draft_generator"].generate(study)

        # 3. Validate
        services["validator"].validate(draft)

        # 4. Confirm
        confirmed = draft.confirm()
        assert confirmed.is_read_only()

        # 5. Generate case plan
        case_plan = services["case_plan_generator"].generate(confirmed)
        assert case_plan.case_type != ""
        assert case_plan.solver != ""

        # 6. If compilable, compile
        if case_plan.can_compile:
            case = services["compiler"].compile(case_plan)
            assert "system" in case
            assert "controlDict" in case["system"]

