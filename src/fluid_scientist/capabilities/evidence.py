"""Evidence artifacts for generated capability verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class VerificationArtifact(BaseModel):
    """Concrete evidence that a generated capability was validated."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    extension_id: str
    capability_id: str
    requirement_id: str
    status: Literal["PASSED", "FAILED", "ENVIRONMENT_BLOCKED"]
    case_dir: str = ""
    validation_report: dict[str, Any] = Field(default_factory=dict)
    generated_files: list[str] = Field(default_factory=list)
    artifact_hash: str = ""


class TestManifest(BaseModel):
    """Manifest for tests and fixture cases used to verify an extension."""

    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    extension_id: str
    capability_id: str
    tests: list[str] = Field(default_factory=list)
    fixtures: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    result: Literal["PASSED", "FAILED", "ENVIRONMENT_BLOCKED"]


class EvidenceStore:
    """Persist verification artifacts and test manifests as content-addressed JSON."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(
        self,
        artifact: VerificationArtifact,
        manifest: TestManifest,
    ) -> tuple[str, str]:
        self.root.mkdir(parents=True, exist_ok=True)
        artifact_path = self.root / f"{artifact.artifact_id}.json"
        manifest_path = self.root / f"{manifest.manifest_id}.json"

        artifact_payload = artifact.model_dump()
        artifact_payload["artifact_hash"] = _hash_payload(artifact_payload)
        artifact_path.write_text(
            json.dumps(artifact_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        manifest_path.write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return str(artifact_path), str(manifest_path)


def _hash_payload(payload: dict[str, Any]) -> str:
    copy = dict(payload)
    copy["artifact_hash"] = ""
    encoded = json.dumps(copy, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = ["EvidenceStore", "TestManifest", "VerificationArtifact"]
