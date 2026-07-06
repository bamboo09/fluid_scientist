"""ResearchSession 模块的测试用例。"""

from __future__ import annotations

import pytest

from fluid_scientist.research.intent_engine import IntentEngine
from fluid_scientist.research.models import (
    ClarificationRequired,
    DraftReady,
    IntentAssessment,
    ResearchSession,
    UnsupportedRequest,
)
from fluid_scientist.research.orchestrator import ResearchOrchestrator
from fluid_scientist.research.scope_engine import ScopeEngine
from fluid_scientist.research.session_store import SessionStore
from fluid_scientist.research.spec_factory import ExperimentSpecFactory


@pytest.fixture()
def orchestrator() -> ResearchOrchestrator:
    """创建一个 fake 模式的编排器实例。"""
    store = SessionStore()
    intent_engine = IntentEngine()  # fake 模式，无 plan_designer
    scope_engine = ScopeEngine()
    return ResearchOrchestrator(store, intent_engine, scope_engine)


@pytest.fixture()
def store(orchestrator: ResearchOrchestrator) -> SessionStore:
    """获取编排器内部的会话存储。"""
    return orchestrator._store  # noqa: SLF001


# --------------------------------------------------------------------------- #
# 1. test_create_session
# --------------------------------------------------------------------------- #


def test_create_session(orchestrator: ResearchOrchestrator) -> None:
    """创建研究会话并验证基本属性。"""
    result = orchestrator.start_session("proj-001", "研究弯管流动的压降")

    assert result.session_id is not None
    assert len(result.session_id) > 0
    # 模糊请求应触发澄清或草稿，但不应是 unsupported
    assert not isinstance(result, UnsupportedRequest)


# --------------------------------------------------------------------------- #
# 2. test_fuzzy_request_triggers_clarification
# --------------------------------------------------------------------------- #


def test_fuzzy_request_triggers_clarification(orchestrator: ResearchOrchestrator) -> None:
    """模糊请求 '研究弯管流动' 应返回 clarification_required。"""
    result = orchestrator.start_session("proj-002", "研究弯管流动")

    assert isinstance(result, ClarificationRequired)
    assert result.session_id is not None
    assert len(result.questions) > 0
    assert len(result.questions) <= 3


# --------------------------------------------------------------------------- #
# 3. test_detailed_request_produces_draft
# --------------------------------------------------------------------------- #


def test_detailed_request_produces_draft(orchestrator: ResearchOrchestrator) -> None:
    """提供足够详细的信息后应返回 draft_ready。"""
    detailed_message = "研究弯管流动的压降，流动为层流，介质为水，管道直径0.05米"
    result = orchestrator.start_session("proj-003", detailed_message)

    assert isinstance(result, DraftReady)
    assert result.session_id is not None
    assert result.experiment_spec_id is None  # Commit 3 填充
    assert result.experiment_version == 1


# --------------------------------------------------------------------------- #
# 4. test_multi_turn_clarification
# --------------------------------------------------------------------------- #


def test_multi_turn_clarification(orchestrator: ResearchOrchestrator) -> None:
    """多轮澄清流程：从模糊到详细，最终返回 draft_ready。"""
    # 第一轮：模糊请求 → 需要澄清
    result1 = orchestrator.start_session("proj-004", "研究弯管流动")
    assert isinstance(result1, ClarificationRequired)
    session_id = result1.session_id

    # 第二轮：补充部分信息，但仍不够 → 继续澄清
    result2 = orchestrator.handle_turn(session_id, "我想看压降")
    assert isinstance(result2, ClarificationRequired)

    # 第三轮：提供完整信息 → 草稿就绪
    result3 = orchestrator.handle_turn(
        session_id, "层流，介质是水，研究管内流动的压降特性"
    )
    assert isinstance(result3, DraftReady)


# --------------------------------------------------------------------------- #
# 5. test_unsupported_request
# --------------------------------------------------------------------------- #


def test_unsupported_request(orchestrator: ResearchOrchestrator) -> None:
    """不支持的请求（多相流）应返回 unsupported。"""
    result = orchestrator.start_session("proj-005", "我想研究多相流的气泡行为")

    assert isinstance(result, UnsupportedRequest)
    assert result.session_id is not None
    assert result.reason is not None
    assert "多相流" in result.reason


# --------------------------------------------------------------------------- #
# 6. test_session_persistence
# --------------------------------------------------------------------------- #


def test_session_persistence(
    orchestrator: ResearchOrchestrator,
    store: SessionStore,
) -> None:
    """会话应在 store 中持久化。"""
    result = orchestrator.start_session("proj-006", "研究弯管流动的压降")

    # 从存储中获取会话
    session = store.get(result.session_id)
    assert session.session_id == result.session_id
    assert session.project_id == "proj-006"
    assert session.original_request == "研究弯管流动的压降"

    # 列出项目下的会话
    sessions = store.list_by_project("proj-006")
    assert len(sessions) == 1
    assert sessions[0].session_id == result.session_id


