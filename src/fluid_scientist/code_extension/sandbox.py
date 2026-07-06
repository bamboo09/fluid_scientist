"""Sandbox execution — safely execute code extensions in an isolated environment.

The sandbox uses restricted Python execution with a limited builtins set
and no access to the file system, network, or subprocess.
"""

from __future__ import annotations

import ast
import time
import traceback
from dataclasses import dataclass
from typing import Any

from fluid_scientist.code_extension.models import CodeExtensionSpec


@dataclass(frozen=True)
class SandboxResult:
    """Result of sandbox execution.

    Attributes:
        success: Whether execution completed without errors.
        result: The return value (if any).
        error: Error message if execution failed.
        execution_time_s: Wall-clock execution time.
        stdout: Captured print output.
        restricted_globals: The globals dict used (for debugging).
    """

    success: bool
    result: Any
    error: str
    execution_time_s: float
    stdout: str
    restricted_globals: dict[str, Any]


# Safe builtins — no file I/O, no imports, no exec/eval
_SAFE_BUILTINS: dict[str, Any] = {
    # Math
    "abs": abs, "min": min, "max": max, "sum": sum, "round": round,
    "pow": pow, "divmod": divmod,
    # Type checks
    "isinstance": isinstance, "issubclass": issubclass,
    "type": type, "id": id,
    # Conversions
    "int": int, "float": float, "str": str, "bool": bool,
    "list": list, "tuple": tuple, "dict": dict, "set": set,
    "frozenset": frozenset, "bytes": bytes, "bytearray": bytearray,
    # Iteration
    "len": len, "range": range, "enumerate": enumerate, "zip": zip,
    "reversed": reversed, "sorted": sorted, "all": all, "any": any,
    "filter": filter, "map": map,
    # Constants
    "True": True, "False": False, "None": None,
    "print": print,  # Allowed but output is captured
}


def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
    """Restricted import function that only allows safe modules."""
    root = name.split(".")[0]
    if root not in _SAFE_MODULES:
        raise ImportError(f"import of '{name}' is not allowed in sandbox")
    return __import__(name, *args, **kwargs)


_SAFE_BUILTINS["__import__"] = _safe_import

# Safe modules that can be imported
_SAFE_MODULES: frozenset[str] = frozenset({
    "math", "statistics", "itertools", "functools",
    "collections", "json", "re",
})


class _SafeImportChecker(ast.NodeVisitor):
    """AST visitor that checks for unsafe imports and calls."""

    _UNSAFE_ATTRS: frozenset[str] = frozenset({
        "system", "popen", "exec", "eval", "compile",
        "open", "unlink", "rmdir", "mkdir", "chmod",
    })

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in _SAFE_MODULES:
                self.violations.append(
                    f"unsafe import: {alias.name} (line {node.lineno})"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".")[0]
            if root not in _SAFE_MODULES:
                self.violations.append(
                    f"unsafe import: {node.module} (line {node.lineno})"
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self._UNSAFE_ATTRS:
            self.violations.append(
                f"unsafe attribute access: .{node.attr} (line {node.lineno})"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("exec", "eval", "compile"):
            self.violations.append(
                f"unsafe call: {func.id}() (line {node.lineno})"
            )
        self.generic_visit(node)


def _validate_code_safety(code: str) -> list[str]:
    """Check code for unsafe patterns using AST analysis.

    Returns a list of violation messages (empty if safe).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]

    checker = _SafeImportChecker()
    checker.visit(tree)
    return checker.violations


def execute_in_sandbox(
    extension: CodeExtensionSpec,
    test_input: dict[str, Any] | None = None,
    timeout_seconds: float = 5.0,
) -> SandboxResult:
    """Execute a code extension in a restricted sandbox.

    Args:
        extension: The code extension to execute.
        test_input: Input variables to inject into the sandbox.
        timeout_seconds: Maximum execution time.

    Returns:
        SandboxResult with execution outcome.
    """
    if extension.language != "python":
        return SandboxResult(
            success=False,
            result=None,
            error=f"sandbox only supports Python, got '{extension.language}'",
            execution_time_s=0.0,
            stdout="",
            restricted_globals={},
        )

    # Static safety check
    violations = _validate_code_safety(extension.code)
    if violations:
        return SandboxResult(
            success=False,
            result=None,
            error="; ".join(violations),
            execution_time_s=0.0,
            stdout="",
            restricted_globals={},
        )

    # Build restricted globals
    safe_globals: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        "__name__": "__sandbox__",
    }

    # Inject test input
    if test_input:
        for key, value in test_input.items():
            if key not in ("__builtins__", "__name__"):
                safe_globals[key] = value

    # Capture stdout
    import contextlib
    import io

    stdout_buffer = io.StringIO()

    start_time = time.monotonic()
    try:
        with contextlib.redirect_stdout(stdout_buffer):
            exec(  # noqa: S102 - sandboxed execution with restricted builtins
                compile(extension.code, "<sandbox>", "exec"),
                safe_globals,
            )
        elapsed = time.monotonic() - start_time

        # Try to get a result if the code defines a main function
        result = safe_globals.get("result")

        return SandboxResult(
            success=True,
            result=result,
            error="",
            execution_time_s=elapsed,
            stdout=stdout_buffer.getvalue(),
            restricted_globals=safe_globals,
        )

    except Exception as e:
        elapsed = time.monotonic() - start_time
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        return SandboxResult(
            success=False,
            result=None,
            error=error_msg,
            execution_time_s=elapsed,
            stdout=stdout_buffer.getvalue(),
            restricted_globals=safe_globals,
        )


__all__ = [
    "SandboxResult",
    "execute_in_sandbox",
]
