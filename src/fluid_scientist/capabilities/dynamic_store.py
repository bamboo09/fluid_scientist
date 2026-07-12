"""Persistence for dynamically verified capabilities."""

from __future__ import annotations

import json
from pathlib import Path

from fluid_scientist.capabilities.registry import Capability, CapabilityRegistry


class DynamicCapabilityStore:
    """Persist generated capabilities so later processes can reload them."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load_into(self, registry: CapabilityRegistry) -> list[Capability]:
        capabilities = self.load()
        for capability in capabilities:
            registry.register(capability)
        return capabilities

    def load(self) -> list[Capability]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return [Capability.model_validate(item) for item in payload]

    def save_from(self, registry: CapabilityRegistry) -> None:
        self.save(registry.list_extended())

    def save(self, capabilities: list[Capability]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                [capability.model_dump() for capability in capabilities],
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


__all__ = ["DynamicCapabilityStore"]
