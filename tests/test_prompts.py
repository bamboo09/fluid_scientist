"""Tests for the ``fluid_scientist.prompts`` package.

Covers:
* SPEC_EDITOR_SYSTEM_PROMPT contains key instructions.
* build_spec_editor_prompt assembles all context sections.
* build_user_prompt includes current spec and user message.
* CRITIC_SYSTEM_PROMPT contains check items.
* CriticResult accepts/rejects correctly.
* build_critic_prompt includes candidate patch.
* TwoCallStrategy with mock model client (accept on first try).
* TwoCallStrategy with critic rejection (retry once).
* TwoCallStrategy max retries (fail after 3 attempts).
* TwoCallStrategy model failure handling.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fluid_scientist.prompts import (
    CRITIC_SYSTEM_PROMPT,
    SPEC_EDITOR_SYSTEM_PROMPT,
    CriticResult,
    TwoCallStrategy,
    build_critic_prompt,
    build_spec_editor_prompt,
    build_user_prompt,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_current_spec() -> dict:
    """Return a minimal current spec dict for testing."""
    return {
        "schema_version": "1.0",
        "spec_id": "test_spec_001",
        "session_id": "session_001",
        "version": 3,
        "parent_version": 2,
        "study": {
            "title": "Cylinder Flow Re=100",
            "objective": "Investigate vortex shedding",
            "research_questions": [],
        },
        "physics": {"material": {"value": "water", "unit": None}},
        "numerics": {
            "time": {
                "mode": "transient",
                "start_time": {"value": 0.0, "unit": "s"},
                "end_time": {"value": 10.0, "unit": "s"},
                "delta_t": {"value": 0.01, "unit": "s"},
            },
            "solver": "icoFoam",
        },
    }


def _make_patch_schema() -> dict:
    """Return a minimal patch schema dict for testing."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SimulationSpecPatch",
        "type": "object",
        "required": ["patch_id", "operations"],
        "properties": {
            "patch_id": {"type": "string"},
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["op", "path"],
                    "properties": {
                        "op": {"type": "string"},
                        "path": {"type": "string"},
                        "value": {},
                        "source_quote": {"type": "string"},
                    },
                },
            },
        },
    }


def _make_context() -> dict:
    """Return a context dict for testing."""
    return {
        "workflow_phase": "UNDERSTANDING",
        "confirmed_facts": [
            {"fact_id": "f1", "content": "流体为水"},
        ],
        "unresolved_conflicts": [],
        "skills": [
            {"skill_id": "fluid.physics_derivation", "name": "Physics Derivation"},
        ],
        "openfoam_env": {
            "version": "Foundation 13",
            "solvers": ["icoFoam", "simpleFoam", "pimpleFoam"],
        },
    }


def _make_candidate_patch() -> dict:
    """Return a minimal candidate patch dict for testing."""
    return {
        "patch_id": "patch_001",
        "session_id": "session_001",
        "base_spec_id": "test_spec_001",
        "base_version": 3,
        "intent": "modify_existing_spec",
        "operations": [
            {
                "op": "replace",
                "path": "/numerics/time/end_time",
                "value": {"value": 15.0, "unit": "s"},
                "source_quote": "仿真时间设为15秒",
                "confidence": 0.99,
            }
        ],
        "clarifications": [],
        "impact_requests": [],
        "untouched_guarantee": True,
        "assistant_message": "已准备将仿真结束时间改为15秒。",
    }


# ---------------------------------------------------------------------------
# Tests: SPEC_EDITOR_SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSpecEditorSystemPrompt:
    """Test that SPEC_EDITOR_SYSTEM_PROMPT contains key instructions."""

    def test_contains_role_declaration(self):
        """The prompt must declare the editor role."""
        assert "结构化编辑器" in SPEC_EDITOR_SYSTEM_PROMPT
        assert "不是模板分类器" in SPEC_EDITOR_SYSTEM_PROMPT

    def test_contains_task_list(self):
        """The prompt must contain all 10 task items."""
        for keyword in [
            "创建、修改、删除、确认、拒绝、撤销还是询问",
            "最小必要 Patch",
            "保留用户没有修改的所有字段",
            "source_quote",
            "单位、相对量和几何关系",
            "clarification",
            "declare_unknown_capability",
            "不把未知形状映射为已有形状",
            "Shell",
            "JSON Schema",
        ]:
            assert keyword in SPEC_EDITOR_SYSTEM_PROMPT, (
                f"Missing key instruction: {keyword}"
            )

    def test_contains_prohibitions(self):
        """The prompt must contain prohibitions."""
        assert "禁止" in SPEC_EDITOR_SYSTEM_PROMPT
        assert "禁止重建完整方案" in SPEC_EDITOR_SYSTEM_PROMPT
        assert "禁止改变用户没有提到的字段" in SPEC_EDITOR_SYSTEM_PROMPT
        assert "禁止把未知语义映射为已有模板" in SPEC_EDITOR_SYSTEM_PROMPT

    def test_no_field_specific_logic(self):
        """The prompt must NOT contain field-specific if/else rules.

        It should not hardcode rules like 'if user says 仿真时间 then...'
        The prompt guides the model to use the generic patch schema.
        """
        # The prompt should not contain Python-like conditional logic.
        assert "if user says" not in SPEC_EDITOR_SYSTEM_PROMPT.lower()
        assert "if \"仿真时间\"" not in SPEC_EDITOR_SYSTEM_PROMPT
        # It should reference the generic patch schema, not specific fields.
        assert "SimulationSpecPatch schema" in SPEC_EDITOR_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests: build_spec_editor_prompt
