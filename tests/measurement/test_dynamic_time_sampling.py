"""Tests for dynamic time sampling — Commit 5.

Verifies:
  1. TimeSampler calculates convection time = L / U
  2. TimeSampler for transient with frequency: interval <= 1/(20*f), duration >= 10/f
  3. TimeSampler for steady state: shorter duration, coarser interval
  4. TimeSampler respects max Courant number
  5. TimeSampler with invalid velocity returns defaults
  6. estimate_vortex_shedding_frequency returns f = 0.2 * U / D for cylinder
  7. estimate_vortex_shedding_frequency returns None for Re < 40
  8. TimeSamplingSpec has new physical context fields
  9. MeasurementPlan has probes, lines, storage_estimate fields
  10. StorageEstimate model works correctly
  11. MetricPlanner._generate_measurement_plan uses dynamic time sampling
      when physics_params provided
  12. Velocity uniformity generates multiple functionObjects (mean + magnitude for CV)
  13. Storage estimate is populated and non-zero
"""

from __future__ import annotations

import pytest

from fluid_scientist.measurement.models import (
    FunctionObjectType,
    LineSamplingSpec,
    MeasurementPlan,
    ProbeSpec,
    StorageEstimate,
    TimeSamplingSpec,
)
from fluid_scientist.measurement.planner import MetricPlanner
from fluid_scientist.measurement.time_sampler import (
    PhysicalContext,
    TimeSampler,
    estimate_vortex_shedding_frequency,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sampler() -> TimeSampler:
    return TimeSampler()


@pytest.fixture
def planner() -> MetricPlanner:
    return MetricPlanner()


# ---------------------------------------------------------------------------
# 1. TimeSampler: convection time = L / U
# ---------------------------------------------------------------------------


class TestConvectionTime:
    def test_convection_time_calculated_correctly(self, sampler: TimeSampler):
        """Convection time = L / U is stored in the spec."""
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=2.0,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        assert spec.convection_time is not None
        assert spec.convection_time == pytest.approx(0.5, abs=1e-6)

    def test_convection_time_with_different_values(self, sampler: TimeSampler):
        """Convection time changes with L and U."""
        ctx = PhysicalContext(
            characteristic_length=0.1,
            characteristic_velocity=0.01,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        assert spec.convection_time is not None
        assert spec.convection_time == pytest.approx(10.0, abs=1e-6)

    def test_characteristic_length_and_velocity_stored(self, sampler: TimeSampler):
        """Characteristic length and velocity are stored in the spec."""
        ctx = PhysicalContext(
            characteristic_length=0.5,
            characteristic_velocity=1.5,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        assert spec.characteristic_length == 0.5
        assert spec.characteristic_velocity == 1.5


# ---------------------------------------------------------------------------
# 2. TimeSampler: transient with frequency
# ---------------------------------------------------------------------------


class TestTransientWithFrequency:
    def test_interval_respects_nyquist(self, sampler: TimeSampler):
        """For transient with frequency: interval <= 1 / (20 * f)."""
        freq = 10.0  # 10 Hz
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            estimated_frequency=freq,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        max_interval = 1.0 / (20.0 * freq)
        assert spec.interval <= max_interval + 1e-10

    def test_duration_at_least_10_cycles(self, sampler: TimeSampler):
        """For transient with frequency: duration >= 10 / f."""
        freq = 5.0  # 5 Hz
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            estimated_frequency=freq,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        min_duration = 10.0 / freq
        actual_duration = spec.end_time - spec.start_time
        assert actual_duration >= min_duration - 1e-6

    def test_nyquist_frequency_stored(self, sampler: TimeSampler):
        """Nyquist frequency is stored and correct."""
        freq = 10.0
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            estimated_frequency=freq,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        assert spec.nyquist_frequency is not None
        expected_nyquist = 1.0 / (2.0 * spec.interval)
        assert spec.nyquist_frequency == pytest.approx(expected_nyquist, abs=1.0)

    def test_samples_per_cycle_and_minimum_cycles(self, sampler: TimeSampler):
        """samples_per_cycle=20 and minimum_cycles=10 for transient with freq."""
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            estimated_frequency=5.0,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        assert spec.samples_per_cycle == 20
        assert spec.minimum_cycles == 10

    def test_derivation_reason_contains_frequency(self, sampler: TimeSampler):
        """derivation_reason mentions the estimated frequency."""
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            estimated_frequency=7.0,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        assert "7.0" in spec.derivation_reason or "7" in spec.derivation_reason

    def test_write_control_is_runTime(self, sampler: TimeSampler):
        """write_control is 'runTime' for dynamic sampling."""
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            estimated_frequency=5.0,
            is_transient=True,
        )
        spec = sampler.calculate(ctx)
        assert spec.write_control == "runTime"


# ---------------------------------------------------------------------------
# 3. TimeSampler: steady state
# ---------------------------------------------------------------------------


class TestSteadyState:
    def test_steady_state_shorter_duration(self, sampler: TimeSampler):
        """Steady state has shorter duration than transient without frequency."""
        steady_ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            is_transient=False,
        )
        transient_ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            is_transient=True,
        )
        steady_spec = sampler.calculate(steady_ctx)
        transient_spec = sampler.calculate(transient_ctx)
        steady_duration = steady_spec.end_time - steady_spec.start_time
        transient_duration = transient_spec.end_time - transient_spec.start_time
        assert steady_duration < transient_duration

    def test_steady_state_coarser_interval(self, sampler: TimeSampler):
        """Steady state has coarser interval than transient."""
        steady_ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            is_transient=False,
        )
        transient_ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            is_transient=True,
            estimated_frequency=10.0,
        )
        steady_spec = sampler.calculate(steady_ctx)
        transient_spec = sampler.calculate(transient_ctx)
        assert steady_spec.interval > transient_spec.interval

    def test_steady_state_duration_is_5_convection_times(self, sampler: TimeSampler):
        """Steady state duration = 5 * convection_time."""
        ctx = PhysicalContext(
            characteristic_length=2.0,
            characteristic_velocity=1.0,
            is_transient=False,
        )
        spec = sampler.calculate(ctx)
        convection_time = 2.0
        expected_duration = 5.0 * convection_time
        actual_duration = spec.end_time - spec.start_time
        assert actual_duration == pytest.approx(expected_duration, abs=1e-4)

    def test_steady_state_nyquist_is_none(self, sampler: TimeSampler):
        """Steady state has nyquist_frequency = None."""
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=1.0,
            is_transient=False,
        )
        spec = sampler.calculate(ctx)
        assert spec.nyquist_frequency is None


