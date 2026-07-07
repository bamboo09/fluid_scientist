"""Immutable artifact manifests and local integrity verification."""

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fluid_scientist.execution.ssh import RemoteArg


class ArtifactIntegrityError(RuntimeError):
    pass


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    build_log_id: str = Field(min_length=1)
    destination: str

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, value: str) -> str:
        RemoteArg(value)
        if value.startswith("/"):
            raise ValueError("destination must be relative to the configured artifact root")
        return value


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: Path, manifest: ArtifactManifest) -> None:
    if path.stat().st_size != manifest.size_bytes:
        raise ArtifactIntegrityError("artifact size does not match manifest")
    if sha256_file(path) != manifest.sha256:
        raise ArtifactIntegrityError("artifact checksum does not match manifest")
