"""Tests for ParameterExtractor."""

from __future__ import annotations

import pytest

from fluid_scientist.study_decomposition.models import ExtractedParameter
from fluid_scientist.study_decomposition.parameter_extractor import ParameterExtractor


@pytest.fixture
def extractor() -> ParameterExtractor:
    return ParameterExtractor()


class TestReynoldsNumber:
    """Test Reynolds number extraction."""

    def test_re_integer(self, extractor: ParameterExtractor) -> None:
        params, ambiguities = extractor.extract("Re=3900", "cylinder")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == 3900
        assert re_params[0].dimensionless is True
        assert re_params[0].source == "user_provided"
        assert re_params[0].confidence == 0.99
        assert ambiguities == []

    def test_re_float(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("Re=500.5", "cylinder")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == pytest.approx(500.5)

    def test_re_with_spaces(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("Re = 5000", "pipe")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == 5000

    def test_re_chinese_label(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("雷诺数=2000", "cylinder")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == 2000

    def test_re_chinese_colon(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("Re：1000", "cylinder")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert len(re_params) == 1
        assert re_params[0].value == 1000

    def test_no_false_re_in_more(self, extractor: ParameterExtractor) -> None:
        """'more=5' must NOT be parsed as Reynolds number."""
        params, _amb = extractor.extract("more=5", "cylinder")
        re_params = [p for p in params if p.canonical_id == "reynolds_number"]
        assert re_params == []


class TestFroudeNumber:
    """Test Froude number extraction."""

    def test_fr_float(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("Fr=0.2", "cylinder")
        fr_params = [p for p in params if p.canonical_id == "froude_number"]
        assert len(fr_params) == 1
        assert fr_params[0].value == pytest.approx(0.2)
        assert fr_params[0].dimensionless is True

    def test_fr_chinese_label(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("弗劳德数=1.5", "cylinder")
        fr_params = [p for p in params if p.canonical_id == "froude_number"]
        assert len(fr_params) == 1
        assert fr_params[0].value == pytest.approx(1.5)


class TestInclinationAngle:
    """Test angle extraction."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("45度", 45.0),
            ("30°", 30.0),
            ("60 deg", 60.0),
            ("90 degrees", 90.0),
        ],
    )
    def test_angle_variants(
        self, extractor: ParameterExtractor, text: str, expected: float
    ) -> None:
        params, _amb = extractor.extract(text, "cylinder")
        angles = [p for p in params if p.canonical_id == "inclination_angle"]
        assert len(angles) == 1
        assert angles[0].value == expected
        assert angles[0].unit == "deg"


class TestGeometricRatios:
    """Test aspect ratio, gap ratio, and expansion ratio extraction."""

    def test_aspect_ratio_chinese(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("长短轴比=3", "elliptic")
        ar = [p for p in params if p.canonical_id == "aspect_ratio"]
        assert len(ar) == 1
        assert ar[0].value == 3
        assert ar[0].dimensionless is True

    def test_aspect_ratio_english(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("aspect ratio 2.5", "elliptic")
        ar = [p for p in params if p.canonical_id == "aspect_ratio"]
        assert len(ar) == 1
        assert ar[0].value == pytest.approx(2.5)

    def test_gap_ratio(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("gap ratio=0.5", "cylinder")
        gr = [p for p in params if p.canonical_id == "gap_ratio"]
        assert len(gr) == 1
        assert gr[0].value == pytest.approx(0.5)

    def test_expansion_ratio(self, extractor: ParameterExtractor) -> None:
        params, _amb = extractor.extract("expansion ratio=2.0", "step")
        er = [p for p in params if p.canonical_id == "expansion_ratio"]
        assert len(er) == 1
        assert er[0].value == pytest.approx(2.0)

    def test_all_ratios_combined(self, extractor: ParameterExtractor) -> None:
        text = "长短轴比=3 gap ratio=0.5 expansion ratio=2.0"
        params, _amb = extractor.extract(text, "elliptic")
        ids = {p.canonical_id for p in params}
        assert {"aspect_ratio", "gap_ratio", "expansion_ratio"} <= ids


class TestReturnTypeAndEdgeCases:
    """Test return types and edge cases."""

    def test_returns_tuple(self, extractor: ParameterExtractor) -> None:
        result = extractor.extract("Re=100", "cylinder")
        assert isinstance(result, tuple)
        assert len(result) == 2
        params, ambiguities = result
        assert isinstance(params, list)
        assert isinstance(ambiguities, list)

    def test_all_params_are_extracted_parameter(
        self, extractor: ParameterExtractor
    ) -> None:
        params, _amb = extractor.extract("Re=100 Fr=1.5 30°", "cylinder")
        assert all(isinstance(p, ExtractedParameter) for p in params)
        ids = {p.canonical_id for p in params}
        assert ids == {"reynolds_number", "froude_number", "inclination_angle"}

    def test_empty_text(self, extractor: ParameterExtractor) -> None:
        params, ambiguities = extractor.extract("", "cylinder")
        assert params == []
        assert ambiguities == []

    def test_study_type_does_not_affect_basic_extraction(
        self, extractor: ParameterExtractor
    ) -> None:
        """The same text should yield the same parameters regardless of study_type."""
        text = "Re=5000"
        params_cyl, _ = extractor.extract(text, "cylinder")
        params_pipe, _ = extractor.extract(text, "pipe")
        params_step, _ = extractor.extract(text, "step")
        assert len(params_cyl) == len(params_pipe) == len(params_step) == 1
        assert params_cyl[0].value == params_pipe[0].value == params_step[0].value == 5000