# ---------------------------------------------------------------------------


class TestBuildSpecEditorPrompt:
    """Test build_spec_editor_prompt assembles all context sections."""

    def test_includes_system_prompt(self):
        """The built prompt must include the system prompt content."""
        prompt = build_spec_editor_prompt(
            context=_make_context(),
            patch_schema=_make_patch_schema(),
            current_spec=_make_current_spec(),
            user_message="仿真时间设为15秒",
            confirmed_facts=_make_context()["confirmed_facts"],
            unresolved_conflicts=[],
            skills=_make_context()["skills"],
            openfoam_env=_make_context()["openfoam_env"],
        )
        assert "结构化编辑器" in prompt

    def test_includes_all_context_sections(self):
        """The built prompt must include all context sections."""
        context = _make_context()
        current_spec = _make_current_spec()
        patch_schema = _make_patch_schema()
        skills = context["skills"]
        openfoam_env = context["openfoam_env"]
        confirmed_facts = context["confirmed_facts"]

        prompt = build_spec_editor_prompt(
            context=context,
            patch_schema=patch_schema,
            current_spec=current_spec,
            user_message="仿真时间设为15秒",
            confirmed_facts=confirmed_facts,
            unresolved_conflicts=[],
            skills=skills,
            openfoam_env=openfoam_env,
        )

        # System prompt
        assert "结构化编辑器" in prompt
        # Workflow phase
        assert "当前工作流阶段" in prompt
        assert "UNDERSTANDING" in prompt
        # OpenFOAM environment
        assert "OpenFOAM 环境" in prompt
        assert "Foundation 13" in prompt
        # Skills
        assert "专业 Skills" in prompt
        assert "fluid.physics_derivation" in prompt
        # Patch schema
        assert "SimulationSpecPatch JSON Schema" in prompt
        assert "patch_id" in prompt
        # Current spec
        assert "SimulationStudySpec" in prompt
        assert "test_spec_001" in prompt
        # Confirmed facts
        assert "已确认事实" in prompt
        assert "流体为水" in prompt
        # User message
        assert "用户本轮消息" in prompt
        assert "仿真时间设为15秒" in prompt

    def test_includes_prior_critic_feedback_on_retry(self):
        """When context has prior_critic_feedback, it should be included."""
        context = _make_context()
        context["prior_critic_feedback"] = {
            "attempt": 1,
            "violations": ["missing end_time change"],
            "required_corrections": ["add replace operation for end_time"],
        }

        prompt = build_spec_editor_prompt(
            context=context,
            patch_schema=_make_patch_schema(),
            current_spec=_make_current_spec(),
            user_message="仿真时间设为15秒",
            confirmed_facts=context["confirmed_facts"],
            unresolved_conflicts=[],
            skills=context["skills"],
            openfoam_env=context["openfoam_env"],
        )

        assert "Critic 反馈" in prompt
        assert "missing end_time change" in prompt


# ---------------------------------------------------------------------------
# Tests: build_user_prompt
# ---------------------------------------------------------------------------


