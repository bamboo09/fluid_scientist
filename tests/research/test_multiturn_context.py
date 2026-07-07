"""多轮 ResearchState 上下文注入与事实冲突检测的测试用例。"""

from __future__ import annotations

from unittest.mock import MagicMock

from fluid_scientist.research.intent_engine import IntentEngine
from fluid_scientist.research.models import (
    ExtractedFact,
    FactConflict,
)
from fluid_scientist.research.orchestrator import ResearchOrchestrator
from fluid_scientist.research.scope_engine import ScopeEngine
from fluid_scientist.research.session_store import SessionStore


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #


def _make_mock_llm_response() -> MagicMock:
    """创建一个返回有效 JSON 的 mock LLM 响应。"""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"task_type":"new_simulation",'
        '"research_objective":"研究管内层流压降",'
        '"physical_system":"internal_flow",'
        '"target_phenomena":["pressure_drop"],'
        '"comparison_dimensions":[],'
        '"explicitly_requested_metrics":["pressure_drop"],'
        '"inferred_candidate_metrics":["velocity_profile"],'
        '"confirmed_physics":{"flow_regime":"laminar","fluid":"water"},'
        '"uncertain_physics":{},'
        '"critical_unknowns":[],'
        '"assumptions":[],'
        '"confidence":0.85,'
        '"missing_critical_information":[],'
        '"ready_for_draft":true,'
        '"unsupported_reason":null}'
    )
    return mock_response


# --------------------------------------------------------------------------- #
# 1. test_llm_receives_full_context
# --------------------------------------------------------------------------- #