# ---------------------------------------------------------------------------
# 4. TimeSampler: max Courant number
# ---------------------------------------------------------------------------


class TestMaxCourant:
    def test_courant_limits_interval(self, sampler: TimeSampler):
        """When Courant limit produces smaller dt, interval is reduced."""
        # Very small viscosity -> very small dt_courant
        ctx_with_courant = PhysicalContext(
            characteristic_length=0.1,
            characteristic_velocity=1.0,
            kinematic_viscosity=1e-6,
            estimated_frequency=10.0,
            is_transient=True,
            max_courant=0.5,
        )
        ctx_without_courant = PhysicalContext(
            characteristic_length=0.1,
            characteristic_velocity=1.0,
            kinematic_viscosity=None,
            estimated_frequency=10.0,
            is_transient=True,
        )
        spec_with = sampler.calculate(ctx_with_courant)
        spec_without = sampler.calculate(ctx_without_courant)
        # With small viscosity, Courant limit should reduce interval
        assert spec_with.interval <= spec_without.interval + 1e-10

    def test_courant_dt_formula(self, sampler: TimeSampler):
        """dt_courant = max_courant * (L/10)^2 / nu."""
        L = 1.0
        nu = 1e-3
        max_courant = 1.0
        ctx = PhysicalContext(
            characteristic_length=L,
            characteristic_velocity=1.0,
            kinematic_viscosity=nu,
            estimated_frequency=1.0,
            is_transient=True,
            max_courant=max_courant,
        )
        spec = sampler.calculate(ctx)
        dt_courant = max_courant * (L / 10) ** 2 / nu
        dt_freq = 1.0 / (20.0 * 1.0)
        if dt_courant < dt_freq:
            assert spec.interval <= dt_courant + 1e-10
        else:
            assert spec.interval <= dt_freq + 1e-10


# ---------------------------------------------------------------------------
# 5. TimeSampler: invalid velocity
# ---------------------------------------------------------------------------


