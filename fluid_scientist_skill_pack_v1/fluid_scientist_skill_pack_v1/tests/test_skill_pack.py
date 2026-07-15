from pathlib import Path

from fluid_skills.runtime.geometry import normalize_cylinder_geometry
from fluid_skills.runtime.observables import (
    build_analysis_goals,
    extract_observables,
    recommend_observables,
)
from fluid_skills.runtime.openfoam13 import smoke_test_plan, static_validate_case
from fluid_skills.runtime.readiness import evaluate_readiness
from fluid_skills.runtime.router import route
from fluid_skills.runtime.topology import (
    classify_flow_topology,
    enforce_2d_boundary_topology,
)
from fluid_skills.runtime.visualization import build_plot_spec

def base_spec():
    return {
        "schema_version": "1.0",
        "case_family": "cylinder_flow_2d",
        "domain": {
            "dimensionality": "2D",
            "length_m": 20.0,
            "height_m": 10.0,
            "thickness_m": 1.0,
        },
        "cylinder": {
            "type": "cylinder",
            "radius_m": 0.1,
            "center_x_m": 5.0,
            "center_y_m": 5.0,
        },
        "boundaries": {
            "left": {"semantic_type": "uniform_velocity_inlet"},
            "right": {"semantic_type": "pressure_outlet"},
            "top": {"semantic_type": "slip_wall"},
            "bottom": {"semantic_type": "no_slip_wall"},
        },
        "forcing": {
            "pressure_gradient": {"enabled": False},
            "body_force": {"enabled": False},
        },
        "bottom_profile": {"enabled": False, "profile_type": "flat"},
        "simulation": {"time_mode": "transient"},
        "observables": [],
        "analysis_goals": [],
    }

def test_router_matches_cylinder_flow():
    result = route("二维圆柱绕流，左侧来流，右侧出口")
    assert result.data["matched"] is True
    assert result.data["pipeline_id"] == "cylinder-flow-2d-v1"

def test_radius_derives_diameter_and_characteristic_dimension():
    result = normalize_cylinder_geometry(base_spec())
    cylinder = result.data["cylinder"]
    assert cylinder["diameter_m"] == 0.2
    assert cylinder["characteristic_dimension_m"] == 0.2
    assert not result.blocking_issues

def test_2d_boundaries_are_empty():
    result = enforce_2d_boundary_topology(base_spec())
    assert result.data["boundaries"]["front"]["semantic_type"] == "empty"
    assert result.data["boundaries"]["back"]["semantic_type"] == "empty"

def test_inlet_outlet_topology():
    result = classify_flow_topology(base_spec())
    assert result.data["flow_topology"]["mode"] == "INLET_OUTLET"
    assert not result.blocking_issues

def test_periodic_requires_driving():
    spec = base_spec()
    spec["boundaries"]["left"] = {"semantic_type": "periodic"}
    spec["boundaries"]["right"] = {"semantic_type": "periodic"}
    result = classify_flow_topology(spec)
    assert result.blocking_issues
    assert result.blocking_issues[0].code == "PERIODIC_FLOW_HAS_NO_DRIVING"

def test_extract_section_mean_velocity():
    result = extract_observables("观测某截面平均流速", base_spec())
    assert result.data["observables"][0]["type"] == "section_mean_velocity"
    assert any(i.code == "SECTION_LOCATION_REQUIRED" for i in result.issues)

def test_recommend_observables_and_build_goals():
    result = recommend_observables(base_spec())
    assert any(o["type"] == "cylinder_drag" for o in result.data["observables"])
    goal_result = build_analysis_goals(result.data)
    assert len(goal_result.data["analysis_goals"]) >= 3

def test_readiness_not_ready_when_section_location_missing():
    spec = base_spec()
    geo = normalize_cylinder_geometry(spec).data
    geo = enforce_2d_boundary_topology(geo).data
    geo = classify_flow_topology(geo).data
    obs_result = extract_observables("观测某截面平均流速", geo)
    goals = build_analysis_goals(obs_result.data).data
    ready = evaluate_readiness(goals, obs_result.issues)
    assert ready.data["draft_status"] == "NEEDS_CLARIFICATION"

def test_smoke_plan_uses_foamrun():
    result = smoke_test_plan(parallel=True)
    flat = [" ".join(cmd) for cmd in result.data["commands"]]
    assert any("foamRun -solver incompressibleFluid" in cmd for cmd in flat)
    assert any("checkMesh -allTopology -allGeometry" in cmd for cmd in flat)

def test_static_validator_accepts_minimal_foundation13_case(tmp_path: Path):
    files = {
        "system/controlDict": "solver incompressibleFluid;",
        "system/fvSchemes": "",
        "system/fvSolution": "",
        "constant/physicalProperties": "",
        "constant/momentumTransport": "",
        "0/U": "",
        "0/p": "",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    result = static_validate_case(tmp_path)
    assert not result.blocking_issues

def test_plot_spec_binds_run_and_spec_version():
    spec = base_spec()
    spec["observables"] = [{"type": "section_mean_velocity"}]
    spec["plot_requests"] = ["velocity_magnitude", "vorticity"]
    run = {
        "run_id": "run-1",
        "case_id": "case-1",
        "spec_version": 3,
        "remote_case_path": "/remote/case-1",
    }
    result = build_plot_spec(spec, run)
    assert result.data["run_id"] == "run-1"
    assert result.data["spec_version"] == 3
    assert any(p["type"] == "section_mean_velocity_history" for p in result.data["plots"])
