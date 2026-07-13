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

from fluid_scientist.prompts import load_prompt_with_knowledge

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
        system_prompt = load_prompt_with_knowledge("observable_inference")
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
        system_prompt = load_prompt_with_knowledge("case_review")
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


# ---------------------------------------------------------------------------
# Case Auto-Fix
# ---------------------------------------------------------------------------

def fix_case_with_llm(
    llm_client: Any,
    case_dir: str,
    review_result: dict[str, Any],
    session_id: str = "",
) -> dict[str, Any]:
    """Fix OpenFOAM case files based on review issues.

    Uses a two-tier approach:
    1. Targeted patching: Apply regex-based fixes for known patterns
       (never rewrite entire files — only patch specific lines)
    2. LLM diagnosis (optional): If static patches can't fix an issue,
       ask the LLM to suggest the specific line change needed (not a
       full file rewrite)

    This avoids the reliability problems of LLM-generated file rewrites.
    """
    case_files = _collect_case_files(case_dir)
    if not case_files:
        return {
            "fixed": False,
            "fixed_files": [],
            "remaining_issues": review_result.get("issues", []),
            "summary": "No case files found to fix",
        }

    issues = review_result.get("issues", [])
    if not issues:
        return {
            "fixed": False,
            "fixed_files": [],
            "remaining_issues": [],
            "summary": "No issues to fix",
        }

    # Use targeted patching (not LLM rewrite)
    return _targeted_patch_fix(case_dir, case_files, issues)