class TestInvalidVelocity:
    def test_zero_velocity_returns_defaults(self, sampler: TimeSampler):
        """Zero velocity returns default TimeSamplingSpec."""
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=0.0,
        )
        spec = sampler.calculate(ctx)
        assert spec.start_time == 0.0
        assert spec.end_time == 100.0
        assert spec.interval == 0.01

    def test_negative_velocity_returns_defaults(self, sampler: TimeSampler):
        """Negative velocity returns default TimeSamplingSpec."""
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=-1.0,
        )
        spec = sampler.calculate(ctx)
        assert spec.start_time == 0.0
        assert spec.end_time == 100.0
        assert spec.interval == 0.01

    def test_invalid_velocity_derivation_reason(self, sampler: TimeSampler):
        """Invalid velocity has a derivation_reason mentioning default."""
        ctx = PhysicalContext(
            characteristic_length=1.0,
            characteristic_velocity=0.0,
        )
        spec = sampler.calculate(ctx)
        assert "default" in spec.derivation_reason.lower() or "Invalid" in spec.derivation_reason


# ---------------------------------------------------------------------------
# 6. estimate_vortex_shedding_frequency: f = 0.2 * U / D
# ---------------------------------------------------------------------------


class TestVortexSheddingFrequency:
    def test_default_strouhal_number(self):
        """For Re >= 200 (default St=0.2): f = 0.2 * U / D."""
        D = 0.1
        U = 1.0
        f = estimate_vortex_shedding_frequency(D, U, reynolds=1000)
        assert f is not None
        expected = 0.2 * U / D
        assert f == pytest.approx(expected, abs=1e-10)

    def test_no_reynolds_uses_default_st(self):
        """Without Reynolds number, uses default St=0.2."""
        D = 0.5
        U = 2.0
        f = estimate_vortex_shedding_frequency(D, U)
        assert f is not None
        expected = 0.2 * U / D
        assert f == pytest.approx(expected, abs=1e-10)

    def test_frequency_scales_with_velocity(self):
        """Frequency scales linearly with velocity."""
        D = 0.1
        f1 = estimate_vortex_shedding_frequency(D, 1.0, reynolds=1000)
        f2 = estimate_vortex_shedding_frequency(D, 2.0, reynolds=2000)
        assert f1 is not None
        assert f2 is not None
        assert f2 == pytest.approx(2.0 * f1, abs=1e-10)


# ---------------------------------------------------------------------------
# 7. estimate_vortex_shedding_frequency: Re < 40 returns None
# ---------------------------------------------------------------------------


class TestVortexSheddingLowReynolds:
    def test_returns_none_for_re_below_40(self):
        """No vortex shedding below Re=40."""
        f = estimate_vortex_shedding_frequency(0.1, 1.0, reynolds=30)
        assert f is None

    def test_returns_none_for_re_equal_zero(self):
        """Re=0 returns None."""
        f = estimate_vortex_shedding_frequency(0.1, 1.0, reynolds=0)
        assert f is None

    def test_returns_none_for_invalid_diameter(self):
        """Invalid diameter returns None."""
        assert estimate_vortex_shedding_frequency(0.0, 1.0) is None
        assert estimate_vortex_shedding_frequency(-1.0, 1.0) is None

    def test_returns_none_for_invalid_velocity(self):
        """Invalid velocity returns None."""
        assert estimate_vortex_shedding_frequency(0.1, 0.0) is None
        assert estimate_vortex_shedding_frequency(0.1, -1.0) is None

    def test_low_reynolds_interpolation(self):
        """For 40 < Re < 200, Strouhal is interpolated."""
        D = 0.1
        U = 1.0
        # Re = 120 -> st = 0.18 + (120-40)/160 * 0.02 = 0.18 + 0.01 = 0.19
        f = estimate_vortex_shedding_frequency(D, U, reynolds=120)
        assert f is not None
        expected = 0.19 * U / D
        assert f == pytest.approx(expected, abs=1e-10)


# ---------------------------------------------------------------------------
# 8. TimeSamplingSpec: new physical context fields
# ---------------------------------------------------------------------------


