"""ResearchContext provenance 的测试用例（Commit 3）。

验证多轮研究上下文的 turn_id 追踪、参数溯源和语义冲突检测。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    Criticality,
    ExperimentSpec,
    ParameterConstraints,
    ParameterDependency,
    ParameterProvenance,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
    ResearchSpec,
)
from fluid_scientist.research.conflict_detector import (
    ConflictDetector,
    ParameterConflict,
)
from fluid_scientist.research.intent_engine import IntentEngine
from fluid_scientist.research.models import (
    ConfirmedFact,
    ExtractedFact,
    ResearchContext,
    ResearchSession,
)
from fluid_scientist.research.orchestrator import ResearchOrchestrator
from fluid_scientist.research.scope_engine import ScopeEngine
from fluid_scientist.research.session_store import SessionStore


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_minimal_spec() -> ExperimentSpec:
    """构造一个带有两个参数的最小 ExperimentSpec。"""
    return ExperimentSpec(
        experiment_id="exp-test-001",
        research=ResearchSpec(
            title="Test Experiment",
            objective="Test objective for validation",
        ),
        parameters=[
            ParameterSpec(
                parameter_id="diameter",
                display_name="直径",
                category="geometry",
                value=0.1,
                unit="m",
                data_type="float",
                source=ParameterSourceInfo(type=ParameterSource.USER),
                status=ParameterStatus.ACCEPTED,
                criticality=Criticality.CRITICAL,
                impact_scope=["reynolds_number", "mesh"],
                constraints=ParameterConstraints(min=0, exclusive_min=True),
                dependencies=ParameterDependency(affects=["reynolds_number"]),
                provenance=ParameterProvenance(created_by="user"),
            ),
            ParameterSpec(
                parameter_id="reynolds_number",
                display_name="Reynolds数",
                category="physics",
                value=100.0,
                data_type="float",
                source=ParameterSourceInfo(
                    type=ParameterSource.DERIVED,
                    reference="diameter, velocity, viscosity",
                ),
                status=ParameterStatus.ACCEPTED,
                criticality=Criticality.CRITICAL,
                dependencies=ParameterDependency(depends_on=["diameter"]),
            ),
        ],
    )


@pytest.fixture()
def orchestrator() -> ResearchOrchestrator:
    """创建一个 fake 模式的编排器实例。"""
    store = SessionStore()
    intent_engine = IntentEngine()  # fake 模式
    scope_engine = ScopeEngine()
    return ResearchOrchestrator(store, intent_engine, scope_engine)


@pytest.fixture()
def store(orchestrator: ResearchOrchestrator) -> SessionStore:
    """获取编排器内部的会话存储。"""
    return orchestrator._store  # noqa: SLF001


# --------------------------------------------------------------------------- #
# 1. ExtractedFact has turn_id and source_text fields
# --------------------------------------------------------------------------- #


def test_extracted_fact_has_turn_id_and_source_text() -> None:
    """ExtractedFact 应包含 turn_id 和 source_text 字段。"""
    fact = ExtractedFact(
        fact_id="fact-001",
        category="geometry",
        key="diameter",
        value="0.05",
        confidence=0.9,
        source="user_input",
        turn_id="turn-001",
        source_text="管道直径0.05米",
    )

    assert fact.turn_id == "turn-001"
    assert fact.source_text == "管道直径0.05米"
    assert fact.fact_id == "fact-001"
    assert fact.category == "geometry"
    assert fact.key == "diameter"
    assert fact.value == "0.05"


def test_extracted_fact_turn_id_defaults_to_none() -> None:
    """ExtractedFact 的 turn_id 和 source_text 默认应为 None。"""
    fact = ExtractedFact(
        fact_id="fact-002",
        category="material",
        key="fluid_type",
        value="water",
    )

    assert fact.turn_id is None
    assert fact.source_text is None


# --------------------------------------------------------------------------- #
# 2. ConfirmedFact carries turn_id and source_text
# --------------------------------------------------------------------------- #


def test_confirmed_fact_carries_turn_id_and_source_text() -> None:
    """ConfirmedFact 应携带 turn_id 和 source_text 溯源信息。"""
    now = datetime.now(UTC).isoformat()
    fact = ConfirmedFact(
        fact_id="cfact-001",
        category="operating_condition",
        key="flow_regime",
        value="laminar",
        confidence=0.95,
        source="user_input",
        turn_id="turn-002",
        source_text="流动为层流",
        confirmed_at=now,
    )

    assert fact.turn_id == "turn-002"
    assert fact.source_text == "流动为层流"
    assert fact.confirmed_at == now
    assert fact.fact_id == "cfact-001"


def test_confirmed_fact_turn_id_is_required() -> None:
    """ConfirmedFact 的 turn_id 是必填字段（非 Optional）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ConfirmedFact(
            fact_id="cfact-002",
            category="material",
            key="fluid_type",
            value="water",
            # turn_id 缺失，应抛出 ValidationError
        )


