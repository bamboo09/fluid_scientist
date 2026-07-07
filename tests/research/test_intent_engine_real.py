"""Test real IntentEngine with LLM mocking."""

from unittest.mock import MagicMock

from fluid_scientist.research.intent_engine import IntentEngine


def test_fake_mode_marks_fallback():
    """IntentEngine without LLM client should mark fallback_used=True."""
    engine = IntentEngine()
    assert not engine.is_real_mode
    result = engine.assess_intent("研究弯管流动", {}, [])
    assert result.fallback_used is True
    assert result.fallback_reason is not None


def test_real_mode_with_mock_llm():
    """IntentEngine with mock LLM should parse structured output."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"task_type":"new_simulation",'
        '"research_objective":"研究圆管层流压降",'
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
    mock_client.chat.completions.create.return_value = mock_response

    engine = IntentEngine(
        llm_client=mock_client, model_name="glm-4.5", provider_name="glm"
    )
    assert engine.is_real_mode
    result = engine.assess_intent("研究层流圆管压降，流体是水", {}, [])
    assert result.fallback_used is False
    assert result.research_objective is not None
    assert result.ready_for_draft is True


def test_real_mode_fallback_on_llm_error():
    """IntentEngine should fallback to rules when LLM call fails."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("Network error")

    engine = IntentEngine(
        llm_client=mock_client, model_name="glm-4.5", provider_name="glm"
    )
    result = engine.assess_intent("研究弯管流动", {}, [])
    assert result.fallback_used is True
    assert "LLM call failed" in result.fallback_reason


def test_real_mode_fallback_on_validation_error():
    """IntentEngine should fallback when LLM returns invalid JSON."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"invalid": "json"}'
    mock_client.chat.completions.create.return_value = mock_response

    engine = IntentEngine(
        llm_client=mock_client, model_name="glm-4.5", provider_name="glm"
    )
    result = engine.assess_intent("研究弯管流动", {}, [])
    assert result.fallback_used is True
    assert (
        "validation" in result.fallback_reason.lower()
        or "LLM" in result.fallback_reason
    )