class TestBuildUserPrompt:
    """Test build_user_prompt includes current spec and user message."""

    def test_includes_current_spec(self):
        """The user prompt must include the current spec."""
        current_spec = _make_current_spec()
        prompt = build_user_prompt(
            user_message="仿真时间设为15秒",
            current_spec=current_spec,
            spec_version=3,
            confirmed_facts=[],
            conflicts=[],
            skills=[],
        )
        assert "SimulationStudySpec" in prompt
        assert "test_spec_001" in prompt
        assert "版本 3" in prompt

    def test_includes_user_message(self):
        """The user prompt must include the user's message."""
        prompt = build_user_prompt(
            user_message="时间步减半",
            current_spec=_make_current_spec(),
            spec_version=1,
            confirmed_facts=[],
            conflicts=[],
            skills=[],
        )
        assert "用户本轮消息" in prompt
        assert "时间步减半" in prompt

    def test_includes_facts_and_conflicts(self):
        """The user prompt must include confirmed facts and conflicts."""
        prompt = build_user_prompt(
            user_message="test",
            current_spec=_make_current_spec(),
            spec_version=1,
            confirmed_facts=[{"fact_id": "f1", "content": "Re=100"}],
            conflicts=[{"conflict_id": "c1", "description": "unit mismatch"}],
            skills=[],
        )
        assert "已确认事实" in prompt
        assert "Re=100" in prompt
        assert "未解决冲突" in prompt
        assert "unit mismatch" in prompt


# ---------------------------------------------------------------------------
# Tests: CRITIC_SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestCriticSystemPrompt:
    """Test that CRITIC_SYSTEM_PROMPT contains check items."""

    def test_contains_role_declaration(self):
        """The prompt must declare the critic role."""
        assert "审查者" in CRITIC_SYSTEM_PROMPT
        assert "Critic" in CRITIC_SYSTEM_PROMPT

    def test_contains_check_items(self):
        """The prompt must contain all 8 check items."""
        for keyword in [
            "遗漏修改",
            "无关字段",
            "错误猜测",
            "模板替代未知语义",
            "单位",
            "物理依赖",
            "风险等级",
            "澄清",
        ]:
            assert keyword in CRITIC_SYSTEM_PROMPT, (
                f"Missing check item: {keyword}"
            )

    def test_contains_output_format(self):
        """The prompt must contain the expected JSON output format."""
        assert "accepted" in CRITIC_SYSTEM_PROMPT
        assert "violations" in CRITIC_SYSTEM_PROMPT
        assert "required_corrections" in CRITIC_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests: CriticResult
# ---------------------------------------------------------------------------


class TestCriticResult:
    """Test CriticResult accepts/rejects correctly."""

    def test_accept_with_defaults(self):
        """A accepted=True result should have empty violations."""
        result = CriticResult(accepted=True)
        assert result.accepted is True
        assert result.violations == []
        assert result.required_corrections == []

    def test_reject_with_violations(self):
        """A accepted=False result should carry violations."""
        result = CriticResult(
            accepted=False,
            violations=["missing end_time operation"],
            required_corrections=["add replace operation for /numerics/time/end_time"],
        )
        assert result.accepted is False
        assert len(result.violations) == 1
        assert len(result.required_corrections) == 1

    def test_reject_with_empty_lists(self):
        """A rejected result can have empty violation lists."""
        result = CriticResult(accepted=False)
        assert result.accepted is False
        assert result.violations == []
        assert result.required_corrections == []

    def test_model_validate_from_dict(self):
        """CriticResult should validate from a plain dict."""
        result = CriticResult.model_validate({
            "accepted": True,
            "violations": [],
            "required_corrections": [],
        })
        assert result.accepted is True

    def test_extra_fields_rejected(self):
        """CriticResult should reject extra fields."""
        with pytest.raises(ValidationError):
            CriticResult.model_validate({
                "accepted": True,
                "violations": [],
                "required_corrections": [],
                "extra_field": "bad",
            })

    def test_accepted_is_required(self):
        """The accepted field is required."""
        with pytest.raises(ValidationError):
            CriticResult.model_validate({
                "violations": [],
                "required_corrections": [],
            })


# ---------------------------------------------------------------------------
# Tests: build_critic_prompt
# ---------------------------------------------------------------------------


class TestBuildCriticPrompt:
    """Test build_critic_prompt includes candidate patch."""

    def test_includes_candidate_patch(self):
        """The critic prompt must include the candidate patch."""
        candidate = _make_candidate_patch()
        prompt = build_critic_prompt(
            candidate_patch=candidate,
            current_spec=_make_current_spec(),
            user_message="仿真时间设为15秒",
        )
        assert "候选 SimulationSpecPatch" in prompt
        assert "patch_001" in prompt
        assert "/numerics/time/end_time" in prompt

    def test_includes_current_spec(self):
        """The critic prompt must include the current spec."""
        prompt = build_critic_prompt(
            candidate_patch=_make_candidate_patch(),
            current_spec=_make_current_spec(),
            user_message="test",
        )
        assert "SimulationStudySpec" in prompt
        assert "test_spec_001" in prompt

    def test_includes_user_message(self):
        """The critic prompt must include the user's message."""
        prompt = build_critic_prompt(
            candidate_patch=_make_candidate_patch(),
            current_spec=_make_current_spec(),
            user_message="把空气改成水",
        )
        assert "用户本轮消息" in prompt
        assert "把空气改成水" in prompt

    def test_includes_critic_system_prompt(self):
        """The critic prompt must include the system prompt content."""
        prompt = build_critic_prompt(
            candidate_patch=_make_candidate_patch(),
            current_spec=_make_current_spec(),
            user_message="test",
        )
        assert "审查者" in prompt
        assert "遗漏修改" in prompt


