"""Main OpenFOAM compiler: ``SimulationStudySpec`` → ``CompiledCase``.

The :class:`OpenFOAMCompiler` is the top-level entry point.  It delegates
to the Foundation 13 sub-compilers for each case file and assembles the
results into a :class:`CompiledCase`.

Determinism contract
--------------------
Compiling the *same* :class:`SimulationStudySpec` always produces the
*same* :class:`CompiledCase`:

* File contents are built from spec fields only — no wall-clock reads.
* ``compiled_at`` is derived from ``spec.provenance.created_at``.
* ``archive_sha256`` is computed from the sorted file dict.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.study_spec.models import SimulationStudySpec

from .foundation13 import (
    compile_control_dict,
    compile_fv_schemes,
    compile_fv_solution,
    compile_nu_tilda_field,
    compile_pressure_field,
    compile_transport_properties,
    compile_turbulence_properties,
    compile_velocity_field,
)

if TYPE_CHECKING:
    pass

__all__ = ["CompiledCase", "OpenFOAMCompiler"]

#: Compiler semantic version (bumped when output format changes).
_COMPILER_VERSION = "1.0.0"


class CompiledCase(BaseModel):
    """The result of compiling a :class:`SimulationStudySpec`.

    Attributes
    ----------
    case_id:
        Unique identifier for this compiled case.
    spec_id:
        The source spec identifier.
    spec_version:
        The source spec version number.
    files:
        Mapping of relative file paths (e.g. ``"system/controlDict"``)
        to their OpenFOAM dictionary content.
    archive_sha256:
        SHA-256 hash of all file contents (sorted by path), or ``None``.
    compiled_at:
        Deterministic timestamp (derived from spec provenance).
    compiler_version:
        Semantic version of the compiler that produced this case.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    spec_id: str
    spec_version: int
    files: dict[str, str] = Field(default_factory=dict)
    archive_sha256: str | None = None
    compiled_at: str
    compiler_version: str


class OpenFOAMCompiler:
    """Deterministic OpenFOAM Foundation 13 compiler.

    Parameters
    ----------
    openfoam_version:
        Target OpenFOAM version string (default ``"foundation13"``).
    """

    def __init__(self, openfoam_version: str = "foundation13") -> None:
        self.openfoam_version = openfoam_version
        self.compiler_version = f"fluid-scientist-openfoam-{openfoam_version}-{_COMPILER_VERSION}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(self, spec: SimulationStudySpec) -> CompiledCase:
        """Compile a full :class:`SimulationStudySpec` into a :class:`CompiledCase`.

        Files produced (always):
            ``system/controlDict``, ``system/fvSchemes``,
            ``system/fvSolution``, ``constant/transportProperties``,
            ``0/U``, ``0/p``

        Files produced (turbulent only):
            ``0/nuTilda``, ``constant/turbulenceProperties``
        """
        files: dict[str, str] = {}

        files["system/controlDict"] = compile_control_dict(
            spec.numerics,
            spec.observations,
            spec.spec_id,
        )
        files["system/fvSchemes"] = compile_fv_schemes(spec.numerics)
        files["system/fvSolution"] = compile_fv_solution(spec.numerics)
        files["constant/transportProperties"] = compile_transport_properties(spec.physics)
        files["0/U"] = compile_velocity_field(
            spec.boundaries,
            spec.geometry.domain,
        )
        files["0/p"] = compile_pressure_field(spec.boundaries)

        is_turbulent = (
            spec.numerics.turbulence_model is not None
            and spec.numerics.turbulence_model != "laminar"
        )
        if is_turbulent:
            files["constant/turbulenceProperties"] = compile_turbulence_properties(
                spec.numerics
            )
            files["0/nuTilda"] = compile_nu_tilda_field(spec.boundaries)

        archive_sha256 = self._compute_sha256(files)

        return CompiledCase(
            case_id=f"{spec.spec_id}_v{spec.version}",
            spec_id=spec.spec_id,
            spec_version=spec.version,
            files=files,
            archive_sha256=archive_sha256,
            compiled_at=spec.provenance.created_at,
            compiler_version=self.compiler_version,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sha256(files: dict[str, str]) -> str:
        """Compute a deterministic SHA-256 over the file dict.

        Files are sorted by path so that insertion order does not matter.
        """
        h = hashlib.sha256()
        for path in sorted(files):
            h.update(path.encode("utf-8"))
            h.update(b"\x00")
            h.update(files[path].encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()
