"""Runtime version fingerprint for reproducible audit.

Provides repo, branch, commit, prompt hash, and compiler version
through a read-only diagnostic endpoint.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/v5", tags=["runtime"])


def _run_git(args: list[str], cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _hash_file(path: Path) -> str:
    if not path.exists():
        return "none"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _collect_source_hash(repo_root: str) -> str:
    """Hash all Python source files under src/fluid_scientist."""
    src_dir = Path(repo_root) / "src" / "fluid_scientist"
    if not src_dir.exists():
        return "none"
    h = hashlib.sha256()
    for py_file in sorted(src_dir.rglob("*.py")):
        rel = py_file.relative_to(src_dir)
        h.update(str(rel).encode())
        try:
            h.update(py_file.read_bytes())
        except (FileNotFoundError, OSError):
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def _collect_prompt_hash(repo_root: str) -> str:
    """Hash all prompt files."""
    prompt_dir = Path(repo_root) / "src" / "fluid_scientist" / "prompts"
    if not prompt_dir.exists():
        return "none"
    h = hashlib.sha256()
    for p in sorted(prompt_dir.iterdir()):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


_cache: dict[str, Any] | None = None


def get_runtime_info() -> dict[str, Any]:
    """Get cached runtime info (computed once at first call)."""
    global _cache
    if _cache is not None:
        return _cache

    repo_root = str(Path(__file__).resolve().parents[3])
    branch = _run_git(["branch", "--show-current"], repo_root)
    commit = _run_git(["rev-parse", "HEAD"], repo_root)
    short_commit = _run_git(["rev-parse", "--short", "HEAD"], repo_root)
    dirty = _run_git(["status", "--porcelain"], repo_root) != ""

    _cache = {
        "repo_root": repo_root,
        "branch": branch,
        "commit": commit,
        "short_commit": short_commit,
        "dirty": dirty,
        "source_hash": _collect_source_hash(repo_root),
        "prompt_hash": _collect_prompt_hash(repo_root),
        "compiler_version": "obstacle_flow_v1",
        "openfoam_distribution": "foundation",
        "openfoam_version": "13",
        "python_version": os.sys.version.split()[0],
    }
    return _cache


@router.get("/runtime-info")
async def runtime_info() -> dict[str, Any]:
    """Return runtime version fingerprint."""
    return get_runtime_info()
