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
_APPLICATION = re.compile(r"\b(?:application|solver)\s+([A-Za-z][A-Za-z0-9_.-]*)\s*;")
_INCLUDE_LINE = re.compile(r"\s*#include(?:Etc)?\s+[\"<]([^\">]+)[\">]\s*")
_FORBIDDEN_CONTENT = (
    re.compile(r"^\s*#!"),
    re.compile(r"#\s*codeStream\b", re.IGNORECASE),
    re.compile(r"#\s*(?:calc|eval)\b", re.IGNORECASE),
    re.compile(r"\bdynamicCode\b", re.IGNORECASE),
    re.compile(r"\bcoded[A-Za-z0-9_]*\b", re.IGNORECASE),
    re.compile(r"\bcode(?:Execute|Write|End)?\s*#\{", re.IGNORECASE),
    re.compile(r"\bsystemCall\b", re.IGNORECASE),
    re.compile(r"\b(?:execute|command)\b", re.IGNORECASE),
    re.compile(r"\b(?:libs|dlopen)\b", re.IGNORECASE),
    re.compile(r"\$\("),
    re.compile(r"`"),
    re.compile(r"(?:^|[\s\"'])/(?:bin|sbin|usr/bin|usr/sbin)/", re.IGNORECASE),
)


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


def _without_comments(content: str) -> str:
    output: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(content):
        character = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if quote is not None:
            output.append(character)
            if character == "\\" and following:
                output.append(following)
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
            output.append(character)
            index += 1
            continue
        if character == "/" and following == "/":
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                index += 1
            continue
        if character == "/" and following == "*":
            end = content.find("*/", index + 2)
            if end < 0:
                raise GeneratedCaseRejected("case dictionary has an unterminated comment")
            index = end + 2
            continue
        output.append(character)
        index += 1
    return "".join(output)


def _validate_include_path(include_path: str) -> None:
    if (
        not include_path
        or "\\" in include_path
        or include_path.startswith("/")
        or include_path.startswith(("~", "$"))
        or (len(include_path) >= 2 and include_path[1] == ":")
        or ":" in include_path
    ):
        raise GeneratedCaseRejected("case dictionary has an unsafe include path")
    path = PurePosixPath(include_path)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise GeneratedCaseRejected("case dictionary has an unsafe include path")


def _validate_content(path: str, content: str) -> None:
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise GeneratedCaseRejected("case file content is not valid UTF-8") from error
    if _has_forbidden_control(content, text=True):
        raise GeneratedCaseRejected("case file content contains forbidden control characters")
    scanned = _without_comments(content)
    if any(pattern.search(scanned) for pattern in _FORBIDDEN_CONTENT):
        raise GeneratedCaseRejected("case file contains a forbidden directive")
    applications = re.findall(
        r"\bapplication\s+([A-Za-z][A-Za-z0-9_.-]*)\s*;", scanned
    )
    if any(application != "incompressibleFluid" for application in applications):
        raise GeneratedCaseRejected("case file selects an unsupported application")
    for line in scanned.splitlines():
        if re.match(r"\s*#include(?:Etc)?\b", line):
            match = _INCLUDE_LINE.fullmatch(line)
            if match is None:
                raise GeneratedCaseRejected("case dictionary has a malformed include")
            _validate_include_path(match.group(1))

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
    control_dict = _without_comments(files_by_path["system/controlDict"])
    declarations = _APPLICATION.findall(control_dict)
    if not declarations or any(value != "incompressibleFluid" for value in declarations):
        raise GeneratedCaseRejected(
            "controlDict must select Foundation 13 incompressibleFluid"
        )


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