# --------------------------------------------------------------------------- #
# 7. test_intent_engine_extracts_facts
# --------------------------------------------------------------------------- #


def test_intent_engine_extracts_facts() -> None:
    """意图引擎应从用户输入中提取物理系统和指标信息。"""
    engine = IntentEngine()  # fake 模式

    intent = engine.assess_intent(
        user_message="研究弯管流动的压降，流动为层流",
        accumulated_context={},
        confirmed_facts=[],
    )

    assert intent.physical_system == "internal_flow"
    assert "pressure_drop" in intent.requested_metrics
    assert intent.ready_for_draft is True
    assert intent.confidence > 0.5


# --------------------------------------------------------------------------- #
# 8. test_scope_engine_generates_questions
# --------------------------------------------------------------------------- #


def test_scope_engine_generates_questions() -> None:
    """范围引擎应针对不完整的意图生成澄清问题。"""
    from datetime import datetime
    from uuid import uuid4

    from fluid_scientist.compat import UTC

    engine = ScopeEngine()
    now = datetime.now(UTC).isoformat()

    # 构造一个不完整的意图（缺少物理系统、指标等）
    incomplete_intent = IntentAssessment(
        task_type="new_simulation",
        research_objective=None,
        physical_system=None,
        requested_metrics=[],
        confidence=0.0,
        ready_for_draft=False,
    )

    session = ResearchSession(
        session_id=uuid4().hex[:12],
        project_id="test-proj",
        original_request="模糊请求",
        created_at=now,
        updated_at=now,
    )

    needs_clarification, questions = engine.evaluate_scope(incomplete_intent, session)

    assert needs_clarification is True
    assert len(questions) > 0
    assert len(questions) <= 3


# --------------------------------------------------------------------------- #
# 9. test_clarification_questions_have_options
# --------------------------------------------------------------------------- #


def test_clarification_questions_have_options(orchestrator: ResearchOrchestrator) -> None:
    """澄清问题应包含选项列表。"""
    result = orchestrator.start_session("proj-009", "研究弯管流动")

    assert isinstance(result, ClarificationRequired)
    for question in result.questions:
        assert len(question.options) > 0
        assert question.allow_free_text is True
        assert question.question_id is not None
        assert len(question.text) > 0


# --------------------------------------------------------------------------- #
# 10. test_confirmed_facts_accumulate
# --------------------------------------------------------------------------- #


def test_confirmed_facts_accumulate(
    orchestrator: ResearchOrchestrator,
    store: SessionStore,
) -> None:
    """事实应在多轮对话中累积。"""
    # 第一轮
    result1 = orchestrator.start_session("proj-010", "研究弯管流动")
    session_id = result1.session_id
    session1 = store.get(session_id)
    facts_count_1 = len(session1.confirmed_facts)
    assert facts_count_1 > 0  # 至少有 physical_system 事实

    # 第二轮：补充更多信息
    orchestrator.handle_turn(session_id, "我想看压降，层流，介质是水")
    session2 = store.get(session_id)
    facts_count_2 = len(session2.confirmed_facts)
    assert facts_count_2 > facts_count_1  # 事实应增加

    # 验证累积的事实包含不同类别
    categories = {fact.category for fact in session2.confirmed_facts}
    assert "geometry" in categories
    assert "operating_condition" in categories or "material" in categories


# --------------------------------------------------------------------------- #
# 11. test_unknown_metric_creates_missing_capability
# --------------------------------------------------------------------------- #


class _FakeIntentEngineWithUnknownMetric(IntentEngine):
    """返回包含未知指标的意图引擎，用于测试 MissingCapability 流程。"""

    def assess_intent(  # type: ignore[override]
        self,
        user_message: str,
        accumulated_context: dict,
        confirmed_facts: list,
    ) -> IntentAssessment:
        return IntentAssessment(
            task_type="new_simulation",
            research_objective="研究管内流动的 custom_entropy_metric 指标特性",
            physical_system="internal_flow",
            requested_metrics=["custom_entropy_metric"],
            ready_for_draft=True,
            confidence=0.8,
        )


class _MockRepository:
    """用于测试的内存 mock 仓库。"""

    def __init__(self) -> None:
        self._specs: dict[str, object] = {}

    def save_experiment_spec(self, stored_spec: object) -> None:
        self._specs[stored_spec.experiment_id] = stored_spec  # type: ignore[attr-defined]

    def load_experiment_spec(self, experiment_id: str) -> object | None:
        return self._specs.get(experiment_id)


