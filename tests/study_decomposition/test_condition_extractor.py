"""Tests for ConditionExtractor."""

from __future__ import annotations

import pytest

from fluid_scientist.study_decomposition.condition_extractor import ConditionExtractor


@pytest.fixture
def extractor() -> ConditionExtractor:
    return ConditionExtractor()


class TestInitialConditions:
    """Test initial condition extraction."""

    def test_initially_at_rest_chinese(
        self, extractor: ConditionExtractor
    ) -> None:
        bcs, ics, amb = extractor.extract("初始静止", "cylinder")
        assert "initially_at_rest" in ics
        assert ics["initially_at_rest"]["type"] == "initially_at_rest"
        assert isinstance(ics["initially_at_rest"]["source_text"], str)
        assert amb == []

    def test_initially_at_rest_english(
        self, extractor: ConditionExtractor
    ) -> None:
        bcs, ics, _amb = extractor.extract("initially at rest", "cylinder")
        assert "initially_at_rest" in ics

    def test_fully_developed_chinese(
        self, extractor: ConditionExtractor
    ) -> None:
        _bcs, ics, _amb = extractor.extract("充分发展流动", "pipe")
        assert "fully_developed" in ics

    def test_fully_developed_english(
        self, extractor: ConditionExtractor
    ) -> None:
        _bcs, ics, _amb = extractor.extract("fully developed inlet", "pipe")
        assert "fully_developed" in ics

    def test_linear_stratification(
        self, extractor: ConditionExtractor
    ) -> None:
        _bcs, ics, _amb = extractor.extract("线性分层", "cylinder")
        assert "linear_stratification" in ics


class TestBoundaryConditions:
    """Test boundary condition extraction."""

    def test_no_slip_chinese(self, extractor: ConditionExtractor) -> None:
        bcs, _ics, _amb = extractor.extract("壁面无滑移", "cylinder")
        assert "no_slip" in bcs
        assert bcs["no_slip"]["type"] == "no_slip"

    def test_no_slip_english(self, extractor: ConditionExtractor) -> None:
        bcs, _ics, _amb = extractor.extract("no-slip wall", "cylinder")
        assert "no_slip" in bcs

    def test_free_slip(self, extractor: ConditionExtractor) -> None:
        bcs, _ics, _amb = extractor.extract("free-slip boundary", "cavity")
        assert "free_slip" in bcs

    def test_pressure_outlet_chinese(
        self, extractor: ConditionExtractor
    ) -> None:
        bcs, _ics, _amb = extractor.extract("压力出口", "pipe")
        assert "pressure_outlet" in bcs

    def test_pressure_outlet_english(
        self, extractor: ConditionExtractor
    ) -> None:
        bcs, _ics, _amb = extractor.extract("pressure outlet", "pipe")
        assert "pressure_outlet" in bcs

    def test_periodic(self, extractor: ConditionExtractor) -> None:
        bcs, _ics, _amb = extractor.extract("周期边界", "cylinder")
        assert "periodic" in bcs

    def test_velocity_profile(self, extractor: ConditionExtractor) -> None:
        bcs, _ics, _amb = extractor.extract("速度剖面入口", "pipe")
        assert "velocity_profile" in bcs

    def test_power_law(self, extractor: ConditionExtractor) -> None:
        bcs, _ics, _amb = extractor.extract("幂律速度剖面", "pipe")
        assert "power_law" in bcs

    def test_parabolic(self, extractor: ConditionExtractor) -> None:
        bcs, _ics, _amb = extractor.extract("抛物型速度剖面", "pipe")
        assert "parabolic" in bcs

    def test_advective(self, extractor: ConditionExtractor) -> None:
        bcs, _ics, _amb = extractor.extract("对流边界条件", "cylinder")
        assert "advective" in bcs


class TestMixedConditions:
    """Test extraction with multiple conditions present."""

    def test_mixed_bcs_and_ics(self, extractor: ConditionExtractor) -> None:
        text = (
            "流场初始静止，入口速度剖面为抛物型，壁面无滑移，"
            "出口压力出口，计算域采用周期边界"
        )
        bcs, ics, _amb = extractor.extract(text, "pipe")
        assert "initially_at_rest" in ics
        assert "velocity_profile" in bcs
        assert "parabolic" in bcs
        assert "no_slip" in bcs
        assert "pressure_outlet" in bcs
        assert "periodic" in bcs

    def test_fully_developed_and_power_law(
        self, extractor: ConditionExtractor
    ) -> None:
        text = "入口充分发展，采用幂律速度剖面"
        bcs, ics, _amb = extractor.extract(text, "pipe")
        assert "fully_developed" in ics
        assert "power_law" in bcs


class TestReturnTypeAndEdgeCases:
    """Test return types and edge cases."""

    def test_returns_tuple_of_three(
        self, extractor: ConditionExtractor
    ) -> None:
        result = extractor.extract("无滑移", "cylinder")
        assert isinstance(result, tuple)
        assert len(result) == 3
        bcs, ics, amb = result
        assert isinstance(bcs, dict)
        assert isinstance(ics, dict)
        assert isinstance(amb, list)

    def test_condition_dict_structure(
        self, extractor: ConditionExtractor
    ) -> None:
        bcs, _ics, _amb = extractor.extract("no-slip wall", "cylinder")
        for cond in bcs.values():
            assert "type" in cond
            assert "source_text" in cond

    def test_empty_text(self, extractor: ConditionExtractor) -> None:
        bcs, ics, amb = extractor.extract("", "cylinder")
        assert bcs == {}
        assert ics == {}
        assert amb == []

    def test_study_type_accepted(
        self, extractor: ConditionExtractor
    ) -> None:
        """Verify that study_type parameter is accepted for all types."""
        for st in ["cylinder", "pipe", "step", "jet", "cavity", "elliptic", "unknown"]:
            bcs, ics, amb = extractor.extract("无滑移", st)
            assert isinstance(bcs, dict)
            assert isinstance(ics, dict)
            assert isinstance(amb, list)
