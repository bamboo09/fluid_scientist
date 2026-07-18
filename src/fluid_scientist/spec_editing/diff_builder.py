"""Diff generation for the spec-editing module.

The :class:`DiffBuilder` produces field-level diffs between two spec
dicts.  A :class:`SpecDiff` contains a list of :class:`FieldDiff`
entries, each describing one changed field with its old value, new
value, the operation that caused the change, and the source quote from
the user's original words.

The diff is **field-level**, not structural: nested dicts are recursed
into, and only leaf values (non-dict, non-list scalars, or entire
list/dict values when they differ as a whole) appear as
:class:`FieldDiff` entries.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import SimulationSpecPatch

__all__ = ["FieldDiff", "SpecDiff", "DiffBuilder"]


class _Missing:
    """Sentinel for missing values in deep comparison."""


_MISSING = _Missing()


class FieldDiff(BaseModel):
    """A single field-level difference between two spec versions.

    Parameters
    ----------
    path:
        JSON Pointer path of the changed field.
    old_value:
        The value before the patch (``None`` if the field was added).
    new_value:
        The value after the patch (``None`` if the field was removed).
    op:
        The operation type that caused this change (``"replace"``,
        ``"add"``, ``"remove"``, ``"merge"``, …).
    source_quote:
        The user's original words that motivated this change, or
        ``None`` if the change was not directly tied to a patch
        operation.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    old_value: Any | None = None
    new_value: Any | None = None
    op: str = "replace"
    source_quote: str | None = None


class SpecDiff(BaseModel):
    """A complete diff between two spec versions.

    Parameters
    ----------
    base_version:
        The version number before the patch.
    new_version:
        The version number after the patch.
    field_diffs:
        List of individual field-level changes.
    summary:
        Human-readable one-line summary of the diff.
    """

    model_config = ConfigDict(extra="forbid")

    base_version: int
    new_version: int
    field_diffs: list[FieldDiff] = Field(default_factory=list)
    summary: str = ""


class DiffBuilder:
    """Build a :class:`SpecDiff` by deep-comparing two spec dicts.

    Usage::

        builder = DiffBuilder()
        diff = builder.build_diff(old_spec.model_dump(),
                                  new_spec.model_dump(),
                                  patch)
    """

    def build_diff(
        self,
        old_spec: dict[str, Any],
        new_spec: dict[str, Any],
        patch: SimulationSpecPatch,
    ) -> SpecDiff:
        """Produce a field-level diff between *old_spec* and *new_spec*.

        The *patch* is used to annotate each :class:`FieldDiff` with the
        ``source_quote`` and ``op`` from the corresponding
        :class:`PatchOperation`.

        Parameters
        ----------
        old_spec:
            The spec dict before the patch.
        new_spec:
            The spec dict after the patch.
        patch:
            The :class:`SimulationSpecPatch` that was applied.

        Returns
        -------
        A :class:`SpecDiff` with one :class:`FieldDiff` per changed
        leaf field.
        """
        # Build a lookup from path -> (op, source_quote) from the patch.
        op_lookup: dict[str, tuple[str, str | None]] = {}
        for op_obj in patch.operations:
            op_lookup[op_obj.path] = (op_obj.op, op_obj.source_quote)

        field_diffs: list[FieldDiff] = []
        self._deep_compare(old_spec, new_spec, "", field_diffs, op_lookup)

        base_version = old_spec.get("version", patch.base_version)
        new_version = new_spec.get("version", base_version + 1)

        summary = self._build_summary(field_diffs)

        return SpecDiff(
            base_version=base_version,
            new_version=new_version,
            field_diffs=field_diffs,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Internal: recursive deep comparison
    # ------------------------------------------------------------------

    def _deep_compare(
        self,
        old: Any,
        new: Any,
        path: str,
        diffs: list[FieldDiff],
        op_lookup: dict[str, tuple[str, str | None]],
    ) -> None:
        """Recursively compare *old* and *new*, appending
        :class:`FieldDiff` entries to *diffs*."""

        # Both dicts — recurse into keys.
        if isinstance(old, dict) and isinstance(new, dict):
            all_keys = set(old.keys()) | set(new.keys())
            for key in sorted(all_keys):
                child_path = f"{path}/{key}"
                self._deep_compare(
                    old.get(key, _MISSING),
                    new.get(key, _MISSING),
                    child_path,
                    diffs,
                    op_lookup,
                )
            return

        # Both lists — compare element by element, plus length differences.
        if isinstance(old, list) and isinstance(new, list):
            max_len = max(len(old), len(new))
            for i in range(max_len):
                child_path = f"{path}/{i}"
                old_elem = old[i] if i < len(old) else _MISSING
                new_elem = new[i] if i < len(new) else _MISSING
                self._deep_compare(old_elem, new_elem, child_path, diffs, op_lookup)
            return

        # Leaf comparison.
        if old is _MISSING and new is not _MISSING:
            op_type, quote = self._lookup_op(path, op_lookup, "add")
            diffs.append(FieldDiff(
                path=path,
                old_value=None,
                new_value=new,
                op=op_type,
                source_quote=quote,
            ))
        elif new is _MISSING and old is not _MISSING:
            op_type, quote = self._lookup_op(path, op_lookup, "remove")
            diffs.append(FieldDiff(
                path=path,
                old_value=old,
                new_value=None,
                op=op_type,
                source_quote=quote,
            ))
        elif old != new:
            op_type, quote = self._lookup_op(path, op_lookup, "replace")
            diffs.append(FieldDiff(
                path=path,
                old_value=old,
                new_value=new,
                op=op_type,
                source_quote=quote,
            ))

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _lookup_op(
        path: str,
        op_lookup: dict[str, tuple[str, str | None]],
        default_op: str,
    ) -> tuple[str, str | None]:
        """Look up the operation type and source quote for *path*.

        If the exact path is not in *op_lookup*, try parent paths
        (e.g. ``/numerics/time/end_time`` might be under
        ``/numerics/time/end_time``).
        """
        if path in op_lookup:
            return op_lookup[path]
        # Try stripping the last segment (the leaf value) and matching
        # the parent container path.
        parent = path.rsplit("/", 1)[0] if "/" in path else path
        if parent in op_lookup:
            return op_lookup[parent]
        return default_op, None

    @staticmethod
    def _build_summary(field_diffs: list[FieldDiff]) -> str:
        """Build a one-line summary of the diff."""
        if not field_diffs:
            return "No changes."
        n = len(field_diffs)
        paths = [d.path for d in field_diffs[:3]]
        if n <= 3:
            return f"Changed {n} field(s): {', '.join(paths)}"
        return f"Changed {n} field(s): {', '.join(paths)}, ..."