# --------------------------------------------------------------------------- #
# 3. ResearchContext accumulates facts across turns
# --------------------------------------------------------------------------- #


def test_research_context_accumulates_facts() -> None:
    """ResearchContext 应能累积多个 ConfirmedFact。"""
    now = datetime.now(UTC).isoformat()
    fact1 = ConfirmedFact(
        fact_id="cfact-001",
        category="geometry",
        key="physical_system",
        value="internal_flow",
        turn_id="turn-001",
        source_text="研究管内流动",
        confirmed_at=now,
    )
    fact2 = ConfirmedFact(
        fact_id="cfact-002",
        category="material",
        key="fluid_type",
        value="water",
        turn_id="turn-002",
        source_text="介质是水",
        confirmed_at=now,
    )

    context = ResearchContext(
        original_request="研究管内流动",
        clarified_objective="研究管内流动的压降",
        user_questions=["管径是多少？"],
        user_answers=["研究管内流动", "介质是水"],
        confirmed_facts=[fact1, fact2],
        source_turn_ids=["turn-001", "turn-002"],
    )

    assert len(context.confirmed_facts) == 2
    assert context.confirmed_facts[0].turn_id == "turn-001"
    assert context.confirmed_facts[1].turn_id == "turn-002"
    assert len(context.user_answers) == 2
    assert len(context.source_turn_ids) == 2


# --------------------------------------------------------------------------- #
# 4. ParameterProvenance includes source_type, turn_id, research_session_id, source_text
# --------------------------------------------------------------------------- #


def test_parameter_provenance_has_new_fields() -> None:
    """ParameterProvenance 应包含 source_type, turn_id, research_session_id, source_text。"""
    provenance = ParameterProvenance(
        created_by="user",
        source_type="user",
        research_session_id="session-001",
        turn_id="turn-001",
        source_text="管道直径0.05米",
    )

    assert provenance.source_type == "user"
    assert provenance.research_session_id == "session-001"
    assert provenance.turn_id == "turn-001"
    assert provenance.source_text == "管道直径0.05米"
    assert provenance.modification_history == []


def test_parameter_provenance_defaults() -> None:
    """ParameterProvenance 新字段应有合理默认值。"""
    provenance = ParameterProvenance()

    assert provenance.source_type == "unknown"
    assert provenance.research_session_id is None
    assert provenance.turn_id is None
    assert provenance.source_text is None
    assert provenance.modification_history == []


def test_parameter_provenance_source_type_literal() -> None:
    """source_type 应接受所有合法值。"""
    valid_values = [
        "user",
        "derived",
        "system_recommended",
        "template_default",
        "literature",
        "generated_by_code",
        "unknown",
    ]
    for val in valid_values:
        p = ParameterProvenance(source_type=val)
        assert p.source_type == val


# --------------------------------------------------------------------------- #
# 5. ConflictDetector detects Reynolds number vs velocity conflict
# --------------------------------------------------------------------------- #


def test_conflict_detector_detects_reynolds_velocity_conflict() -> None:
    """ConflictDetector 应检测 Reynolds 数与速度不一致的冲突。"""
    detector = ConflictDetector()

    # Re=100, U=2 m/s, D=0.05, nu=1e-6
    # 计算得 Re = 2 * 0.05 / 1e-6 = 100000，与 100 严重不符
    parameters = {
        "reynolds_number": 100.0,
        "inlet_velocity": 2.0,
        "diameter": 0.05,
        "kinematic_viscosity": 1e-6,
    }

    conflicts = detector.detect_conflicts(parameters, changed_parameter_id="inlet_velocity")

    assert len(conflicts) >= 1
    conflict = conflicts[0]
    assert conflict.parameter_a == "reynolds_number"
    assert conflict.parameter_b == "inlet_velocity"
    assert conflict.conflict_type == "dependency_violation"
    assert conflict.resolution_suggestion is not None
    assert "Re" in conflict.description or "雷诺数" in conflict.description


