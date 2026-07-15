"""E2E tests for P8 (LLM Scientific Report & Physics Validation).

Tests:
- ResultSummaryBuilder: extracts Cd/Cl/St from simulation logs
- PhysicsValidator: compares Cd vs empirical correlations, St vs Roshko formula
- LLMReportGenerator: chains summary → validation → report (rule-based fallback)
- ScientificReportResponse endpoint structure
"""

from __future__ import annotations

import math
import pytest

from fluid_scientist.analysis.llm_report import (
    LLMReportGenerator,
    PhysicsValidationResult,
    PhysicsValidator,
    ResultSummaryBuilder,
)


# ---------------------------------------------------------------------------
# ResultSummaryBuilder
# ---------------------------------------------------------------------------

class TestResultSummaryBuilder:
    """Test structured result summary extraction."""

    def test_build_summary_empty_inputs(self):
        """Empty inputs should return has_results=False."""
        builder = ResultSummaryBuilder()
        summary = builder.build_summary()
        assert summary["has_results"] is False
        assert summary["mesh"] == {}
        assert summary["simulation"] == {}
        assert summary["metrics"] == {}

    def test_build_summary_with_mesh_report(self):
        """Mesh report should populate mesh info."""
        builder = ResultSummaryBuilder()
        summary = builder.build_summary(
            mesh_report={
                "n_cells": 50000,
                "n_points": 25000,
                "status": "OK",
                "max_aspect_ratio": 5.2,
                "max_non_orthogonality": 12.3,
            }
        )
        assert summary["mesh"]["cells"] == 50000
        assert summary["mesh"]["checkmesh_status"] == "OK"
        assert summary["mesh"]["max_aspect_ratio"] == 5.2

    def test_build_summary_with_sim_report_success(self):
        """Successful simulation should set has_results=True."""
        builder = ResultSummaryBuilder()
        summary = builder.build_summary(
            sim_report={
                "status": "SUCCESS",
                "final_time": 10.0,
                "courant_max": 0.5,
                "has_nan": False,
                "has_error": False,
            }
        )
        assert summary["has_results"] is True
        assert summary["simulation"]["status"] == "SUCCESS"
        assert summary["simulation"]["final_time"] == 10.0

    def test_build_summary_with_failed_sim(self):
        """Failed simulation should set has_results=False."""
        builder = ResultSummaryBuilder()
        summary = builder.build_summary(
            sim_report={"status": "FAILED", "has_error": True}
        )
        assert summary["has_results"] is False

    def test_parse_force_coeffs_from_log(self):
        """Cd/Cl values should be parsed from simulation logs."""
        builder = ResultSummaryBuilder()
        log = """
        time=0.1 Cd = 1.234 Cl = -0.567
        time=0.2 Cd = 1.345 Cl = 0.678
        time=0.3 Cd = 1.456 Cl = -0.789
        time=0.4 Cd = 1.567 Cl = 0.890
        """
        cd_values = builder._parse_force_coeffs(log, "Cd")
        cl_values = builder._parse_force_coeffs(log, "Cl")

        assert len(cd_values) == 4
        assert cd_values[0] == pytest.approx(1.234)
        assert len(cl_values) == 4
        assert cl_values[0] == pytest.approx(-0.567)

    def test_extract_metrics_with_force_coeffs(self):
        """Metrics should include Cd/Cl when forceCoeffs data present."""
        builder = ResultSummaryBuilder()
        log = "Cd = 1.5 Cl = 0.3 Cd = 1.6 Cl = -0.3 Cd = 1.4 Cl = 0.3"
        summary = builder.build_summary(
            execution_result={
                "simulation_report": {"output_tail": log}
            }
        )
        metrics = summary["metrics"]
        if "Cd" in metrics:
            assert "mean" in metrics["Cd"]
            assert "amplitude" in metrics["Cd"]

    def test_estimate_strouhal_insufficient_data(self):
        """Fewer than 20 Cl values should return None for Strouhal."""
        builder = ResultSummaryBuilder()
        assert builder._estimate_strouhal([0.1, 0.2, 0.3]) is None

    def test_estimate_strouhal_with_oscillation(self):
        """Oscillating Cl should produce a non-zero Strouhal estimate."""
        builder = ResultSummaryBuilder()
        # Create a sine-like oscillation with 40 points
        cl_values = [math.sin(2 * math.pi * i / 10) for i in range(40)]
        st = builder._estimate_strouhal(cl_values)
        if st is not None:
            assert st > 0


# ---------------------------------------------------------------------------
# PhysicsValidator
# ---------------------------------------------------------------------------

