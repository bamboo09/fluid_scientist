"""Comprehensive tests for the ``fluid_scientist.session_state`` package.

Covers:
* Session creation and retrieval.
* Adding conversation turns.
* Pending patch lifecycle (set -> confirm -> clear).
* Pending patch rejection.
* Intent detection defaults (MODIFY_EXISTING_SPEC).
* Intent detection for "新建实验" -> CREATE_SPEC.
* Intent detection for "确认" with pending patch -> CONFIRM_PENDING_PATCH.
* Intent detection for "撤销" -> UNDO_LAST_PATCH.
* Intent detection for question patterns -> REQUEST_EXPLANATION.
* Intent detection for rejection with pending patch -> REJECT_PENDING_PATCH.
* Context builder produces all 11 context sections.
* Context compression preserves numerical values and units.
* Confirmed facts management.
* Conflict resolution.
* Phase transitions.
* Model trace recording.
* Session summary compression doesn't lose key data.
* Active spec set / get via VersionedSpecStore.
* Session persistence across method calls.
* No silent fallback (KeyError on missing session).
"""

from __future__ import annotations

import pytest

from fluid_scientist.session_state import (
    ConflictRecord,
    ContextBuilder,
    ConversationTurn,
    FactRecord,
    IntentDetector,
    ModelContext,
    ResearchSessionState,
    SessionManager,
    SessionPhase,
    UserIntent,
)
from fluid_scientist.session_state.models import (
    ConversationTurn as ModelsConversationTurn,
)
from fluid_scientist.spec_editing import (
    PatchOperation,
    SimulationSpecPatch,
)
from fluid_scientist.study_spec import (
    BoundaryDefinition,
    DomainSpec,
    ExecutionDefinition,
    GeometryDefinition,
    MeshDefinition,
    NumericsDefinition,
    ObservationDefinition,
    PhysicsDefinition,
    Quantity,
    SimulationStudySpec,
    SpecProvenance,
    SourcedValue,
    StudyDefinition,
    TimeControl,
    ValidationDefinition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sourced(value, unit=None, status="user_explicit", confidence=0.9):
    """Build a SourcedValue for test specs."""
    return SourcedValue(
        value=value,
        unit=unit,
        status=status,
        source_turn_ids=["turn_0"],
        confidence=confidence,
    )


def _make_spec(spec_id="spec_001", session_id="session_test", version=1):
    """Build a minimal but fully-populated SimulationStudySpec."""
    return SimulationStudySpec(
        spec_id=spec_id,
        session_id=session_id,
        version=version,
        study=StudyDefinition(
            title="Cylinder Flow Study",
            objective="Study vortex shedding behind a cylinder.",
            research_questions=["What is the Strouhal number?"],
        ),
        physics=PhysicsDefinition(
            material=_sourced("water"),
            density=_sourced(998.0, "kg/m^3"),
            kinematic_viscosity=_sourced(1.0e-6, "m^2/s"),
            reynolds_number=_sourced(150.0),
        ),
        geometry=GeometryDefinition(
            domain=DomainSpec(
                length=_sourced(10.0, "m"),
                width=_sourced(5.0, "m"),
                dimensions="2d",
            ),
        ),
        boundaries=BoundaryDefinition(conditions=[]),
        numerics=NumericsDefinition(
            time=TimeControl(
                mode="transient",
                start_time=Quantity(value=0.0, unit="s"),
                end_time=Quantity(value=10.0, unit="s"),
                delta_t=Quantity(value=0.01, unit="s"),
            ),
            solver="pimpleFoam",
        ),
        mesh=MeshDefinition(
            resolution=_sourced(50000),
            mesh_type="blockMesh",
        ),
        observations=ObservationDefinition(),
        execution=ExecutionDefinition(target_id="local"),
        validation=ValidationDefinition(),
        provenance=SpecProvenance(
            created_at="2026-01-01T00:00:00+00:00",
            created_by="test",
        ),
    )


def _make_patch(
    patch_id="patch_001",
    session_id="session_test",
    base_spec_id="spec_001",
    base_version=1,
    intent="modify_existing_spec",
):
    """Build a minimal SimulationSpecPatch for testing."""
    return SimulationSpecPatch(
        patch_id=patch_id,
        session_id=session_id,
        base_spec_id=base_spec_id,
        base_version=base_version,
        intent=intent,
        operations=[
            PatchOperation(
                op="replace",
                path="/numerics/time/end_time",
                value=15.0,
                source_quote="仿真时间设为15秒",
            ),
        ],
        assistant_message="I will set the end time to 15 seconds.",
    )


def _make_turn(
    turn_id="turn_1",
    user_message="你好",
    intent="modify_existing_spec",
):
    """Build a ConversationTurn for testing."""
    return ConversationTurn(
        turn_id=turn_id,
        timestamp="2026-01-01T00:00:00+00:00",
        user_message=user_message,
        assistant_message="OK",
        intent=intent,
    )


# ---------------------------------------------------------------------------
# Session creation and retrieval
# ---------------------------------------------------------------------------


class TestSessionCreation:
    """Tests for session creation and retrieval."""

    def test_create_session_returns_valid_state(self):
        manager = SessionManager()
        session = manager.create_session("project_alpha")

        assert session.session_id.startswith("session_")
        assert session.project_id == "project_alpha"
        assert session.active_spec_id == ""
        assert session.active_spec_version == 0
        assert session.turns == []
        assert session.compact_summary == ""
        assert session.confirmed_facts == []
        assert session.unresolved_conflicts == []
        assert session.pending_patch is None
        assert session.patch_history == []
        assert session.model_trace_ids == []
        assert session.current_phase == SessionPhase.UNDERSTANDING
        assert session.created_at != ""
        assert session.last_active_at != ""

    def test_create_session_generates_unique_ids(self):
        manager = SessionManager()
        s1 = manager.create_session("p1")
        s2 = manager.create_session("p1")
        assert s1.session_id != s2.session_id

    def test_get_session_returns_none_for_missing(self):
        manager = SessionManager()
        assert manager.get_session("nonexistent") is None

    def test_get_session_returns_created_session(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        retrieved = manager.get_session(session.session_id)
        assert retrieved is session

    def test_sessions_persist_across_calls(self):
        """Sessions must survive across method calls (in-memory persistence)."""
        manager = SessionManager()
        session = manager.create_session("p1")
        sid = session.session_id

        # Do some unrelated calls.
        manager.create_session("p2")
        manager.create_session("p3")

        # The first session must still be there.
        retrieved = manager.get_session(sid)
        assert retrieved is not None
        assert retrieved.project_id == "p1"


# ---------------------------------------------------------------------------
# Adding turns
# ---------------------------------------------------------------------------


class TestAddingTurns:
    """Tests for adding conversation turns."""

    def test_add_turn_appends_to_turns(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        turn = _make_turn(turn_id="t1", user_message="hello")
        manager.add_turn(session.session_id, turn)

        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.turns) == 1
        assert retrieved.turns[0].turn_id == "t1"
        assert retrieved.turns[0].user_message == "hello"

    def test_add_multiple_turns_preserves_order(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        for i in range(5):
            manager.add_turn(
                session.session_id,
                _make_turn(turn_id=f"t{i}", user_message=f"msg_{i}"),
            )
        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.turns) == 5
        for i, turn in enumerate(retrieved.turns):
            assert turn.turn_id == f"t{i}"
            assert turn.user_message == f"msg_{i}"

    def test_add_turn_updates_last_active_at(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        original_time = session.last_active_at
        manager.add_turn(session.session_id, _make_turn())
        retrieved = manager.get_session(session.session_id)
        assert retrieved.last_active_at >= original_time


# ---------------------------------------------------------------------------
# Pending patch lifecycle
# ---------------------------------------------------------------------------


class TestPendingPatchLifecycle:
    """Tests for the pending patch lifecycle: set -> confirm -> clear."""

    def test_set_pending_patch(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        patch = _make_patch()
        manager.set_pending_patch(session.session_id, patch)

        retrieved = manager.get_session(session.session_id)
        assert retrieved.pending_patch is not None
        assert retrieved.pending_patch.patch_id == "patch_001"

    def test_confirm_pending_patch_returns_id(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        patch = _make_patch(patch_id="patch_confirm")
        manager.set_pending_patch(session.session_id, patch)

        result = manager.confirm_pending_patch(session.session_id)
        assert result == "patch_confirm"

    def test_confirm_adds_to_patch_history(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        patch = _make_patch(patch_id="patch_hist")
        manager.set_pending_patch(session.session_id, patch)
        manager.confirm_pending_patch(session.session_id)

        retrieved = manager.get_session(session.session_id)
        assert "patch_hist" in retrieved.patch_history

    def test_confirm_does_not_clear_pending(self):
        """confirm_pending_patch returns the id but does NOT clear."""
        manager = SessionManager()
        session = manager.create_session("p1")
        patch = _make_patch()
        manager.set_pending_patch(session.session_id, patch)
        manager.confirm_pending_patch(session.session_id)

        retrieved = manager.get_session(session.session_id)
        assert retrieved.pending_patch is not None

    def test_full_lifecycle_set_confirm_clear(self):
        """Full lifecycle: set -> confirm -> clear."""
        manager = SessionManager()
        session = manager.create_session("p1")
        patch = _make_patch(patch_id="patch_lifecycle")

        # Set
        manager.set_pending_patch(session.session_id, patch)
        assert manager.get_session(session.session_id).pending_patch is not None

        # Confirm
        patch_id = manager.confirm_pending_patch(session.session_id)
        assert patch_id == "patch_lifecycle"
        assert "patch_lifecycle" in manager.get_session(
            session.session_id
        ).patch_history

        # Clear
        manager.clear_pending_patch(session.session_id)
        assert manager.get_session(session.session_id).pending_patch is None

    def test_confirm_returns_none_without_pending(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        result = manager.confirm_pending_patch(session.session_id)
        assert result is None


# ---------------------------------------------------------------------------
# Pending patch rejection
# ---------------------------------------------------------------------------


class TestPendingPatchRejection:
    """Tests for rejecting a pending patch."""

    def test_rejection_clears_without_confirming(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        patch = _make_patch(patch_id="patch_reject")
        manager.set_pending_patch(session.session_id, patch)

        # Reject: clear without confirming.
        manager.clear_pending_patch(session.session_id)

        retrieved = manager.get_session(session.session_id)
        assert retrieved.pending_patch is None
        assert "patch_reject" not in retrieved.patch_history

    def test_rejection_does_not_add_to_history(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.set_pending_patch(
            session.session_id, _make_patch(patch_id="reject_no_hist")
        )
        manager.clear_pending_patch(session.session_id)

        retrieved = manager.get_session(session.session_id)
        assert retrieved.patch_history == []


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


class TestIntentDetection:
    """Tests for the IntentDetector."""

    def setup_method(self):
        self.detector = IntentDetector()

    def test_default_modifies_existing_spec(self):
        """Short messages default to MODIFY_EXISTING_SPEC."""
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("把入口速度改成3m/s", session)
        assert intent == UserIntent.MODIFY_EXISTING_SPEC

    def test_short_message_defaults_to_modify(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("好的，开始吧", session)
        # "好的" is in confirm patterns, but there's no pending patch,
        # so it defaults to MODIFY_EXISTING_SPEC.
        assert intent == UserIntent.MODIFY_EXISTING_SPEC

    def test_create_spec_for_new_experiment(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("新建实验", session)
        assert intent == UserIntent.CREATE_SPEC

    def test_create_spec_for_new_experiment_phrase(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("新建另一个实验", session)
        assert intent == UserIntent.CREATE_SPEC

    def test_create_spec_for_new_scheme(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("另外创建一个方案", session)
        assert intent == UserIntent.CREATE_SPEC

    def test_create_spec_for_copy_as_new(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("复制为新方案", session)
        assert intent == UserIntent.CREATE_SPEC

    def test_confirm_with_pending_patch(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.set_pending_patch(session.session_id, _make_patch())
        intent = self.detector.detect_intent("确认", session)
        assert intent == UserIntent.CONFIRM_PENDING_PATCH

    def test_confirm_can_with_pending_patch(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.set_pending_patch(session.session_id, _make_patch())
        intent = self.detector.detect_intent("可以", session)
        assert intent == UserIntent.CONFIRM_PENDING_PATCH

    def test_confirm_agree_with_pending_patch(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.set_pending_patch(session.session_id, _make_patch())
        intent = self.detector.detect_intent("同意", session)
        assert intent == UserIntent.CONFIRM_PENDING_PATCH

    def test_confirm_without_pending_defaults_to_modify(self):
        """Without a pending patch, '确认' defaults to MODIFY_EXISTING_SPEC."""
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("确认", session)
        assert intent == UserIntent.MODIFY_EXISTING_SPEC

    def test_reject_with_pending_patch(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.set_pending_patch(session.session_id, _make_patch())
        intent = self.detector.detect_intent("不", session)
        assert intent == UserIntent.REJECT_PENDING_PATCH

    def test_reject_cancel_with_pending_patch(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.set_pending_patch(session.session_id, _make_patch())
        intent = self.detector.detect_intent("取消", session)
        assert intent == UserIntent.REJECT_PENDING_PATCH

    def test_reject_refuse_with_pending_patch(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.set_pending_patch(session.session_id, _make_patch())
        intent = self.detector.detect_intent("拒绝", session)
        assert intent == UserIntent.REJECT_PENDING_PATCH

    def test_undo_last_patch(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("撤销", session)
        assert intent == UserIntent.UNDO_LAST_PATCH

    def test_undo_rollback(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("回退", session)
        assert intent == UserIntent.UNDO_LAST_PATCH

    def test_undo_english(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("undo", session)
        assert intent == UserIntent.UNDO_LAST_PATCH

    def test_request_explanation_why(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("为什么选择这个求解器", session)
        assert intent == UserIntent.REQUEST_EXPLANATION

    def test_request_explanation_how(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("怎么设置边界条件", session)
        assert intent == UserIntent.REQUEST_EXPLANATION

    def test_request_explanation_what_is(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("什么是雷诺数", session)
        assert intent == UserIntent.REQUEST_EXPLANATION

    def test_question_takes_priority_over_confirm(self):
        """A question starting with '为什么' is REQUEST_EXPLANATION even
        if it contains '确认'."""
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.set_pending_patch(session.session_id, _make_patch())
        intent = self.detector.detect_intent("为什么确认这个方案", session)
        assert intent == UserIntent.REQUEST_EXPLANATION

    def test_intent_is_not_field_level(self):
        """Intent detection is high-level only -- '把时间设为15秒' is
        MODIFY_EXISTING_SPEC, not a field-specific intent."""
        manager = SessionManager()
        session = manager.create_session("p1")
        intent = self.detector.detect_intent("把仿真时间设为15秒", session)
        assert intent == UserIntent.MODIFY_EXISTING_SPEC


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


class TestContextBuilder:
    """Tests for the ContextBuilder."""

    def test_build_context_produces_all_11_sections(self):
        """The context must have all 11 sections populated."""
        manager = SessionManager()
        session = manager.create_session("p1")

        # Add some turns, facts, and conflicts.
        manager.add_turn(session.session_id, _make_turn(
            turn_id="t1", user_message="研究圆柱绕流"
        ))
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f1",
            key="reynolds_number",
            value=150,
            source_turn_id="t1",
            confirmed=True,
        ))
        manager.add_conflict(session.session_id, ConflictRecord(
            conflict_id="c1",
            description="Inlet velocity conflicts with Re.",
            paths=["/physics/velocity", "/physics/reynolds_number"],
        ))

        builder = ContextBuilder()
        spec = _make_spec()
        spec_dict = spec.model_dump(mode="json")

        context = builder.build_context(
            session=session,
            spec=spec_dict,
            user_message="把入口速度改成3m/s",
            skills=["mesh_design", "turbulence_modeling"],
            openfoam_env={"version": "v2312", "solvers": ["pimpleFoam"]},
        )

        # 1. System role
        assert isinstance(context.system_role, str)
        assert len(context.system_role) > 0
        assert "semantic editor" in context.system_role.lower()
        assert "PROHIBITIONS" in context.system_role

        # 2. Workflow phase
        assert context.workflow_phase == "understanding"

        # 3. OpenFOAM environment
        assert context.openfoam_environment == {
            "version": "v2312",
            "solvers": ["pimpleFoam"],
        }

        # 4. Enabled skills
        assert context.enabled_skills == ["mesh_design", "turbulence_modeling"]

        # 5. Patch schema
        assert isinstance(context.patch_schema, dict)
        assert "properties" in context.patch_schema

        # 6. Current spec
        assert context.current_spec is not None
        assert context.current_spec["spec_id"] == "spec_001"

        # 7. Confirmed facts
        assert len(context.confirmed_facts) == 1
        assert context.confirmed_facts[0]["key"] == "reynolds_number"

        # 8. Unresolved conflicts
        assert len(context.unresolved_conflicts) == 1
        assert context.unresolved_conflicts[0]["conflict_id"] == "c1"

        # 9. Session summary
        assert isinstance(context.session_summary, str)
        assert len(context.session_summary) > 0

        # 10. Recent conversation
        assert len(context.recent_conversation) == 1
        assert context.recent_conversation[0]["turn_id"] == "t1"

        # 11. User message
        assert context.user_message == "把入口速度改成3m/s"

    def test_build_context_with_no_spec(self):
        """Context works when no spec exists yet (spec=None)."""
        manager = SessionManager()
        session = manager.create_session("p1")
        builder = ContextBuilder()

        context = builder.build_context(
            session=session,
            spec=None,
            user_message="我想做一个圆柱绕流仿真",
            skills=[],
            openfoam_env={},
        )
        assert context.current_spec is None
        assert context.user_message == "我想做一个圆柱绕流仿真"

    def test_build_context_with_empty_skills_and_env(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        builder = ContextBuilder()

        context = builder.build_context(
            session=session,
            spec=None,
            user_message="hello",
            skills=[],
            openfoam_env={},
        )
        assert context.enabled_skills == []
        assert context.openfoam_environment == {}

    def test_build_context_uses_stored_summary(self):
        """If compact_summary is set, it is used as the session summary."""
        manager = SessionManager()
        session = manager.create_session("p1")
        session.compact_summary = "Pre-stored summary."
        builder = ContextBuilder()

        context = builder.build_context(
            session=session,
            spec=None,
            user_message="hello",
            skills=[],
            openfoam_env={},
        )
        assert context.session_summary == "Pre-stored summary."

    def test_get_recent_turns_default_count(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        for i in range(10):
            manager.add_turn(
                session.session_id,
                _make_turn(turn_id=f"t{i}"),
            )
        builder = ContextBuilder()
        recent = builder.get_recent_turns(session)
        assert len(recent) == 5
        assert recent[0].turn_id == "t5"
        assert recent[-1].turn_id == "t9"

    def test_get_recent_turns_custom_count(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        for i in range(10):
            manager.add_turn(
                session.session_id,
                _make_turn(turn_id=f"t{i}"),
            )
        builder = ContextBuilder()
        recent = builder.get_recent_turns(session, count=3)
        assert len(recent) == 3
        assert recent[0].turn_id == "t7"

    def test_get_recent_turns_empty_session(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        builder = ContextBuilder()
        assert builder.get_recent_turns(session) == []

    def test_get_recent_turns_fewer_than_count(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_turn(session.session_id, _make_turn(turn_id="t0"))
        builder = ContextBuilder()
        recent = builder.get_recent_turns(session, count=5)
        assert len(recent) == 1


# ---------------------------------------------------------------------------
# Context compression
# ---------------------------------------------------------------------------


class TestContextCompression:
    """Tests for summary compression."""

    def test_compression_preserves_numerical_values(self):
        """Numerical values and units must appear in the summary."""
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f1",
            key="inlet_velocity",
            value={"value": 2.5, "unit": "m/s"},
            source_turn_id="t1",
            confirmed=True,
        ))
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f2",
            key="reynolds_number",
            value=10000,
            source_turn_id="t1",
            confirmed=False,
        ))

        builder = ContextBuilder()
        summary = builder.compress_summary(session)

        assert "2.5" in summary
        assert "m/s" in summary
        assert "10000" in summary
        assert "inlet_velocity" in summary
        assert "reynolds_number" in summary

    def test_compression_preserves_confirmation_status(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f1",
            key="velocity",
            value=3.0,
            source_turn_id="t1",
            confirmed=True,
        ))
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f2",
            key="density",
            value=998.0,
            source_turn_id="t1",
            confirmed=False,
        ))

        builder = ContextBuilder()
        summary = builder.compress_summary(session)

        assert "confirmed=True" in summary
        assert "confirmed=False" in summary

    def test_compression_preserves_conflicts(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_conflict(session.session_id, ConflictRecord(
            conflict_id="c1",
            description="Velocity and Re are inconsistent.",
            paths=["/physics/velocity", "/physics/reynolds_number"],
        ))

        builder = ContextBuilder()
        summary = builder.compress_summary(session)

        assert "Velocity and Re are inconsistent" in summary
        assert "/physics/velocity" in summary

    def test_compression_preserves_recent_patches(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        for i in range(3):
            manager.set_pending_patch(
                session.session_id,
                _make_patch(patch_id=f"patch_{i}"),
            )
            manager.confirm_pending_patch(session.session_id)
            manager.clear_pending_patch(session.session_id)

        builder = ContextBuilder()
        summary = builder.compress_summary(session)

        assert "patch_0" in summary
        assert "patch_1" in summary
        assert "patch_2" in summary

    def test_compression_preserves_research_objectives(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_turn(session.session_id, _make_turn(
            turn_id="t1",
            user_message="我想研究圆柱绕流的涡街脱落现象",
        ))

        builder = ContextBuilder()
        summary = builder.compress_summary(session)

        assert "圆柱绕流" in summary
        assert "涡街脱落" in summary

    def test_compression_preserves_geometry_relationships(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f1",
            key="geometry_relation",
            value="cylinder attached_to bottom_wall",
            source_turn_id="t1",
            confirmed=True,
        ))

        builder = ContextBuilder()
        summary = builder.compress_summary(session)

        assert "geometry_relation" in summary
        assert "attached_to" in summary

    def test_compression_empty_session(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        builder = ContextBuilder()
        summary = builder.compress_summary(session)
        assert summary == ""

    def test_summary_compression_does_not_lose_key_data(self):
        """Comprehensive check: all key data types are preserved."""
        manager = SessionManager()
        session = manager.create_session("p1")

        # Objective
        manager.add_turn(session.session_id, _make_turn(
            turn_id="t1",
            user_message="研究Re=150的圆柱绕流",
        ))

        # Numerical facts with units
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f1",
            key="inlet_velocity",
            value={"value": 1.0, "unit": "m/s"},
            source_turn_id="t1",
            confirmed=True,
        ))
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f2",
            key="cylinder_diameter",
            value={"value": 0.1, "unit": "m"},
            source_turn_id="t1",
            confirmed=True,
        ))

        # Conflict
        manager.add_conflict(session.session_id, ConflictRecord(
            conflict_id="c1",
            description="Mesh too coarse for Re=150",
            paths=["/mesh/resolution"],
        ))

        # Patch
        manager.set_pending_patch(
            session.session_id,
            _make_patch(patch_id="patch_key"),
        )
        manager.confirm_pending_patch(session.session_id)
        manager.clear_pending_patch(session.session_id)

        builder = ContextBuilder()
        summary = builder.compress_summary(session)

        # All key data must survive compression.
        assert "Re=150" in summary  # objective
        assert "1.0" in summary  # numerical value
        assert "m/s" in summary  # unit
        assert "0.1" in summary  # numerical value
        assert "m" in summary  # unit
        assert "Mesh too coarse" in summary  # conflict
        assert "patch_key" in summary  # recent patch
        assert "confirmed=True" in summary  # confirmation status


# ---------------------------------------------------------------------------
# Confirmed facts management
# ---------------------------------------------------------------------------


class TestConfirmedFacts:
    """Tests for confirmed fact management."""

    def test_add_fact(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        fact = FactRecord(
            fact_id="f1",
            key="velocity",
            value=3.0,
            source_turn_id="t1",
            confirmed=True,
        )
        manager.add_fact(session.session_id, fact)

        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.confirmed_facts) == 1
        assert retrieved.confirmed_facts[0].key == "velocity"
        assert retrieved.confirmed_facts[0].value == 3.0
        assert retrieved.confirmed_facts[0].confirmed is True

    def test_add_multiple_facts(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        for i in range(5):
            manager.add_fact(session.session_id, FactRecord(
                fact_id=f"f{i}",
                key=f"key_{i}",
                value=i,
                source_turn_id="t1",
                confirmed=i % 2 == 0,
            ))

        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.confirmed_facts) == 5
        for i, fact in enumerate(retrieved.confirmed_facts):
            assert fact.key == f"key_{i}"
            assert fact.value == i

    def test_fact_with_dict_value(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_fact(session.session_id, FactRecord(
            fact_id="f1",
            key="inlet_velocity",
            value={"value": 2.5, "unit": "m/s"},
            source_turn_id="t1",
            confirmed=True,
        ))

        retrieved = manager.get_session(session.session_id)
        assert isinstance(retrieved.confirmed_facts[0].value, dict)
        assert retrieved.confirmed_facts[0].value["value"] == 2.5


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


class TestConflictResolution:
    """Tests for conflict management and resolution."""

    def test_add_conflict(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        conflict = ConflictRecord(
            conflict_id="c1",
            description="Test conflict",
            paths=["/path/a", "/path/b"],
        )
        manager.add_conflict(session.session_id, conflict)

        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.unresolved_conflicts) == 1
        assert retrieved.unresolved_conflicts[0].conflict_id == "c1"
        assert retrieved.unresolved_conflicts[0].status == "unresolved"

    def test_resolve_conflict_removes_from_list(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_conflict(session.session_id, ConflictRecord(
            conflict_id="c1",
            description="Conflict 1",
            paths=["/path/a"],
        ))
        manager.add_conflict(session.session_id, ConflictRecord(
            conflict_id="c2",
            description="Conflict 2",
            paths=["/path/b"],
        ))

        manager.resolve_conflict(session.session_id, "c1", "turn_5")

        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.unresolved_conflicts) == 1
        assert retrieved.unresolved_conflicts[0].conflict_id == "c2"

    def test_resolve_nonexistent_conflict_is_noop(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_conflict(session.session_id, ConflictRecord(
            conflict_id="c1",
            description="Conflict 1",
            paths=["/path/a"],
        ))

        # Resolving a non-existent conflict should not crash.
        manager.resolve_conflict(session.session_id, "nonexistent", "t1")

        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.unresolved_conflicts) == 1

    def test_add_multiple_conflicts(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        for i in range(3):
            manager.add_conflict(session.session_id, ConflictRecord(
                conflict_id=f"c{i}",
                description=f"Conflict {i}",
                paths=[f"/path/{i}"],
            ))

        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.unresolved_conflicts) == 3


# ---------------------------------------------------------------------------
# Phase transitions
# ---------------------------------------------------------------------------


class TestPhaseTransitions:
    """Tests for session phase transitions."""

    def test_initial_phase_is_understanding(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        assert session.current_phase == SessionPhase.UNDERSTANDING

    def test_update_phase(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.update_phase(session.session_id, SessionPhase.CLARIFYING)

        retrieved = manager.get_session(session.session_id)
        assert retrieved.current_phase == SessionPhase.CLARIFYING

    def test_full_phase_progression(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        sid = session.session_id

        phases = [
            SessionPhase.UNDERSTANDING,
            SessionPhase.CLARIFYING,
            SessionPhase.DRAFT_READY,
            SessionPhase.PLAN_CONFIRMED,
            SessionPhase.COMPILED,
            SessionPhase.RUN_CONFIRMED,
            SessionPhase.RUNNING,
            SessionPhase.RESULTS_READY,
            SessionPhase.REVIEWED,
        ]

        for phase in phases:
            manager.update_phase(sid, phase)
            retrieved = manager.get_session(sid)
            assert retrieved.current_phase == phase

    def test_phase_string_values(self):
        """SessionPhase values are accessible as strings."""
        assert str(SessionPhase.UNDERSTANDING) == "understanding"
        assert str(SessionPhase.DRAFT_READY) == "draft_ready"
        assert str(SessionPhase.RESULTS_READY) == "results_ready"


# ---------------------------------------------------------------------------
# Model trace recording
# ---------------------------------------------------------------------------


class TestModelTraceRecording:
    """Tests for model trace recording."""

    def test_add_model_trace(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        manager.add_model_trace(session.session_id, "trace_001")

        retrieved = manager.get_session(session.session_id)
        assert "trace_001" in retrieved.model_trace_ids

    def test_add_multiple_traces(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        for i in range(5):
            manager.add_model_trace(session.session_id, f"trace_{i}")

        retrieved = manager.get_session(session.session_id)
        assert len(retrieved.model_trace_ids) == 5
        assert retrieved.model_trace_ids == [
            "trace_0", "trace_1", "trace_2", "trace_3", "trace_4"
        ]

    def test_trace_ids_in_turn(self):
        """Turns also carry their own trace ids."""
        manager = SessionManager()
        session = manager.create_session("p1")
        turn = ConversationTurn(
            turn_id="t1",
            timestamp="2026-01-01T00:00:00+00:00",
            user_message="hello",
            intent="modify_existing_spec",
            model_trace_ids=["trace_a", "trace_b"],
        )
        manager.add_turn(session.session_id, turn)

        retrieved = manager.get_session(session.session_id)
        assert retrieved.turns[0].model_trace_ids == ["trace_a", "trace_b"]


# ---------------------------------------------------------------------------
# Active spec management
# ---------------------------------------------------------------------------


class TestActiveSpec:
    """Tests for active spec get/set via VersionedSpecStore."""

    def test_get_active_spec_returns_none_initially(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        assert manager.get_active_spec(session.session_id) is None

    def test_set_and_get_active_spec(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        spec = _make_spec(spec_id="spec_active", session_id=session.session_id)
        manager.set_active_spec(session.session_id, spec)

        retrieved_spec = manager.get_active_spec(session.session_id)
        assert retrieved_spec is not None
        assert retrieved_spec.spec_id == "spec_active"

    def test_set_active_spec_updates_session_fields(self):
        manager = SessionManager()
        session = manager.create_session("p1")
        spec = _make_spec(spec_id="spec_update", session_id=session.session_id)
        manager.set_active_spec(session.session_id, spec)

        retrieved = manager.get_session(session.session_id)
        assert retrieved.active_spec_id == "spec_update"
        assert retrieved.active_spec_version >= 1


# ---------------------------------------------------------------------------
# No silent fallback
# ---------------------------------------------------------------------------


class TestNoSilentFallback:
    """Tests that missing sessions raise errors (no silent fallback)."""

    def test_add_turn_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.add_turn("nonexistent", _make_turn())

    def test_set_pending_patch_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.set_pending_patch("nonexistent", _make_patch())

    def test_clear_pending_patch_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.clear_pending_patch("nonexistent")

    def test_confirm_pending_patch_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.confirm_pending_patch("nonexistent")

    def test_update_phase_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.update_phase("nonexistent", SessionPhase.RUNNING)

    def test_add_fact_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.add_fact("nonexistent", FactRecord(
                fact_id="f1", key="k", value=1, source_turn_id="t1"
            ))

    def test_add_conflict_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.add_conflict("nonexistent", ConflictRecord(
                conflict_id="c1", description="d"
            ))

    def test_resolve_conflict_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.resolve_conflict("nonexistent", "c1", "t1")

    def test_add_model_trace_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.add_model_trace("nonexistent", "trace_1")

    def test_get_active_spec_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.get_active_spec("nonexistent")

    def test_set_active_spec_missing_session_raises(self):
        manager = SessionManager()
        with pytest.raises(KeyError):
            manager.set_active_spec("nonexistent", _make_spec())


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestModelValidation:
    """Tests for Pydantic model validation."""

    def test_conversation_turn_validation(self):
        turn = ConversationTurn(
            turn_id="t1",
            timestamp="2026-01-01T00:00:00+00:00",
            user_message="hello",
            intent="modify_existing_spec",
        )
        assert turn.turn_id == "t1"
        assert turn.assistant_message is None
        assert turn.patch_id is None
        assert turn.model_trace_ids == []

    def test_fact_record_validation(self):
        fact = FactRecord(
            fact_id="f1",
            key="velocity",
            value=3.0,
            source_turn_id="t1",
        )
        assert fact.confirmed is False  # default

    def test_conflict_record_validation(self):
        conflict = ConflictRecord(
            conflict_id="c1",
            description="test",
        )
        assert conflict.status == "unresolved"
        assert conflict.paths == []
        assert conflict.resolution_turn_id is None

    def test_research_session_state_defaults(self):
        state = ResearchSessionState(
            session_id="s1",
            project_id="p1",
        )
        assert state.active_spec_id == ""
        assert state.active_spec_version == 0
        assert state.turns == []
        assert state.compact_summary == ""
        assert state.confirmed_facts == []
        assert state.unresolved_conflicts == []
        assert state.pending_patch is None
        assert state.patch_history == []
        assert state.model_trace_ids == []
        assert state.current_phase == SessionPhase.UNDERSTANDING

    def test_research_session_state_with_pending_patch(self):
        patch = _make_patch()
        state = ResearchSessionState(
            session_id="s1",
            project_id="p1",
            pending_patch=patch,
        )
        assert state.pending_patch is not None
        assert state.pending_patch.patch_id == "patch_001"

    def test_session_phase_enum_values(self):
        assert SessionPhase.UNDERSTANDING == "understanding"
        assert SessionPhase.REVIEWED == "reviewed"

    def test_user_intent_enum_values(self):
        assert UserIntent.CREATE_SPEC == "create_spec"
        assert UserIntent.MODIFY_EXISTING_SPEC == "modify_existing_spec"
        assert UserIntent.CONFIRM_PENDING_PATCH == "confirm_pending_patch"
        assert UserIntent.REJECT_PENDING_PATCH == "reject_pending_patch"
        assert UserIntent.UNDO_LAST_PATCH == "undo_last_patch"
        assert UserIntent.REQUEST_EXPLANATION == "request_explanation"