# --------------------------------------------------------------------------- #
# 6. ConflictDetector returns empty list when parameters are consistent
# --------------------------------------------------------------------------- #


def test_conflict_detector_no_conflict_when_consistent() -> None:
    """参数一致时 ConflictDetector 应返回空列表。"""
    detector = ConflictDetector()

    # Re=100, U=0.002 m/s, D=0.05, nu=1e-6
    # 计算得 Re = 0.002 * 0.05 / 1e-6 = 100，与 100 一致
    parameters = {
        "reynolds_number": 100.0,
        "inlet_velocity": 0.002,
        "diameter": 0.05,
        "kinematic_viscosity": 1e-6,
    }

    conflicts = detector.detect_conflicts(parameters)
    assert len(conflicts) == 0


def test_conflict_detector_empty_parameters() -> None:
    """空参数字典应返回空冲突列表。"""
    detector = ConflictDetector()
    conflicts = detector.detect_conflicts({})
    assert len(conflicts) == 0


def test_conflict_detector_missing_dependency() -> None:
    """只有 Reynolds 数没有速度时不应报冲突。"""
    detector = ConflictDetector()
    conflicts = detector.detect_conflicts({"reynolds_number": 100.0})
    assert len(conflicts) == 0


def test_conflict_detector_mass_flow_conflict() -> None:
    """ConflictDetector 应检测质量流量与速度不一致。"""
    detector = ConflictDetector()

    # m_dot=0.1, U=2 m/s, rho=1000, D=0.05
    # A = pi * (0.025)^2 = 0.001963...
    # calc m_dot = 1000 * 2 * 0.001963 = 3.927，与 0.1 严重不符
    parameters = {
        "mass_flow_rate": 0.1,
        "inlet_velocity": 2.0,
        "density": 1000.0,
        "diameter": 0.05,
    }

    conflicts = detector.detect_conflicts(parameters, changed_parameter_id="mass_flow_rate")
    assert len(conflicts) >= 1
    conflict = conflicts[0]
    assert conflict.parameter_a == "mass_flow_rate"
    assert conflict.parameter_b == "inlet_velocity"


# --------------------------------------------------------------------------- #
# 7. ExperimentSpec.update_parameter records modification history
# --------------------------------------------------------------------------- #


def test_update_parameter_records_modification_history() -> None:
    """update_parameter 应在 provenance.modification_history 中记录修改。"""
    spec = make_minimal_spec()
    original_value = spec.get_parameter("diameter").value
    assert original_value == 0.1

    # 修改参数
    updated_spec = spec.update_parameter("diameter", 0.2)

    updated_param = updated_spec.get_parameter("diameter")
    assert updated_param.value == 0.2
    assert updated_param.status == ParameterStatus.MODIFIED

    # 验证 modification_history
    history = updated_param.provenance.modification_history
    assert len(history) == 1
    assert history[0]["old_value"] == 0.1
    assert history[0]["new_value"] == 0.2
    assert "modified_at" in history[0]


def test_update_parameter_multiple_modifications() -> None:
    """多次修改应累积 modification_history。"""
    spec = make_minimal_spec()

    # 第一次修改
    spec = spec.update_parameter("diameter", 0.15)
    assert len(spec.get_parameter("diameter").provenance.modification_history) == 1

    # 第二次修改
    spec = spec.update_parameter("diameter", 0.2)
    history = spec.get_parameter("diameter").provenance.modification_history
    assert len(history) == 2
    assert history[0]["old_value"] == 0.1
    assert history[0]["new_value"] == 0.15
    assert history[1]["old_value"] == 0.15
    assert history[1]["new_value"] == 0.2


def test_update_parameter_last_modified_by() -> None:
    """update_parameter 应设置 last_modified_by 为 user。"""
    spec = make_minimal_spec()
    updated_spec = spec.update_parameter("diameter", 0.2)

    updated_param = updated_spec.get_parameter("diameter")
    assert updated_param.provenance.last_modified_by == "user"


def test_update_parameter_raises_keyerror_for_unknown() -> None:
    """修改不存在的参数应抛出 KeyError。"""
    spec = make_minimal_spec()
    with pytest.raises(KeyError):
        spec.update_parameter("nonexistent_param", 1.0)


