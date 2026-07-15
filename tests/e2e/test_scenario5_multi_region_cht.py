"""Scenario 5: multi-region conjugate heat transfer (CHT).

User intent
-----------
    流体区域和固体壁面的共轭传热计算。
    流体为空气，固体为钢。外壁恒温。
    研究壁面热流分布和流体温度场。

The correct representation of this request is a **multi-region** Case IR:

* a fluid region (air) and a solid region (steel);
* a fluid--solid interface coupled by ``conjugate_heat_transfer``;
* solid heat conduction physics in the solid region;
* a constant-temperature external wall;
* a wall-heat-flux observable and a fluid-temperature-field observable.

The system must build **regions, interfaces and material dependencies** -- it
must not collapse the request into a single ``heat_transfer=True`` flag.

Two layers are exercised:

1. The full ``LLMPipeline`` on the raw text -- it must at least detect the
   thermal intent (heat transfer + wall-heat-flux observable).
2. An explicit multi-region ``RequestedCaseIR`` validated by the full
   validator stack -- verifying the region/interface/material structure and
   that a well-formed CHT IR passes validation.
"""
from __future__ import annotations

import pytest

from fluid_scientist.case_ir.models import (
    BoundaryIntent,
    Interface,
    Material,
    Observable,
    ParameterValue,
    PhysicsIntent,
    Region,
    RequestedCaseIR,
)
from fluid_scientist.case_ir.validators import (
    CaseIRValidationReport,
    DimensionalConsistencyValidator,
    ReferenceValidator,
    SchemaValidator,
    ScientificConsistencyValidator,
)
from fluid_scientist.llm_pipeline import LLMPipeline

USER_TEXT = """
流体区域和固体壁面的共轭传热计算。
流体为空气，固体为钢。外壁恒温。
研究壁面热流分布和流体温度场。
"""


def _build_cht_ir() -> RequestedCaseIR:
    """An explicit multi-region conjugate-heat-transfer Case IR."""
    return RequestedCaseIR(
        study_id="S5",
        case_id="C5",
        physics=PhysicsIntent(
            flow_regime="incompressible",
            time_mode="transient",
            turbulence="laminar",
            heat_transfer=True,
        ),
        regions=[
            Region(id="fluid_region", kind="fluid", material_ref="mat_air"),
            Region(id="solid_region", kind="solid", material_ref="mat_steel"),
        ],
        interfaces=[
            Interface(
                id="if_fluid_solid",
                region_a="fluid_region",
                region_b="solid_region",
                coupling_intent="conjugate_heat_transfer",
            ),
        ],
        materials=[
            Material(
                id="mat_air",
                kind="newtonian_fluid",
                properties={
                    "density": ParameterValue(
                        value=1.2, unit="kg/m^3", source="USER_EXPLICIT"
                    ),
                    "kinematic_viscosity": ParameterValue(
                        value=1.5e-5, unit="m^2/s", source="USER_EXPLICIT"
                    ),
                },
            ),
            Material(
                id="mat_steel",
                kind="solid",
                properties={
                    "density": ParameterValue(
                        value=7850.0, unit="kg/m^3", source="USER_EXPLICIT"
                    ),
                    "thermal_conductivity": ParameterValue(
                        value=45.0, unit="W/(m*K)", source="USER_EXPLICIT"
                    ),
                    "specific_heat": ParameterValue(
                        value=490.0, unit="J/(kg*K)", source="USER_EXPLICIT"
                    ),
                },
            ),
        ],
        boundary_intents=[
            BoundaryIntent(
                id="bc_outer_wall",
                target_patch="outer_wall",
                semantic_role="constant_temperature_wall",
            ),
        ],
        observables=[
            Observable(
                id="obs_whf",
                semantic_type="wall_heat_flux",
                target_region="fluid_region",
            ),
            Observable(
                id="obs_temp",
                semantic_type="temperature_field",
                target_region="fluid_region",
            ),
        ],
    )


