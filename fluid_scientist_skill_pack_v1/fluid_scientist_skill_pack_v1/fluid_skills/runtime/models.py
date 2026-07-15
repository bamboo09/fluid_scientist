from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SkillStatus = Literal["SUCCESS", "PARTIAL", "FAILED", "ENVIRONMENT_BLOCKED"]

@dataclass
class SkillIssue:
    code: str
    message: str
    blocking: bool = False
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

@dataclass
class SkillResult:
    skill_id: str
    status: SkillStatus
    data: dict[str, Any] = field(default_factory=dict)
    issues: list[SkillIssue] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocking_issues(self) -> list[SkillIssue]:
        return [issue for issue in self.issues if issue.blocking]
