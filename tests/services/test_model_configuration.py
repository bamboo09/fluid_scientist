from dataclasses import replace

import pytest

from fluid_scientist.services.model_configuration import (
    CaseBuilderConfiguration,
    ModelConfiguration,
)


class Builder:
    provider_name = "glm"
    model_name = "glm-case-builder"

    def generate_case(self, custom_plan, *, capabilities, progress=None):
        raise NotImplementedError


class Planner:
    def design_experiment(self, question, *, capabilities, progress=None):
        raise NotImplementedError


def test_case_builder_configuration_is_all_or_none_and_immutable() -> None:
    with pytest.raises(ValueError, match="configured together"):
        CaseBuilderConfiguration(provider="glm")

    configured = CaseBuilderConfiguration(
        provider="glm", model="glm-case-builder", builder=Builder()
    )

    assert configured.configured is True
    with pytest.raises((AttributeError, TypeError)):
        configured.model = "changed"


def test_case_builder_attribution_must_match_builder_metadata() -> None:
    with pytest.raises(ValueError, match="metadata"):
        CaseBuilderConfiguration(provider="deepseek", model="glm-case-builder", builder=Builder())


def test_planner_and_case_builder_snapshots_replace_independently() -> None:
    planner = Planner()
    planning = ModelConfiguration(provider="openai", model="gpt", plan_designer=planner)
    builder = CaseBuilderConfiguration(
        provider="glm", model="glm-case-builder", builder=Builder()
    )

    changed_planning = replace(planning, model="gpt-next")
    changed_builder = replace(builder, model="glm-next", builder=BuilderWithNextMetadata())

    assert planning.model == "gpt"
    assert builder.model == "glm-case-builder"
    assert changed_planning.plan_designer is planner
    assert changed_builder.provider == "glm"


class BuilderWithNextMetadata(Builder):
    model_name = "glm-next"


def test_empty_case_builder_snapshot_does_not_change_legacy_configuration() -> None:
    planning = ModelConfiguration(result_analyst=None, legacy_designer=None)
    case_builder = CaseBuilderConfiguration()

    assert planning.configured is False
    assert case_builder.configured is False
    assert "api_key" not in repr(case_builder)