# ---------------------------------------------------------------------------
# Tests: TwoCallStrategy
# ---------------------------------------------------------------------------


class TestTwoCallStrategyAcceptFirstTry:
    """Test TwoCallStrategy with mock model client (accept on first try)."""

    def test_accept_on_first_try(self):
        """When the critic accepts on the first try, return the patch."""
        primary_calls: list[str] = []
        critic_calls: list[str] = []

        def mock_client(prompt: str) -> dict:
            if "审查者" in prompt:
                critic_calls.append(prompt)
                return {
                    "accepted": True,
                    "violations": [],
                    "required_corrections": [],
                }
            primary_calls.append(prompt)
            return _make_candidate_patch()

        strategy = TwoCallStrategy(
            system_prompt_builder=build_spec_editor_prompt,
            critic_prompt_builder=build_critic_prompt,
        )

        patch, critic, errors = strategy.execute(
            model_client=mock_client,
            context=_make_context(),
            user_message="仿真时间设为15秒",
            current_spec=_make_current_spec(),
            patch_schema=_make_patch_schema(),
        )

        assert patch is not None
        assert patch["patch_id"] == "patch_001"
        assert critic is not None
        assert critic.accepted is True
        assert errors == []
        # Only one primary call and one critic call.
        assert len(primary_calls) == 1
        assert len(critic_calls) == 1


class TestTwoCallStrategyRetryOnce:
    """Test TwoCallStrategy with critic rejection (retry once)."""

    def test_retry_once_then_accept(self):
        """When the critic rejects once then accepts, the strategy retries."""
        primary_calls: list[str] = []
        critic_calls: list[str] = []

        def mock_client(prompt: str) -> dict:
            if "审查者" in prompt:
                critic_calls.append(prompt)
                if len(critic_calls) == 1:
                    # First critic call: reject.
                    return {
                        "accepted": False,
                        "violations": ["missing end_time change"],
                        "required_corrections": [
                            "add replace operation for /numerics/time/end_time"
                        ],
                    }
                # Second critic call: accept.
                return {
                    "accepted": True,
                    "violations": [],
                    "required_corrections": [],
                }
            primary_calls.append(prompt)
            return _make_candidate_patch()

        strategy = TwoCallStrategy(
            system_prompt_builder=build_spec_editor_prompt,
            critic_prompt_builder=build_critic_prompt,
        )

        patch, critic, errors = strategy.execute(
            model_client=mock_client,
            context=_make_context(),
            user_message="仿真时间设为15秒",
            current_spec=_make_current_spec(),
            patch_schema=_make_patch_schema(),
        )

        assert patch is not None
        assert critic is not None
        assert critic.accepted is True
        assert errors == []
        # Two primary calls (initial + 1 retry).
        assert len(primary_calls) == 2
        assert len(critic_calls) == 2

    def test_retry_includes_critic_feedback_in_context(self):
        """On retry, the context should include prior_critic_feedback."""
        context = _make_context()
        critic_calls: list[str] = []

        def mock_client(prompt: str) -> dict:
            if "审查者" in prompt:
                critic_calls.append(prompt)
                if len(critic_calls) == 1:
                    return {
                        "accepted": False,
                        "violations": ["violation 1"],
                        "required_corrections": ["fix 1"],
                    }
                return {
                    "accepted": True,
                    "violations": [],
                    "required_corrections": [],
                }
            # Check that the retry prompt includes the critic feedback.
            if len(critic_calls) >= 1:
                assert "Critic 反馈" in prompt
                assert "violation 1" in prompt
            return _make_candidate_patch()

        strategy = TwoCallStrategy(
            system_prompt_builder=build_spec_editor_prompt,
            critic_prompt_builder=build_critic_prompt,
        )

        strategy.execute(
            model_client=mock_client,
            context=context,
            user_message="test",
            current_spec=_make_current_spec(),
            patch_schema=_make_patch_schema(),
        )