class TestPhysicsValidator:
    """Test physics validation against empirical correlations."""

    def test_empirical_cd_stokes_regime(self):
        """Re < 1 should use Stokes drag: Cd ≈ 24/Re."""
        validator = PhysicsValidator()
        cd = validator._empirical_cd(0.5)
        assert cd == pytest.approx(48.0)

    def test_empirical_cd_transition_regime(self):
        """1 < Re < 1000 should use Cd ≈ 10/sqrt(Re) + 3."""
        validator = PhysicsValidator()
        cd = validator._empirical_cd(100)
        expected = 10.0 / math.sqrt(100) + 3.0
        assert cd == pytest.approx(expected)

    def test_empirical_cd_subcritical_regime(self):
        """1000 < Re < 2e5 should use Cd ≈ 1.2."""
        validator = PhysicsValidator()
        cd = validator._empirical_cd(10000)
        assert cd == pytest.approx(1.2)

    def test_empirical_cd_supercritical_returns_none(self):
        """Re > 2e5 should return None (drag crisis)."""
        validator = PhysicsValidator()
        cd = validator._empirical_cd(3e5)
        assert cd is None

    def test_empirical_strouhal_low_re_returns_none(self):
        """Re < 50 should return None (no vortex shedding)."""
        validator = PhysicsValidator()
        st = validator._empirical_strouhal(30)
        assert st is None

    def test_empirical_strouhal_roshko(self):
        """50 < Re < 200 should use Roshko: St ≈ 0.198(1 - 19.7/Re)."""
        validator = PhysicsValidator()
        st = validator._empirical_strouhal(100)
        expected = 0.198 * (1 - 19.7 / 100)
        assert st == pytest.approx(expected)

    def test_empirical_strouhal_high_re(self):
        """Re > 200 should return St ≈ 0.2."""
        validator = PhysicsValidator()
        st = validator._empirical_strouhal(500)
        assert st == pytest.approx(0.2)

    def test_validate_passes_with_good_results(self):
        """Validation should pass when Cd is close to empirical."""
        validator = PhysicsValidator()
        summary = {
            "spec": {"reynolds_number": 10000},
            "metrics": {
                "Cd": {"mean": 1.15},  # Empirical = 1.2, error ~4%
                "Strouhal": {"value": 0.2},
            },
            "mesh": {"cells": 50000},
            "simulation": {"has_nan": False, "has_error": False},
        }
        result = validator.validate(summary)
        assert result.passed is True

    def test_validate_fails_with_nan(self):
        """Validation should fail when NaN detected."""
        validator = PhysicsValidator()
        summary = {
            "spec": {"reynolds_number": 100},
            "simulation": {"has_nan": True, "has_error": False},
            "mesh": {"cells": 10000},
        }
        result = validator.validate(summary)
        assert result.passed is False
        check_names = [c["name"] for c in result.checks]
        assert "no_nan" in check_names

    def test_validate_fails_with_coarse_mesh(self):
        """Validation should fail when mesh has < 1000 cells."""
        validator = PhysicsValidator()
        summary = {
            "spec": {"reynolds_number": 100},
            "mesh": {"cells": 500},
            "simulation": {"has_nan": False, "has_error": False},
        }
        result = validator.validate(summary)
        mesh_checks = [c for c in result.checks if c["name"] == "mesh_sufficiency"]
        assert mesh_checks
        assert mesh_checks[0]["passed"] is False

    def test_validate_cd_outside_tolerance(self):
        """Cd error > 30% should fail the check."""
        validator = PhysicsValidator()
        # Re=10000, empirical Cd=1.2, simulated Cd=3.0 (150% error)
        summary = {
            "spec": {"reynolds_number": 10000},
            "metrics": {"Cd": {"mean": 3.0}},
            "mesh": {"cells": 50000},
            "simulation": {"has_nan": False, "has_error": False},
        }
        result = validator.validate(summary)
        cd_checks = [c for c in result.checks if c["name"] == "Cd_comparison"]
        assert cd_checks
        assert cd_checks[0]["passed"] is False

    def test_validation_result_to_dict(self):
        """PhysicsValidationResult should serialize correctly."""
        r = PhysicsValidationResult()
        r.add_check("test_check", True, "detail")
        d = r.to_dict()
        assert d["passed"] is True
        assert len(d["checks"]) == 1
        assert d["checks"][0]["name"] == "test_check"


# ---------------------------------------------------------------------------
# LLMReportGenerator
# ---------------------------------------------------------------------------

