"""Static validation and deterministic packaging for model-authored OpenFOAM cases."""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import tarfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

from fluid_scientist.adapters.custom_openfoam import (
    CustomCaseManifest,
    CustomCaseRejected,
    validate_custom_case_archive,
)
from fluid_scientist.case_generation.models import GeneratedCaseDraft
from fluid_scientist.case_generation.rendering import (
    GeneratedCaseRejected,
    RenderedGeneratedCase,
    render_defaults,
    render_generated_case,
)
from fluid_scientist.openfoam_security import (
    OpenFOAMSecurityRejected,
    require_literal_solver,
    validate_dictionary_security,
    validate_member_path_policy,
)

_ALLOWED_ROOTS = frozenset({"0", "constant", "system", "fluidScientist"})
_MANDATORY_CLASSES = {
    "0/U": "volVectorField",
    "0/p": "volScalarField",
    "constant/physicalProperties": "dictionary",
    "system/controlDict": "dictionary",
    "system/fvSchemes": "dictionary",
    "system/fvSolution": "dictionary",
    "system/blockMeshDict": "dictionary",
}
_SCRIPT_SUFFIXES = frozenset(
    {
        ".sh",
        ".bash",
        ".zsh",
        ".csh",
        ".fish",
        ".py",
        ".pl",
        ".rb",
        ".ps1",
        ".bat",
        ".cmd",
        ".exe",
        ".com",
        ".dll",
        ".so",
        ".dylib",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
    }
)
_FOAM_HEADER = re.compile(r"\bFoamFile\s*\{(?P<body>.*?)\}", re.DOTALL)
_CLASS = re.compile(r"\bclass\s+([A-Za-z][A-Za-z0-9_]*)\s*;")
_OBJECT = re.compile(r"\bobject\s+([A-Za-z][A-Za-z0-9_]*)\s*;")


@dataclass(frozen=True, slots=True)
class ValidatedGeneratedCase:
    files: tuple[tuple[str, str], ...]
    preprocessing: tuple[str, ...]
    archive: bytes
    archive_sha256: str
    manifest: CustomCaseManifest

    @property
    def digest(self) -> str:
        return self.archive_sha256

    @property
    def files_by_path(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.files))

    @property
    def preview(self) -> tuple[tuple[str, int], ...]:
        return tuple((path, len(content.encode("utf-8"))) for path, content in self.files)


def _has_forbidden_control(value: str, *, text: bool) -> bool:
    allowed = {"\n", "\r", "\t"} if text else set()
    return any(
        character not in allowed and unicodedata.category(character).startswith("C")
        for character in value
    )


