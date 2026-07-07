"""Small runtime compatibility helpers for supported Python versions."""

from datetime import timezone

UTC = timezone.utc

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - exercised on Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10-compatible subset of enum.StrEnum."""

        def __str__(self) -> str:
            return str(self.value)


__all__ = ["StrEnum", "UTC"]
