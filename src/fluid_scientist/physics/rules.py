"""Declarative rule evaluation without executable expressions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RuleViolation:
    rule_id: str
    severity: str
    message: str
    action: str


@dataclass(frozen=True)
class PhysicsRule:
    rule_id: str
    severity: str
    when: dict[str, Any]
    metric: str
    operator: str
    expected: Any
    message: str
    action: str


class RuleEngine:
    def __init__(self, rules: tuple[PhysicsRule, ...]) -> None:
        self._rules = rules

    @classmethod
    def from_yaml(cls, path: Path) -> "RuleEngine":
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        rules = tuple(
            PhysicsRule(
                rule_id=item["id"],
                severity=item["severity"],
                when=item.get("when", {}),
                metric=item["check"]["metric"],
                operator=item["check"]["operator"],
                expected=item["check"]["expected"],
                message=item["message"],
                action=item["action"],
            )
            for item in payload["rules"]
        )
        return cls(rules)

    def evaluate(self, context: dict[str, Any]) -> list[RuleViolation]:
        violations: list[RuleViolation] = []
        for rule in self._rules:
            if not all(context.get(key) == value for key, value in rule.when.items()):
                continue
            actual = context.get(rule.metric)
            if not self._passes(rule.operator, actual, rule.expected):
                violations.append(
                    RuleViolation(
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        message=rule.message,
                        action=rule.action,
                    )
                )
        return violations

    @staticmethod
    def _passes(operator: str, actual: Any, expected: Any) -> bool:
        if operator == "in":
            return actual in expected
        if operator == "equals":
            return actual == expected
        if operator == "gte":
            return actual is not None and actual >= expected
        if operator == "lte":
            return actual is not None and actual <= expected
        raise ValueError(f"unsupported rule operator: {operator}")

