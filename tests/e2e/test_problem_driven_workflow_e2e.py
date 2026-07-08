"""
E2E tests for the problem-driven workflow (Commits 8-9).

Covers:
  - PhysicsSpecBuilder (fake mode)
  - RequirementGraph slot planning
  - ParameterValueResolver resolution & derivation
  - Enhanced WorkbenchAgent intents
  - Physics spec prompt
  - Legacy flow isolation (app.js)
  - Button state machine completeness (app.js)
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]


def read_asset(relative: str) -> str:
    """Read a project asset file as UTF-8 text."""
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def function_body(js: str, signature: str) -> str:
    """Extract the body of a JS function given its signature string."""
    start = js.find(signature)
    assert start != -1, f"function not found: {signature}"
    search_from = start + len(signature)
    end = len(js)
    for marker in ("\nfunction ", "\nasync function "):
        pos = js.find(marker, search_from)
        if pos != -1 and pos < end:
            end = pos
    return js[start:end]


# ---------------------------------------------------------------------------
# TestPhysicsSpecBuilder
# ---------------------------------------------------------------------------

class TestPhysicsSpecBuilder:
    """Verify PhysicsSpecBuilder fake-mode extraction."""

    def setup_method(self):
        from fluid_scientist.workbench.physics_spec_builder import (
            PhysicsSpecBuilder,
        )
        self.builder = PhysicsSpecBuilder()  # fake mode

    def test_pipe_flow_detected(self):
        result = self.builder.build("研究水在圆管中的压降")
        assert result.physical_system == "pipe_flow"

    def test_cylinder_flow_detected(self):
        result = self.builder.build("研究圆柱绕流阻力系数")
        assert result.physical_system == "external_flow"

    def test_cavity_flow_detected(self):
        result = self.builder.build("研究方腔驱动流")
        assert result.physical_system == "cavity_flow"

    def test_water_detected(self):
        result = self.builder.build("研究水在圆管中的压降")
        assert result.material_or_fluid_name == "water"

    def test_air_detected(self):
        result = self.builder.build("空气流过圆柱")
        assert result.material_or_fluid_name == "air"

    def test_pressure_drop_metric_detected(self):
        result = self.builder.build("研究压降")
        assert "pressure_drop" in result.target_metrics

    def test_known_values_extracted(self):
        result = self.builder.build("管径50毫米")
        assert "diameter" in result.known_conditions
        diameter = result.known_conditions["diameter"]
        val = diameter["value"] if isinstance(diameter, dict) else diameter
        assert abs(val - 0.05) < 0.01, f"expected 0.05, got {val}"

    def test_turbulent_detected(self):
        result = self.builder.build("湍流管流")
        assert result.flow_regime == "turbulent"


# ---------------------------------------------------------------------------
# TestRequirementGraph
# ---------------------------------------------------------------------------

class TestRequirementGraph:
    """Verify RequirementGraph slot planning."""

    def setup_method(self):
        from fluid_scientist.workbench.physics_spec_builder import (
            PhysicsSpecResult,
        )
        from fluid_scientist.workbench.requirement_graph import (
            RequirementGraph,
        )
        self.graph = RequirementGraph()
        self.PhysicsSpecResult = PhysicsSpecResult

    def test_pipe_generates_geometry_slots(self):
        spec = self.PhysicsSpecResult(physical_system="pipe_flow", geometry_type="pipe")
        slots = self.graph.plan(spec)
        slot_ids = [s.slot_id for s in slots]
        assert "geometry.diameter" in slot_ids
        assert "geometry.length" in slot_ids

    def test_cylinder_generates_domain_slots(self):
        spec = self.PhysicsSpecResult(physical_system="external_flow", geometry_type="cylinder")
        slots = self.graph.plan(spec)
        slot_ids = [s.slot_id for s in slots]
        assert "geometry.domain_width" in slot_ids
        assert "geometry.domain_height" in slot_ids

    def test_cavity_generates_side_length(self):
        spec = self.PhysicsSpecResult(physical_system="cavity_flow", geometry_type="cavity")
        slots = self.graph.plan(spec)
        slot_ids = [s.slot_id for s in slots]
        assert "geometry.side_length" in slot_ids

    def test_material_slots_always_present(self):
        spec = self.PhysicsSpecResult(physical_system="pipe_flow")
        slots = self.graph.plan(spec)
        slot_ids = [s.slot_id for s in slots]
        assert "material.density" in slot_ids
        assert "material.kinematic_viscosity" in slot_ids

    def test_boundary_slots_generated(self):
        spec = self.PhysicsSpecResult(physical_system="pipe_flow")
        slots = self.graph.plan(spec)
        slot_ids = [s.slot_id for s in slots]
        assert "boundary.inlet_condition" in slot_ids
        assert "boundary.outlet_condition" in slot_ids

    def test_initial_condition_slots(self):
        spec = self.PhysicsSpecResult(physical_system="pipe_flow")
        slots = self.graph.plan(spec)
        slot_ids = [s.slot_id for s in slots]
        assert "initial.velocity_field" in slot_ids

    def test_numerics_slots_generated(self):
        spec = self.PhysicsSpecResult(physical_system="pipe_flow")
        slots = self.graph.plan(spec)
        slot_ids = [s.slot_id for s in slots]
        assert "numerics.solver" in slot_ids
        assert "numerics.time_step" in slot_ids

    def test_measurement_slots_from_metrics(self):
        spec = self.PhysicsSpecResult(physical_system="pipe_flow")
        metric_plan = [
            {
                "metric_id": "pressure_drop",
                "required_data": ["pressure_inlet", "pressure_outlet"],
            },
        ]
        slots = self.graph.plan(spec, metric_plan=metric_plan)
        slot_ids = [s.slot_id for s in slots]
        measurement_ids = [s for s in slot_ids if s.startswith("measurement.")]
        assert len(measurement_ids) > 0
        assert any("pressure_drop" in s for s in measurement_ids)


# ---------------------------------------------------------------------------
# TestParameterValueResolver
# ---------------------------------------------------------------------------

class TestParameterValueResolver:
    """Verify ParameterValueResolver resolution & derivation."""

    def setup_method(self):
        from fluid_scientist.workbench.parameter_value_resolver import (
            ParameterValueResolver,
        )
        from fluid_scientist.workbench.physics_spec_builder import (
            PhysicsSpecResult,
        )
        from fluid_scientist.workbench.requirement_graph import (
            RequirementGraph,
        )
        self.resolver = ParameterValueResolver()
        self.graph = RequirementGraph()
        self.PhysicsSpecResult = PhysicsSpecResult

    def _resolve(self, spec=None, user_values=None):
        if spec is None:
            spec = self.PhysicsSpecResult(
                physical_system="pipe_flow",
                material_or_fluid_name="water",
            )
        slots = self.graph.plan(spec)
        return self.resolver.resolve(slots, spec, user_values=user_values or {})

    def _find(self, params, suffix):
        for p in params:
            if p.slot_id.endswith(suffix):
                return p
        return None

    def test_water_density_recommended(self):
        params = self._resolve()
        p = self._find(params, "density")
        assert p is not None
        assert p.status == "model_recommended"
        assert abs(p.value - 998.2) < 0.1

    def test_water_viscosity_recommended(self):
        params = self._resolve()
        p = self._find(params, "kinematic_viscosity")
        assert p is not None
        assert p.value is not None
        assert abs(p.value - 1e-6) < 1e-7

    def test_user_value_takes_priority(self):
        params = self._resolve(user_values={"diameter": 0.05})
        p = self._find(params, "diameter")
        assert p is not None
        assert p.value == 0.05
        assert p.status == "user_confirmed"

    def test_solver_recommended(self):
        params = self._resolve()
        p = self._find(params, "solver")
        assert p is not None
        assert p.value is not None
        assert p.value in ("simpleFoam", "pimpleFoam", "rhoPimpleFoam",
                           "rhoSimpleFoam", "interFoam", "twoLiquidMixingFoam")

    def test_outlet_pressure_recommended(self):
        params = self._resolve()
        p = self._find(params, "outlet_condition")
        assert p is not None
        assert p.value == "pressure_outlet"

    def test_wall_no_slip_recommended(self):
        params = self._resolve()
        p = self._find(params, "wall_condition")
        assert p is not None
        assert p.value == "no_slip"

    def test_initial_velocity_default(self):
        params = self._resolve()
        p = self._find(params, "velocity_field")
        assert p is not None
        assert p.value is not None
        assert p.status == "advanced_default"

    def test_reynolds_derived(self):
        params = self._resolve(user_values={
            "diameter": 0.05,
            "inlet_velocity": 1.0,
            "kinematic_viscosity": 1e-6,
        })
        p = self._find(params, "reynolds_number")
        assert p is not None
        assert p.status == "derived"
        expected = 1.0 * 0.05 / 1e-6
        assert abs(p.value - expected) < 1.0

    def test_mean_velocity_derived_from_mass_flow(self):
        params = self._resolve(user_values={
            "mass_flow_rate": 2.0,
            "density": 998.2,
            "diameter": 0.05,
        })
        p = self._find(params, "mean_velocity")
        assert p is not None
        assert p.status == "derived"
        area = math.pi * (0.05 / 2) ** 2
        expected = 2.0 / (998.2 * area)
        assert abs(p.value - expected) < 0.01

    def test_unknown_required_when_cannot_recommend(self):
        spec = self.PhysicsSpecResult(physical_system="cavity_flow")
        params = self._resolve(spec=spec)
        p = self._find(params, "lid_velocity")
        assert p is not None
        assert p.status == "unknown_required"


# ---------------------------------------------------------------------------
# TestEnhancedWorkbenchAgent
# ---------------------------------------------------------------------------

class TestEnhancedWorkbenchAgent:
    """Verify WorkbenchAgent handles new edit intents."""

    def setup_method(self):
        from fluid_scientist.workbench.workbench_agent import WorkbenchAgent
        self.agent = WorkbenchAgent()
        self.spec = {
            "experiment_id": "test",
            "experiment_version": 1,
            "status": "draft",
            "parameters": [],
            "physics": {},
        }

    def test_change_initial_condition_intent(self):
        proposal = self.agent.process_turn("增加初始压力条件", self.spec)
        assert proposal.edit_intent == "change_initial_condition"

    def test_change_mesh_intent(self):
        proposal = self.agent.process_turn("修改网格加密", self.spec)
        assert proposal.edit_intent == "change_mesh"

    def test_change_numerics_intent(self):
        proposal = self.agent.process_turn("修改时间步长", self.spec)
        assert proposal.edit_intent == "change_numerics"

    def test_outlet_velocity_uniformity_metric(self):
        proposal = self.agent.process_turn("增加出口速度均匀性指标", self.spec)
        assert proposal.edit_intent == "add_metric"
        assert proposal.proposed_operations
        metric_ids = [
            op.metric.metric_id
            for op in proposal.proposed_operations
            if op.metric is not None
        ]
        assert any("velocity_uniformity" in mid for mid in metric_ids)

    def test_change_inlet_to_mass_flow(self):
        proposal = self.agent.process_turn("修改入口边界为质量流量", self.spec)
        assert proposal.edit_intent == "change_boundary_condition"
        assert proposal.invalidates

    def test_explain_parameter_no_target(self):
        proposal = self.agent.process_turn("解释参数", self.spec)
        assert proposal.edit_intent == "clarification_required"

    def test_explain_parameter_with_target(self):
        proposal = self.agent.process_turn("解释雷诺数", self.spec)
        assert proposal.edit_intent == "explain_parameter"

    def test_add_parameter_still_clarification(self):
        proposal = self.agent.process_turn("增加一个参数", self.spec)
        assert proposal.edit_intent == "clarification_required"


# ---------------------------------------------------------------------------
# TestPhysicsSpecPrompt
# ---------------------------------------------------------------------------

class TestPhysicsSpecPrompt:
    """Verify the physics spec prompt file."""

    def test_physics_spec_prompt_exists(self):
        from fluid_scientist.prompts import load_prompt
        prompt = load_prompt("physics_spec_prompt")
        assert prompt
        assert len(prompt) > 0

    def test_prompt_contains_physical_system(self):
        from fluid_scientist.prompts import load_prompt
        prompt = load_prompt("physics_spec_prompt")
        assert "physical_system" in prompt

    def test_prompt_contains_known_conditions(self):
        from fluid_scientist.prompts import load_prompt
        prompt = load_prompt("physics_spec_prompt")
        assert "known_conditions" in prompt


# ---------------------------------------------------------------------------
# TestLegacyFlowIsolation
# ---------------------------------------------------------------------------

class TestLegacyFlowIsolation:
    """Verify legacy flow is properly isolated in app.js."""

    def setup_method(self):
        self.js = read_asset("apps/web/app.js")

    def test_workflow_mode_is_v2(self):
        assert 'workflowMode = "v2"' in self.js or \
               'workflowMode = "v2";' in self.js or \
               'const workflowMode = "v2"' in self.js

    def test_renderPlanCard_guarded(self):
        # The function itself may not reference legacy; instead verify
        # that all calls to renderPlanCard are within legacy guards.
        call_pattern = re.compile(r'renderPlanCard\s*\(')
        calls = list(call_pattern.finditer(self.js))
        # Filter out the function definition itself
        call_sites = []
        for m in calls:
            prefix = self.js[max(0, m.start() - 30):m.start()]
            if "function " not in prefix:
                call_sites.append(m)
        assert len(call_sites) > 0, "renderPlanCard should be called somewhere"
        for site in call_sites:
            # Look backwards for a legacy guard within 300 chars
            window = self.js[max(0, site.start() - 300):site.start()]
            assert "legacy" in window.lower(), \
                f"renderPlanCard call at pos {site.start()} not guarded by legacy mode"

    def test_confirmAndSubmitPlan_guarded(self):
        body = function_body(self.js, "function confirmAndSubmitPlan(")
        assert "legacy" in body.lower(), \
            "confirmAndSubmitPlan should reference legacy mode"

    def test_no_legacy_plan_api_in_v2(self):
        # Legacy API endpoints that should only appear in legacy-guarded or
        # deprecated functions.
        # /api/plan-operations is a legacy-only API.
        # /api/experiment-plans/*/compile is the legacy compile endpoint
        # (the V2 equivalent is /api/projects/*/experiment-specs/*/compile).
        legacy_api_patterns = [
            "/api/plan-operations",
            r"experiment-plans/[^`\"']+\/compile",
        ]
        for pattern in legacy_api_patterns:
            occurrences = [m.start() for m in re.finditer(pattern, self.js)]
            for pos in occurrences:
                # Look backwards for a legacy guard or @deprecated within 2000 chars
                window = self.js[max(0, pos - 2000):pos]
                if "legacy" not in window.lower() and "deprecated" not in window.lower():
                    line_start = self.js.rfind("\n", 0, pos) + 1
                    line = self.js[line_start:self.js.find("\n", pos)]
                    pytest.fail(
                        f"Legacy API pattern {pattern} found outside legacy/deprecated guard: "
                        f"...{line.strip()}..."
                    )

    def test_legacy_flow_isolation_tests_exist(self):
        assert (ROOT / "tests" / "e2e" / "test_legacy_flow_isolation.py").exists()


# ---------------------------------------------------------------------------
# TestButtonStateMachineComplete
# ---------------------------------------------------------------------------

class TestButtonStateMachineComplete:
    """Verify getWorkbenchActions covers all expected button states."""

    def setup_method(self):
        self.js = read_asset("apps/web/app.js")
        self.actions_body = function_body(
            self.js, "function getWorkbenchActions("
        )

    def test_failed_has_error_button(self):
        # Extract the failed case block
        case_match = re.search(
            r'case\s+"failed"\s*:(.*?)(?:case\s+"|default\s*:|\n\s*\})',
            self.actions_body,
            re.DOTALL,
        )
        assert case_match, "failed case not found in getWorkbenchActions"
        failed_block = case_match.group(1)
        assert "查看错误" in failed_block or "spec-error-btn" in failed_block

    def test_failed_has_back_to_draft(self):
        case_match = re.search(
            r'case\s+"failed"\s*:(.*?)(?:case\s+"|default\s*:|\n\s*\})',
            self.actions_body,
            re.DOTALL,
        )
        assert case_match, "failed case not found in getWorkbenchActions"
        failed_block = case_match.group(1)
        assert "回到草案" in failed_block or "spec-back-draft-btn" in failed_block

    def test_ready_has_revalidate(self):
        case_match = re.search(
            r'case\s+"ready"\s*:(.*?)(?:case\s+"|default\s*:|\n\s*\})',
            self.actions_body,
            re.DOTALL,
        )
        assert case_match, "ready case not found in getWorkbenchActions"
        ready_block = case_match.group(1)
        assert "重新校验" in ready_block or "spec-revalidate-btn" in ready_block

    def test_compiled_has_submit(self):
        case_match = re.search(
            r'case\s+"compiled"\s*:(.*?)(?:case\s+"|default\s*:|\n\s*\})',
            self.actions_body,
            re.DOTALL,
        )
        assert case_match, "compiled case not found in getWorkbenchActions"
        compiled_block = case_match.group(1)
        assert "提交运行" in compiled_block

    def test_running_has_status(self):
        case_match = re.search(
            r'case\s+"running"\s*:(.*?)(?:case\s+"|default\s*:|\n\s*\})',
            self.actions_body,
            re.DOTALL,
        )
        assert case_match, "running case not found in getWorkbenchActions"
        running_block = case_match.group(1)
        assert "查看运行状态" in running_block

    def test_completed_has_report(self):
        case_match = re.search(
            r'case\s+"completed"\s*:(.*?)(?:case\s+"|default\s*:|\n\s*\})',
            self.actions_body,
            re.DOTALL,
        )
        assert case_match, "completed case not found in getWorkbenchActions"
        completed_block = case_match.group(1)
        assert "查看分析报告" in completed_block