class TestTwoCallStrategyMaxRetries:
    """Test TwoCallStrategy max retries (fail after 3 attempts)."""

    def test_fail_after_3_attempts(self):
        """When the critic always rejects, fail after 3 total attempts."""
        primary_calls: list[str] = []
        critic_calls: list[str] = []

        def mock_client(prompt: str) -> dict:
            if "审查者" in prompt:
                critic_calls.append(prompt)
                return {
                    "accepted": False,
                    "violations": ["always reject"],
                    "required_corrections": ["fix everything"],
                }
            primary_calls.append(prompt)
            return _make_candidate_patch()

        strategy = TwoCallStrategy(
            system_prompt_builder=build_spec_editor_prompt,
            critic_prompt_builder=build_critic_prompt,
        )

        patch, critic, errors = strategy.execute(
            model_client=mock_client,
            context=_make_context(),
            user_message="test",
            current_spec=_make_current_spec(),
            patch_schema=_make_patch_schema(),
        )

        assert patch is None
        assert critic is not None
        assert critic.accepted is False
        assert "always reject" in critic.violations
        assert errors == ["fix everything"]
        # 3 primary calls (1 initial + 2 retries).
        assert len(primary_calls) == 3
        assert len(critic_calls) == 3

    def test_max_retries_constant(self):
        """MAX_RETRIES should be 2 (3 total attempts)."""
        assert TwoCallStrategy.MAX_RETRIES == 2


class TestTwoCallStrategyModelFailure:
    """Test TwoCallStrategy model failure handling."""

    def test_model_failure_on_primary_call(self):
        """When the primary_reasoner raises, return MODEL_FAILED."""
        def mock_client(prompt: str) -> dict:
            if "审查者" in prompt:
                return {"accepted": True, "violations": [], "required_corrections": []}
            raise RuntimeError("API timeout")

        strategy = TwoCallStrategy(
            system_prompt_builder=build_spec_editor_prompt,
            critic_prompt_builder=build_critic_prompt,
        )

        patch, critic, errors = strategy.execute(
            model_client=mock_client,
            context=_make_context(),
            user_message="test",
            current_spec=_make_current_spec(),
            patch_schema=_make_patch_schema(),
        )

        assert patch is None
        assert critic is None
        assert len(errors) == 1
        assert "MODEL_FAILED" in errors[0]
        assert "API timeout" in errors[0]

    def test_model_failure_on_critic_call(self):
        """When the critic call raises, return MODEL_FAILED."""
        def mock_client(prompt: str) -> dict:
            if "审查者" in prompt:
                raise ConnectionError("network error")
            return _make_candidate_patch()

        strategy = TwoCallStrategy(
            system_prompt_builder=build_spec_editor_prompt,
            critic_prompt_builder=build_critic_prompt,
        )

        patch, critic, errors = strategy.execute(
            model_client=mock_client,
            context=_make_context(),
            user_message="test",
            current_spec=_make_current_spec(),
            patch_schema=_make_patch_schema(),
        )

        assert patch is None
        assert critic is None
        assert len(errors) == 1
        assert "MODEL_FAILED" in errors[0]
        assert "network error" in errors[0]

    def test_model_returns_non_dict(self):
        """When the primary_reasoner returns a non-dict, return MODEL_FAILED."""
        def mock_client(prompt: str) -> dict:
            if "审查者" in prompt:
                return {"accepted": True, "violations": [], "required_corrections": []}
            return "not a dict"  # type: ignore[return-value]

        strategy = TwoCallStrategy(
            system_prompt_builder=build_spec_editor_prompt,
            critic_prompt_builder=build_critic_prompt,
        )

        patch, critic, errors = strategy.execute(
            model_client=mock_client,
            context=_make_context(),
            user_message="test",
            current_spec=_make_current_spec(),
            patch_schema=_make_patch_schema(),
        )

        assert patch is None
        assert critic is None
        assert len(errors) == 1
        assert "MODEL_FAILED" in errors[0]
        assert "non-dict" in errors[0]

    def test_critic_returns_invalid_critic_result(self):
        """When the critic output fails validation, return MODEL_FAILED."""
        def mock_client(prompt: str) -> dict:
            if "审查者" in prompt:
                # Missing required 'accepted' field.
                return {"violations": [], "required_corrections": []}
            return _make_candidate_patch()

        strategy = TwoCallStrategy(
            system_prompt_builder=build_spec_editor_prompt,
            critic_prompt_builder=build_critic_prompt,
        )

        patch, critic, errors = strategy.execute(
            model_client=mock_client,
            context=_make_context(),
            user_message="test",
            current_spec=_make_current_spec(),
            patch_schema=_make_patch_schema(),
        )

        assert patch is None
        assert critic is None
        assert len(errors) == 1
        assert "MODEL_FAILED" in errors[0]