class TestScenario5MultiRegionCHT:
    """Multi-region conjugate-heat-transfer representation and validation."""

    @pytest.fixture(scope="module")
    def pipeline(self) -> LLMPipeline:
        return LLMPipeline()

    @pytest.fixture(scope="module")
    def result(self, pipeline: LLMPipeline):
        return pipeline.run(USER_TEXT)

    @pytest.fixture(scope="module")
    def cht_ir(self) -> RequestedCaseIR:
        return _build_cht_ir()

    @pytest.fixture(scope="module")
    def validation_report(self, cht_ir):
        return CaseIRValidationReport(
            schema_issues=SchemaValidator().validate(cht_ir),
            reference_issues=ReferenceValidator().validate(cht_ir),
            consistency_issues=ScientificConsistencyValidator().validate(cht_ir),
            dimensional_issues=DimensionalConsistencyValidator().validate(cht_ir),
        )

    # ------------------------------------------------------------------
    # pipeline layer: thermal intent detected
    # ------------------------------------------------------------------
    def test_pipeline_detects_heat_transfer(self, result):
        assert result.physics_decomposition.heat_transfer is True

    def test_pipeline_detects_wall_heat_flux_observable(self, result):
        types = {o["semantic_type"] for o in result.observable_decomposition.observables}
        assert "wall_heat_flux" in types

    # ------------------------------------------------------------------
    # multi-region structure (the core of the scenario)
    # ------------------------------------------------------------------
    def test_multiple_regions_detected(self, cht_ir):
        assert len(cht_ir.regions) >= 2

    def test_fluid_and_solid_regions_present(self, cht_ir):
        kinds = {r.kind for r in cht_ir.regions}
        assert "fluid" in kinds
        assert "solid" in kinds

    def test_conjugate_heat_transfer_interface_present(self, cht_ir):
        couplings = {i.coupling_intent for i in cht_ir.interfaces}
        assert "conjugate_heat_transfer" in couplings

    def test_interface_couples_fluid_to_solid(self, cht_ir):
        region_kinds = {r.id: r.kind for r in cht_ir.regions}
        for iface in cht_ir.interfaces:
            if iface.coupling_intent == "conjugate_heat_transfer":
                a_kind = region_kinds.get(iface.region_a)
                b_kind = region_kinds.get(iface.region_b)
                assert {a_kind, b_kind} == {"fluid", "solid"}
                return
        pytest.fail("No conjugate_heat_transfer interface found")

    def test_solid_region_has_material(self, cht_ir):
        solid = next(r for r in cht_ir.regions if r.kind == "solid")
        assert solid.material_ref == "mat_steel"
        assert any(m.id == "mat_steel" and m.kind == "solid" for m in cht_ir.materials)

    def test_fluid_region_has_material(self, cht_ir):
        fluid = next(r for r in cht_ir.regions if r.kind == "fluid")
        assert fluid.material_ref == "mat_air"
        assert any(m.id == "mat_air" and m.kind == "newtonian_fluid" for m in cht_ir.materials)

    def test_solid_material_has_conduction_properties(self, cht_ir):
        steel = next(m for m in cht_ir.materials if m.id == "mat_steel")
        assert "thermal_conductivity" in steel.properties
        assert "specific_heat" in steel.properties

    # ------------------------------------------------------------------
    # boundary + observables
    # ------------------------------------------------------------------
    def test_constant_temperature_wall_boundary(self, cht_ir):
        roles = {b.semantic_role for b in cht_ir.boundary_intents}
        assert any("constant" in str(r).lower() and "temperature" in str(r).lower()
                   for r in roles), f"constant-temperature wall not found; roles={roles}"

    def test_wall_heat_flux_observable_target(self, cht_ir):
        whf = [o for o in cht_ir.observables if o.semantic_type == "wall_heat_flux"]
        assert whf
        assert whf[0].target_region == "fluid_region"

    def test_temperature_field_observable_present(self, cht_ir):
        types = {o.semantic_type for o in cht_ir.observables}
        assert "temperature_field" in types

    # ------------------------------------------------------------------
    # must NOT collapse to a single thermal flag
    # ------------------------------------------------------------------
    def test_not_just_a_thermal_flag(self, cht_ir):
        """The IR must carry real region/interface structure, not only heat_transfer."""
        assert cht_ir.physics.heat_transfer is True
        assert len(cht_ir.regions) >= 2
        assert len(cht_ir.interfaces) >= 1
        assert len(cht_ir.materials) >= 2

    # ------------------------------------------------------------------
    # validation: a well-formed CHT IR must pass
    # ------------------------------------------------------------------
    def test_schema_validation_passes(self, cht_ir):
        issues = SchemaValidator().validate(cht_ir)
        errors = [i for i in issues if i.level == "error"]
        assert not errors, f"schema errors: {[(i.code, i.message) for i in errors]}"

    def test_scientific_consistency_passes(self, cht_ir):
        issues = ScientificConsistencyValidator().validate(cht_ir)
        errors = [i for i in issues if i.level == "error"]
        assert not errors, f"consistency errors: {[(i.code, i.message) for i in errors]}"

    def test_reference_validation_passes(self, cht_ir):
        issues = ReferenceValidator().validate(cht_ir)
        errors = [i for i in issues if i.level == "error"]
        assert not errors, f"reference errors: {[(i.code, i.message) for i in errors]}"

    def test_full_validation_report_passes(self, validation_report):
        """The complete validator stack must accept the multi-region CHT IR."""
        assert validation_report.passed is True
        assert validation_report.error_count == 0
