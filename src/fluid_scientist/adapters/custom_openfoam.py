"""Static validation for user-supplied OpenFOAM case archives."""

import hashlib
import io
import tarfile
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.openfoam_security import (
    OpenFOAMSecurityRejected,
    require_literal_solver,
    validate_dictionary_security,
    validate_member_path_policy,
)


class CustomCaseRejected(ValueError):
    pass


class CustomCaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    archive_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    solver: Literal["incompressibleFluid"]
    members: tuple[str, ...]
    has_mesh: bool
    needs_block_mesh: bool
    needs_mirror_mesh: bool
    uncompressed_bytes: int = Field(ge=0)


_REQUIRED = {
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
}
def validate_custom_case_archive(
    payload: bytes,
    *,
    max_archive_bytes: int = 50 * 1024 * 1024,
    max_uncompressed_bytes: int = 500 * 1024 * 1024,
    max_members: int = 5_000,
) -> CustomCaseManifest:
    if not payload or len(payload) > max_archive_bytes:
        raise CustomCaseRejected("archive size exceeds the allowed limit")
    try:
        bundle = tarfile.open(fileobj=io.BytesIO(payload), mode="r:*")  # noqa: SIM115
    except tarfile.TarError as error:
        raise CustomCaseRejected("archive is not a readable tar bundle") from error

    with bundle:
        members = bundle.getmembers()
        if len(members) > max_members:
            raise CustomCaseRejected("archive contains too many members")
        names: list[str] = []
        texts: dict[str, str] = {}
        total_size = 0
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                raise CustomCaseRejected("archive member path is unsafe")
            try:
                validate_member_path_policy(member.name)
            except OpenFOAMSecurityRejected as error:
                raise CustomCaseRejected(str(error)) from error
            if member.issym() or member.islnk():
                raise CustomCaseRejected("archive links are not allowed")
            if not (member.isfile() or member.isdir()):
                raise CustomCaseRejected("archive contains a forbidden member type")
            total_size += member.size
            if total_size > max_uncompressed_bytes:
                raise CustomCaseRejected("archive expands beyond the allowed limit")
            normalized = path.as_posix()
            names.append(normalized)
            if member.isfile():
                handle = bundle.extractfile(member)
                if handle is None:
                    raise CustomCaseRejected("archive member could not be read")
                raw = handle.read()
                try:
                    texts[normalized] = raw.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise CustomCaseRejected(
                        "case file content is not valid UTF-8"
                    ) from error

    name_set = set(names)
    missing = sorted(_REQUIRED - name_set)
    if missing:
        raise CustomCaseRejected("required OpenFOAM case files are missing: " + ", ".join(missing))
    if not any(name.startswith("0/") for name in name_set):
        raise CustomCaseRejected("initial field directory 0 is missing")
    has_mesh = any(name.startswith("constant/polyMesh/") for name in name_set)
    has_block_mesh = "system/blockMeshDict" in name_set
    if not has_mesh and not has_block_mesh:
        raise CustomCaseRejected("case needs constant/polyMesh or system/blockMeshDict")

    try:
        scans = {
            name: validate_dictionary_security(text) for name, text in texts.items()
        }
        require_literal_solver(scans["system/controlDict"])
    except OpenFOAMSecurityRejected as error:
        raise CustomCaseRejected(str(error)) from error

    return CustomCaseManifest(
        archive_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        solver="incompressibleFluid",
        members=tuple(sorted(name_set)),
        has_mesh=has_mesh,
        needs_block_mesh=not has_mesh,
        needs_mirror_mesh="system/mirrorMeshDict" in name_set,
        uncompressed_bytes=total_size,
    )
