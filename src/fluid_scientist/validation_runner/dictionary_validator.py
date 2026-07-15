"""Dictionary validator for OpenFOAM 13 files.

The :class:`DictionaryValidator` checks the structural correctness of
each OpenFOAM dictionary file in a :class:`CompiledCase` -- brace
balance, FoamFile header validity, required-keyword presence, dimension
specification format, and value-type sanity.

This is a pure-Python structural check that simulates the kind of
validation ``foamDictionary`` would perform.  When OpenFOAM is installed,
these checks can be augmented with actual ``foamDictionary -entry`` calls.
"""

from __future__ import annotations

import re

from fluid_scientist.compiler.compiler import CompiledCase
from fluid_scientist.validation_runner.static_validator import ValidationResult


# ---------------------------------------------------------------------------
# Required keywords per file pattern
# ---------------------------------------------------------------------------

REQUIRED_KEYWORDS: dict[str, list[str]] = {
    "system/controlDict": ["application", "solver", "startFrom", "stopAt", "deltaT", "writeControl"],
    "system/fvSchemes": ["ddtSchemes", "gradSchemes", "divSchemes", "laplacianSchemes"],
    "system/fvSolution": ["solvers"],
    "system/blockMeshDict": ["vertices", "blocks", "boundary"],
    "constant/physicalProperties": ["viscosityModel", "nu"],
    "constant/momentumTransport": ["simulationType"],
}

# Files that should have a FoamFile header
HEADER_REQUIRED_FILES: list[str] = [
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
    "system/blockMeshDict",
    "constant/physicalProperties",
    "constant/momentumTransport",
]


class DictionaryValidator:
    """Validates OpenFOAM dictionary file structure.

    Checks performed per file:

    1. FoamFile header presence and correctness.
    2. Brace balance (``{`` and ``}``).
    3. Required keywords for known file types.
    4. Dimension specification format ``[M L T ...]``.
    5. Semicolon termination of leaf entries.
    """

    def __init__(self) -> None:
        pass

    def validate(self, case: CompiledCase) -> ValidationResult:
        """Run dictionary structure checks on all files.

        Parameters:
            case: The compiled case to validate.

        Returns:
            A :class:`ValidationResult` with all errors and warnings.
        """
        result = ValidationResult(check_name="dictionary_validation")

        for fpath, content in case.files.items():
            self._check_foamfile_header(fpath, content, result)
            self._check_brace_balance(fpath, content, result)
            self._check_required_keywords(fpath, content, result)
            self._check_dimensions(fpath, content, result)
            self._check_semicolons(fpath, content, result)

        return result

    # ------------------------------------------------------------------
    # FoamFile header
    # ------------------------------------------------------------------

    def _check_foamfile_header(
        self, fpath: str, content: str, result: ValidationResult
    ) -> None:
        """Check that the FoamFile header is present and well-formed."""
        if fpath not in HEADER_REQUIRED_FILES:
            return

        if "FoamFile" not in content:
            result.add_error(f"{fpath}: missing FoamFile header")
            return

        # Check version
        if not re.search(r"version\s+\d+\.\d+", content):
            result.add_error(f"{fpath}: FoamFile header missing 'version'")

        # Check format
        if not re.search(r"format\s+\w+", content):
            result.add_error(f"{fpath}: FoamFile header missing 'format'")

        # Check class (for field files)
        if fpath.startswith("0/"):
            if not re.search(r"class\s+\w+", content):
                result.add_error(f"{fpath}: FoamFile header missing 'class'")

        # Check object
        if not re.search(r"object\s+\w+", content):
            result.add_error(f"{fpath}: FoamFile header missing 'object'")

    # ------------------------------------------------------------------
    # Brace balance
    # ------------------------------------------------------------------

    def _check_brace_balance(
        self, fpath: str, content: str, result: ValidationResult
    ) -> None:
        """Check that curly braces are balanced."""
        # Remove comments and strings for counting
        cleaned = re.sub(r"//.*", "", content)
        cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'"[^"]*"', '""', cleaned)

        open_count = cleaned.count("{")
        close_count = cleaned.count("}")

        if open_count != close_count:
            result.add_error(
                f"{fpath}: unbalanced braces -- "
                f"{{ count={open_count}, }} count={close_count}"
            )

        # Check parentheses too (for blockMeshDict)
        paren_open = cleaned.count("(")
        paren_close = cleaned.count(")")

        if paren_open != paren_close:
            result.add_error(
                f"{fpath}: unbalanced parentheses -- "
                f"( count={paren_open}, ) count={paren_close}"
            )

    # ------------------------------------------------------------------
    # Required keywords
    # ------------------------------------------------------------------

    def _check_required_keywords(
        self, fpath: str, content: str, result: ValidationResult
    ) -> None:
        """Check that required top-level keywords are present."""
        required = REQUIRED_KEYWORDS.get(fpath, [])
        for kw in required:
            # Match keyword at start of line followed by whitespace, {, or ;
            # This covers: keyword value; keyword { ... }; keyword;
            pattern = rf"^\s*{kw}[\s;{{]"
            if not re.search(pattern, content, re.MULTILINE):
                result.add_error(
                    f"{fpath}: missing required keyword '{kw}'"
                )

    # ------------------------------------------------------------------
    # Dimension specifications
    # ------------------------------------------------------------------

    def _check_dimensions(
        self, fpath: str, content: str, result: ValidationResult
    ) -> None:
        """Check that dimension specifications are well-formed."""
        # Find all dimension specifications: keyword [n n n n n n n]
        dim_pattern = re.compile(r"(\w+)\s+\[(\s*[\d\s-]+\s*)\]")
        for m in dim_pattern.finditer(content):
            dim_name = m.group(1)
            dim_values = m.group(2).strip().split()
            if len(dim_values) != 7:
                result.add_error(
                    f"{fpath}: dimension '{dim_name}' has {len(dim_values)} "
                    f"components, expected 7 (mass length time temperature "
                    f"quantity current luminous)"
                )
            else:
                for val in dim_values:
                    try:
                        int(val)
                    except ValueError:
                        result.add_error(
                            f"{fpath}: dimension '{dim_name}' has "
                            f"non-integer component: {val}"
                        )

    # ------------------------------------------------------------------
    # Semicolon termination
    # ------------------------------------------------------------------

    def _check_semicolons(
        self, fpath: str, content: str, result: ValidationResult
    ) -> None:
        """Check that leaf dictionary entries are terminated with semicolons."""
        # This is a heuristic check: look for lines that look like
        # "keyword value" without a trailing semicolon or opening brace
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                continue
            if stripped.startswith("*"):
                continue

            # Skip lines that are just braces or parentheses
            if stripped in ("{", "}", "(", ")", "};", ");"):
                continue

            # Skip lines that end with { or ( (start of sub-dictionary)
            if stripped.endswith("{") or stripped.endswith("("):
                continue

            # Skip lines that are part of a vector or list
            if stripped.startswith("(") and stripped.endswith(")"):
                continue

            # Check for "keyword value" pattern that should end with ;
            # but not if it's inside a list or already ends with ;
            if re.match(r"^\w+\s+\S+", stripped):
                if not stripped.endswith(";") and not stripped.endswith("}") and not stripped.endswith(")"):
                    # Check it's not a sub-dictionary key
                    if not re.match(r"^\w+\s*$", stripped):
                        result.add_warning(
                            f"{fpath}:{i}: entry may be missing semicolon: "
                            f"{stripped[:60]}"
                        )


__all__ = [
    "DictionaryValidator",
    "REQUIRED_KEYWORDS",
    "HEADER_REQUIRED_FILES",
]