def _normalize_paths(files: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_path, content in files:
        try:
            validate_member_path_policy(raw_path)
        except OpenFOAMSecurityRejected as error:
            raise GeneratedCaseRejected(str(error)) from error
        try:
            raw_path.encode("utf-8")
        except UnicodeEncodeError as error:
            raise GeneratedCaseRejected("case file path is not valid UTF-8") from error
        if (
            not raw_path
            or "\\" in raw_path
            or raw_path.startswith(("/", "//"))
            or (len(raw_path) >= 2 and raw_path[1] == ":")
            or ":" in raw_path
            or _has_forbidden_control(raw_path, text=False)
        ):
            raise GeneratedCaseRejected("case file path is unsafe")
        path = PurePosixPath(raw_path)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise GeneratedCaseRejected("case file path is not canonical")
        canonical = path.as_posix()
        if (
            canonical != raw_path
            or unicodedata.normalize("NFC", raw_path) != raw_path
            or len(path.parts) < 2
            or path.parts[0] not in _ALLOWED_ROOTS
        ):
            raise GeneratedCaseRejected("case file path is outside the allowed roots")
        if any(part.startswith(".") for part in path.parts):
            raise GeneratedCaseRejected("hidden case files are not allowed")
        if path.suffix.casefold() in _SCRIPT_SUFFIXES:
            raise GeneratedCaseRejected("script and executable case files are not allowed")
        collision_key = unicodedata.normalize("NFC", canonical).casefold()
        if collision_key in seen:
            raise GeneratedCaseRejected("case file path is a duplicate or case collision")
        seen.add(collision_key)
        normalized.append((canonical, content))
    return tuple(sorted(normalized))


def _validate_content(path: str, content: str) -> None:
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise GeneratedCaseRejected("case file content is not valid UTF-8") from error
    if _has_forbidden_control(content, text=True):
        raise GeneratedCaseRejected("case file content contains forbidden control characters")
    try:
        scan = validate_dictionary_security(content)
    except OpenFOAMSecurityRejected as error:
        raise GeneratedCaseRejected(str(error)) from error
    scanned = scan.comment_stripped

    expected_class = _MANDATORY_CLASSES.get(path)
    if expected_class is None:
        return
    header = _FOAM_HEADER.search(scanned)
    if header is None:
        raise GeneratedCaseRejected("mandatory case file has no FoamFile header")
    class_match = _CLASS.search(header.group("body"))
    if class_match is None or class_match.group(1) != expected_class:
        raise GeneratedCaseRejected("mandatory case file has the wrong FoamFile class")
    object_match = _OBJECT.search(header.group("body"))
    if object_match is None or object_match.group(1) != PurePosixPath(path).name:
        raise GeneratedCaseRejected("mandatory case file has the wrong FoamFile object")


def _validate_solver(files_by_path: Mapping[str, str]) -> None:
    try:
        scan = validate_dictionary_security(files_by_path["system/controlDict"])
        require_literal_solver(scan)
    except OpenFOAMSecurityRejected as error:
        raise GeneratedCaseRejected(str(error)) from error


def _package(files: tuple[tuple[str, str], ...]) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.GNU_FORMAT) as bundle:
        for path, content in files:
            payload = content.encode("utf-8")
            member = tarfile.TarInfo(path)
            member.size = len(payload)
            member.mode = 0o644
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = 0
            bundle.addfile(member, io.BytesIO(payload))
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=compressed, mtime=0, compresslevel=9
    ) as stream:
        stream.write(tar_buffer.getvalue())
    return compressed.getvalue()


def validate_generated_case(
    draft: GeneratedCaseDraft,
    values: Mapping[str, object] | None = None,
    *,
    max_archive_bytes: int = 50 * 1024 * 1024,
) -> ValidatedGeneratedCase:
    """Render, validate, deterministically package, and independently revalidate a draft."""

    rendered: RenderedGeneratedCase
    rendered = render_defaults(draft) if values is None else render_generated_case(draft, values)
    files = _normalize_paths(rendered.files)
    files_by_path = MappingProxyType(dict(files))
    missing = sorted(set(_MANDATORY_CLASSES) - set(files_by_path))
    if missing:
        raise GeneratedCaseRejected("mandatory OpenFOAM case files are missing")
    for path, content in files:
        _validate_content(path, content)
    _validate_solver(files_by_path)

    archive = _package(files)
    if not archive or len(archive) > max_archive_bytes:
        raise GeneratedCaseRejected("generated case archive size exceeds the allowed limit")
    try:
        manifest = validate_custom_case_archive(archive, max_archive_bytes=max_archive_bytes)
    except CustomCaseRejected as error:
        raise GeneratedCaseRejected(
            "generated case failed downstream archive validation"
        ) from error
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    if manifest.archive_sha256 != digest:
        raise GeneratedCaseRejected("generated case archive digest verification failed")
    return ValidatedGeneratedCase(
        files=files,
        preprocessing=draft.preprocessing,
        archive=archive,
        archive_sha256=digest,
        manifest=manifest,
    )


__all__ = ["GeneratedCaseRejected", "ValidatedGeneratedCase", "validate_generated_case"]