class TestTimeSamplingSpecFields:
    def test_new_fields_exist(self):
        """TimeSamplingSpec has physical context fields."""
        spec = TimeSamplingSpec()
        assert hasattr(spec, "characteristic_length")
        assert hasattr(spec, "characteristic_velocity")
        assert hasattr(spec, "convection_time")
        assert hasattr(spec, "estimated_frequency")
        assert hasattr(spec, "nyquist_frequency")
        assert hasattr(spec, "samples_per_cycle")
        assert hasattr(spec, "minimum_cycles")
        assert hasattr(spec, "derivation_reason")

    def test_new_fields_default_none(self):
        """New physical context fields default to None or empty string."""
        spec = TimeSamplingSpec()
        assert spec.characteristic_length is None
        assert spec.characteristic_velocity is None
        assert spec.convection_time is None
        assert spec.estimated_frequency is None
        assert spec.nyquist_frequency is None
        assert spec.samples_per_cycle is None
        assert spec.minimum_cycles is None
        assert spec.derivation_reason == ""

    def test_fields_can_be_set(self):
        """Physical context fields can be set."""
        spec = TimeSamplingSpec(
            start_time=5.0,
            end_time=50.0,
            interval=0.005,
            write_control="runTime",
            characteristic_length=0.1,
            characteristic_velocity=1.0,
            convection_time=0.1,
            estimated_frequency=2.0,
            nyquist_frequency=20.0,
            samples_per_cycle=20,
            minimum_cycles=10,
            derivation_reason="test reason",
        )
        assert spec.characteristic_length == 0.1
        assert spec.characteristic_velocity == 1.0
        assert spec.convection_time == 0.1
        assert spec.estimated_frequency == 2.0
        assert spec.nyquist_frequency == 20.0
        assert spec.samples_per_cycle == 20
        assert spec.minimum_cycles == 10
        assert spec.derivation_reason == "test reason"


# ---------------------------------------------------------------------------
# 9. MeasurementPlan: new fields
# ---------------------------------------------------------------------------


class TestMeasurementPlanNewFields:
    def test_has_probes_field(self):
        """MeasurementPlan has probes field."""
        mp = MeasurementPlan()
        assert hasattr(mp, "probes")
        assert mp.probes == []

    def test_has_lines_field(self):
        """MeasurementPlan has lines field."""
        mp = MeasurementPlan()
        assert hasattr(mp, "lines")
        assert mp.lines == []

    def test_has_storage_estimate_field(self):
        """MeasurementPlan has storage_estimate field."""
        mp = MeasurementPlan()
        assert hasattr(mp, "storage_estimate")
        assert mp.storage_estimate is None

    def test_probes_can_be_set(self):
        """Probes can be added to MeasurementPlan."""
        probe = ProbeSpec(id="probe_1", field="U", positions=[{"x": 0.1, "y": 0.2}])
        mp = MeasurementPlan(probes=[probe])
        assert len(mp.probes) == 1
        assert mp.probes[0].id == "probe_1"

    def test_lines_can_be_set(self):
        """Lines can be added to MeasurementPlan."""
        line = LineSamplingSpec(
            id="line_1",
            field="U",
            start={"x": 0.0, "y": 0.0},
            end={"x": 1.0, "y": 0.0},
            num_points=100,
        )
        mp = MeasurementPlan(lines=[line])
        assert len(mp.lines) == 1
        assert mp.lines[0].id == "line_1"
        assert mp.lines[0].num_points == 100


# ---------------------------------------------------------------------------
# 10. StorageEstimate model
# ---------------------------------------------------------------------------


class TestStorageEstimateModel:
    def test_default_values(self):
        """StorageEstimate has correct defaults."""
        se = StorageEstimate()
        assert se.estimated_bytes == 0
        assert se.breakdown == {}
        assert se.exceeds_budget is False
        assert se.budget_bytes is None

    def test_can_set_all_fields(self):
        """All StorageEstimate fields can be set."""
        se = StorageEstimate(
            estimated_bytes=1000000,
            breakdown={"fo_forceCoeffs_1": 100000, "field_U": 500000},
            exceeds_budget=True,
            budget_bytes=500000,
        )
        assert se.estimated_bytes == 1000000
        assert se.breakdown["fo_forceCoeffs_1"] == 100000
        assert se.exceeds_budget is True
        assert se.budget_bytes == 500000

    def test_breakdown_is_dict_of_str_to_int(self):
        """Breakdown is a dict mapping category names to byte counts."""
        se = StorageEstimate(
            estimated_bytes=300,
            breakdown={"a": 100, "b": 200},
        )
        assert isinstance(se.breakdown, dict)
        for k, v in se.breakdown.items():
            assert isinstance(k, str)
            assert isinstance(v, int)


# ---------------------------------------------------------------------------
# 11. MetricPlanner._generate_measurement_plan: dynamic time sampling
# ---------------------------------------------------------------------------