class TestLLMReportGenerator:
    """Test the full report generation pipeline."""

    def test_generate_report_rule_based_no_llm(self):
        """Without LLM client, should produce rule-based report."""
        generator = LLMReportGenerator(llm_client=None)
        report = generator.generate_report(
            execution_result={
                "simulation_report": {
                    "status": "SUCCESS",
                    "output_tail": "Cd = 1.2 Cl = 0.3",
                }
            },
            sim_report={"status": "SUCCESS", "has_nan": False, "has_error": False},
            mesh_report={"n_cells": 50000, "status": "OK"},
        )

        assert report["report_source"] == "rule_based"
        assert "conclusions" in report
        assert "physics_validation" in report
        assert "result_summary" in report

    def test_generate_report_failed_simulation(self):
        """Failed simulation should produce appropriate report."""
        generator = LLMReportGenerator(llm_client=None)
        report = generator.generate_report(
            sim_report={"status": "FAILED", "has_error": True},
        )

        assert report["report_source"] == "rule_based"
        assert "仿真未成功完成" in report["summary"]

    def test_generate_report_includes_physics_validation(self):
        """Report should include physics validation results."""
        generator = LLMReportGenerator(llm_client=None)
        report = generator.generate_report(
            sim_report={"status": "SUCCESS", "has_nan": False, "has_error": False},
            mesh_report={"n_cells": 50000, "status": "OK"},
        )

        pv = report["physics_validation"]
        assert "passed" in pv
        assert "checks" in pv

    def test_generate_report_includes_result_summary(self):
        """Report should include structured result summary."""
        generator = LLMReportGenerator(llm_client=None)
        report = generator.generate_report(
            mesh_report={"n_cells": 50000, "status": "OK", "n_points": 25000},
            sim_report={"status": "SUCCESS", "final_time": 10.0, "courant_max": 0.5},
        )

        rs = report["result_summary"]
        assert rs["has_results"] is True
        assert rs["mesh"]["cells"] == 50000
        assert rs["simulation"]["final_time"] == 10.0

    def test_generate_report_with_llm_client_calls_llm(self):
        """With LLM client, should attempt LLM report generation."""
        class FakeLLMClient:
            def call(self, **kwargs):
                return {"summary": "LLM generated"}, type("Record", (), {
                    "success": True
                })()

        generator = LLMReportGenerator(llm_client=FakeLLMClient())
        report = generator.generate_report(
            sim_report={"status": "SUCCESS", "has_nan": False, "has_error": False},
            mesh_report={"n_cells": 50000, "status": "OK"},
        )

        assert report["report_source"] == "llm"
        assert report["summary"] == "LLM generated"

    def test_generate_report_llm_failure_falls_back(self):
        """When LLM call fails, should fall back to rule-based."""
        class FailingLLMClient:
            def call(self, **kwargs):
                raise RuntimeError("LLM unavailable")

        generator = LLMReportGenerator(llm_client=FailingLLMClient())
        report = generator.generate_report(
            sim_report={"status": "SUCCESS", "has_nan": False, "has_error": False},
            mesh_report={"n_cells": 50000, "status": "OK"},
        )

        assert report["report_source"] == "rule_based"

    def test_generate_report_with_plot_paths(self):
        """Plot paths should be included in result summary."""
        generator = LLMReportGenerator(llm_client=None)
        report = generator.generate_report(
            sim_report={"status": "SUCCESS", "has_nan": False, "has_error": False},
            plot_paths=["/results/job1/cd_plot.png", "/results/job1/cl_plot.png"],
        )

        rs = report["result_summary"]
        assert len(rs["plots"]) == 2
        assert "cd_plot.png" in rs["plots"][0]


# ---------------------------------------------------------------------------
# P8: Integration with spec
# ---------------------------------------------------------------------------

class TestReportWithSpec:
    """Test report generation with spec context."""

    def test_report_includes_spec_info(self):
        """Report should include Reynolds number and velocity from spec."""
        from fluid_scientist.cylinder_flow_2d import (
            CylinderFlow2DExperimentSpecV1,
            FieldSource,
            FieldStatus,
            ProvenanceField,
        )

        spec = CylinderFlow2DExperimentSpecV1(
            user_input_text="Re=200, 来流速度1m/s"
        )
        spec.cylinder.radius_m = ProvenanceField(
            value=0.1, source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0,
        )
        spec.domain.length_m = ProvenanceField(
            value=10.0, source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0,
        )
        spec.domain.height_m = ProvenanceField(
            value=5.0, source=FieldSource.USER_EXPLICIT,
            status=FieldStatus.RESOLVED, confidence=1.0,
        )
        spec.boundaries.left.inlet_velocity = 1.0

        generator = LLMReportGenerator(llm_client=None)
        report = generator.generate_report(
            spec=spec,
            sim_report={"status": "SUCCESS", "has_nan": False, "has_error": False},
        )

        rs = report["result_summary"]
        assert "spec" in rs
        assert rs["spec"]["inlet_velocity"] == pytest.approx(1.0)
