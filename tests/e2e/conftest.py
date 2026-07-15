"""Pytest configuration for the end-to-end scenario tests.

The ``fluid_scientist`` package used to run these tests must resolve to the
project's own ``src/`` tree (which contains the new pipeline modules:
``llm_pipeline``, ``case_ir``, ``capabilities`` and ``platform``).  In some
local environments a stale editable install of an older copy of the package
shadows the project source.  To make the e2e suite self-contained and
deterministic, we prepend the project ``src`` directory to ``sys.path`` so
that the local implementation always wins.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
