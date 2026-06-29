from pathlib import Path

from fluid_scientist.physics.rules import RuleEngine


def test_hard_rule_reports_incompatible_turbulence_model() -> None:
    rules_path = Path(__file__).parents[2] / "src/fluid_scientist/physics/default_rules.yaml"
    engine = RuleEngine.from_yaml(rules_path)

    violations = engine.evaluate(
        {"flow_regime": "turbulent", "turbulence_model": "laminar"}
    )

    assert [(item.rule_id, item.severity) for item in violations] == [
        ("RULE-TURB-001", "HARD")
    ]


def test_rule_is_not_applied_outside_its_condition() -> None:
    rules_path = Path(__file__).parents[2] / "src/fluid_scientist/physics/default_rules.yaml"
    engine = RuleEngine.from_yaml(rules_path)

    assert engine.evaluate({"flow_regime": "laminar", "turbulence_model": "laminar"}) == []