# --------------------------------------------------------------------------- #
# 8. Multi-turn context preserves facts from earlier turns
# --------------------------------------------------------------------------- #


def test_multi_turn_context_preserves_earlier_facts(
    orchestrator: ResearchOrchestrator,
    store: SessionStore,
) -> None:
    """多轮对话中 ResearchContext 应保留来自早期轮次的事实。"""
    # 第一轮：模糊请求
    result1 = orchestrator.start_session("proj-ctx-001", "研究弯管流动")
    session_id = result1.session_id

    # 检查第一轮后的 research_context
    session1 = store.get(session_id)
    assert session1.research_context is not None
    rc1 = session1.research_context
    assert rc1.original_request == "研究弯管流动"
    assert len(rc1.user_answers) == 1
    assert rc1.user_answers[0] == "研究弯管流动"
    assert len(rc1.source_turn_ids) == 1

    # 第一轮至少应有 physical_system 事实
    fact_keys_turn1 = {f.key for f in rc1.confirmed_facts}
    assert "physical_system" in fact_keys_turn1

    # 记录第一轮的 turn_id
    turn1_ids = set(rc1.source_turn_ids)

    # 第二轮：补充更多信息
    orchestrator.handle_turn(session_id, "我想看压降，层流，介质是水")

    # 检查第二轮后的 research_context
    session2 = store.get(session_id)
    assert session2.research_context is not None
    rc2 = session2.research_context

    # 应有两个 user_answers（两轮各一个）
    assert len(rc2.user_answers) == 2
    assert rc2.user_answers[0] == "研究弯管流动"
    assert rc2.user_answers[1] == "我想看压降，层流，介质是水"

    # 应有两个 turn_ids
    assert len(rc2.source_turn_ids) == 2
    # 第一轮的 turn_id 应保留
    assert turn1_ids.issubset(set(rc2.source_turn_ids))

    # 应保留第一轮的 physical_system 事实
    fact_keys_turn2 = {f.key for f in rc2.confirmed_facts}
    assert "physical_system" in fact_keys_turn2

    # 应有新增事实（如 flow_regime, fluid_type）
    assert "flow_regime" in fact_keys_turn2 or "fluid_type" in fact_keys_turn2

    # 第三轮：继续补充
    orchestrator.handle_turn(
        session_id, "管道直径0.05米，研究管内流动的压降特性"
    )

    # 检查第三轮后的 research_context
    session3 = store.get(session_id)
    assert session3.research_context is not None
    rc3 = session3.research_context

    # 应有三个 user_answers
    assert len(rc3.user_answers) == 3
    assert rc3.user_answers[0] == "研究弯管流动"
    assert rc3.user_answers[1] == "我想看压降，层流，介质是水"
    assert rc3.user_answers[2] == "管道直径0.05米，研究管内流动的压降特性"

    # 应有三个 turn_ids
    assert len(rc3.source_turn_ids) == 3

    # 早期轮次的事实应仍然存在
    fact_keys_turn3 = {f.key for f in rc3.confirmed_facts}
    assert "physical_system" in fact_keys_turn3


def test_research_context_confirmed_facts_have_turn_ids(
    orchestrator: ResearchOrchestrator,
    store: SessionStore,
) -> None:
    """ResearchContext 中的 ConfirmedFact 应携带 turn_id。"""
    result = orchestrator.start_session("proj-ctx-002", "研究弯管流动的压降，层流")

    session = store.get(result.session_id)
    assert session.research_context is not None
    rc = session.research_context

    # 每个 ConfirmedFact 应有非空 turn_id
    for fact in rc.confirmed_facts:
        assert fact.turn_id is not None
        assert len(fact.turn_id) > 0
        assert fact.source_text is not None


def test_research_context_confirmed_facts_have_source_text(
    orchestrator: ResearchOrchestrator,
    store: SessionStore,
) -> None:
    """ResearchContext 中的 ConfirmedFact 应携带 source_text。"""
    message = "研究弯管流动的压降，层流，介质是水"
    result = orchestrator.start_session("proj-ctx-003", message)

    session = store.get(result.session_id)
    assert session.research_context is not None
    rc = session.research_context

    # 每个 ConfirmedFact 的 source_text 应等于用户消息
    for fact in rc.confirmed_facts:
        assert fact.source_text == message
