"""Governed extraction, testing, approval, and publication of candidate Skills."""

import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import StrEnum


class PublishBlocked(RuntimeError):
    """Raised when a candidate lacks the evidence required for publication."""


class CandidateState(StrEnum):
    DRAFT = "DRAFT"
    RED_RECORDED = "RED_RECORDED"
    GREEN_PASSED = "GREEN_PASSED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"


class SkillCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    redacted_context: dict[str, Any]
    source_refs: tuple[str, ...] = Field(min_length=1)
    red_scenarios: list[str] = Field(default_factory=list)
    green_results: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    state: CandidateState = CandidateState.DRAFT

    def record_red(self, failure: str) -> None:
        self.red_scenarios.append(failure)
        self.state = CandidateState.RED_RECORDED

    def record_green(self, result: str) -> None:
        if not self.red_scenarios:
            raise PublishBlocked("GREEN evidence requires a recorded RED failure")
        self.green_results.append(result)
        self.state = CandidateState.GREEN_PASSED

    def approve(self, approved_by: str) -> None:
        if not self.red_scenarios or not self.green_results:
            raise PublishBlocked("RED and GREEN evidence are required before approval")
        self.approved_by = approved_by
        self.state = CandidateState.APPROVED


class SkillCandidateExtractor:
    _sensitive_keys = {"secret", "token", "password", "host", "hostname", "path", "user"}
    _secret_pattern = re.compile(r"sk-[A-Za-z0-9_-]+")

    def extract(self, event: dict[str, Any]) -> SkillCandidate:
        source_refs = tuple(str(item) for item in event.get("source_refs", ()))
        if not source_refs:
            raise ValueError("candidate extraction requires source_refs")
        pattern = str(event.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("candidate extraction requires a reusable pattern")
        context = {
            key: self._redact(key, value)
            for key, value in event.items()
            if key not in {"source_refs", "pattern"}
        }
        return SkillCandidate(
            candidate_id=str(uuid4()),
            pattern=pattern,
            redacted_context=context,
            source_refs=source_refs,
        )

    def _redact(self, key: str, value: Any) -> Any:
        if key.lower() in self._sensitive_keys:
            return f"<redacted:{key.lower()}>"
        if isinstance(value, dict):
            return {nested: self._redact(nested, item) for nested, item in value.items()}
        if isinstance(value, list):
            return [self._redact(key, item) for item in value]
        if isinstance(value, str):
            return self._secret_pattern.sub("<redacted:secret>", value)
        return value


class SkillPublisher:
    def publish(self, candidate: SkillCandidate) -> SkillCandidate:
        if not candidate.red_scenarios or not candidate.green_results:
            raise PublishBlocked("RED and GREEN evidence are required before publication")
        if candidate.state != CandidateState.APPROVED or not candidate.approved_by:
            raise PublishBlocked("human approval is required before publication")
        candidate.state = CandidateState.PUBLISHED
        return candidate

