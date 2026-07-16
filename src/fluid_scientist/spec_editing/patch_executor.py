"""Atomic patch application for the spec-editing module.

The :class:`PatchExecutor` applies a :class:`SimulationSpecPatch` to a
:class:`SimulationStudySpec` in an **all-or-nothing** fashion.

Pipeline:

1. **Validate** the patch using :class:`PatchValidator`.
2. **Dry-run** on a deep copy — apply every operation to the copy.  If
   any operation fails, the copy is discarded and no changes are made.
3. **Apply** — if the dry-run succeeds, the original spec dict is
   replaced with the modified copy.
4. **Update provenance** — append a modification-history entry.
5. **Build diff** — produce a :class:`SpecDiff` via :class:`DiffBuilder`.
6. **Return** the new :class:`SimulationStudySpec` and the
   :class:`SpecDiff`.

The executor never partially applies a patch.  If operation 3 of 5
fails, operations 1-2 are rolled back (the copy is simply discarded).
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from fluid_scientist.compat import UTC
from fluid_scientist.study_spec.models import SimulationStudySpec

from .diff_builder import DiffBuilder, SpecDiff
from .errors import PatchApplicationError
from .models import PatchOperation, SimulationSpecPatch
from .patch_validator import PatchValidator
from .path_registry import PathRegistry
from .quantity_resolver import QuantityResolver

__all__ = ["PatchExecutor"]


class _Missing:
    """Sentinel for missing values in deep comparison."""


_MISSING = _Missing()


class PatchExecutor:
    """Apply a :class:`SimulationSpecPatch` to a
    :class:`SimulationStudySpec` atomically.

    Usage::

        executor = PatchExecutor(registry, resolver, validator)
        new_spec, diff = executor.apply(patch, current_spec)
    """

    def __init__(
        self,
        path_registry: PathRegistry,
        quantity_resolver: QuantityResolver,
        validator: PatchValidator,
    ) -> None:
        self._registry = path_registry
        self._resolver = quantity_resolver
        self._validator = validator
        self._diff_builder = DiffBuilder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(
        self,
        patch: SimulationSpecPatch,
        current_spec: SimulationStudySpec,
    ) -> tuple[SimulationStudySpec, SpecDiff]:
        """Apply *patch* to *current_spec* atomically.

        Parameters
        ----------
        patch:
            The patch to apply.
        current_spec:
            The current :class:`SimulationStudySpec`.

        Returns
        -------
        A tuple ``(new_spec, diff)`` where ``new_spec`` is the modified
        spec (version incremented) and ``diff`` is the
        :class:`SpecDiff`.

        Raises
        ------
        PatchApplicationError
            If the patch fails during the dry-run (after validation
            has already passed).
        """
        current_dict = current_spec.model_dump()

        # 1. Validate (caller should have done this, but we check again).
        errors = self._validator.validate(patch, current_dict)
        if errors:
            raise PatchApplicationError(
                f"Patch validation failed: {'; '.join(errors)}"
            )

        # 2. Dry-run on a deep copy.
        old_dict = copy.deepcopy(current_dict)
        working_dict = copy.deepcopy(current_dict)

        for op in patch.operations:
            try:
                working_dict = self.apply_operation(working_dict, op)
            except Exception as exc:
                raise PatchApplicationError(
                    f"Operation '{op.op} {op.path}' failed during "
                    f"dry-run: {exc}"
                ) from exc

        # 3. Verify untouched_guarantee if asserted.
        if patch.untouched_guarantee:
            self._verify_untouched(old_dict, working_dict, patch)

        # 4. Increment version and update provenance.
        new_version = working_dict.get("version", 1) + 1
        working_dict["version"] = new_version
        working_dict["parent_version"] = old_dict.get("version", 1)
        self._update_provenance(working_dict, patch, new_version)

        # 5. Reconstruct the SimulationStudySpec from the modified dict.
        new_spec = SimulationStudySpec.model_validate(working_dict)

        # 6. Build diff.
        diff = self._diff_builder.build_diff(old_dict, working_dict, patch)

        return new_spec, diff

    def apply_operation(
        self,
        spec_dict: dict[str, Any],
        op: PatchOperation,
    ) -> dict[str, Any]:
        """Apply a single :class:`PatchOperation` to *spec_dict*.

        Returns the modified spec dict (the input dict is mutated in
        place and also returned for convenience).

        Raises
        ------
        PatchApplicationError
            If the operation cannot be applied (e.g. path not found,
            type mismatch at runtime).
        """
        # Resolve any relative expressions in the value.
        resolved_value = self._resolver.resolve(
            op.value, spec_dict, op.path
        ) if op.value is not None else None

        if op.op == "replace":
            self._apply_replace(spec_dict, op.path, resolved_value)
        elif op.op == "add":
            self._apply_add(spec_dict, op.path, resolved_value)
        elif op.op == "remove":
            self._apply_remove(spec_dict, op.path)
        elif op.op == "merge":
            self._apply_merge(spec_dict, op.path, resolved_value)
        elif op.op == "append_unique":
            self._apply_append_unique(spec_dict, op.path, resolved_value)
        elif op.op == "move":
            self._apply_move(spec_dict, op.path, op.from_path or "")
        elif op.op == "copy":
            self._apply_copy(spec_dict, op.path, op.from_path or "")
        elif op.op == "test":
            self._apply_test(spec_dict, op.path, resolved_value)
        elif op.op == "set_relation":
            self._apply_set_relation(spec_dict, op)
        elif op.op == "unset_relation":
            self._apply_unset_relation(spec_dict, op)
        elif op.op == "declare_unknown_capability":
            # No-op on the spec dict; this is handled elsewhere.
            pass
        else:
            raise PatchApplicationError(
                f"Unknown operation type: '{op.op}'"
            )

        return spec_dict

    # ------------------------------------------------------------------
    # Internal: individual operation implementations
    # ------------------------------------------------------------------

    def _apply_replace(
        self,
        spec: dict[str, Any],
        path: str,
        value: Any,
    ) -> None:
        """Replace the value at *path* with *value*.

        When the current value is a Quantity/SourcedValue dict (i.e. a
        dict with a ``"value"`` key) but the replacement *value* is a
        bare number, the bare number is wrapped into the existing dict
        structure to preserve unit and provenance metadata.  This
        allows relative-expression results (which are bare numbers) to
        be applied to Quantity-typed fields without losing the unit.
        """
        parent, key = self._resolve_parent(spec, path)
        if parent is None:
            raise PatchApplicationError(f"Path '{path}' not found in spec.")
        if isinstance(parent, dict) and key not in parent:
            raise PatchApplicationError(
                f"Path '{path}' does not exist for 'replace' operation."
            )

        current = parent[key]

        # Wrap bare numbers into existing Quantity/SourcedValue dicts.
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and isinstance(current, dict)
            and "value" in current
        ):
            wrapped = copy.deepcopy(current)
            wrapped["value"] = value
            parent[key] = wrapped
        else:
            parent[key] = value

    def _apply_add(
        self,
        spec: dict[str, Any],
        path: str,
        value: Any,
    ) -> None:
        """Add a new value at *path* (or append to array if path ends
        with ``/-``)."""
        # Handle array-append case: path ends with /-
        if path.endswith("/-"):
            array_path = path[:-2]
            parent, key = self._resolve_parent(spec, array_path)
            if parent is None:
                raise PatchApplicationError(
                    f"Array path '{array_path}' not found in spec."
                )
            container = parent[key] if isinstance(parent, dict) else parent
            if not isinstance(container, list):
                raise PatchApplicationError(
                    f"Path '{array_path}' is not an array."
                )
            container.append(value)
            return

        parent, key = self._resolve_parent_for_add(spec, path)
        if parent is None:
            raise PatchApplicationError(f"Cannot create path '{path}'.")
        if isinstance(parent, dict):
            parent[key] = value
        elif isinstance(parent, list):
            idx = int(key) if isinstance(key, str) and key.isdigit() else key
            parent[idx] = value  # type: ignore[index]

    def _apply_remove(
        self,
        spec: dict[str, Any],
        path: str,
    ) -> None:
        """Remove the value at *path*."""
        parent, key = self._resolve_parent(spec, path)
        if parent is None:
            raise PatchApplicationError(f"Path '{path}' not found in spec.")
        if isinstance(parent, dict):
            if key not in parent:
                raise PatchApplicationError(
                    f"Path '{path}' does not exist for 'remove' operation."
                )
            del parent[key]
        elif isinstance(parent, list):
            idx = int(key) if isinstance(key, str) else key
            if not isinstance(idx, int) or idx < 0 or idx >= len(parent):
                raise PatchApplicationError(
                    f"Index '{key}' out of range for 'remove' operation."
                )
            del parent[idx]  # type: ignore[arg-type]

    def _apply_merge(
        self,
        spec: dict[str, Any],
        path: str,
        value: Any,
    ) -> None:
        """Deep-merge *value* into the dict at *path*."""
        parent, key = self._resolve_parent(spec, path)
        if parent is None:
            raise PatchApplicationError(f"Path '{path}' not found in spec.")
        current = parent.get(key) if isinstance(parent, dict) else None
        if not isinstance(current, dict) or not isinstance(value, dict):
            # If either side is not a dict, fall back to replace.
            parent[key] = value
            return
        merged = copy.deepcopy(current)
        self._deep_merge(merged, value)
        parent[key] = merged

    def _apply_append_unique(
        self,
        spec: dict[str, Any],
        path: str,
        value: Any,
    ) -> None:
        """Append *value* to the array at *path* if not already present."""
        # Strip the /- append sentinel so _resolve_parent returns the array container.
        if path.endswith("/-"):
            path = path[:-2]
        parent, key = self._resolve_parent(spec, path)
        if parent is None:
            raise PatchApplicationError(f"Path '{path}' not found in spec.")
        container = parent.get(key) if isinstance(parent, dict) else None
        if not isinstance(container, list):
            raise PatchApplicationError(
                f"Path '{path}' is not an array for 'append_unique'."
            )
        if value not in container:
            container.append(value)

    def _apply_move(
        self,
        spec: dict[str, Any],
        path: str,
        from_path: str,
    ) -> None:
        """Move value from *from_path* to *path*."""
        value = self._read_value(spec, from_path)
        if value is None:
            raise PatchApplicationError(
                f"from_path '{from_path}' has no value to move."
            )
        self._apply_remove(spec, from_path)
        self._apply_replace(spec, path, value)

    def _apply_copy(
        self,
        spec: dict[str, Any],
        path: str,
        from_path: str,
    ) -> None:
        """Copy value from *from_path* to *path*."""
        value = self._read_value(spec, from_path)
        if value is None:
            raise PatchApplicationError(
                f"from_path '{from_path}' has no value to copy."
            )
        self._apply_replace(spec, path, copy.deepcopy(value))

    def _apply_test(
        self,
        spec: dict[str, Any],
        path: str,
        value: Any,
    ) -> None:
        """Test that the value at *path* equals *value*."""
        current = self._read_value(spec, path)
        if current != value:
            raise PatchApplicationError(
                f"Test failed at '{path}': expected {value!r} but got "
                f"{current!r}."
            )

    def _apply_set_relation(
        self,
        spec: dict[str, Any],
        op: PatchOperation,
    ) -> None:
        """Add a geometry relation to the spec."""
        geometry = spec.get("geometry")
        if not isinstance(geometry, dict):
            raise PatchApplicationError("Spec has no geometry block.")
        relations = geometry.setdefault("relations", [])
        if op.relation is not None:
            relations.append(op.relation)

    def _apply_unset_relation(
        self,
        spec: dict[str, Any],
        op: PatchOperation,
    ) -> None:
        """Remove a geometry relation from the spec."""
        geometry = spec.get("geometry")
        if not isinstance(geometry, dict):
            raise PatchApplicationError("Spec has no geometry block.")
        relations = geometry.get("relations", [])
        if op.relation is not None and isinstance(relations, list):
            # Remove matching relation by relation_id.
            rel_id = op.relation.get("relation_id")
            if rel_id:
                relations[:] = [
                    r for r in relations
                    if not (isinstance(r, dict) and r.get("relation_id") == rel_id)
                ]

    # ------------------------------------------------------------------
    # Internal: path resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_parent(
        spec: dict[str, Any],
        json_pointer: str,
    ) -> tuple[Any, Any]:
        """Resolve the parent container and key for a JSON Pointer.

        Returns ``(parent, key)`` where ``parent[key]`` is the target.
        Returns ``(None, None)`` if the path cannot be resolved.
        """
        if not json_pointer or json_pointer == "/":
            return spec, ""

        parts = json_pointer.lstrip("/").split("/")
        if not parts:
            return spec, ""

        current: Any = spec
        for part in parts[:-1]:
            if part == "-":
                if isinstance(current, list) and current:
                    current = current[-1]
                else:
                    return None, None
                continue
            if isinstance(current, dict):
                if part not in current:
                    return None, None
                current = current[part]
            elif isinstance(current, list):
                try:
                    idx = int(part)
                except ValueError:
                    return None, None
                if idx < 0 or idx >= len(current):
                    return None, None
                current = current[idx]
            else:
                return None, None

        key: Any = parts[-1]
        if key == "-" and isinstance(current, list):
            key = len(current)  # Append position.
        return current, key

    @staticmethod
    def _resolve_parent_for_add(
        spec: dict[str, Any],
        json_pointer: str,
    ) -> tuple[Any, Any]:
        """Like :meth:`_resolve_parent` but creates intermediate dicts
        if they don't exist (for 'add' operations)."""
        if not json_pointer or json_pointer == "/":
            return spec, ""

        parts = json_pointer.lstrip("/").split("/")
        if not parts:
            return spec, ""

        current: Any = spec
        for part in parts[:-1]:
            if isinstance(current, dict):
                if part not in current:
                    current[part] = {}
                current = current[part]
            elif isinstance(current, list):
                try:
                    idx = int(part)
                except ValueError:
                    return None, None
                if idx < 0 or idx >= len(current):
                    return None, None
                current = current[idx]
            else:
                return None, None

        return current, parts[-1]

    @staticmethod
    def _read_value(spec: dict[str, Any], json_pointer: str) -> Any:
        """Read the value at a JSON Pointer path."""
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

    @staticmethod
    def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
        """Deep-merge *source* into *target* (in place)."""
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                PatchExecutor._deep_merge(target[key], value)
            else:
                target[key] = value

    # ------------------------------------------------------------------
    # Internal: provenance and untouched-guarantee
    # ------------------------------------------------------------------

    @staticmethod
    def _update_provenance(
        spec_dict: dict[str, Any],
        patch: SimulationSpecPatch,
        new_version: int,
    ) -> None:
        """Append a modification-history entry to the spec's provenance."""
        provenance = spec_dict.get("provenance")
        if not isinstance(provenance, dict):
            return
        history = provenance.setdefault("modification_history", [])
        history.append({
            "patch_id": patch.patch_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "summary": patch.assistant_message or f"Applied patch {patch.patch_id}",
            "from_version": patch.base_version,
            "to_version": new_version,
        })

    def _verify_untouched(
        self,
        old_dict: dict[str, Any],
        new_dict: dict[str, Any],
        patch: SimulationSpecPatch,
    ) -> None:
        """Verify that only paths touched by the patch have changed.

        Raises :class:`PatchApplicationError` if a path not in the
        patch's operations has changed (excluding ``version``,
        ``parent_version``, and ``provenance`` which are always updated).
        """
        # Collect all paths touched by the patch — exact operation paths only.
        # A changed path is allowed if it is exactly an operation path or a
        # descendant of one (e.g. /numerics/time/end_time/value is a
        # descendant of /numerics/time/end_time).  We do NOT add parent
        # paths, because that would incorrectly allow sibling changes.
        touched_paths: set[str] = set()
        for op in patch.operations:
            touched_paths.add(op.path)

        # Always-allowed changes.
        always_allowed = {
            "/version",
            "/parent_version",
            "/provenance",
            "/provenance/modification_history",
        }

        # Collect changed leaf paths.
        changed: list[str] = []
        self._collect_changed_paths(old_dict, new_dict, "", changed)

        for path in changed:
            if path in always_allowed:
                continue
            # Allow if the changed path is an exact operation path or a
            # descendant of one.
            if any(
                path == tp or path.startswith(tp + "/")
                for tp in touched_paths
            ):
                continue
            raise PatchApplicationError(
                f"untouched_guarantee violated: path '{path}' changed "
                f"but was not in the patch operations."
            )

    @staticmethod
    def _collect_changed_paths(
        old: Any,
        new: Any,
        path: str,
        changed: list[str],
    ) -> None:
        """Recursively collect paths where old and new differ."""
        if isinstance(old, dict) and isinstance(new, dict):
            all_keys = set(old.keys()) | set(new.keys())
            for key in sorted(all_keys):
                child_path = f"{path}/{key}"
                PatchExecutor._collect_changed_paths(
                    old.get(key, _MISSING),
                    new.get(key, _MISSING),
                    child_path,
                    changed,
                )
        elif isinstance(old, list) and isinstance(new, list):
            max_len = max(len(old), len(new))
            for i in range(max_len):
                child_path = f"{path}/{i}"
                old_elem = old[i] if i < len(old) else _MISSING
                new_elem = new[i] if i < len(new) else _MISSING
                PatchExecutor._collect_changed_paths(old_elem, new_elem, child_path, changed)
        else:
            if old is _MISSING or new is _MISSING or old != new:
                if path:  # Skip root.
                    changed.append(path)
