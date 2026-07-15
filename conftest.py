"""Project-root conftest for pytest.

This file exists solely to work around broken filesystem directory entries
(``tmp_test``, ``.tmp_pytest``, ``.pytest_cache``) that cannot be stat'd by
Python on this machine.  ``collect_ignore`` is checked by pytest's
``pytest_ignore_collect`` hook *before* it calls ``Path.is_dir()``, so
listing them here prevents the ``FileNotFoundError`` that would otherwise
abort collection before any tests run.
"""
from __future__ import annotations

collect_ignore = [
    "tmp_test",
    ".tmp_pytest",
    ".pytest_cache",
]