def _targeted_patch_fix(
    case_dir: str,
    files: dict[str, str],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply targeted regex patches for known issue patterns.

    Never rewrites an entire file — only patches the specific lines
    that need fixing. This is much more reliable than LLM rewrites.
    """
    fixed_files: list[str] = []
    remaining_issues: list[dict[str, Any]] = []
    case_path = Path(case_dir)
    patches_applied: list[str] = []

    for issue in issues:
        filepath = issue.get("file", "")
        severity = issue.get("severity", "")
        desc = issue.get("description", "").lower()
        suggestion = issue.get("suggestion", "").lower()
        patched = False

        if not filepath:
            remaining_issues.append(issue)
            continue

        full_path = case_path / filepath
        if not full_path.exists():
            remaining_issues.append(issue)
            continue

        content = files.get(filepath, "")
        original = content

        # --- Patch: controlDict solver should be incompressibleFluid ---
        if "controldict" in filepath.lower() and ("incompressiblefluid" in desc or "incompressiblefluid" in suggestion):
            content = content.replace("application pimpleFoam;", "solver incompressibleFluid;")
            content = content.replace("application simpleFoam;", "solver incompressibleFluid;")
            content = content.replace("application pisoFoam;", "solver incompressibleFluid;")
            content = content.replace("application icoFoam;", "solver incompressibleFluid;")
            if content != original:
                patched = True
                patches_applied.append(f"controlDict: solver -> incompressibleFluid")

        # --- Patch: remove libs directive ---
        if "libs" in desc and ("forbidden" in desc or "dynamic" in desc):
            content = re.sub(r'\n\s*libs\s*\([^)]*\)\s*;', '', content)
            if content != original:
                patched = True
                patches_applied.append(f"{filepath}: removed libs directive")

        # --- Patch: remove $ variable references ---
        if "$" in desc and "forbidden" in desc:
            content = re.sub(r'\$\w+', '1', content)
            if content != original:
                patched = True
                patches_applied.append(f"{filepath}: replaced $ variables")

        # --- Patch: 0/p internalField should be scalar ---
        if filepath == "0/p" and ("scalar" in desc or "vector" in desc and "internalfield" in desc):
            # Fix: uniform (0 0 0) -> uniform 0
            content = re.sub(r'uniform\s*\(\s*\d[\d\s.]*\)', 'uniform 0', content)
            if content != original:
                patched = True
                patches_applied.append("0/p: internalField -> scalar")

        # --- Patch: function object names with spaces ---
        if "space" in desc or "function object" in desc:
            # Find function object names with spaces and replace with underscores
            # Pattern: "name with space" at the start of a sub-dict key
            content = re.sub(
                r'^(\s*)([\w]+)\s+([\w]+)',
                lambda m: f"{m.group(1)}{m.group(2)}_{m.group(3)}" if m.group(3)[0].islower() else m.group(0),
                content,
                flags=re.MULTILINE
            )
            if content != original:
                patched = True
                patches_applied.append(f"{filepath}: sanitized function object names")

        # --- Patch: missing relaxationFactors ---
        if "fvsolution" in filepath.lower() and "relaxation" in desc:
            if "relaxationFactors" not in content:
                # Add relaxationFactors before the closing brace of solvers or at end
                relax_block = """
relaxationFactors
{
    equations
    {
        U 0.7;
        p 0.3;
    }
}
"""
                content = content.rstrip() + "\n" + relax_block
                if content != original:
                    patched = True
                    patches_applied.append("fvSolution: added relaxationFactors")

        # --- Patch: numerical divergence ---
        if "diverg" in desc or "nan" in desc or "floating point" in desc:
            if "fvsolution" in filepath.lower():
                # Reduce relaxation factors
                content = re.sub(r'(U\s+)0\.\d+', r'\g<1>0.3', content)
                content = re.sub(r'(p\s+)0\.\d+', r'\g<1>0.1', content)
                if content != original:
                    patched = True
                    patches_applied.append("fvSolution: reduced relaxation factors")
            if "controldict" in filepath.lower():
                # Halve deltaT
                content = re.sub(
                    r'(deltaT\s+)(\d+\.?\d*)',
                    lambda m: f"{m.group(1)}{float(m.group(2)) / 2}",
                    content
                )
                if content != original:
                    patched = True
                    patches_applied.append("controlDict: halved deltaT")

        # --- Patch: nu missing dimensions ---
        if "nu" in desc and ("dimension" in desc or "missing" in desc):
            # Add dimensions to nu if it's a bare number
            content = re.sub(
                r'nu\s+(\d+\.?\d*[eE]?[-+]?\d*)\s*;',
                r'nu [0 2 -1 0 0 0 0] \1;',
                content
            )
            if content != original:
                patched = True
                patches_applied.append("transportProperties: added nu dimensions")

        if patched:
            # Validate the patched content before writing
            if _validate_openfoam_syntax(content):
                try:
                    full_path.write_text(content, encoding="utf-8")
                    files[filepath] = content  # Update in-memory copy
                    if filepath not in fixed_files:
                        fixed_files.append(filepath)
                except Exception as e:
                    logger.warning("Failed to write patch for %s: %s", filepath, e)
                    remaining_issues.append(issue)
            else:
                logger.warning("Patched content failed validation for %s, skipping", filepath)
                remaining_issues.append(issue)
        else:
            remaining_issues.append(issue)

    summary = f"Applied {len(patches_applied)} patch(es)" if patches_applied else "No patches could be applied"
    if patches_applied:
        summary += ": " + "; ".join(patches_applied)

    return {
        "fixed": len(fixed_files) > 0,
        "fixed_files": fixed_files,
        "remaining_issues": remaining_issues,
        "summary": summary,
    }


def _validate_openfoam_syntax(content: str) -> bool:
    """Quick validation of OpenFOAM dictionary syntax.

    Checks for common issues that would cause parse errors:
    - Balanced braces
    - Balanced parentheses
    - No unterminated comments
    """
    # Check balanced braces
    brace_depth = 0
    paren_depth = 0
    in_comment = False
    in_string = False
    i = 0
    while i < len(content):
        ch = content[i]
        # Handle line comments
        if not in_string and ch == '/' and i + 1 < len(content) and content[i + 1] == '/':
            # Skip to end of line
            while i < len(content) and content[i] != '\n':
                i += 1
            continue
        # Handle block comments
        if not in_string and ch == '/' and i + 1 < len(content) and content[i + 1] == '*':
            in_comment = True
            i += 2
            continue
        if in_comment:
            if ch == '*' and i + 1 < len(content) and content[i + 1] == '/':
                in_comment = False
                i += 2
                continue
            i += 1
            continue
        if ch == '{':
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth < 0:
                return False  # Unbalanced
        elif ch == '(':
            paren_depth += 1
        elif ch == ')':
            paren_depth -= 1
            if paren_depth < 0:
                return False
        i += 1
    return brace_depth == 0 and paren_depth == 0 and not in_comment


def _format_fix_input(issues: list[dict[str, Any]], files: dict[str, str]) -> str:
    """Format issues + file contents for the LLM fix prompt."""
    import json as _json
    parts = []
    parts.append("[ISSUES]")
    parts.append(_json.dumps({"has_issues": True, "issues": issues}, ensure_ascii=False, indent=2))
    parts.append("")
    parts.append("[CURRENT FILES]")
    for filepath, content in sorted(files.items()):
        parts.append(f"=== FILE: {filepath} ===")
        parts.append(content)
        parts.append("")
    return "\n".join(parts)


def _parse_and_apply_fixes(output: Any, case_dir: str) -> list[str]:
    """Parse LLM fix output and write fixed files to disk.

    Returns list of file paths that were modified.
    """
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            # Try to extract JSON from text
            match = re.search(r'\{.*\}', output, re.DOTALL)
            if match:
                try:
                    output = json.loads(match.group())
                except json.JSONDecodeError:
                    return []
            else:
                return []

    if not isinstance(output, dict):
        return []

    fixed_files = []
    case_path = Path(case_dir)
    for filepath, content in output.items():
        if not isinstance(content, str):
            continue
        full_path = case_path / filepath
        if not full_path.exists():
            # Try to create parent directories
            full_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            full_path.write_text(content, encoding="utf-8")
            fixed_files.append(filepath)
        except Exception as e:
            logger.warning("Failed to write fixed file %s: %s", filepath, e)

    return fixed_files


def _static_case_fix(
    case_dir: str,
    files: dict[str, str],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply deterministic fixes for known issue patterns."""
    fixed_files = []
    remaining_issues = []
    case_path = Path(case_dir)

    for issue in issues:
        filepath = issue.get("file", "")
        severity = issue.get("severity", "")
        desc = issue.get("description", "").lower()
        fixed = False

        if not filepath:
            remaining_issues.append(issue)
            continue

        full_path = case_path / filepath
        if not full_path.exists():
            remaining_issues.append(issue)
            continue

        content = files.get(filepath, "")
        new_content = content

        # Fix: controlDict solver should be incompressibleFluid
        if "controldict" in filepath.lower() and "incompressiblefluid" in desc:
            new_content = content.replace("application pimpleFoam;", "solver incompressibleFluid;")
            new_content = new_content.replace("application simpleFoam;", "solver incompressibleFluid;")
            new_content = new_content.replace("application pisoFoam;", "solver incompressibleFluid;")
            if new_content != content:
                fixed = True

        # Fix: remove libs directive
        if "libs" in desc and "forbidden" in desc:
            new_content = re.sub(r'\n\s*libs\s*\([^)]*\)\s*;', '', new_content)
            if new_content != content:
                fixed = True

        # Fix: remove $ variable references
        if "$" in desc and "forbidden" in desc:
            # Replace common patterns like $value with literal values
            # This is a best-effort static fix
            new_content = re.sub(r'\$\w+', '1', new_content)
            if new_content != content:
                fixed = True

        # Fix: 0/p internalField should be scalar
        if filepath == "0/p" and "scalar" in desc:
            new_content = re.sub(
                r'uniform\s*\([^)]*\)',
                'uniform 0',
                new_content
            )
            if new_content != content:
                fixed = True

        # Fix: remove codeStream/codedFixedValue
        if "codestream" in desc or "codedfixedvalue" in desc:
            # This is complex — just flag as remaining
            pass

        if fixed:
            try:
                full_path.write_text(new_content, encoding="utf-8")
                files[filepath] = new_content  # Update in-memory copy
                fixed_files.append(filepath)
            except Exception as e:
                logger.warning("Failed to write static fix for %s: %s", filepath, e)
                remaining_issues.append(issue)
        else:
            remaining_issues.append(issue)

    return {
        "fixed": len(fixed_files) > 0,
        "fixed_files": fixed_files,
        "remaining_issues": remaining_issues,
        "summary": f"Static fix applied to {len(fixed_files)} file(s)" if fixed_files else "No static fixes could be applied",
    }


# ---------------------------------------------------------------------------
# Runtime Failure Diagnosis & Fix
# ---------------------------------------------------------------------------

def diagnose_and_fix_runtime_error(
    llm_client: Any,
    case_dir: str,
    error_message: str,
    case_plan_summary: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """Diagnose a runtime error from a failed workstation job and fix the case.

    This is used when a job has already been submitted to the workstation and
    failed during execution (e.g. blockMesh crashed, solver diverged). The LLM
    analyzes the error message along with the case files to determine the root
    cause and output fixed files.

    Returns:
        {
            "diagnosis": {
                "failure_stage": str,   # blockMesh|checkMesh|foamRun|unknown
                "error_type": str,      # syntax|parameter|mesh|numerical|memory|other
                "root_cause": str,
                "affected_files": list[str],
            },
            "fixed": bool,
            "fixed_files": list[str],
            "summary": str,
        }
    """
    case_files = _collect_case_files(case_dir)
    if not case_files:
        return {
            "diagnosis": {
                "failure_stage": "unknown",
                "error_type": "other",
                "root_cause": "No case files found to diagnose",
                "affected_files": [],
            },
            "fixed": False,
            "fixed_files": [],
            "summary": "No case files found",
        }

    # Always use targeted patching (not LLM rewrite) for reliability
    return _targeted_runtime_fix(case_dir, case_files, error_message)


def _targeted_runtime_fix(
    case_dir: str,
    files: dict[str, str],
    error_message: str,
) -> dict[str, Any]:
    """Diagnose runtime error using pattern matching and apply targeted patches.

    This replaces the previous LLM-rewrite approach which was unreliable.
    LLM may still be used for diagnosis (explaining the error to the user)
    but file fixes are always done via targeted regex patches.
    """
    error_lower = error_message.lower()
    failure_stage = "unknown"
    error_type = "other"
    root_cause = error_message
    affected_files: list[str] = []
    fixed_files: list[str] = []
    patches_applied: list[str] = []
    case_path = Path(case_dir)

    # Determine failure stage
    if "blockmesh" in error_lower:
        failure_stage = "blockMesh"
    elif "checkmesh" in error_lower:
        failure_stage = "checkMesh"
    elif "foamrun" in error_lower or "pimplefoam" in error_lower or "pimple" in error_lower:
        failure_stage = "foamRun"
    elif "decompose" in error_lower:
        failure_stage = "decomposePar"

    # Pattern: ill defined primitiveEntry
    if "ill defined primitiveentry" in error_lower:
        error_type = "syntax"
        root_cause = "OpenFOAM dictionary entry contains invalid syntax (keyword with spaces or invalid characters)"
        affected_files = ["system/controlDict"]
        cd = files.get("system/controlDict", "")
        if cd:
            original = cd
            # Fix function object names with spaces by replacing spaces with underscores
            # in lines that look like dictionary keys (word word followed by {)
            cd = re.sub(
                r'^(\s*)(\w+)\s+(\w+)\s*$',
                lambda m: f"{m.group(1)}{m.group(2)}_{m.group(3)}" if m.group(3)[0].islower() else m.group(0),
                cd,
                flags=re.MULTILINE
            )
            if cd != original and _validate_openfoam_syntax(cd):
                (case_path / "system" / "controlDict").write_text(cd, encoding="utf-8")
                fixed_files.append("system/controlDict")
                patches_applied.append("sanitized function object names")

    # Pattern: incompressibleFluid / literal solver
    elif "incompressiblefluid" in error_lower or "literal solver" in error_lower or "must select exactly one" in error_lower:
        error_type = "parameter"
        root_cause = "controlDict does not use 'solver incompressibleFluid;' as required by the workstation"
        affected_files = ["system/controlDict"]
        cd = files.get("system/controlDict", "")
        if cd:
            original = cd
            cd = cd.replace("application pimpleFoam;", "solver incompressibleFluid;")
            cd = cd.replace("application simpleFoam;", "solver incompressibleFluid;")
            cd = cd.replace("application pisoFoam;", "solver incompressibleFluid;")
            if cd != original and _validate_openfoam_syntax(cd):
                (case_path / "system" / "controlDict").write_text(cd, encoding="utf-8")
                fixed_files.append("system/controlDict")
                patches_applied.append("solver -> incompressibleFluid")

    # Pattern: libs forbidden
    elif "libs" in error_lower and ("forbidden" in error_lower or "dynamic" in error_lower):
        error_type = "parameter"
        root_cause = "controlDict contains 'libs' directive which is forbidden by workstation security policy"
        affected_files = ["system/controlDict"]
        cd = files.get("system/controlDict", "")
        if cd:
            original = cd
            cd = re.sub(r'\n\s*libs\s*\([^)]*\)\s*;', '', cd)
            if cd != original and _validate_openfoam_syntax(cd):
                (case_path / "system" / "controlDict").write_text(cd, encoding="utf-8")
                fixed_files.append("system/controlDict")
                patches_applied.append("removed libs directive")

    # Pattern: system calls / shell scripts
    elif "system call" in error_lower or "shell" in error_lower or "allrun" in error_lower:
        error_type = "parameter"
        root_cause = "Case contains shell scripts or system calls which are forbidden by workstation security policy"
        affected_files = ["Allrun"]
        # Remove Allrun if it exists
        allrun_path = case_path / "Allrun"
        if allrun_path.exists():
            allrun_path.unlink()
            fixed_files.append("Allrun (deleted)")
            patches_applied.append("removed Allrun script")

    # Pattern: codeStream / codedFixedValue
    elif "codestream" in error_lower or "codedfixedvalue" in error_lower or "coded" in error_lower:
        error_type = "parameter"
        root_cause = "Case contains dynamic code (codeStream/codedFixedValue) which is forbidden"
        affected_files = []
        for filepath, content in files.items():
            if "codeStream" in content or "codedFixedValue" in content:
                affected_files.append(filepath)

    # Pattern: divergence / NaN / floating point
    elif "diverg" in error_lower or "floating point" in error_lower or "nan" in error_lower:
        error_type = "numerical"
        root_cause = "Numerical divergence detected — solver became unstable"
        affected_files = ["system/fvSolution", "system/controlDict"]

        # Fix fvSolution: reduce relaxation factors
        fvs = files.get("system/fvSolution", "")
        if fvs:
            original = fvs
            fvs = re.sub(r'(U\s+)0\.\d+', r'\g<1>0.3', fvs)
            fvs = re.sub(r'(p\s+)0\.\d+', r'\g<1>0.1', fvs)
            if fvs != original and _validate_openfoam_syntax(fvs):
                (case_path / "system" / "fvSolution").write_text(fvs, encoding="utf-8")
                fixed_files.append("system/fvSolution")
                patches_applied.append("reduced relaxation factors")

        # Fix controlDict: halve deltaT
        cd = files.get("system/controlDict", "")
        if cd:
            original = cd
            cd = re.sub(
                r'(deltaT\s+)(\d+\.?\d*)',
                lambda m: f"{m.group(1)}{float(m.group(2)) / 2}",
                cd
            )
            if cd != original and _validate_openfoam_syntax(cd):
                (case_path / "system" / "controlDict").write_text(cd, encoding="utf-8")
                fixed_files.append("system/controlDict")
                patches_applied.append("halved deltaT")

    # Pattern: unterminated comment
    elif "unterminated" in error_lower and "comment" in error_lower:
        error_type = "syntax"
        root_cause = "OpenFOAM dictionary file has an unterminated block comment"
        affected_files = []
        for filepath, content in files.items():
            if not _validate_openfoam_syntax(content):
                affected_files.append(filepath)
                # Try to fix by ensuring all /* */ comments are closed
                original = content
                # Count unclosed /* comments
                open_count = content.count('/*')
                close_count = content.count('*/')
                if open_count > close_count:
                    # Add missing */ at the end
                    content = content.rstrip() + '\n' + '*/' * (open_count - close_count)
                    if content != original:
                        try:
                            (case_path / filepath).write_text(content, encoding="utf-8")
                            if filepath not in fixed_files:
                                fixed_files.append(filepath)
                            patches_applied.append(f"{filepath}: closed unterminated comment")
                        except Exception:
                            pass

    # Pattern: out of memory
    elif "out of memory" in error_lower or "memory" in error_lower:
        error_type = "memory"
        root_cause = "Job ran out of memory — mesh may be too large"
        affected_files = ["system/blockMeshDict"]
        # Fix: reduce cell count
        bmd = files.get("system/blockMeshDict", "")
        if bmd:
            original = bmd
            # Halve each cell count in hex blocks
            bmd = re.sub(
                r'hex\s*\(([^)]+)\)\s*\((\d+)\s+(\d+)\s+(\d+)\)',
                lambda m: f"hex ({m.group(1)}) ({max(1, int(m.group(2))//2)} {max(1, int(m.group(3))//2)} {m.group(4)})",
                bmd
            )
            if bmd != original:
                (case_path / "system" / "blockMeshDict").write_text(bmd, encoding="utf-8")
                fixed_files.append("system/blockMeshDict")
                patches_applied.append("halved mesh resolution")

    summary_parts = [f"Diagnosis: {failure_stage}/{error_type}"]
    if patches_applied:
        summary_parts.append(f"Applied {len(patches_applied)} patch(es): {'; '.join(patches_applied)}")
    else:
        summary_parts.append("Could not auto-fix — manual intervention needed")

    return {
        "diagnosis": {
            "failure_stage": failure_stage,
            "error_type": error_type,
            "root_cause": root_cause,
            "affected_files": affected_files,
        },
        "fixed": len(fixed_files) > 0,
        "fixed_files": fixed_files,
        "summary": ". ".join(summary_parts),
    }


def _format_diagnosis_input(
    error_message: str,
    case_plan_summary: dict[str, Any] | None,
    files: dict[str, str],
) -> str:
    """Format error + case plan + file contents for the LLM diagnosis prompt."""
    import json as _json
    parts = []
    parts.append("[ERROR]")
    parts.append(error_message or "(no error message)")
    parts.append("")
    parts.append("[CASE PLAN]")
    if case_plan_summary:
        parts.append(_json.dumps(case_plan_summary, ensure_ascii=False, indent=2, default=str))
    else:
        parts.append("{}")
    parts.append("")
    parts.append("[CURRENT FILES]")
    for filepath, content in sorted(files.items()):
        parts.append(f"=== FILE: {filepath} ===")
        parts.append(content)
        parts.append("")
    return "\n".join(parts)


def _parse_diagnosis_output(output: Any, case_dir: str) -> dict[str, Any] | None:
    """Parse LLM diagnosis output and apply fixes to disk."""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', output, re.DOTALL)
            if match:
                try:
                    output = json.loads(match.group())
                except json.JSONDecodeError:
                    return None
            else:
                return None

    if not isinstance(output, dict):
        return None

    diagnosis = output.get("diagnosis", {})
    fixes = output.get("fixes", {})
    summary = output.get("summary", "")

    # Apply fixes to disk
    fixed_files = []
    case_path = Path(case_dir)
    for filepath, content in fixes.items():
        if not isinstance(content, str):
            continue
        full_path = case_path / filepath
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            fixed_files.append(filepath)
        except Exception as e:
            logger.warning("Failed to write diagnosed fix for %s: %s", filepath, e)

    return {
        "diagnosis": {
            "failure_stage": diagnosis.get("failure_stage", "unknown"),
            "error_type": diagnosis.get("error_type", "other"),
            "root_cause": diagnosis.get("root_cause", ""),
            "affected_files": diagnosis.get("affected_files", []),
        },
        "fixed": len(fixed_files) > 0,
        "fixed_files": fixed_files,
        "summary": summary or f"Fixed {len(fixed_files)} file(s)",
    }


def _static_runtime_diagnosis(
    case_dir: str,
    files: dict[str, str],
    error_message: str,
) -> dict[str, Any]:
    """Deterministic runtime error diagnosis when LLM is unavailable."""
    error_lower = error_message.lower()
    failure_stage = "unknown"
    error_type = "other"
    root_cause = error_message
    affected_files: list[str] = []
    fixed_files: list[str] = []

    case_path = Path(case_dir)

    # Determine failure stage
    if "blockmesh" in error_lower:
        failure_stage = "blockMesh"
    elif "checkmesh" in error_lower:
        failure_stage = "checkMesh"
    elif "foamrun" in error_lower or "pimplefoam" in error_lower or "solver" in error_lower:
        failure_stage = "foamRun"

    # Common error patterns
    if "ill defined primitiveentry" in error_lower:
        error_type = "syntax"
        root_cause = "OpenFOAM dictionary entry contains invalid syntax (likely keyword with spaces or invalid characters)"
        affected_files = ["system/controlDict"]
        # Fix: sanitize function object names
        cd = files.get("system/controlDict", "")
        if cd:
            # Replace function object names with spaces
            new_cd = re.sub(
                r'(\w+_\w+)\s+(\w+)',
                lambda m: m.group(1) + "_" + m.group(2) if " " in m.group(0) else m.group(0),
                cd
            )
            if new_cd != cd:
                (case_path / "system" / "controlDict").write_text(new_cd, encoding="utf-8")
                fixed_files.append("system/controlDict")

    elif "incompressiblefluid" in error_lower or "literal solver" in error_lower:
        error_type = "parameter"
        root_cause = "controlDict does not use 'solver incompressibleFluid;' as required by the workstation"
        affected_files = ["system/controlDict"]
        cd = files.get("system/controlDict", "")
        if cd:
            new_cd = cd.replace("application pimpleFoam;", "solver incompressibleFluid;")
            new_cd = new_cd.replace("application simpleFoam;", "solver incompressibleFluid;")
            if new_cd != cd:
                (case_path / "system" / "controlDict").write_text(new_cd, encoding="utf-8")
                fixed_files.append("system/controlDict")

    elif "libs" in error_lower and ("forbidden" in error_lower or "dynamic" in error_lower):
        error_type = "parameter"
        root_cause = "controlDict contains 'libs' directive which is forbidden by workstation security policy"
        affected_files = ["system/controlDict"]
        cd = files.get("system/controlDict", "")
        if cd:
            new_cd = re.sub(r'\n\s*libs\s*\([^)]*\)\s*;', '', cd)
            if new_cd != cd:
                (case_path / "system" / "controlDict").write_text(new_cd, encoding="utf-8")
                fixed_files.append("system/controlDict")

    elif "diverg" in error_lower or "floating point" in error_lower or "nan" in error_lower:
        error_type = "numerical"
        root_cause = "Numerical divergence detected — solver became unstable"
        affected_files = ["system/fvSolution", "system/controlDict"]
        # Fix: reduce relaxation factors and deltaT
        fvs = files.get("system/fvSolution", "")
        if fvs:
            new_fvs = re.sub(r'(relaxationFactors\s*\{[^}]*?)0\.\d+', r'\g<1>0.3', fvs)
            if new_fvs != fvs:
                (case_path / "system" / "fvSolution").write_text(new_fvs, encoding="utf-8")
                fixed_files.append("system/fvSolution")
        cd = files.get("system/controlDict", "")
        if cd:
            # Halve deltaT
            new_cd = re.sub(
                r'(deltaT\s+)(\d+\.?\d*)',
                lambda m: f"{m.group(1)}{float(m.group(2)) / 2}",
                cd
            )
            if new_cd != cd:
                (case_path / "system" / "controlDict").write_text(new_cd, encoding="utf-8")
                fixed_files.append("system/controlDict")

    return {
        "diagnosis": {
            "failure_stage": failure_stage,
            "error_type": error_type,
            "root_cause": root_cause,
            "affected_files": affected_files,
        },
        "fixed": len(fixed_files) > 0,
        "fixed_files": fixed_files,
        "summary": f"Static diagnosis: {failure_stage}/{error_type}. Fixed {len(fixed_files)} file(s)." if fixed_files else f"Static diagnosis: {failure_stage}/{error_type}. Could not auto-fix.",
    }
