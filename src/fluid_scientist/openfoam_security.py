"""Shared static trust-boundary checks for OpenFOAM dictionary text."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass


class OpenFOAMSecurityRejected(ValueError):
    """Dictionary text or a member path violates the fixed worker policy."""


@dataclass(frozen=True, slots=True)
class DictionaryScan:
    comment_stripped: str
    operative: str


_ARCHIVE_SUFFIXES = (
    ".tar.gz",
    ".tar.zst",
    ".tar.lz4",
    ".tar.bz2",
    ".tar.xz",
    ".tar",
    ".tgz",
    ".tbz",
    ".tbz2",
    ".txz",
    ".zip",
    ".7z",
    ".bz2",
    ".xz",
    ".gz",
    ".zst",
    ".rar",
    ".lz4",
    ".lzma",
    ".cpio",
    ".cab",
    ".iso",
)
TRUSTED_RUNTIME_LIBRARIES = frozenset(
    {
        "libfieldFunctionObjects.so",
        "libutilityFunctionObjects.so",
        "libforces.so",
        "libsampling.so",
    }
)
_INCLUDE_FAMILY = re.compile(r"#\s*include[A-Za-z0-9_]*", re.IGNORECASE)
_FORBIDDEN_OPERATIVE = (
    re.compile(r"(?:^|\n)\s*#!"),
    re.compile(r"#\s*(?:codeStream|calc|eval)\b", re.IGNORECASE),
    re.compile(r"\bdynamicCode\b", re.IGNORECASE),
    re.compile(r"\bcoded[A-Za-z0-9_]*\b", re.IGNORECASE),
    re.compile(r"\bcode(?:Execute|Write|End)?\s*#\{", re.IGNORECASE),
    re.compile(r"\bsystemCall\b", re.IGNORECASE),
    re.compile(r"\b(?:execute|command)\b", re.IGNORECASE),
    re.compile(r"\bdlopen\b", re.IGNORECASE),
    re.compile(r"\$\("),
    re.compile(r"`"),
    re.compile(r"(?:^|[\s\"'])/(?:bin|sbin|usr/bin|usr/sbin)/", re.IGNORECASE),
)
_SOLVER_KEYWORD = re.compile(r"\b(?:application|solver)\b")
_LITERAL_SOLVER = re.compile(
    r"\b(?:application|solver)\s+incompressibleFluid\s*;"
)
_LIBS_KEYWORD = re.compile(r"\blibs\b")
_LIBS_DECLARATION = re.compile(
    r'libs\s*\(\s*"(?P<library>[A-Za-z][A-Za-z0-9]*\.so)"\s*\)\s*;'
)


def scan_dictionary(content: str) -> DictionaryScan:
    """Remove comments and mask quoted strings without joining file boundaries."""

    stripped = io.StringIO()
    operative = io.StringIO()
    index = 0
    quote: str | None = None
    while index < len(content):
        character = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if quote is not None:
            stripped.write(character)
            operative.write("\n" if character in "\r\n" else " ")
            if character == "\\" and following:
                stripped.write(following)
                operative.write("\n" if following in "\r\n" else " ")
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
            stripped.write(character)
            operative.write(" ")
            index += 1
            continue
        if character == "/" and following == "/":
            stripped.write("  ")
            operative.write("  ")
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                stripped.write(" ")
                operative.write(" ")
                index += 1
            continue
        if character == "/" and following == "*":
            stripped.write("  ")
            operative.write("  ")
            index += 2
            while index < len(content):
                character = content[index]
                following = content[index + 1] if index + 1 < len(content) else ""
                if character == "*" and following == "/":
                    stripped.write("  ")
                    operative.write("  ")
                    index += 2
                    break
                replacement = "\n" if character in "\r\n" else " "
                stripped.write(replacement)
                operative.write(replacement)
                index += 1
            else:
                raise OpenFOAMSecurityRejected(
                    "case dictionary has an unterminated comment"
                )
            continue
        stripped.write(character)
        operative.write(character)
        index += 1
    if quote is not None:
        raise OpenFOAMSecurityRejected("case dictionary has an unterminated quote")
    return DictionaryScan(stripped.getvalue(), operative.getvalue())


def validate_dictionary_security(
    content: str, *, allowed_libraries: frozenset[str] = frozenset()
) -> DictionaryScan:
    scan = scan_dictionary(content)
    if _INCLUDE_FAMILY.search(scan.comment_stripped):
        raise OpenFOAMSecurityRejected("case dictionary include directives are forbidden")
    if any(pattern.search(scan.comment_stripped) for pattern in _FORBIDDEN_OPERATIVE):
        raise OpenFOAMSecurityRejected(
            "dynamic code, system calls, and forbidden directives are forbidden"
        )
    for keyword in _LIBS_KEYWORD.finditer(scan.operative):
        declaration = _LIBS_DECLARATION.match(scan.comment_stripped, keyword.start())
        if (
            declaration is None
            or declaration.group("library") not in allowed_libraries
        ):
            raise OpenFOAMSecurityRejected(
                "case dictionary runtime library declaration is forbidden"
            )
    return scan


def require_literal_solver(scan: DictionaryScan) -> None:
    keywords = tuple(_SOLVER_KEYWORD.finditer(scan.operative))
    literals = tuple(_LITERAL_SOLVER.finditer(scan.operative))
    if "$" in scan.operative or len(keywords) != 1 or len(literals) != 1:
        raise OpenFOAMSecurityRejected(
            "controlDict must select exactly one literal incompressibleFluid solver"
        )


def validate_member_path_policy(path: str) -> None:
    try:
        encoded = path.encode("utf-8")
        components = tuple(part.encode("utf-8") for part in path.split("/"))
    except UnicodeEncodeError as error:
        raise OpenFOAMSecurityRejected("case member path is not valid UTF-8") from error
    if len(encoded) > 4095 or any(len(component) > 255 for component in components):
        raise OpenFOAMSecurityRejected("case member path exceeds safe filesystem limits")
    folded = path.casefold()
    if any(folded.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
        raise OpenFOAMSecurityRejected("archive and compression members are forbidden")


__all__ = [
    "DictionaryScan",
    "OpenFOAMSecurityRejected",
    "TRUSTED_RUNTIME_LIBRARIES",
    "require_literal_solver",
    "scan_dictionary",
    "validate_dictionary_security",
    "validate_member_path_policy",
]
