"""Shared static trust-boundary checks for OpenFOAM dictionary text."""

from __future__ import annotations

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
    ".tar",
    ".tgz",
    ".zip",
    ".7z",
    ".bz2",
    ".xz",
    ".gz",
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
    re.compile(r"\b(?:libs|dlopen)\b", re.IGNORECASE),
    re.compile(r"\$\("),
    re.compile(r"`"),
    re.compile(r"(?:^|[\s\"'])/(?:bin|sbin|usr/bin|usr/sbin)/", re.IGNORECASE),
)
_SOLVER_KEYWORD = re.compile(r"\b(?:application|solver)\b")
_LITERAL_SOLVER = re.compile(
    r"\b(?:application|solver)\s+incompressibleFluid\s*;"
)


def scan_dictionary(content: str) -> DictionaryScan:
    """Remove comments and mask quoted strings without joining file boundaries."""

    stripped: list[str] = []
    operative: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(content):
        character = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if quote is not None:
            stripped.append(character)
            operative.append("\n" if character in "\r\n" else " ")
            if character == "\\" and following:
                stripped.append(following)
                operative.append("\n" if following in "\r\n" else " ")
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
            stripped.append(character)
            operative.append(" ")
            index += 1
            continue
        if character == "/" and following == "/":
            stripped.extend("  ")
            operative.extend("  ")
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                stripped.append(" ")
                operative.append(" ")
                index += 1
            continue
        if character == "/" and following == "*":
            stripped.extend("  ")
            operative.extend("  ")
            index += 2
            while index < len(content):
                character = content[index]
                following = content[index + 1] if index + 1 < len(content) else ""
                if character == "*" and following == "/":
                    stripped.extend("  ")
                    operative.extend("  ")
                    index += 2
                    break
                replacement = "\n" if character in "\r\n" else " "
                stripped.append(replacement)
                operative.append(replacement)
                index += 1
            else:
                raise OpenFOAMSecurityRejected(
                    "case dictionary has an unterminated comment"
                )
            continue
        stripped.append(character)
        operative.append(character)
        index += 1
    if quote is not None:
        raise OpenFOAMSecurityRejected("case dictionary has an unterminated quote")
    return DictionaryScan("".join(stripped), "".join(operative))


def validate_dictionary_security(content: str) -> DictionaryScan:
    scan = scan_dictionary(content)
    if _INCLUDE_FAMILY.search(scan.comment_stripped):
        raise OpenFOAMSecurityRejected("case dictionary include directives are forbidden")
    if any(pattern.search(scan.comment_stripped) for pattern in _FORBIDDEN_OPERATIVE):
        raise OpenFOAMSecurityRejected(
            "dynamic code, system calls, and forbidden directives are forbidden"
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
    "require_literal_solver",
    "scan_dictionary",
    "validate_dictionary_security",
    "validate_member_path_policy",
]