def test_llm_receives_full_context() -> None:
    """LLM 调用时应接收包含对话历史和已确认事实的增强消息。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_llm_response()

    engine = IntentEngine(
        llm_client=mock_client, model_name="glm-4.5", provider_name="glm"
    )

    accumulated_context = {
        "all_messages": "用户之前提到研究弯管流动",
        "research_objective": "研究管内流动的压降",
    }
    confirmed_facts = [
        ExtractedFact(
            fact_id="fact-001",
            category="material",
            key="fluid_type",
            value="water",
            confidence=0.9,
            turn_id="turn-001",
            source_text="介质是水",
        ),
        ExtractedFact(
            fact_id="fact-002",
            category="geometry",
            key="physical_system",
            value="internal_flow",
            confidence=0.8,
            turn_id="turn-001",
            source_text="弯管",
        ),
    ]

    engine.assess_intent(
        user_message="现在改成空气",
        accumulated_context=accumulated_context,
        confirmed_facts=confirmed_facts,
    )

    # 验证 LLM 被调用
    assert mock_client.chat.completions.create.called

    # 获取调用参数
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]

    # 验证消息结构
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"

    user_content = messages[1]["content"]

    # 验证对话历史被注入
    assert "[对话历史]" in user_content
    assert "用户之前提到研究弯管流动" in user_content

    # 验证已确认事实被注入
    assert "[已确认事实]" in user_content
    assert "material/fluid_type: water" in user_content
    assert "geometry/physical_system: internal_flow" in user_content

    # 验证当前用户消息被注入
    assert "[当前用户消息]" in user_content
    assert "现在改成空气" in user_content


# --------------------------------------------------------------------------- #
# 2. test_fact_conflict_detection
# --------------------------------------------------------------------------- #


def test_fact_conflict_detection() -> None:
    """相同 (category, key) 但不同 value 的事实应检测到冲突。"""
    existing_fact = ExtractedFact(
        fact_id="fact-old",
        category="material",
        key="fluid_type",
        value="water",
        confidence=0.9,
        turn_id="turn-001",
        source_text="介质是水",
    )
    new_fact = ExtractedFact(
        fact_id="fact-new",
        category="material",
        key="fluid_type",
        value="air",
        confidence=0.9,
        turn_id="turn-002",
        source_text="改成空气",
    )

    conflicts = ResearchOrchestrator._detect_fact_conflicts(
        [existing_fact], [new_fact]
    )

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert isinstance(conflict, FactConflict)
    assert conflict.category == "material"
    assert conflict.key == "fluid_type"
    assert conflict.old_value == "water"
    assert conflict.new_value == "air"
    assert conflict.old_turn_id == "turn-001"
    assert conflict.new_turn_id == "turn-002"
    assert conflict.resolution == "new_value_wins"


def test_no_conflict_when_values_same() -> None:
    """相同 (category, key) 且相同 value 的事实不应产生冲突。"""
    existing_fact = ExtractedFact(
        fact_id="fact-old",
        category="material",
        key="fluid_type",
        value="water",
        confidence=0.9,
        turn_id="turn-001",
    )
    new_fact = ExtractedFact(
        fact_id="fact-new",
        category="material",
        key="fluid_type",
        value="water",
        confidence=0.9,
        turn_id="turn-002",
    )

    conflicts = ResearchOrchestrator._detect_fact_conflicts(
        [existing_fact], [new_fact]
    )

    assert len(conflicts) == 0


def test_no_conflict_when_key_different() -> None:
    """不同 (category, key) 的事实不应产生冲突。"""
    existing_fact = ExtractedFact(
        fact_id="fact-old",
        category="material",
        key="fluid_type",
        value="water",
        confidence=0.9,
        turn_id="turn-001",
    )
    new_fact = ExtractedFact(
        fact_id="fact-new",
        category="operating_condition",
        key="flow_regime",
        value="laminar",
        confidence=0.9,
        turn_id="turn-002",
    )

    conflicts = ResearchOrchestrator._detect_fact_conflicts(
        [existing_fact], [new_fact]
    )

    assert len(conflicts) == 0


# --------------------------------------------------------------------------- #
# 3. test_multiturn_session_context_injection
# --------------------------------------------------------------------------- #


def test_multiturn_session_context_injection() -> None:
    """多轮会话中，第二轮改变流体介质应检测到事实冲突并存储。"""
    store = SessionStore()
    intent_engine = IntentEngine()  # fake 模式
    scope_engine = ScopeEngine()
    orchestrator = ResearchOrchestrator(store, intent_engine, scope_engine)

    # 第一轮：介质是水
    result1 = orchestrator.start_session(
        "proj-conflict", "研究弯管流动，介质是水"
    )
    session_id = result1.session_id

    # 验证第一轮后没有冲突
    session1 = store.get(session_id)
    conflicts1 = session1.accumulated_context.get("fact_conflicts", [])
    assert len(conflicts1) == 0

    # 验证第一轮提取了 fluid_type=water
    water_facts = [
        f for f in session1.confirmed_facts
        if f.category == "material" and f.key == "fluid_type"
    ]
    assert len(water_facts) == 1
    assert water_facts[0].value == "water"

    # 第二轮：改成空气
    result2 = orchestrator.handle_turn(session_id, "介质改成空气")

    # 验证冲突被检测并存储
    session2 = store.get(session_id)
    conflicts2 = session2.accumulated_context.get("fact_conflicts", [])
    assert len(conflicts2) >= 1

    # 找到 fluid_type 的冲突
    fluid_conflicts = [
        c for c in conflicts2
        if c.category == "material" and c.key == "fluid_type"
    ]
    assert len(fluid_conflicts) == 1
    conflict = fluid_conflicts[0]
    assert conflict.old_value == "water"
    assert conflict.new_value == "air"

    # 验证合并后的事实使用新值
    current_fluid_facts = [
        f for f in session2.confirmed_facts
        if f.category == "material" and f.key == "fluid_type"
    ]
    assert len(current_fluid_facts) == 1
    assert current_fluid_facts[0].value == "air"


# --------------------------------------------------------------------------- #
# 4. test_confirmed_facts_passed_to_llm
# --------------------------------------------------------------------------- #


def test_confirmed_facts_passed_to_llm() -> None:
    """会话中的 confirmed_facts 应被包含在 LLM 调用的消息中。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_llm_response()

    engine = IntentEngine(
        llm_client=mock_client, model_name="glm-4.5", provider_name="glm"
    )

    confirmed_facts = [
        ExtractedFact(
            fact_id="fact-001",
            category="operating_condition",
            key="flow_regime",
            value="laminar",
            confidence=0.9,
            turn_id="turn-001",
        ),
        ExtractedFact(
            fact_id="fact-002",
            category="material",
            key="fluid_type",
            value="water",
            confidence=0.9,
            turn_id="turn-001",
        ),
    ]

    engine.assess_intent(
        user_message="研究压降",
        accumulated_context={"all_messages": "之前讨论了弯管流动"},
        confirmed_facts=confirmed_facts,
    )

    # 验证 LLM 被调用
    assert mock_client.chat.completions.create.called

    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_content = messages[1]["content"]

    # 验证每个 confirmed_fact 都出现在 LLM 消息中
    assert "operating_condition/flow_regime: laminar" in user_content
    assert "material/fluid_type: water" in user_content

    # 验证置信度和 turn_id 也被包含
    assert "confidence: 0.9" in user_content
    assert "turn: turn-001" in user_content


def test_empty_context_passes_plain_message() -> None:
    """没有历史和事实时，LLM 应收到原始用户消息。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_mock_llm_response()

    engine = IntentEngine(
        llm_client=mock_client, model_name="glm-4.5", provider_name="glm"
    )

    engine.assess_intent(
        user_message="研究弯管流动",
        accumulated_context={},
        confirmed_facts=[],
    )

    assert mock_client.chat.completions.create.called
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    user_content = messages[1]["content"]

    # 没有上下文时，用户消息应为原始消息（无增强前缀）
    assert user_content == "研究弯管流动"
    assert "[对话历史]" not in user_content
    assert "[已确认事实]" not in user_content