def test_unknown_metric_creates_missing_capability() -> None:
    """未知指标应被转化为 MissingCapability 并存储在会话中。"""
    from fluid_scientist.measurement.planner import MetricPlanner

    store = SessionStore()
    intent_engine = _FakeIntentEngineWithUnknownMetric()
    scope_engine = ScopeEngine()
    spec_factory = ExperimentSpecFactory()
    repo = _MockRepository()

    orchestrator = ResearchOrchestrator(
        session_store=store,
        intent_engine=intent_engine,
        scope_engine=scope_engine,
        spec_factory=spec_factory,
        workflow_repository=repo,
        metric_planner=MetricPlanner(),
    )

    # 消息中包含层流和水，以满足 ScopeEngine 的澄清条件
    message = "研究管内流动的 custom_entropy_metric，流动为层流，介质为水"
    result = orchestrator.start_session("test-proj", message)

    assert isinstance(result, UnsupportedRequest)
    assert len(result.missing_capabilities) == 1
    cap = result.missing_capabilities[0]
    assert cap.capability_id == "cap-custom_entropy_metric"
    assert cap.capability_type == "metric_algorithm"
    assert cap.severity == "blocking"
    assert cap.code_extension_allowed is True

    # 验证会话中也保存了 missing_capabilities
    session = store.get(result.session_id)
    assert len(session.missing_capabilities) == 1
    assert session.missing_capabilities[0].capability_id == "cap-custom_entropy_metric"


# --------------------------------------------------------------------------- #
# 12. test_missing_capability_blocks_draft_ready
# --------------------------------------------------------------------------- #


def test_missing_capability_blocks_draft_ready() -> None:
    """有阻塞型缺失能力时应返回 UnsupportedRequest 而非 DraftReady。"""
    from fluid_scientist.measurement.planner import MetricPlanner
    from fluid_scientist.research.models import ResearchSessionStatus

    store = SessionStore()
    intent_engine = _FakeIntentEngineWithUnknownMetric()
    scope_engine = ScopeEngine()
    spec_factory = ExperimentSpecFactory()
    repo = _MockRepository()

    orchestrator = ResearchOrchestrator(
        session_store=store,
        intent_engine=intent_engine,
        scope_engine=scope_engine,
        spec_factory=spec_factory,
        workflow_repository=repo,
        metric_planner=MetricPlanner(),
    )

    message = "研究管内流动的 custom_entropy_metric，流动为层流，介质为水"
    result = orchestrator.start_session("test-proj", message)

    # 不应是 DraftReady
    assert not isinstance(result, DraftReady)
    assert isinstance(result, UnsupportedRequest)
    assert "缺失能力" in result.reason

    # 会话状态应为 AWAITING_CODE_APPROVAL
    session = store.get(result.session_id)
    assert session.status == ResearchSessionStatus.AWAITING_CODE_APPROVAL


# --------------------------------------------------------------------------- #
# 13. test_missing_capability_triggers_code_extension
# --------------------------------------------------------------------------- #


def test_missing_capability_triggers_code_extension() -> None:
    """有 extension_registry 时应自动创建并注册 CodeExtensionSpec。"""
    from fluid_scientist.code_extension.models import ExtensionStatus
    from fluid_scientist.code_extension.registry import ExtensionRegistry
    from fluid_scientist.measurement.planner import MetricPlanner

    store = SessionStore()
    intent_engine = _FakeIntentEngineWithUnknownMetric()
    scope_engine = ScopeEngine()
    spec_factory = ExperimentSpecFactory()
    repo = _MockRepository()
    registry = ExtensionRegistry()

    orchestrator = ResearchOrchestrator(
        session_store=store,
        intent_engine=intent_engine,
        scope_engine=scope_engine,
        spec_factory=spec_factory,
        workflow_repository=repo,
        metric_planner=MetricPlanner(),
        extension_registry=registry,
    )

    message = "研究管内流动的 custom_entropy_metric，流动为层流，介质为水"
    result = orchestrator.start_session("test-proj", message)

    assert isinstance(result, UnsupportedRequest)

    # 验证 CodeExtensionSpec 已注册
    extension = registry.get("ext-cap-custom_entropy_metric")
    assert extension is not None
    assert extension.extension_type.value == "post_processing"
    assert extension.status == ExtensionStatus.DRAFT
    assert extension.language == "python"
    assert "custom_entropy_metric" in extension.name


# --------------------------------------------------------------------------- #
# 14. test_supported_metric_no_missing_capability
# --------------------------------------------------------------------------- #


def test_supported_metric_no_missing_capability() -> None:
    """标准指标（如 pressure_drop）不应产生 MissingCapability，应返回 DraftReady。"""
    from fluid_scientist.measurement.planner import MetricPlanner

    store = SessionStore()
    intent_engine = IntentEngine()  # 使用真实的 fake 模式引擎
    scope_engine = ScopeEngine()
    spec_factory = ExperimentSpecFactory()
    repo = _MockRepository()

    orchestrator = ResearchOrchestrator(
        session_store=store,
        intent_engine=intent_engine,
        scope_engine=scope_engine,
        spec_factory=spec_factory,
        workflow_repository=repo,
        metric_planner=MetricPlanner(),
    )

    # pressure_drop 是标准指标，不应触发 MissingCapability
    message = "研究弯管流动的压降，流动为层流，介质为水，管道直径0.05米"
    result = orchestrator.start_session("test-proj", message)

    assert isinstance(result, DraftReady)
    # 会话中不应有 missing_capabilities
    session = store.get(result.session_id)
    assert len(session.missing_capabilities) == 0
