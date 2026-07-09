"""Tests for CapabilityPreChecker and PriorityRanker."""

from __future__ import annotations

from fluid_scientist.study_decomposition.capability_checker import (
    CapabilityPreChecker,
    PriorityRanker,
)
from fluid_scientist.study_decomposition.models import (
    ObservableSpec,
    StudyIntent,
)


def _make_study(
    study_id: str = "test_001",
    title: str = "Test",
    geo_type: str = "cylinder",
    **kwargs,
) -> StudyIntent:
    defaults = dict(
        study_id=study_id,
        title=title,
        raw_text="test",
        study_type="test",
        research_objective="test",
        geometry={"type": geo_type},
    )
    defaults.update(kwargs)
    return StudyIntent(**defaults)


class TestCapabilityPreChecker:
    def test_backward_facing_step_draftable(self) -> None:
        study = _make_study(
            study_id="bfs",
            title="Backward facing step",
            geo_type="backward_facing_step",
            physical_models={"turbulent": True},
        )
        checker = CapabilityPreChecker()
        result = checker.check(study)
        assert result.readiness_level == "draftable"
        assert result.can_compile is True
        assert len(result.blocking_reasons) == 0

    def test_stratified_cylinder_not_compilable(self) -> None:
        study = _make_study(
            study_id="strat",
            title="Stratified oscillating cylinder",
            geo_type="cylinder",
            physical_models={
                "density_stratification": True,
                "moving_body": True,
                "buoyancy": True,
            },
        )
        checker = CapabilityPreChecker()
        result = checker.check(study)
        assert result.readiness_level == "not_compilable_yet"
        assert result.can_compile is False
        assert len(result.blocking_reasons) > 0

    def test_missing_buoyancy_for_stratified(self) -> None:
        study = _make_study(
            physical_models={"density_stratification": True},
        )
        checker = CapabilityPreChecker()
        result = checker.check(study)
        cap_ids = [m["capability_id"] for m in result.missing_capabilities]
        assert "buoyancy_model_writer" in cap_ids

    def test_missing_dynamic_mesh_for_moving_body(self) -> None:
        study = _make_study(
            physical_models={"moving_body": True},
        )
        checker = CapabilityPreChecker()
        result = checker.check(study)
        cap_ids = [m["capability_id"] for m in result.missing_capabilities]
        assert "dynamic_mesh_writer" in cap_ids

    def test_missing_internal_wave_postprocess(self) -> None:
        study = _make_study(
            observables=[
                ObservableSpec(
                    observable_id="internal_wave",
                    display_name="Internal Wave",
                    category="internal_wave",
                )
            ],
        )
        checker = CapabilityPreChecker()
        result = checker.check(study)
        cap_ids = [m["capability_id"] for m in result.missing_capabilities]
        assert "internal_wave_postprocess" in cap_ids

    def test_inclined_cylinder_warning_not_blocking(self) -> None:
        study = _make_study(
            geo_type="cylinder",
            physical_models={"inclined": True},
        )
        checker = CapabilityPreChecker()
        result = checker.check(study)
        assert result.readiness_level == "needs_clarification"
        assert result.can_compile is False

    def test_can_compile_false_when_blocking_missing(self) -> None:
        study = _make_study(
            physical_models={"thermal": True},
        )
        checker = CapabilityPreChecker()
        result = checker.check(study)
        assert result.can_compile is False
        cap_ids = [m["capability_id"] for m in result.missing_capabilities]
        assert "energy_equation_solver" in cap_ids


class TestPriorityRanker:
    def test_backward_step_ranked_first(self) -> None:
        studies = [
            _make_study(
                study_id="strat",
                title="Stratified",
                geo_type="cylinder",
                physical_models={"density_stratification": True, "moving_body": True},
            ),
            _make_study(
                study_id="bfs",
                title="Backward facing step",
                geo_type="backward_facing_step",
                physical_models={"turbulent": True},
            ),
        ]
        checker = CapabilityPreChecker()
        results = {s.study_id: checker.check(s) for s in studies}
        ranker = PriorityRanker()
        ranked = ranker.rank(studies, results)
        assert ranked[0].study_id == "bfs"
        assert ranked[0].recommended_priority == 1

    def test_stratified_ranked_last(self) -> None:
        studies = [
            _make_study(
                study_id="bfs",
                title="Backward facing step",
                geo_type="backward_facing_step",
                physical_models={"turbulent": True},
            ),
            _make_study(
                study_id="strat",
                title="Stratified",
                geo_type="cylinder",
                physical_models={"density_stratification": True, "moving_body": True},
            ),
        ]
        checker = CapabilityPreChecker()
        results = {s.study_id: checker.check(s) for s in studies}
        ranker = PriorityRanker()
        ranked = ranker.rank(studies, results)
        assert ranked[-1].study_id == "strat"
        assert ranked[-1].recommended_priority == 2

    def test_five_canonical_studies_ordered(self) -> None:
        """Test the 5 canonical CFD studies are ordered correctly."""
        studies = [
            _make_study(
                study_id="c1_inclined_cyl",
                title="Near-wall inclined cylinder",
                geo_type="cylinder",
                physical_models={"inclined": True, "turbulent": True},
            ),
            _make_study(
                study_id="c2_jet",
                title="Inclined impinging jet",
                geo_type="jet",
                physical_models={"inclined": True, "thermal": True},
            ),
            _make_study(
                study_id="c3_elliptic",
                title="Inclined elliptic cylinder",
                geo_type="elliptic",
                physical_models={"inclined": True},
            ),
            _make_study(
                study_id="c4_stratified",
                title="Stratified oscillating cylinder",
                geo_type="cylinder",
                physical_models={
                    "density_stratification": True,
                    "moving_body": True,
                },
            ),
            _make_study(
                study_id="c5_bfs",
                title="Backward facing step",
                geo_type="backward_facing_step",
                physical_models={"turbulent": True},
            ),
        ]
        checker = CapabilityPreChecker()
        results = {s.study_id: checker.check(s) for s in studies}
        ranker = PriorityRanker()
        ranked = ranker.rank(studies, results)

        # BFS should be priority 1
        assert ranked[0].study_id == "c5_bfs"
        assert ranked[0].recommended_priority == 1

        # Stratified should be last
        assert ranked[-1].study_id == "c4_stratified"
        assert ranked[-1].recommended_priority == 5

        # All priorities are unique 1..5
        priorities = {s.recommended_priority for s in ranked}
        assert priorities == {1, 2, 3, 4, 5}

    def test_priority_reason_set(self) -> None:
        study = _make_study(
            geo_type="backward_facing_step",
            physical_models={"turbulent": True},
        )
        checker = CapabilityPreChecker()
        result = checker.check(study)
        ranker = PriorityRanker()
        ranked = ranker.rank([study], {study.study_id: result})
        assert ranked[0].priority_reason != ""
