from __future__ import annotations

from copy import deepcopy
from typing import Any

SOURCE_PRIORITY = {
    "SYSTEM_DEFAULT": 10,
    "MODEL_RECOMMENDED": 20,
    "SYSTEM_DERIVED": 30,
    "FORMULA_DERIVED": 40,
    "USER_EXPLICIT": 50,
    "USER_CONFIRMED": 60,
}

def source_priority(source: str | None) -> int:
    return SOURCE_PRIORITY.get(source or "SYSTEM_DEFAULT", 0)

def merge_value(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if current is None:
        return deepcopy(incoming)
    if source_priority(incoming.get("source")) >= source_priority(current.get("source")):
        return deepcopy(incoming)
    return deepcopy(current)

def ensure_spec_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(spec)
    result.setdefault("schema_version", "1.0")
    result.setdefault("case_family", "cylinder_flow_2d")
    result.setdefault("domain", {})
    result["domain"].setdefault("dimensionality", "2D")
    result["domain"].setdefault("thickness_m", 1.0)
    result.setdefault("cylinder", {})
    result["cylinder"].setdefault("type", "cylinder")
    result.setdefault("bottom_profile", {"enabled": False, "profile_type": "flat"})
    result.setdefault("boundaries", {})
    result.setdefault("flow_topology", {"mode": None})
    result.setdefault("observables", [])
    result.setdefault("analysis_goals", [])
    result.setdefault("blocking_issues", [])
    result.setdefault("recommendations", [])
    result.setdefault("draft_status", "NEEDS_CLARIFICATION")
    return result
