"""ExperimentSpecFactory 的测试用例。

验证 Dynamic Schema Engine 能正确从研究会话生成 ExperimentSpec，
包括物理规格转换、参数生成、状态初始化以及与 orchestrator 的集成。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    Compressibility,
    ConfirmationPolicy,
    Criticality,
    Dimensions,
    ExperimentSpec,
    ExperimentStatus,
    FlowRegime,
    ParameterSource,
    PhaseType,
    TaskType,
    TemporalType,
)
from fluid_scientist.research.intent_engine import IntentEngine
from fluid_scientist.research.models import (
    DraftReady,
    IntentAssessment,
    ResearchPhysicsSpec,
    ResearchSession,
)
from fluid_scientist.research.orchestrator import ResearchOrchestrator
from fluid_scientist.research.scope_engine import ScopeEngine
from fluid_scientist.research.session_store import SessionStore
from fluid_scientist.research.spec_factory import ExperimentSpecFactory

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def now() -> str:
    """当前时间的 ISO 格式字符串。"""
    return datetime.now(UTC).isoformat()


@pytest.fixture()
def session(now: str) -> ResearchSession:
    """构造一个已澄清的研究会话。"""
    return ResearchSession(
        session_id="test-session-001",
        project_id="test-proj",
        original_request="研究层流圆管内流动的压降特性",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture()
def intent() -> IntentAssessment:
    """构造一个 ready_for_draft 的意图评估。"""
    return IntentAssessment(
        task_type="new_simulation",
        research_objective="研究层流圆管内流动的压降特性",
        physical_system="internal_flow",
        requested_metrics=["pressure_drop", "velocity_profile"],
        ready_for_draft=True,
        confidence=0.8,
    )


@pytest.fixture()
def physics_spec() -> ResearchPhysicsSpec:
    """构造一个物理规格。"""
    return ResearchPhysicsSpec(
        dimensions="2D",
        phases="single_phase",
        compressibility="incompressible",
        flow_regime="laminar",
        temporal_type="steady",
    )


@pytest.fixture()
def factory() -> ExperimentSpecFactory:
    """创建 ExperimentSpecFactory 实例。"""
    return ExperimentSpecFactory()


class MockRepository:
    """用于测试的内存 mock 仓库。"""

    def __init__(self) -> None:
        self._specs: dict[str, object] = {}

    def save_experiment_spec(self, stored_spec: object) -> None:
        self._specs[stored_spec.experiment_id] = stored_spec

    def load_experiment_spec(self, experiment_id: str) -> object | None:
        return self._specs.get(experiment_id)


# --------------------------------------------------------------------------- #
# 1. test_create_spec_from_clarified_session
# --------------------------------------------------------------------------- #


def test_create_spec_from_clarified_session(
    factory: ExperimentSpecFactory,
    session: ResearchSession,
    intent: IntentAssessment,
    physics_spec: ResearchPhysicsSpec,
) -> None:
    """提供完整信息的会话能生成 ExperimentSpec。"""
    spec = factory.create_from_schema(
        session=session,
        intent=intent,
        physics_spec=physics_spec,
    )

    assert isinstance(spec, ExperimentSpec)
    assert spec.experiment_id.startswith("exp-")
    assert spec.schema_version == "1.0.0"
    assert spec.experiment_version == 1
    assert spec.task_type == TaskType.NEW_SIMULATION
    assert spec.research.title == "研究层流圆管内流动的压降特性"
    assert spec.research.objective == "研究层流圆管内流动的压降特性"
    assert spec.research.comparison_target == "internal_flow"


# --------------------------------------------------------------------------- #
# 2. test_spec_has_parameters_from_dynamic_schema
# --------------------------------------------------------------------------- #


def test_spec_has_parameters_from_dynamic_schema(
    factory: ExperimentSpecFactory,
    session: ResearchSession,
    intent: IntentAssessment,
    physics_spec: ResearchPhysicsSpec,
) -> None:
    """生成的 spec 有来自 dynamic schema 的参数。"""
    spec = factory.create_from_schema(
        session=session,
        intent=intent,
        physics_spec=physics_spec,
    )

    assert len(spec.parameters) > 0
    # 验证包含几何参数
    param_ids = {p.parameter_id for p in spec.parameters}
    assert "diameter" in param_ids
    # 验证参数有正确的 source（system_recommended）
    for param in spec.parameters:
        assert param.source.type == ParameterSource.SYSTEM_RECOMMENDED
    # 验证参数有 criticality 和 confirmation_policy
    for param in spec.parameters:
        assert param.criticality in Criticality
        assert param.confirmation_policy in ConfirmationPolicy


# --------------------------------------------------------------------------- #
# 3. test_spec_status_is_draft
# --------------------------------------------------------------------------- #


def test_spec_status_is_draft(
    factory: ExperimentSpecFactory,
    session: ResearchSession,
    intent: IntentAssessment,
    physics_spec: ResearchPhysicsSpec,
) -> None:
    """生成的 spec 状态为 draft。"""
    spec = factory.create_from_schema(
        session=session,
        intent=intent,
        physics_spec=physics_spec,
    )

    assert spec.status == ExperimentStatus.DRAFT


# --------------------------------------------------------------------------- #
# 4. test_spec_physics_converted_correctly
# --------------------------------------------------------------------------- #


def test_spec_physics_converted_correctly(
    factory: ExperimentSpecFactory,
    session: ResearchSession,
    intent: IntentAssessment,
    physics_spec: ResearchPhysicsSpec,
) -> None:
    """PhysicsSpec 正确转换。"""
    spec = factory.create_from_schema(
        session=session,
        intent=intent,
        physics_spec=physics_spec,
    )

    assert spec.physics.dimensions == Dimensions.TWO_D
    assert spec.physics.phases == PhaseType.SINGLE_PHASE
    assert spec.physics.compressibility == Compressibility.INCOMPRESSIBLE
    assert spec.physics.flow_regime == FlowRegime.LAMINAR
    assert spec.physics.temporal_type == TemporalType.STEADY
    assert spec.physics.gravity_enabled is None


# --------------------------------------------------------------------------- #
# 5. test_spec_factory_with_default_physics
# --------------------------------------------------------------------------- #


def test_spec_factory_with_default_physics(
    factory: ExperimentSpecFactory,
    session: ResearchSession,
    intent: IntentAssessment,
) -> None:
    """physics_spec 为 None 时使用默认值。"""
    spec = factory.create_from_schema(
        session=session,
        intent=intent,
        physics_spec=None,
    )

    assert spec.physics.dimensions is None
    assert spec.physics.phases is None
    assert spec.physics.compressibility is None
    assert spec.physics.flow_regime is None
    assert spec.physics.temporal_type is None


# --------------------------------------------------------------------------- #
# 6. test_spec_can_serialize_to_json
# --------------------------------------------------------------------------- #


def test_spec_can_serialize_to_json(
    factory: ExperimentSpecFactory,
    session: ResearchSession,
    intent: IntentAssessment,
    physics_spec: ResearchPhysicsSpec,
) -> None:
    """生成的 ExperimentSpec 可以正确序列化为 JSON。"""
    spec = factory.create_from_schema(
        session=session,
        intent=intent,
        physics_spec=physics_spec,
    )

    json_str = spec.model_dump_json()
    assert len(json_str) > 0
    # 验证可以从 JSON 反序列化回来
    restored = ExperimentSpec.model_validate_json(json_str)
    assert restored.experiment_id == spec.experiment_id
    assert len(restored.parameters) == len(spec.parameters)


# --------------------------------------------------------------------------- #
# 7. test_orchestrator_creates_spec_on_draft_ready
# --------------------------------------------------------------------------- #


def test_orchestrator_creates_spec_on_draft_ready() -> None:
    """orchestrator 在 ready_for_draft 时创建 spec（需要 mock repository）。"""
    store = SessionStore()
    intent_engine = IntentEngine()
    scope_engine = ScopeEngine()
    spec_factory = ExperimentSpecFactory()
    repo = MockRepository()

    orchestrator = ResearchOrchestrator(
        session_store=store,
        intent_engine=intent_engine,
        scope_engine=scope_engine,
        spec_factory=spec_factory,
        workflow_repository=repo,
    )

    detailed_message = "研究弯管流动的压降，流动为层流，介质为水，管道直径0.05米"
    result = orchestrator.start_session("test-proj", detailed_message)

    assert isinstance(result, DraftReady)
    assert result.experiment_spec_id is not None
    # 验证 spec 已保存到 mock repository
    stored = repo.load_experiment_spec(result.experiment_spec_id)
    assert stored is not None
    assert stored.experiment_id == result.experiment_spec_id


# --------------------------------------------------------------------------- #
# 8. test_orchestrator_draft_ready_has_spec_id
# --------------------------------------------------------------------------- #


def test_orchestrator_draft_ready_has_spec_id() -> None:
    """DraftReady 结果包含 experiment_spec_id。"""
    store = SessionStore()
    intent_engine = IntentEngine()
    scope_engine = ScopeEngine()
    spec_factory = ExperimentSpecFactory()
    repo = MockRepository()

    orchestrator = ResearchOrchestrator(
        session_store=store,
        intent_engine=intent_engine,
        scope_engine=scope_engine,
        spec_factory=spec_factory,
        workflow_repository=repo,
    )

    detailed_message = "研究弯管流动的压降，流动为层流，介质为水，管道直径0.05米"
    result = orchestrator.start_session("test-proj", detailed_message)

    assert isinstance(result, DraftReady)
    assert result.experiment_spec_id is not None
    assert result.experiment_spec_id.startswith("exp-")
    assert result.experiment_version == 1
    # 不应有警告（spec 生成成功）
    assert len(result.warnings) == 0


# --------------------------------------------------------------------------- #
# 9. test_orchestrator_without_factory_returns_none_spec_id
# --------------------------------------------------------------------------- #


def test_orchestrator_without_factory_returns_none_spec_id() -> None:
    """没有 spec_factory 时，DraftReady 的 experiment_spec_id 仍为 None。"""
    store = SessionStore()
    intent_engine = IntentEngine()
    scope_engine = ScopeEngine()

    orchestrator = ResearchOrchestrator(
        session_store=store,
        intent_engine=intent_engine,
        scope_engine=scope_engine,
    )

    detailed_message = "研究弯管流动的压降，流动为层流，介质为水，管道直径0.05米"
    result = orchestrator.start_session("test-proj", detailed_message)

    assert isinstance(result, DraftReady)
    assert result.experiment_spec_id is None
