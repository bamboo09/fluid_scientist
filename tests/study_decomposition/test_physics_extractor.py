"""Tests for PhysicsFrameExtractor.

Covers the five canonical CFD study descriptions used to validate the
study-decomposition draft workflow, plus focused unit tests for each
extraction helper (parameters, observables, conditions, analysis goals).
"""

from __future__ import annotations

import pytest

from fluid_scientist.study_decomposition.models import (
    ExtractedParameter,
    ObservableSpec,
)
from fluid_scientist.study_decomposition.physics_extractor import PhysicsFrameExtractor


@pytest.fixture
def extractor() -> PhysicsFrameExtractor:
    return PhysicsFrameExtractor()


# ---------------------------------------------------------------------------
# Five canonical CFD studies
# ---------------------------------------------------------------------------


class TestCanonicalStudies:
    """The five canonical CFD study descriptions used as acceptance cases."""

    def test_near_wall_inclined_cylinder_wake(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        text = "近壁倾斜圆柱 Re=3900 三维湍流尾迹"
        frame = extractor.extract(text)

        assert frame.dimension == "3D"
        assert frame.temporal_type == "transient"
        assert frame.flow_regime == "turbulent"
        assert frame.is_inclined is True
        assert frame.near_wall is True
        assert frame.is_wall_bounded is True
        assert frame.geometry_type == "cylinder"

        re_params = [
            p for p in extractor.extract_parameters(text)
            if p.canonical_id == "reynolds_number"
        ]
        assert len(re_params) == 1
        assert re_params[0].value == 3900
        assert re_params[0].dimensionless is True
        assert re_params[0].source == "user_provided"

    def test_inclined_jet_impingement(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        text = "倾斜圆射流 45 度冲击平壁 Re=23000"
        frame = extractor.extract(text)

        assert frame.is_inclined is True
        assert frame.geometry_type == "jet"
        # "平壁" (flat wall) is not "近壁" (near wall): no false positive.
        assert frame.near_wall is False

        params = extractor.extract_parameters(text)
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == 23000

        angle_params = [p for p in params if p.canonical_id == "inclination_angle"]
        assert len(angle_params) == 1
        assert angle_params[0].value == 45
        assert angle_params[0].unit == "deg"

    def test_inclined_elliptic_cylinder(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        text = "倾斜椭圆柱 Re=5000 非定常绕流"
        frame = extractor.extract(text)

        assert frame.is_inclined is True
        assert frame.geometry_type == "elliptic"
        assert frame.temporal_type == "transient"

        re_params = [
            p for p in extractor.extract_parameters(text)
            if p.canonical_id == "reynolds_number"
        ]
        assert len(re_params) == 1
        assert re_params[0].value == 5000

    def test_stratified_oscillating_cylinder(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        text = "密度分层流体中水平圆柱垂向振荡 Re=500 Fr=0.2"
        frame = extractor.extract(text)

        assert frame.has_density_stratification is True
        assert frame.has_buoyancy is True
        assert frame.is_moving_body is True
        assert frame.geometry_type == "cylinder"

        params = extractor.extract_parameters(text)
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == 500

        fr_params = [p for p in params if p.canonical_id == "froude_number"]
        assert len(fr_params) == 1
        assert fr_params[0].value == pytest.approx(0.2)
        assert fr_params[0].dimensionless is True

    def test_backward_facing_step(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        text = "后台阶流动 Re=5000 分离再附"
        frame = extractor.extract(text)

        assert frame.geometry_type == "step"

        re_params = [
            p for p in extractor.extract_parameters(text)
            if p.canonical_id == "reynolds_number"
        ]
        assert len(re_params) == 1
        assert re_params[0].value == 5000

        # "再附" should surface a reattachment observable.
        observables = extractor.extract_observables(text)
        assert any(o.observable_id == "reattachment" for o in observables)


# ---------------------------------------------------------------------------
# extract() flag coverage
# ---------------------------------------------------------------------------


class TestExtractFlags:
    """Targeted checks for individual PhysicsFrame boolean flags."""

    def test_dimension_2d(self, extractor: PhysicsFrameExtractor) -> None:
        frame = extractor.extract("二维层流方腔流动")
        assert frame.dimension == "2D"
        assert frame.flow_regime == "laminar"
        assert frame.geometry_type == "cavity"

    def test_dimension_english_3d(self, extractor: PhysicsFrameExtractor) -> None:
        frame = extractor.extract("3D turbulent pipe flow")
        assert frame.dimension == "3D"
        assert frame.flow_regime == "turbulent"
        assert frame.geometry_type == "pipe"

    def test_steady_overrides_turbulent_inference(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        # Turbulent but explicitly steady -> steady wins.
        frame = extractor.extract("steady turbulent pipe flow")
        assert frame.temporal_type == "steady"
        assert frame.flow_regime == "turbulent"

    def test_transitional_regime(self, extractor: PhysicsFrameExtractor) -> None:
        frame = extractor.extract("转捩流动 transitional boundary layer")
        assert frame.flow_regime == "transitional"

    def test_thermal_flag(self, extractor: PhysicsFrameExtractor) -> None:
        frame = extractor.extract(" heated cylinder with temperature gradient")
        assert frame.has_thermal is True

    def test_spanwise_periodic_flag(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        frame = extractor.extract("展向周期边界 cylinder wake")
        assert frame.has_spanwise_periodic is True
        assert frame.geometry_type == "cylinder"

    def test_buoyancy_keyword_direct(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        frame = extractor.extract("buoyancy-driven cavity flow")
        assert frame.has_buoyancy is True
        assert frame.has_density_stratification is False

    def test_empty_text_returns_empty_frame(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        frame = extractor.extract("")
        assert frame.dimension is None
        assert frame.temporal_type is None
        assert frame.flow_regime is None
        assert frame.geometry_type is None


# ---------------------------------------------------------------------------
# extract_parameters
# ---------------------------------------------------------------------------


class TestExtractParameters:
    def test_returns_extracted_parameter_instances(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        params = extractor.extract_parameters("Re=100 Fr=1.5 30°")
        assert all(isinstance(p, ExtractedParameter) for p in params)
        ids = {p.canonical_id for p in params}
        assert ids == {"reynolds_number", "froude_number", "inclination_angle"}

    def test_re_with_spaces(self, extractor: PhysicsFrameExtractor) -> None:
        params = extractor.extract_parameters("Re = 5000 圆柱绕流")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == 5000

    def test_re_chinese_label(self, extractor: PhysicsFrameExtractor) -> None:
        params = extractor.extract_parameters("雷诺数=2000")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == 2000

    def test_angle_variants(self, extractor: PhysicsFrameExtractor) -> None:
        for text, expected in [("45度", 45.0), ("30°", 30.0), ("60 deg", 60.0)]:
            params = extractor.extract_parameters(text)
            angles = [
                p for p in params if p.canonical_id == "inclination_angle"
            ]
            assert len(angles) == 1
            assert angles[0].value == expected

    def test_aspect_gap_expansion_ratios(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        text = "长短轴比=3 gap ratio=0.5 expansion ratio=2.0"
        params = extractor.extract_parameters(text)
        values = {p.canonical_id: p.value for p in params}
        assert values.get("aspect_ratio") == 3
        assert values.get("gap_ratio") == pytest.approx(0.5)
        assert values.get("expansion_ratio") == pytest.approx(2.0)

    def test_no_false_re_inside_other_words(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        # "more=5" must NOT be parsed as Reynolds; "Fr=0.2" must not be Re.
        params = extractor.extract_parameters("more=5 Fr=0.2")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert re_params == []
        fr_params = [p for p in params if p.canonical_id == "froude_number"]
        assert len(fr_params) == 1

    def test_empty_text_no_parameters(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        assert extractor.extract_parameters("") == []


# ---------------------------------------------------------------------------
# extract_observables
# ---------------------------------------------------------------------------


class TestExtractObservables:
    def test_returns_observable_spec_instances(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        observables = extractor.extract_observables("阻力与升力频谱")
        assert all(isinstance(o, ObservableSpec) for o in observables)
        ids = {o.observable_id for o in observables}
        assert {"drag", "lift", "spectrum"} <= ids

    def test_dedupes_observable_ids(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        # "涡" and "vortex" both map to vortex_structure -> only one entry.
        observables = extractor.extract_observables("涡脱落 vortex shedding")
        ids = [o.observable_id for o in observables]
        assert ids.count("vortex_structure") == 1

    def test_categories_are_valid(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        observables = extractor.extract_observables(
            "drag pressure heat flux reattachment internal wave mixing layer"
        )
        categories = {o.category for o in observables}
        assert categories <= {
            "force", "pressure", "heat_flux", "reattachment",
            "internal_wave", "mixing",
        }

    def test_empty_text_no_observables(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        assert extractor.extract_observables("") == []


# ---------------------------------------------------------------------------
# extract_conditions
# ---------------------------------------------------------------------------


class TestExtractConditions:
    def test_returns_tuple_of_lists(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        ics, bcs = extractor.extract_conditions("初始静止 壁面无滑移")
        assert isinstance(ics, list)
        assert isinstance(bcs, list)

    def test_mixed_conditions(self, extractor: PhysicsFrameExtractor) -> None:
        text = (
            "流场初始静止，入口速度剖面为抛物型，壁面无滑移，"
            "出口压力出口，计算域采用周期边界"
        )
        ics, bcs = extractor.extract_conditions(text)
        ic_types = {c["type"] for c in ics}
        bc_types = {c["type"] for c in bcs}
        assert ic_types == {"initially_at_rest"}
        assert {
            "velocity_profile", "parabolic", "no_slip",
            "pressure_outlet", "periodic",
        } <= bc_types

    def test_fully_developed_and_power_law(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        text = "入口充分发展，采用幂律速度剖面"
        ics, bcs = extractor.extract_conditions(text)
        assert any(c["type"] == "fully_developed" for c in ics)
        assert any(c["type"] == "power_law" for c in bcs)

    def test_empty_text_no_conditions(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        ics, bcs = extractor.extract_conditions("")
        assert ics == []
        assert bcs == []


# ---------------------------------------------------------------------------
# extract_analysis_goals
# ---------------------------------------------------------------------------


class TestExtractAnalysisGoals:
    def test_detects_chinese_goal_keywords(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        goals = extractor.extract_analysis_goals(
            "揭示圆柱尾迹的卡门涡街机理"
        )
        assert len(goals) >= 1
        assert any("揭示" in g for g in goals)
        assert any("机理" in g for g in goals)

    def test_detects_english_goal_keywords(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        goals = extractor.extract_analysis_goals(
            "investigate the wake mechanism of the cylinder"
        )
        assert len(goals) >= 1
        assert any("investigate" in g for g in goals)
        assert any("mechanism" in g for g in goals)

    def test_no_goals_in_plain_description(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        goals = extractor.extract_analysis_goals("近壁倾斜圆柱 Re=3900 三维湍流尾迹")
        assert goals == []

    def test_goals_are_deduped(
        self, extractor: PhysicsFrameExtractor
    ) -> None:
        goals = extractor.extract_analysis_goals("揭示 揭示 机理 机理")
        # Each canonical goal description appears at most once.
        assert len(goals) == len(set(goals))
