"""Runtime build identity and Trae-baseline ancestry guard."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter


TRAE_MERGE_BASELINE_SHA = "98cfed86139a4ef5fd7a52509991d83aa7edb433"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api/system", tags=["system"])


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=_REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def _git_succeeds(*args: str) -> bool:
    try:
        subprocess.run(
            ["git", *args],
            cwd=_REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def get_build_info() -> dict[str, Any]:
    """Return the code identity actually serving the current process."""
    current_sha = os.environ.get("FLUID_BUILD_SHA") or _git("rev-parse", "HEAD")
    branch = os.environ.get("FLUID_BUILD_BRANCH") or _git("branch", "--show-current")
    contains_baseline = False
    if current_sha:
        contains_baseline = _git_succeeds(
            "merge-base", "--is-ancestor", TRAE_MERGE_BASELINE_SHA, current_sha
        )
    return {
        "current_sha": current_sha,
        "branch": branch,
        "required_baseline_sha": TRAE_MERGE_BASELINE_SHA,
        "contains_required_baseline": contains_baseline,
        "source": "environment" if os.environ.get("FLUID_BUILD_SHA") else "git",
    }


@router.get("/build-info")
async def build_info() -> dict[str, Any]:
    return get_build_info()
