"""Extension registry — plugin registration, approval workflow, and rollback."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from fluid_scientist.code_extension.models import (
    CodeExtensionSpec,
    ExtensionStatus,
)
from fluid_scientist.code_extension.sandbox import SandboxResult, execute_in_sandbox
from fluid_scientist.code_extension.testing import (
    TestResult,
    all_tests_passed,
    run_tests,
    test_summary,
)


@dataclass
class ExtensionRegistry:
    """Registry of code extensions with lifecycle management.

    Attributes:
        extensions: Registered extensions indexed by extension_id.
        history: Change history for each extension.
    """

    extensions: dict[str, CodeExtensionSpec] = field(default_factory=dict)
    history: dict[str, list[str]] = field(default_factory=dict)

    def register(self, extension: CodeExtensionSpec) -> None:
        """Register a new extension in DRAFT status."""
        if extension.extension_id in self.extensions:
            raise ValueError(
                f"extension '{extension.extension_id}' already registered"
            )
        self.extensions[extension.extension_id] = extension
        self._record_history(
            extension.extension_id,
            f"Registered as {extension.status.value}"
        )

    def get(self, extension_id: str) -> CodeExtensionSpec | None:
        """Get an extension by ID."""
        return self.extensions.get(extension_id)

    def list_by_status(self, status: ExtensionStatus) -> list[CodeExtensionSpec]:
        """List all extensions with a given status."""
        return [
            ext for ext in self.extensions.values() if ext.status == status
        ]

    def list_all(self) -> list[CodeExtensionSpec]:
        """List all registered extensions."""
        return list(self.extensions.values())

    def update(self, extension_id: str, extension: CodeExtensionSpec) -> None:
        """Update an existing extension."""
        if extension_id not in self.extensions:
            raise KeyError(f"extension '{extension_id}' not found")
        self.extensions[extension_id] = extension
        self._record_history(
            extension_id,
            f"Updated to status {extension.status.value}"
        )

    def _record_history(self, extension_id: str, message: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        entry = f"[{timestamp}] {message}"
        if extension_id not in self.history:
            self.history[extension_id] = []
        self.history[extension_id].append(entry)

    def get_history(self, extension_id: str) -> list[str]:
        """Get the change history for an extension."""
        return self.history.get(extension_id, [])


def approve_extension(
    registry: ExtensionRegistry,
    extension_id: str,
    reviewer: str,
    notes: str = "",
) -> CodeExtensionSpec:
    """Approve an extension that has passed auto-testing.

    The extension must be in AUTO_TESTED status. After approval, it moves
    to APPROVED status and can be registered as a plugin.

    Raises:
        ValueError: if the extension is not in AUTO_TESTED status.
    """
    extension = registry.get(extension_id)
    if extension is None:
        raise KeyError(f"extension '{extension_id}' not found")

    if extension.status != ExtensionStatus.AUTO_TESTED:
        raise ValueError(
            f"extension must be in AUTO_TESTED status, got {extension.status.value}"
        )

    now = datetime.now(timezone.utc).isoformat()
    updated = extension.model_copy(update={
        "status": ExtensionStatus.APPROVED,
        "review_notes": f"Approved by {reviewer}. {notes}".strip(),
        "updated_at": now,
    })
    registry.update(extension_id, updated)
    return updated


def register_plugin(
    registry: ExtensionRegistry,
    extension_id: str,
) -> CodeExtensionSpec:
    """Register an approved extension as an active plugin.

    The extension must be in APPROVED status. After registration, it is
    available for use in simulations.

    Raises:
        ValueError: if the extension is not in APPROVED status.
    """
    extension = registry.get(extension_id)
    if extension is None:
        raise KeyError(f"extension '{extension_id}' not found")

    if extension.status != ExtensionStatus.APPROVED:
        raise ValueError(
            f"extension must be in APPROVED status, got {extension.status.value}"
        )

    now = datetime.now(timezone.utc).isoformat()
    updated = extension.model_copy(update={
        "status": ExtensionStatus.REGISTERED,
        "updated_at": now,
    })
    registry.update(extension_id, updated)
    return updated


def rollback_extension(
    registry: ExtensionRegistry,
    extension_id: str,
    reason: str,
) -> CodeExtensionSpec:
    """Roll back a registered extension.

    The extension must be in REGISTERED or DEPRECATED status. After rollback,
    it moves to ROLLED_BACK status and is no longer available for use.

    Raises:
        ValueError: if the extension is not in a rollback-able status.
    """
    extension = registry.get(extension_id)
    if extension is None:
        raise KeyError(f"extension '{extension_id}' not found")

    if extension.status not in (ExtensionStatus.REGISTERED, ExtensionStatus.DEPRECATED):
        raise ValueError(
            f"extension must be REGISTERED or DEPRECATED to rollback, "
            f"got {extension.status.value}"
        )

    now = datetime.now(timezone.utc).isoformat()
    updated = extension.model_copy(update={
        "status": ExtensionStatus.ROLLED_BACK,
        "review_notes": f"Rolled back: {reason}",
        "updated_at": now,
    })
    registry.update(extension_id, updated)
    return updated


def sandbox_test_extension(
    registry: ExtensionRegistry,
    extension_id: str,
) -> tuple[CodeExtensionSpec, SandboxResult]:
    """Run sandbox testing for an extension.

    The extension must be in DRAFT status. After successful sandbox testing,
    it moves to SANDBOX_TESTED status.

    Returns the updated extension and the sandbox result.
    """
    extension = registry.get(extension_id)
    if extension is None:
        raise KeyError(f"extension '{extension_id}' not found")

    if extension.status != ExtensionStatus.DRAFT:
        raise ValueError(
            f"extension must be in DRAFT status, got {extension.status.value}"
        )

    result = execute_in_sandbox(extension)

    if result.success:
        now = datetime.now(timezone.utc).isoformat()
        updated = extension.model_copy(update={
            "status": ExtensionStatus.SANDBOX_TESTED,
            "updated_at": now,
        })
        registry.update(extension_id, updated)
        return updated, result
    else:
        # Transition to rejected
        now = datetime.now(timezone.utc).isoformat()
        updated = extension.model_copy(update={
            "status": ExtensionStatus.REJECTED,
            "review_notes": f"Sandbox failed: {result.error[:200]}",
            "updated_at": now,
        })
        registry.update(extension_id, updated)
        return updated, result


def auto_test_extension(
    registry: ExtensionRegistry,
    extension_id: str,
) -> tuple[CodeExtensionSpec, list[TestResult]]:
    """Run automatic tests for an extension.

    The extension must be in SANDBOX_TESTED status. After all tests pass,
    it moves to AUTO_TESTED status. If any test fails, it moves to REJECTED.

    Returns the updated extension and the test results.
    """
    extension = registry.get(extension_id)
    if extension is None:
        raise KeyError(f"extension '{extension_id}' not found")

    if extension.status != ExtensionStatus.SANDBOX_TESTED:
        raise ValueError(
            f"extension must be in SANDBOX_TESTED status, "
            f"got {extension.status.value}"
        )

    results = run_tests(extension)

    now = datetime.now(timezone.utc).isoformat()
    if all_tests_passed(results):
        updated = extension.model_copy(update={
            "status": ExtensionStatus.AUTO_TESTED,
            "updated_at": now,
        })
    else:
        summary = test_summary(results)
        updated = extension.model_copy(update={
            "status": ExtensionStatus.REJECTED,
            "review_notes": f"Auto-test failed:\n{summary}",
            "updated_at": now,
        })

    registry.update(extension_id, updated)
    return updated, results


__all__ = [
    "ExtensionRegistry",
    "approve_extension",
    "auto_test_extension",
    "register_plugin",
    "rollback_extension",
    "sandbox_test_extension",
]
