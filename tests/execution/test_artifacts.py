import hashlib

import pytest

from fluid_scientist.execution.artifacts import (
    ArtifactIntegrityError,
    ArtifactManifest,
    sha256_file,
    verify_artifact,
)


def test_artifact_checksum_and_manifest(tmp_path) -> None:
    artifact = tmp_path / "pipe-case.tar"
    artifact.write_bytes(b"immutable case")
    digest = hashlib.sha256(b"immutable case").hexdigest()

    manifest = ArtifactManifest(
        artifact_id="pipe-case-v1",
        source="git:abc1234",
        version=1,
        sha256=digest,
        size_bytes=len(b"immutable case"),
        build_log_id="build:pipe-case-v1",
        destination="projects/pipe/case-v1.tar",
    )

    assert sha256_file(artifact) == digest
    verify_artifact(artifact, manifest)


def test_artifact_checksum_mismatch_is_rejected(tmp_path) -> None:
    artifact = tmp_path / "pipe-case.tar"
    artifact.write_bytes(b"tampered")
    manifest = ArtifactManifest(
        artifact_id="pipe-case-v1",
        source="git:abc1234",
        version=1,
        sha256="a" * 64,
        size_bytes=len(b"tampered"),
        build_log_id="build:pipe-case-v1",
        destination="projects/pipe/case-v1.tar",
    )

    with pytest.raises(ArtifactIntegrityError, match="checksum"):
        verify_artifact(artifact, manifest)