class TestDynamicTimeSamplingInPlanner:
    def test_uses_dynamic_sampling_with_physics_params(self, planner: MetricPlanner):
        """When physics_params has valid velocity and length, dynamic sampling is used."""
        physics_params = {
            "diameter": 0.1,
            "velocity": 1.0,
            "kinematic_viscosity": 1e-6,
            "is_transient": True,
        }
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["pressure_drop"],
            experiment_type="cylinder_flow",
            physics_params=physics_params,
        )
        ts = plan.time_sampling
        # Dynamic sampling should populate physical context
        assert ts.characteristic_length == 0.1
        assert ts.characteristic_velocity == 1.0
        assert ts.convection_time is not None
        assert ts.derivation_reason != ""

    def test_falls_back_without_physics_params(self, planner: MetricPlanner):
        """Without physics_params, falls back to hardcoded defaults."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["pressure_drop"],
            experiment_type="cylinder_flow",
            physics_params=None,
        )
        ts = plan.time_sampling
        # Hardcoded defaults
        assert ts.start_time == 20.0
        assert ts.end_time == 100.0
        assert ts.interval == 0.01
        # No physical context
        assert ts.characteristic_length is None
        assert ts.convection_time is None

    def test_strouhal_with_physics_uses_vortex_frequency(self, planner: MetricPlanner):
        """Strouhal metric with physics params estimates vortex shedding frequency."""
        physics_params = {
            "diameter": 0.1,
            "velocity": 1.0,
            "kinematic_viscosity": 1e-5,
            "is_transient": True,
        }
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["strouhal_number"],
            experiment_type="cylinder_flow",
            physics_params=physics_params,
        )
        ts = plan.time_sampling
        # Should have estimated frequency from vortex shedding
        assert ts.estimated_frequency is not None
        assert ts.estimated_frequency > 0
        # Should use runTime write control
        assert ts.write_control == "runTime"

    def test_strouhal_without_physics_keeps_hardcoded(self, planner: MetricPlanner):
        """Strouhal without physics params keeps old hardcoded values."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["strouhal_number"],
            experiment_type="cylinder_flow",
            physics_params=None,
        )
        ts = plan.time_sampling
        assert ts.interval <= 0.005
        assert ts.end_time >= 200.0

    def test_propose_metrics_passes_physics_params(self, planner: MetricPlanner):
        """propose_metrics extracts physics params from physics_spec and passes them."""
        from fluid_scientist.research.models import ResearchPhysicsSpec

        physics_spec = ResearchPhysicsSpec(
            geometry_facts={"diameter": 0.1},
            operating_conditions={"inlet_velocity": 1.0},
            material_facts={"kinematic_viscosity": 1e-6},
            temporal_type="transient",
        )
        plan = planner.propose_metrics(
            research_objective="test",
            physics_spec=physics_spec,
            user_metrics=["pressure_drop"],
            experiment_type="cylinder_flow",
        )
        ts = plan.measurement_plan.time_sampling
        # Should have dynamic sampling
        assert ts.characteristic_length == 0.1
        assert ts.characteristic_velocity == 1.0
        assert ts.convection_time is not None

    def test_propose_metrics_without_physics_spec(self, planner: MetricPlanner):
        """propose_metrics without physics_spec uses hardcoded defaults."""
        plan = planner.propose_metrics(
            research_objective="test",
            physics_spec=None,
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )
        ts = plan.measurement_plan.time_sampling
        assert ts.start_time == 20.0
        assert ts.characteristic_length is None


# ---------------------------------------------------------------------------
# 12. Velocity uniformity: multiple functionObjects
# ---------------------------------------------------------------------------


