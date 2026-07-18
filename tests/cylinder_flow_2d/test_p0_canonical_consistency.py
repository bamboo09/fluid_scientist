"""P0 regressions for canonical spec identity, roles, and persistence."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from fluid_scientist.api import cylinder_flow_router as router
from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    FieldSource,
    FieldStatus,
    ProvenanceField,
)
from fluid_scientist.cylinder_flow_2d.pipeline import CylinderFlow2DV1Pipeline
from fluid_scientist.intent import IntentCandidateSet, ResolvedField


def test_unspecified_material_stays_null_when_viscosity_is_formula_derived() -> None:
    spec = CylinderFlow2DV1Pipeline().run(
        "建立一个6m乘4m的二维计算域，入口速度1m/s，圆柱直径0.2m，Re=200"
    ).spec

    assert spec.fluid.type.value is None
    assert spec.fluid.density_kg_m3.value is None
    assert spec.fluid.kinematic_viscosity_m2_s.value == pytest.approx(0.001)
    assert spec.fluid.kinematic_viscosity_m2_s.source == FieldSource.FORMULA_DERIVED
    serialized_fluid = json.dumps(spec.fluid.model_dump(mode="json"), ensure_ascii=False)
    assert "water" not in serialized_fluid.lower()
    assert "998" not in serialized_fluid


def test_rectangular_domain_does_not_create_rectangle_obstacle() -> None:
    spec = CylinderFlow2DV1Pipeline().run(
        "建立一个长6m宽4m的矩形计算域，入口速度1m/s"
    ).spec

    assert spec.domain.length_m.value == pytest.approx(6.0)
    assert spec.domain.height_m.value == pytest.approx(4.0)
    assert spec.rectangle.enabled is False
    assert spec.rectangle.width_m.value is None


def test_only_resolved_obstacle_candidate_enters_canonical_spec() -> None:
    spec = CylinderFlow2DExperimentSpecV1()
    spec.rectangle.enabled = True
    spec.rectangle.width_m = ProvenanceField(
        value=0.5, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED
    )
    spec.trapezoid.enabled = True
    spec.trapezoid.bottom_width_m = ProvenanceField(
        value=0.5, source=FieldSource.MODEL_RECOMMENDED, status=FieldStatus.RESOLVED
    )
    candidates = IntentCandidateSet(
        resolved_fields=[ResolvedField(field_path="obstacle.type", value="trapezoid")]
    )

    router._enforce_selected_obstacle_candidate(spec, candidates)

    assert spec.trapezoid.enabled is True
    assert spec.rectangle.enabled is False


class _MemoryPersistence:
    def __init__(self, spec_id: str, spec: CylinderFlow2DExperimentSpecV1) -> None:
        self.data = {spec_id: spec.model_dump(mode="json")}
        self.fail_next_save = False

    def save_spec(self, spec_id, spec, session_id="", user_input="") -> None:
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("simulated durable write failure")
        payload = spec.model_dump(mode="json") if hasattr(spec, "model_dump") else spec
        self.data[spec_id] = copy.deepcopy(payload)

    def load_spec(self, spec_id):
        value = self.data.get(spec_id)
        return copy.deepcopy(value) if value is not None else None

    def delete_spec(self, spec_id) -> None:
        self.data.pop(spec_id, None)


def _modifiable_spec() -> CylinderFlow2DExperimentSpecV1:
    spec = CylinderFlow2DV1Pipeline().run(
        "二维圆柱绕流，入口速度1m/s，圆柱直径0.2m，Re=200，仿真10秒"
    ).spec
    spec.spec_version = 3
    spec.experiment_id = "spec_p0"
    return spec


def test_modify_persists_read_back_and_returns_diff(monkeypatch) -> None:
    spec = _modifiable_spec()
    persistence = _MemoryPersistence("spec_p0", spec)
    router._spec_store["spec_p0"] = spec
    monkeypatch.setattr(router, "_get_persistence", lambda: persistence)

    response = asyncio.run(router.modify_spec(router.ModifyRequest(
        spec_id="spec_p0", modification_text="把仿真时间改为20秒"
    )))

    assert response.success is True
    assert response.spec_version == 4
    assert router._spec_store["spec_p0"].simulation.end_time == pytest.approx(20.0)
    assert persistence.load_spec("spec_p0")["simulation"]["end_time"] == pytest.approx(20.0)
    assert any(change["path"] == "simulation.end_time" for change in response.change_summary)


def test_modify_failure_does_not_mutate_canonical_spec(monkeypatch) -> None:
    spec = _modifiable_spec()
    original = spec.model_dump(mode="json")
    persistence = _MemoryPersistence("spec_p0", spec)
    persistence.fail_next_save = True
    router._spec_store["spec_p0"] = spec
    monkeypatch.setattr(router, "_get_persistence", lambda: persistence)

    response = asyncio.run(router.modify_spec(router.ModifyRequest(
        spec_id="spec_p0", modification_text="把仿真时间改为20秒"
    )))

    assert response.success is False
    assert response.error.startswith("SPEC_PERSISTENCE_FAILED:")
    assert router._spec_store["spec_p0"].model_dump(mode="json") == original
    assert persistence.load_spec("spec_p0") == original
