"""Tests for ObservableExtractor."""

from __future__ import annotations

import pytest

from fluid_scientist.study_decomposition.models import ObservableSpec
from fluid_scientist.study_decomposition.observable_extractor import ObservableExtractor


@pytest.fixture
def extractor() -> ObservableExtractor:
    return ObservableExtractor()


class TestObservableDetection:
    """Test detection of individual observables."""

    def test_drag_chinese(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("阻力系数", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "drag" in ids

    def test_drag_english(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("drag coefficient", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "drag" in ids

    def test_lift_chinese(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("升力系数", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "lift" in ids

    def test_strouhal(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("斯特劳哈尔数", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "strouhal_number" in ids

    def test_spectrum(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("频谱分析", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "spectrum" in ids

    def test_pressure(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("压力分布", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "pressure" in ids

    def test_heat_flux(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("热通量", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "heat_flux" in ids

    def test_reattachment_chinese(
        self, extractor: ObservableExtractor
    ) -> None:
        obs, _amb = extractor.extract("再附长度", "step")
        ids = {o.observable_id for o in obs}
        assert "reattachment" in ids

    def test_reattachment_english(
        self, extractor: ObservableExtractor
    ) -> None:
        obs, _amb = extractor.extract("reattachment length", "step")
        ids = {o.observable_id for o in obs}
        assert "reattachment" in ids

    def test_recirculation(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("回流区结构", "step")
        ids = {o.observable_id for o in obs}
        assert "recirculation" in ids

    def test_vortex_chinese(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("涡脱落", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "vortex_structure" in ids

    def test_vortex_english(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("vortex shedding", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "vortex_structure" in ids

    def test_wake(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("尾迹结构", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "wake" in ids

    def test_mixing_layer(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("混合层发展", "jet")
        ids = {o.observable_id for o in obs}
        assert "mixing_layer" in ids

    def test_internal_wave(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("内波传播", "cylinder")
        ids = {o.observable_id for o in obs}
        assert "internal_wave" in ids

    def test_reynolds_stress(self, extractor: ObservableExtractor) -> None:
        obs, _amb = extractor.extract("雷诺应力分布", "pipe")
        ids = {o.observable_id for o in obs}
        assert "reynolds_stress" in ids


class TestDeduplication:
    """Test that observables are de-duplicated."""

    def test_dedupes_observable_ids(
        self, extractor: ObservableExtractor
    ) -> None:
        """"涡" and "vortex" both map to vortex_structure -> only one entry."""
        obs, _amb = extractor.extract("涡脱落 vortex shedding", "cylinder")
        ids = [o.observable_id for o in obs]
        assert ids.count("vortex_structure") == 1


class TestMultipleObservables:
    """Test extraction of multiple observables from a single text."""

    def test_drag_lift_spectrum(
        self, extractor: ObservableExtractor
    ) -> None:
        obs, _amb = extractor.extract("阻力与升力频谱", "cylinder")
        ids = {o.observable_id for o in obs}
        assert {"drag", "lift", "spectrum"} <= ids

    def test_all_force_categories_valid(
        self, extractor: ObservableExtractor
    ) -> None:
        obs, _amb = extractor.extract(
            "drag pressure heat flux reattachment internal wave mixing layer",
            "cylinder",
        )
        categories = {o.category for o in obs}
        assert categories <= {
            "force", "pressure", "heat_flux", "reattachment",
            "internal_wave", "mixing",
        }


class TestReturnTypeAndEdgeCases:
    """Test return types and edge cases."""

    def test_returns_tuple(self, extractor: ObservableExtractor) -> None:
        result = extractor.extract("drag", "cylinder")
        assert isinstance(result, tuple)
        assert len(result) == 2
        obs, amb = result
        assert isinstance(obs, list)
        assert isinstance(amb, list)

    def test_all_observables_are_observable_spec(
        self, extractor: ObservableExtractor
    ) -> None:
        obs, _amb = extractor.extract("阻力 升力 压力 涡", "cylinder")
        assert all(isinstance(o, ObservableSpec) for o in obs)

    def test_empty_text(self, extractor: ObservableExtractor) -> None:
        obs, amb = extractor.extract("", "cylinder")
        assert obs == []
        assert amb == []

    def test_no_observables_in_plain_description(
        self, extractor: ObservableExtractor
    ) -> None:
        obs, _amb = extractor.extract("Re=3900 三维湍流", "cylinder")
        assert obs == []

    def test_observables_have_display_names(
        self, extractor: ObservableExtractor
    ) -> None:
        obs, _amb = extractor.extract("drag lift", "cylinder")
        for o in obs:
            assert o.display_name != ""
            assert o.observable_id != ""
            assert o.category != ""
