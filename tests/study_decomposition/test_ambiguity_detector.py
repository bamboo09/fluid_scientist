"""Tests for AmbiguityDetector."""

from __future__ import annotations

from fluid_scientist.study_decomposition.ambiguity_detector import AmbiguityDetector
from fluid_scientist.study_decomposition.models import (
    ExtractedParameter,
    ObservableSpec,
    StudyIntent,
)


def _make_study(**kwargs) -> StudyIntent:
    defaults = dict(
        study_id="test_001",
        title="Test study",
        raw_text="test",
        study_type="test",
        research_objective="test",
    )
    defaults.update(kwargs)
    return StudyIntent(**defaults)


class TestAmbiguityDetectorBlocking:
    def test_no_geometry_dimensions_blocking(self) -> None:
        study = _make_study(geometry={"type": "cylinder"})
        detector = AmbiguityDetector()
        items = detector.detect(study)
        blocking = [i for i in items if i.severity == "blocking_for_case_generation"]
        assert any(i.field == "characteristic_length" for i in blocking)

    def test_heat_flux_ambiguity_detected(self) -> None:
        study = _make_study(
            geometry={"type": "jet"},
            physical_models={"thermal": True},
            observables=[
                ObservableSpec(
                    observable_id="heat_flux",
                    display_name="Heat Flux",
                    category="heat_flux",
                )
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        heat_items = [i for i in items if i.field == "heat_flux_role"]
        assert len(heat_items) == 1
        assert heat_items[0].severity == "blocking_for_case_generation"

    def test_moving_body_without_oscillation_params(self) -> None:
        study = _make_study(
            geometry={"type": "cylinder"},
            physical_models={"moving_body": True},
            known_parameters=[
                ExtractedParameter(
                    canonical_id="cylinder_diameter",
                    display_name="D",
                    value=0.1,
                    unit="m",
                    source_text="D=0.1",
                    source="user_provided",
                ),
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        osc_items = [i for i in items if i.field == "oscillation_parameters"]
        assert len(osc_items) == 1
        assert osc_items[0].severity == "blocking_for_case_generation"

    def test_density_stratification_formula_unknown(self) -> None:
        study = _make_study(
            geometry={"type": "cylinder"},
            physical_models={"density_stratification": True},
            known_parameters=[
                ExtractedParameter(
                    canonical_id="cylinder_diameter",
                    display_name="D",
                    value=0.1,
                    unit="m",
                    source_text="D=0.1",
                    source="user_provided",
                ),
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        strat_items = [i for i in items if i.field == "density_stratification_formula"]
        assert len(strat_items) == 1
        assert strat_items[0].severity == "blocking_for_case_generation"

    def test_fr_definition_unknown(self) -> None:
        study = _make_study(
            geometry={"type": "cylinder"},
            known_parameters=[
                ExtractedParameter(
                    canonical_id="cylinder_diameter",
                    display_name="D",
                    value=0.1,
                    unit="m",
                    source_text="D=0.1",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="froude_number",
                    display_name="Fr",
                    value=0.2,
                    dimensionless=True,
                    source_text="Fr=0.2",
                    source="user_provided",
                ),
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        fr_items = [i for i in items if i.field == "froude_number_definition"]
        assert len(fr_items) == 1
        assert fr_items[0].severity == "blocking_for_case_generation"


class TestAmbiguityDetectorNeedsConfirmation:
    def test_re_without_characteristic_length(self) -> None:
        study = _make_study(
            geometry={"type": "cylinder"},
            known_parameters=[
                ExtractedParameter(
                    canonical_id="reynolds_number",
                    display_name="Re",
                    value=3900,
                    dimensionless=True,
                    source_text="Re=3900",
                    source="user_provided",
                ),
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        re_items = [i for i in items if i.field == "reynolds_characteristic_length"]
        assert len(re_items) == 1
        assert re_items[0].severity == "needs_confirmation"

    def test_turbulence_model_not_specified(self) -> None:
        study = _make_study(
            geometry={"type": "cylinder"},
            physical_models={"turbulent": True},
            known_parameters=[
                ExtractedParameter(
                    canonical_id="cylinder_diameter",
                    display_name="D",
                    value=0.1,
                    unit="m",
                    source_text="D=0.1",
                    source="user_provided",
                ),
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        turb_items = [i for i in items if i.field == "turbulence_model"]
        assert len(turb_items) == 1
        assert turb_items[0].severity == "needs_confirmation"

    def test_domain_size_not_specified(self) -> None:
        study = _make_study(
            geometry={"type": "cylinder"},
            known_parameters=[
                ExtractedParameter(
                    canonical_id="cylinder_diameter",
                    display_name="D",
                    value=0.1,
                    unit="m",
                    source_text="D=0.1",
                    source="user_provided",
                ),
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        domain_items = [i for i in items if i.field == "domain_size"]
        assert len(domain_items) == 1
        assert domain_items[0].severity == "needs_confirmation"

    def test_fully_developed_inlet_ambiguous(self) -> None:
        study = _make_study(
            geometry={"type": "pipe"},
            initial_conditions=[{"type": "fully_developed"}],
            known_parameters=[
                ExtractedParameter(
                    canonical_id="pipe_diameter",
                    display_name="D",
                    value=0.1,
                    unit="m",
                    source_text="D=0.1",
                    source="user_provided",
                ),
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        inlet_items = [i for i in items if i.field == "inlet_implementation"]
        assert len(inlet_items) == 1


class TestAmbiguityDetectorNonBlocking:
    def test_fully_specified_study_mostly_non_blocking(self) -> None:
        study = _make_study(
            geometry={"type": "backward_facing_step"},
            physical_models={"turbulent": True},
            known_parameters=[
                ExtractedParameter(
                    canonical_id="step_height",
                    display_name="H",
                    value=0.05,
                    unit="m",
                    source_text="H=0.05",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="reynolds_number",
                    display_name="Re",
                    value=5000,
                    dimensionless=True,
                    source_text="Re=5000",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="turbulence_model",
                    display_name="Turbulence Model",
                    value="LES",
                    source_text="LES",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="domain_length",
                    display_name="Domain Length",
                    value=1.0,
                    unit="m",
                    source_text="",
                    source="user_provided",
                ),
                ExtractedParameter(
                    canonical_id="domain_width",
                    display_name="Domain Width",
                    value=0.5,
                    unit="m",
                    source_text="",
                    source="user_provided",
                ),
            ],
        )
        detector = AmbiguityDetector()
        items = detector.detect(study)
        blocking = [i for i in items if i.severity == "blocking_for_case_generation"]
        assert len(blocking) == 0
        non_blocking = [i for i in items if i.severity == "non_blocking_assumption"]
        assert len(non_blocking) >= 3
