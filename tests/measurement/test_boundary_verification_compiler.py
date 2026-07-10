from fluid_scientist.measurement.boundary_verification_compiler import (
    BoundaryVerificationCompiler,
)
from fluid_scientist.workbench.experiment_design_synthesizer import ExperimentDesign


def test_boundary_verification_compiler_adds_free_slip_and_global_checks() -> None:
    design = ExperimentDesign(
        research_objective="free slip check",
        boundary_conditions={
            "top": {"type": "free_slip"},
            "inlet": {"type": "inlet_velocity"},
            "wall": {"type": "no_slip"},
            "spanwise": {"type": "periodic"},
            "outlet": {"type": "outlet_pressure"},
        },
    )

    metrics = BoundaryVerificationCompiler().compile(design)
    ids = {m["metric_id"] for m in metrics}

    assert "free_slip_normal_velocity_error" in ids
    assert "no_slip_wall_error" in ids
    assert "inlet_profile_error" in ids
    assert "periodic_boundary_mismatch" in ids
    assert "outlet_backflow_ratio" in ids
    assert "mass_conservation_error" in ids
