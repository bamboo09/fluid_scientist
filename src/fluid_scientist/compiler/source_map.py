"""Source-map data structures for the OpenFOAM 13 compiler.

The :class:`SourceMap` records, for every value written into a compiled
OpenFOAM dictionary file, the originating Case IR path and the component
that produced it.  This gives full provenance traceability from the
generated case back to the scientific intent.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceMapEntry(BaseModel):
    """A single provenance entry.

    Attributes:
        file: Target file path, e.g. ``"constant/physicalProperties"``.
        path: Dot-path within the file, e.g. ``"nu"``.
        source: Case IR path, e.g. ``"/materials/fluid/kinematic_viscosity"``.
        value: The value that was written.
        component_id: The component that generated this entry.
    """

    model_config = ConfigDict(extra="forbid")

    file: str
    path: str
    source: str
    value: Any
    component_id: str = ""


class SourceMap(BaseModel):
    """A collection of :class:`SourceMapEntry` records.

    Provides ``add`` and ``lookup`` helpers so the compiler can
    incrementally build and query the map during compilation.
    """

    model_config = ConfigDict(extra="forbid")

    entries: list[SourceMapEntry] = Field(default_factory=list)

    def add(
        self,
        file: str,
        path: str,
        source: str,
        value: Any,
        component_id: str = "",
    ) -> None:
        """Append a new source-map entry."""
        self.entries.append(
            SourceMapEntry(
                file=file,
                path=path,
                source=source,
                value=value,
                component_id=component_id,
            )
        )

    def lookup(self, file: str, path: str) -> SourceMapEntry | None:
        """Find the entry for *file* and *path*, or ``None``."""
        for e in self.entries:
            if e.file == file and e.path == path:
                return e
        return None

    def lookup_file(self, file: str) -> list[SourceMapEntry]:
        """Return all entries for a given file."""
        return [e for e in self.entries if e.file == file]

    def lookup_component(self, component_id: str) -> list[SourceMapEntry]:
        """Return all entries produced by a given component."""
        return [e for e in self.entries if e.component_id == component_id]


__all__ = [
    "SourceMap",
    "SourceMapEntry",
]
