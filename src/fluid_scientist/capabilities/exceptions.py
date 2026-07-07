"""Exception handling for capability resolution — no silent swallowing."""

from __future__ import annotations

from fluid_scientist.capabilities.models import MissingCapability


class CapabilityError(Exception):
    """Base error for capability resolution failures."""

    def __init__(
        self,
        message: str,
        *,
        capabilities: list[MissingCapability] | None = None,
    ) -> None:
        super().__init__(message)
        self.capabilities = capabilities or []


class BlockingCapabilityError(CapabilityError):
    """Raised when blocking capabilities prevent workflow progression."""

    pass


class ExtensionApprovalError(CapabilityError):
    """Raised when extension approval fails."""

    pass


__all__ = ["BlockingCapabilityError", "CapabilityError", "ExtensionApprovalError"]
