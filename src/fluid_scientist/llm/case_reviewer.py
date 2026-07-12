"""LLM-powered observable inference and case review services.

This module provides two LLM-driven capabilities:

1. **Observable Inference**: Given a user's research objective, uses an LLM
   to infer appropriate OpenFOAM observables (function objects) instead of
   relying on brittle keyword matching.

2. **Case Review**: Before submitting to the workstation, sends the generated
   OpenFOAM case files to an LLM for review, catching syntax errors, invalid
   configurations, and security policy violations that would cause runtime
   failures.

Both services gracefully degrade to deterministic fallbacks when the LLM is
unavailable (mock mode or API errors).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fluid_scientist.prompts import load_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observable Inference
# ---------------------------------------------------------------------------

def infer_observables_with_llm(
    llm_client: Any,
    research_goal: str,
    session_id: str = "",
) -> list[dict[str, Any]]:
    """Use an LLM to infer appropriate analysis goals from a research goal.

    Returns a list of analysis goal dicts, each with keys:
    - phenomenon: str (English snake_case)
    - target_quantity: str (English snake_case)
    - temporal_mode: str (statistical/transient/time_averaged)
    - statistic: str (mean/rms/psd/snapshot)

    Falls back to a default set if the LLM call fails.
    """
    if not llm_client:
        return _default_observables(research_goal)

    try:
        system_prompt = load_prompt("observable_inference")
        user_message = f"Research goal: {research_goal}"

        output, _record = llm_client.call(
            purpose="observable_inference",
            prompt_name="observable_inference",
            system_prompt=system_prompt,
            user_message=user_message,
            session_id=session_id,
            output_schema="json",
        )

        goals = _parse_analysis_goals(output)
        if goals:
            logger.info("LLM inferred %d analysis goals for: %s", len(goals), research_goal[:80])
            return goals

        logger.warning("LLM returned no analysis goals, using defaults")
        return _default_observables(research_goal)

    except Exception as e:
        logger.warning("LLM observable inference failed (%s), using defaults", e)
        return _default_observables(research_goal)


def _parse_analysis_goals(output: Any) -> list[dict[str, Any]]:
    """Parse LLM output into a list of analysis goal dicts."""
    if isinstance(output, dict):
        goals = output.get("analysis_goals", [])
    elif isinstance(output, str):
        # Try to extract JSON from the response
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                goals = parsed.get("analysis_goals", [])
            elif isinstance(parsed, list):
                goals = parsed
            else:
                return []
        except json.JSONDecodeError:
            # Try to find JSON in the text
            match = re.search(r'\{[^{}]*"analysis_goals"[^{}]*\[.*?\][^{}]*\}', output, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    goals = parsed.get("analysis_goals", [])
                except json.JSONDecodeError:
                    return []
            else:
                return []
    else:
        return []

    # Validate and clean each goal
    cleaned = []
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        phenomenon = str(goal.get("phenomenon", "")).strip()
        if not phenomenon or not phenomenon.isascii():
            continue
        target = str(goal.get("target_quantity", phenomenon)).strip()
        if not target.isascii():
            target = phenomenon
        cleaned.append({
            "phenomenon": phenomenon,
            "target_quantity": target,
            "temporal_mode": str(goal.get("temporal_mode", "statistical")),
            "statistic": str(goal.get("statistic", "mean")),
        })
    return cleaned


def _default_observables(research_goal: str) -> list[dict[str, Any]]:
    """Deterministic fallback when LLM is unavailable."""
    return [
        {
            "phenomenon": "baseline_flow",
            "target_quantity": "velocity_field",
            "temporal_mode": "time_averaged",
            "statistic": "mean",
        },
        {
            "phenomenon": "vortex_identification",
            "target_quantity": "q_criterion",
            "temporal_mode": "statistical",
            "statistic": "mean+snapshot",
        },
    ]


# ---------------------------------------------------------------------------
# Case Review
# ---------------------------------------------------------------------------

def review_case_with_llm(
    llm_client: Any,
    case_dir: str,
    session_id: str = "",
) -> dict[str, Any]:
    """Use an LLM to review OpenFOAM case files for potential issues.

    Reads all files from case_dir and sends them to the LLM for review.

    Returns:
        {
            "has_issues": bool,
            "issues": [
                {
                    "severity": "error" | "warning",
                    "file": str,
                    "line": int | None,
                    "description": str,
                    "suggestion": str,
                }
            ],
            "summary": str,
        }
    """
    # Collect case files
    case_files = _collect_case_files(case_dir)
    if not case_files:
        return {
            "has_issues": True,
            "issues": [{
                "severity": "error",
                "file": "",
                "line": None,
                "description": "No case files found in case directory",
                "suggestion": "Ensure the case has been compiled before review",
            }],
            "summary": "No files to review",
        }

    if not llm_client:
        # Fallback: basic static checks
        return _static_case_review(case_files)

    try:
        system_prompt = load_prompt("case_review")
        user_message = _format_case_files_for_review(case_files)

        output, _record = llm_client.call(
            purpose="case_review",
            prompt_name="case_review",
            system_prompt=system_prompt,
            user_message=user_message,
            session_id=session_id,
            output_schema="json",
        )

        result = _parse_review_output(output)
        if result:
            logger.info("LLM case review found %d issues", len(result.get("issues", [])))
            return result

        logger.warning("LLM case review returned no result, using static review")
        return _static_case_review(case_files)

    except Exception as e:
        logger.warning("LLM case review failed (%s), using static review", e)
        return _static_case_review(case_files)


def _collect_case_files(case_dir: str) -> dict[str, str]:
    """Read all OpenFOAM case files from case_dir.

    Returns a dict mapping relative file paths to file contents.
    """
    files: dict[str, str] = {}
    case_path = Path(case_dir)
    if not case_path.exists():
        return files

    # Read files from standard OpenFOAM directories
    for subdir in ("system", "constant", "0", "constant/polyMesh"):
        dir_path = case_path / subdir
        if dir_path.exists():
            for f in dir_path.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    rel_path = f"{subdir}/{f.name}"
                    try:
                        files[rel_path] = f.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

    # Also read top-level files (Allrun, etc.)
    for f in case_path.iterdir():
        if f.is_file() and not f.name.startswith("."):
            try:
                files[f.name] = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    return files


def _format_case_files_for_review(files: dict[str, str]) -> str:
    """Format case files into a single message for the LLM."""
    parts = []
    for filepath, content in sorted(files.items()):
        parts.append(f"=== FILE: {filepath} ===\n{content}\n")
    return "\n".join(parts)


def _parse_review_output(output: Any) -> dict[str, Any] | None:
    """Parse LLM review output into a structured result."""
    if isinstance(output, dict):
        return {
            "has_issues": output.get("has_issues", False),
            "issues": output.get("issues", []),
            "summary": output.get("summary", ""),
        }
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                return {
                    "has_issues": parsed.get("has_issues", False),
                    "issues": parsed.get("issues", []),
                    "summary": parsed.get("summary", ""),
                }
        except json.JSONDecodeError:
            return None
    return None


def _static_case_review(files: dict[str, str]) -> dict[str, Any]:
    """Basic static checks when LLM is unavailable."""
    issues: list[dict[str, Any]] = []

    # Check controlDict
    cd = files.get("system/controlDict", "")
    if cd:
        if "incompressibleFluid" not in cd:
            issues.append({
                "severity": "error",
                "file": "system/controlDict",
                "line": None,
                "description": "controlDict must contain 'solver incompressibleFluid;'",
                "suggestion": "Replace 'application pimpleFoam;' with 'solver incompressibleFluid;'",
            })
        if "libs" in cd and '"' in cd:
            issues.append({
                "severity": "error",
                "file": "system/controlDict",
                "line": None,
                "description": "controlDict contains 'libs' directive which is forbidden by workstation security policy",
                "suggestion": "Remove all 'libs (...)' entries",
            })
        if "$" in cd:
            issues.append({
                "severity": "error",
                "file": "system/controlDict",
                "line": None,
                "description": "controlDict contains '$' variable references which are forbidden",
                "suggestion": "Replace all variable references with literal values",
            })

    # Check for codeStream/codedFixedValue
    for filepath, content in files.items():
        if "codeStream" in content or "codedFixedValue" in content:
            issues.append({
                "severity": "error",
                "file": filepath,
                "line": None,
                "description": f"{filepath} contains dynamic code (codeStream/codedFixedValue) which is forbidden",
                "suggestion": "Replace dynamic code with static configurations",
            })

    # Check blockMeshDict
    bmd = files.get("system/blockMeshDict", "")
    if bmd:
        if "hex" not in bmd:
            issues.append({
                "severity": "error",
                "file": "system/blockMeshDict",
                "line": None,
                "description": "blockMeshDict missing 'hex' block definition",
                "suggestion": "Add blocks section with hex (0 1 2 3 4 5 6 7) (nx ny nz) simpleGrading (1 1 1)",
            })

    return {
        "has_issues": len(issues) > 0,
        "issues": issues,
        "summary": f"Static review found {len(issues)} issue(s)",
    }
