"""Run real-model Structured Understanding and Skill/context ablations.

The script never prints credentials.  It uses the model already configured by
the application and emits one compact JSON audit record per requested mode.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from fluid_scientist.api import v5_router
from fluid_scientist.llm.structured_understanding import (
    ModelNativeUnderstandingService,
    UnderstandingContext,
)
from fluid_scientist.skills.skill_resolver import SkillResolver


USER_MESSAGE = "把仿真结束时间从10s修改为20s；圆柱半径0.1m已经确认，其他字段保持不变。"
BASE_SPEC = {
    "spec_id": "audit_study_1",
    "version": 3,
    "case_family": "cylinder_flow_2d",
    "geometry": {"entities": [{"id": "cylinder_1", "type": "cylinder", "radius": {"value": 0.1, "unit": "m"}}]},
    "numerics": {"time": {"end_time": {"value": 10.0, "unit": "s"}}},
}
HISTORY = [{"role": "user", "content": "研究二维圆柱绕流的涡脱落。"}]
FACTS = [{"key": "geometry.entities.cylinder_1.radius", "value": {"value": 0.1, "unit": "m"}, "confirmed": True}]
REFERENCES = [{"reference_id": "openfoam-foundation-13", "content": "OpenFOAM Foundation 13 deterministic compiler target."}]


def build_context(mode: str) -> UnderstandingContext:
    resolver = SkillResolver()
    selected = resolver.select_skills(user_text=USER_MESSAGE)
    professional = resolver.resolve_documents(
        [item.skill_id for item in selected], user_text=USER_MESSAGE
    )
    values: dict[str, Any] = {
        "user_message": USER_MESSAGE,
        "current_spec": BASE_SPEC,
        "conversation_history": HISTORY,
        "confirmed_facts": FACTS,
        "unresolved_conflicts": [],
        "workflow_skills": [{"skill_id": "research_session", "content": "Preserve confirmed facts and apply a minimal patch."}],
        "professional_skills": professional,
        "references": REFERENCES,
    }
    removals = {
        "no_spec": ("current_spec", None),
        "no_history": ("conversation_history", []),
        "no_facts": ("confirmed_facts", []),
        "skills_off": ("professional_skills", []),
        "no_workflow_skill": ("workflow_skills", []),
        "no_references": ("references", []),
    }
    if mode in removals:
        key, value = removals[mode]
        values[key] = value
    return UnderstandingContext(**values)


def score(output) -> dict[str, Any]:
    patch = output.proposed_patch
    end_ops = [op for op in patch.operations if op.path.endswith("/end_time")]
    radius_ops = [op for op in patch.operations if "radius" in op.path]
    score_value = 0
    score_value += 35 if len(end_ops) == 1 else 0
    score_value += 20 if end_ops and (end_ops[0].value == 20 or (isinstance(end_ops[0].value, dict) and end_ops[0].value.get("value") == 20)) else 0
    score_value += 15 if end_ops and end_ops[0].source_quote in USER_MESSAGE else 0
    score_value += 15 if not radius_ops else 0
    score_value += 10 if patch.untouched_guarantee else 0
    score_value += 5 if any(fact.path.endswith("end_time") and fact.origin == "USER_EXPLICIT" for fact in output.facts) else 0
    return {
        "score": score_value,
        "end_time_operation_count": len(end_ops),
        "radius_operation_count": len(radius_ops),
        "untouched_guarantee": patch.untouched_guarantee,
        "fact_count": len(output.facts),
        "entity_count": len(output.entities),
        "relation_count": len(output.relations),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["on", "skills_off", "no_spec", "no_history", "no_facts", "no_workflow_skill", "no_references", "wrong_skill", "skill_missing"])
    args = parser.parse_args()
    resolver = SkillResolver()
    if args.mode in {"wrong_skill", "skill_missing"}:
        requested = ["fluid.error_diagnosis"] if args.mode == "wrong_skill" else ["fluid.not_installed"]
        try:
            resolver.resolve_documents(requested, user_text=USER_MESSAGE)
            result = {"mode": args.mode, "guarded": False, "error": None}
        except Exception as exc:
            result = {"mode": args.mode, "guarded": True, "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False))
        return

    client = v5_router._llm_client
    if client is None:
        raise SystemExit("MODEL_UNAVAILABLE")
    context = build_context(args.mode)

    def call_model(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        parsed, record = client.call(
            purpose="structured_understanding",
            prompt_name="structured_understanding_ablation",
            system_prompt=(
                "Act as the primary CFD semantic reasoner. Return exactly the JSON object described by the supplied schema. "
                "Use exact evidence quotes. Produce a minimal SimulationSpecPatch and never modify confirmed, unmentioned fields."
            ),
            user_message=prompt,
            output_schema=schema,
            session_id=f"audit_{args.mode}",
            prompt_version="structured-understanding-ablation-v1",
        )
        if not record.success or record.fallback_used:
            raise RuntimeError(record.error or "model failed")
        return parsed

    try:
        understanding, validation = ModelNativeUnderstandingService(call_model).understand(context)
        result = {
            "mode": args.mode,
            "provider": client._provider,
            "model": client._model_name,
            "valid": validation.valid,
            **score(understanding),
        }
    except Exception as exc:
        result = {
            "mode": args.mode,
            "provider": client._provider,
            "model": client._model_name,
            "valid": False,
            "score": 0,
            "error": str(exc),
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
