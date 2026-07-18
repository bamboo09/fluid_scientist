"""End-to-end failure-baseline tests for the model-editing pipeline.

These tests verify that the new spec-editing system (``study_spec``,
``spec_editing``, ``model_runtime``, ``dependencies``) correctly handles
the known issues described in the refactoring plan:

* Single-field edit with untouched-guarantee.
* Multiple consecutive edits without state loss.
* Geometry semantic-type preservation.
* Material-change dependency cascade (Re recomputation, invalidation).
* Probe addition reaching the measurement plan.
* Unknown geometry not silently mapped to a template.
* No silent fallback on model failures.
* Ambiguity clarification with multiple alternatives.
"""
from __future__ import annotations