class TestVelocityUniformityMultipleFOs:
    def test_generates_mean_and_magnitude_fos(self, planner: MetricPlanner):
        """Velocity uniformity generates both mean and magnitude functionObjects."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["outlet_velocity_uniformity"],
            experiment_type="laminar_pipe",
        )
        fo_names = {fo.name for fo in plan.function_objects}
        assert "velocity_outlet_mean" in fo_names
        assert "velocity_outlet_magnitude" in fo_names

    def test_mean_fo_uses_areaAverage_U(self, planner: MetricPlanner):
        """Mean FO uses areaAverage operation on U field."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["outlet_velocity_uniformity"],
            experiment_type="laminar_pipe",
        )
        mean_fo = next(
            fo for fo in plan.function_objects if fo.name == "velocity_outlet_mean"
        )
        assert mean_fo.field == "U"
        assert mean_fo.operation == "areaAverage"
        assert mean_fo.type == FunctionObjectType.SURFACE_FIELD_VALUE

    def test_magnitude_fo_uses_mag_U(self, planner: MetricPlanner):
        """Magnitude FO uses areaAverage operation on mag(U) field."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["outlet_velocity_uniformity"],
            experiment_type="laminar_pipe",
        )
        mag_fo = next(
            fo for fo in plan.function_objects if fo.name == "velocity_outlet_magnitude"
        )
        assert mag_fo.field == "mag(U)"
        assert mag_fo.operation == "areaAverage"

    def test_cv_formula_documented_in_comment(self, planner: MetricPlanner):
        """The CV formula is documented (mean + magnitude for CV calculation)."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["outlet_velocity_uniformity"],
            experiment_type="laminar_pipe",
        )
        # At least 2 FOs for velocity uniformity
        vel_fos = [
            fo for fo in plan.function_objects
            if fo.surface == "outlet_uniformity_section"
        ]
        assert len(vel_fos) >= 2

    def test_velocity_uniformity_binding(self, planner: MetricPlanner):
        """Velocity uniformity has a metric binding."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["outlet_velocity_uniformity"],
            experiment_type="laminar_pipe",
        )
        binding = next(
            b for b in plan.metric_bindings
            if b.metric_id == "outlet_velocity_uniformity"
        )
        assert binding.function_object == "velocity_outlet_mean"

    def test_velocity_uniformity_via_propose_metrics(self, planner: MetricPlanner):
        """Velocity uniformity via propose_metrics generates multiple FOs."""
        plan = planner.propose_metrics(
            research_objective="test",
            user_metrics=["outlet_velocity_uniformity"],
            experiment_type="laminar_pipe",
        )
        mp = plan.measurement_plan
        fo_names = {fo.name for fo in mp.function_objects}
        assert "velocity_outlet_mean" in fo_names
        assert "velocity_outlet_magnitude" in fo_names


# ---------------------------------------------------------------------------
# 13. Storage estimate populated and non-zero
# ---------------------------------------------------------------------------


class TestStorageEstimatePopulated:
    def test_storage_estimate_not_none(self, planner: MetricPlanner):
        """Storage estimate is populated (not None)."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["pressure_drop", "drag_coefficient"],
            experiment_type="cylinder_flow",
        )
        assert plan.storage_estimate is not None

    def test_storage_estimate_non_zero(self, planner: MetricPlanner):
        """Storage estimate has non-zero estimated_bytes."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["pressure_drop", "drag_coefficient"],
            experiment_type="cylinder_flow",
        )
        assert plan.storage_estimate is not None
        assert plan.storage_estimate.estimated_bytes > 0

    def test_storage_estimate_breakdown_populated(self, planner: MetricPlanner):
        """Storage estimate breakdown has entries for functionObjects and fields."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )
        assert plan.storage_estimate is not None
        breakdown = plan.storage_estimate.breakdown
        # Should have entries for function objects and fields
        assert len(breakdown) > 0
        # Should include field entries
        field_keys = [k for k in breakdown if k.startswith("field_")]
        assert len(field_keys) >= 2  # U and p
        # Should include fo entries
        fo_keys = [k for k in breakdown if k.startswith("fo_")]
        assert len(fo_keys) >= 1

    def test_storage_estimate_via_propose_metrics(self, planner: MetricPlanner):
        """Storage estimate is populated via propose_metrics."""
        plan = planner.propose_metrics(
            research_objective="test",
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )
        se = plan.measurement_plan.storage_estimate
        assert se is not None
        assert se.estimated_bytes > 0

    def test_storage_estimate_scales_with_timesteps(self, planner: MetricPlanner):
        """More timesteps -> more storage."""
        # Short duration
        plan_short = MetricPlanner._generate_measurement_plan(
            metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
            physics_params=None,  # uses hardcoded: end=100, interval=0.01
        )
        # Long duration (Strouhal)
        plan_long = MetricPlanner._generate_measurement_plan(
            metrics=["strouhal_number"],
            experiment_type="cylinder_flow",
            physics_params=None,  # uses hardcoded: end=200, interval=0.005
        )
        se_short = plan_short.storage_estimate
        se_long = plan_long.storage_estimate
        assert se_short is not None
        assert se_long is not None
        # Long plan has more timesteps
        assert se_long.estimated_bytes > se_short.estimated_bytes

    def test_storage_estimate_exceeds_budget_default_false(self, planner: MetricPlanner):
        """exceeds_budget defaults to False."""
        plan = MetricPlanner._generate_measurement_plan(
            metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )
        assert plan.storage_estimate is not None
        assert plan.storage_estimate.exceeds_budget is False
        assert plan.storage_estimate.budget_bytes is None
