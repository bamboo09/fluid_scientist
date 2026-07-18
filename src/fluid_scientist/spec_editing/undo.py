"""Undo support for the spec-editing module.

The :class:`UndoEngine` generates *reverse patches* that, when applied,
restore the spec to the state before a given patch was applied.

For each operation in the original patch, the undo engine generates a
reverse operation:

* ``replace`` → reverse ``replace`` (stores old value).
* ``add`` → reverse ``remove``.
* ``remove`` → reverse ``add`` (with the old value).
* ``merge`` → reverse ``replace`` (with the old value).
* ``move`` → reverse ``move`` (swap from/to paths).
* ``append_unique`` → reverse ``remove`` (remove the appended element).

The reverse patch carries the same ``patch_id`` suffix (``"_undo"``) and
a new ``base_version`` matching the post-application version.
"""

from __future__ import annotations

from typing import Any

from .models import PatchOperation, SimulationSpecPatch

__all__ = ["UndoEngine"]


class UndoEngine:
    """Generate reverse patches that undo a previously applied patch.

    Usage::

        undo_engine = UndoEngine()
        reverse_patch = undo_engine.create_reverse_patch(patch, current_spec_dict)
        # Apply reverse_patch to revert.
    """

    def create_reverse_patch(
        self,
        patch: SimulationSpecPatch,
        current_spec: dict[str, Any],
    ) -> SimulationSpecPatch:
        """Generate a :class:`SimulationSpecPatch` that reverses *patch*.

        Parameters
        ----------
        patch:
            The original patch that was applied.
        current_spec:
            The spec dict **before** *patch* was applied (i.e. the
            pre-patch state).  This is used to read the original
            (pre-patch) values so the reverse patch can restore them.
            When the resulting reverse patch is applied to the
            post-patch spec, it will restore the pre-patch values.

        Returns
        -------
        A new :class:`SimulationSpecPatch` whose operations, when
        applied to the post-patch spec, will restore the spec to its
        pre-*patch* state.
        """
        reverse_ops: list[PatchOperation] = []

        for op in patch.operations:
            reverse_op = self._reverse_operation(op, current_spec)
            if reverse_op is not None:
                reverse_ops.append(reverse_op)

        # The reverse patch targets the post-application version.
        post_version = patch.base_version + 1

        return SimulationSpecPatch(
            patch_id=f"{patch.patch_id}_undo",
            session_id=patch.session_id,
            base_spec_id=patch.base_spec_id,
            base_version=post_version,
            intent="undo_last_patch",
            operations=reverse_ops,
            clarifications=[],
            impact_requests=[],
            untouched_guarantee=patch.untouched_guarantee,
            assistant_message=f"Undo of patch {patch.patch_id}",
        )

    # ------------------------------------------------------------------
    # Internal: per-operation reversal
    # ------------------------------------------------------------------

    def _reverse_operation(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation | None:
        """Generate the reverse of a single :class:`PatchOperation`.

        Returns ``None`` if the operation cannot be reversed (e.g.
        ``test``, ``copy``, ``declare_unknown_capability``).
        """
        if op.op == "replace":
            return self._reverse_replace(op, current_spec)
        elif op.op == "add":
            return self._reverse_add(op, current_spec)
        elif op.op == "remove":
            return self._reverse_remove(op, current_spec)
        elif op.op == "merge":
            return self._reverse_merge(op, current_spec)
        elif op.op == "move":
            return self._reverse_move(op, current_spec)
        elif op.op == "append_unique":
            return self._reverse_append_unique(op, current_spec)
        elif op.op == "set_relation":
            return self._reverse_set_relation(op, current_spec)
        elif op.op == "unset_relation":
            return self._reverse_unset_relation(op, current_spec)
        # test, copy, declare_unknown_capability — not reversible.
        return None

    def _reverse_replace(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation:
        """Reverse a ``replace``: read current value and replace back."""
        current_value = self._read_value(current_spec, op.path)
        return PatchOperation(
            op="replace",
            path=op.path,
            value=current_value,
            source_quote=f"Undo: {op.source_quote}",
            confidence=op.confidence,
            rationale=f"Reversing replace at {op.path}",
        )

    def _reverse_add(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation:
        """Reverse an ``add``: remove the added value."""
        return PatchOperation(
            op="remove",
            path=op.path,
            source_quote=f"Undo: {op.source_quote}",
            confidence=op.confidence,
            rationale=f"Reversing add at {op.path}",
        )

    def _reverse_remove(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation:
        """Reverse a ``remove``: re-add the old value (from op.value
        if it was stored, or from the current spec which won't have it).

        For ``remove``, the original value should have been stored in
        ``op.value`` by the executor.  If not, we cannot restore it.
        """
        old_value = op.value
        return PatchOperation(
            op="add",
            path=op.path,
            value=old_value,
            source_quote=f"Undo: {op.source_quote}",
            confidence=op.confidence,
            rationale=f"Reversing remove at {op.path}",
        )

    def _reverse_merge(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation:
        """Reverse a ``merge``: replace with the pre-merge value.

        Since ``merge`` combines the old dict with the new dict, the
        reverse is to replace with the old value.  The old value is read
        from the current spec (which has the merged result); we cannot
        perfectly un-merge, so we replace with the value at the path
        before the merge.  Since we don't have the pre-merge value here,
        we store ``None`` and rely on the executor to have recorded it.

        For simplicity, the reverse of merge stores the current value
        (post-merge) so that a subsequent undo will at least not crash.
        The proper pre-merge value should be captured by the executor.
        """
        current_value = self._read_value(current_spec, op.path)
        return PatchOperation(
            op="replace",
            path=op.path,
            value=current_value,
            source_quote=f"Undo: {op.source_quote}",
            confidence=op.confidence,
            rationale=f"Reversing merge at {op.path}",
        )

    def _reverse_move(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation:
        """Reverse a ``move``: move the value back from *path* to
        *from_path*."""
        current_value = self._read_value(current_spec, op.path)
        return PatchOperation(
            op="move",
            path=op.from_path or "",
            from_path=op.path,
            value=current_value,
            source_quote=f"Undo: {op.source_quote}",
            confidence=op.confidence,
            rationale=f"Reversing move from {op.from_path} to {op.path}",
        )

    def _reverse_append_unique(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation:
        """Reverse an ``append_unique``: remove the appended element."""
        return PatchOperation(
            op="remove",
            path=op.path,
            value=op.value,
            source_quote=f"Undo: {op.source_quote}",
            confidence=op.confidence,
            rationale=f"Reversing append_unique at {op.path}",
        )

    def _reverse_set_relation(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation:
        """Reverse a ``set_relation``: unset the relation."""
        return PatchOperation(
            op="unset_relation",
            path=op.path,
            entity_id=op.entity_id,
            relation=op.relation,
            source_quote=f"Undo: {op.source_quote}",
            confidence=op.confidence,
            rationale="Reversing set_relation",
        )

    def _reverse_unset_relation(
        self,
        op: PatchOperation,
        current_spec: dict[str, Any],
    ) -> PatchOperation:
        """Reverse an ``unset_relation``: re-set the relation."""
        return PatchOperation(
            op="set_relation",
            path=op.path,
            entity_id=op.entity_id,
            relation=op.relation,
            source_quote=f"Undo: {op.source_quote}",
            confidence=op.confidence,
            rationale="Reversing unset_relation",
        )

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_value(spec: dict[str, Any], json_pointer: str) -> Any:
        """Read the value at a JSON Pointer path from a spec dict."""
        if not json_pointer or json_pointer == "/":
            return spec
        parts = json_pointer.lstrip("/").split("/")
        current: Any = spec
        for part in parts:
            if part == "-":
                if isinstance(current, list) and current:
                    current = current[-1]
                else:
                    return None
                continue
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            elif isinstance(current, list):
                try:
                    idx = int(part)
                except ValueError:
                    return None
                if idx < 0 or idx >= len(current):
                    return None
                current = current[idx]
            else:
                return None
        return current
