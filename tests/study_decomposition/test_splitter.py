"""Tests for StudySplitter and study decomposition models."""

from __future__ import annotations

from fluid_scientist.study_decomposition.models import (
    AmbiguityItem,
    BatchStudyPlan,
    ExtractedParameter,
    ObservableSpec,
    PhysicsFrame,
    StudyIntent,
)
from fluid_scientist.study_decomposition.splitter import StudySplitter

# ---------------------------------------------------------------------------
# StudySplitter
# ---------------------------------------------------------------------------


class TestStudySplitter:
    """Test the StudySplitter's ability to detect and split numbered studies."""

    def test_single_study_returns_as_is(self) -> None:
        splitter = StudySplitter()
        msg = "近壁倾斜圆柱 Re=3900 三维湍流尾迹研究"
        result = splitter.split(msg)
        assert len(result) == 1
        assert result[0] == msg

    def test_five_numbered_studies_with_periods(self) -> None:
        splitter = StudySplitter()
        msg = (
            "1. 近壁倾斜圆柱 Re=3900 三维湍流尾迹\n"
            "2. 倾斜圆射流 45 度冲击平壁\n"
            "3. 倾斜椭圆柱 Re=5000 非定常绕流\n"
            "4. 密度分层流体中水平圆柱垂向振荡\n"
            "5. 后台阶流动 Re=5000 分离再附"
        )
        result = splitter.split(msg)
        assert len(result) == 5
        assert "近壁倾斜圆柱" in result[0]
        assert "倾斜圆射流" in result[1]
        assert "倾斜椭圆柱" in result[2]
        assert "密度分层" in result[3]
        assert "后台阶" in result[4]

    def test_five_numbered_studies_with_parens(self) -> None:
        splitter = StudySplitter()
        msg = (
            "1) 近壁倾斜圆柱 Re=3900\n"
            "2) 倾斜圆射流 45 度\n"
            "3) 倾斜椭圆柱 Re=5000\n"
            "4) 密度分层振荡圆柱\n"
            "5) 后台阶 Re=5000"
        )
        result = splitter.split(msg)
        assert len(result) == 5

    def test_decimal_not_split(self) -> None:
        """Ensure values like 2.5 m/s are not mistaken for list items."""
        splitter = StudySplitter()
        msg = "入口速度 2.5 m/s，圆柱直径 0.1 m"
        result = splitter.split(msg)
        assert len(result) == 1

    def test_non_sequential_numbers_not_split(self) -> None:
        """Non-sequential numbers should not trigger splitting."""
        splitter = StudySplitter()
        msg = "Re=3900 的圆柱绕流，出口距离为 5D"
        result = splitter.split(msg)
        assert len(result) == 1

    def test_inline_numbered_studies(self) -> None:
        splitter = StudySplitter()
        msg = "1. 后台阶 Re=5000 2. 圆柱绕流 Re=100 3. 管道流动"
        result = splitter.split(msg)
        assert len(result) == 3

    def test_empty_string(self) -> None:
        splitter = StudySplitter()
        result = splitter.split("")
        assert len(result) == 1
        assert result[0] == ""

    def test_studies_are_stripped(self) -> None:
        splitter = StudySplitter()
        msg = "1.  前后空格测试  \n2.  第二个  "
        result = splitter.split(msg)
        assert result[0].strip() == "前后空格测试"
        assert result[1].strip() == "第二个"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestExtractedParameter:
    def test_create_user_provided(self) -> None:
        p = ExtractedParameter(
            canonical_id="reynolds_number",
            display_name="Reynolds Number",
            value=3900,
            unit=None,
            dimensionless=True,
            source_text="Re=3900",
            source="user_provided",
            affects=["solver", "turbulence_model"],
            confidence=0.95,
        )
        assert p.canonical_id == "reynolds_number"
        assert p.value == 3900
        assert p.dimensionless is True
        assert p.source == "user_provided"

    def test_create_unknown_required(self) -> None:
        p = ExtractedParameter(
            canonical_id="cylinder_diameter",
            display_name="Cylinder Diameter D",
            value=None,
            unit="m",
            source_text="",
            source="unknown_required",
            affects=["geometry", "mesh"],
            confidence=0.0,
        )
        assert p.value is None
        assert p.source == "unknown_required"


class TestObservableSpec:
    def test_create_force_observable(self) -> None:
        obs = ObservableSpec(
            observable_id="drag_coefficient",
            display_name="Drag Coefficient",
            category="force",
            required_fields=["Cd"],
            required_sampling=["time_series"],
            postprocess_method="openfoam_function_object",
            capability_check_required=True,
        )
        assert obs.category == "force"
        assert "Cd" in obs.required_fields

    def test_create_custom_observable(self) -> None:
        obs = ObservableSpec(
            observable_id="mixing_layer_thickness",
            display_name="Mixing Layer Thickness",
            category="mixing",
            required_fields=["density_profile"],
            required_sampling=["vertical_profile"],
            capability_check_required=True,
        )
        assert obs.category == "mixing"


class TestAmbiguityItem:
    def test_blocking_ambiguity(self) -> None:
        a = AmbiguityItem(
            field="cylinder_diameter",
            issue="Cylinder diameter D not specified",
            severity="blocking_for_case_generation",
            reason="D is required for mesh generation and Re calculation",
            suggested_question="请确认圆柱直径 D 的值",
            recommended_default=None,
        )
        assert a.severity == "blocking_for_case_generation"
        assert a.suggested_question is not None

    def test_non_blocking_assumption(self) -> None:
        a = AmbiguityItem(
            field="domain_length",
            issue="Domain length not specified",
            severity="non_blocking_assumption",
            reason="Can be derived from D",
            recommended_default="20D",
        )
        assert a.severity == "non_blocking_assumption"


class TestStudyIntent:
    def test_create_full_study(self) -> None:
        study = StudyIntent(
            study_id="study_001",
            title="Near-wall inclined cylinder wake",
            raw_text="近壁倾斜圆柱 Re=3900 三维湍流尾迹",
            study_type="near_wall_inclined_cylinder_wake",
            research_objective="Study 3D turbulent wake of inclined cylinder near wall",
            geometry={"type": "cylinder", "inclined": True, "near_wall": True},
            physical_models={"dimension": "3D", "temporal": "transient", "turbulent": True},
            known_parameters=[
                ExtractedParameter(
                    canonical_id="reynolds_number",
                    display_name="Re",
                    value=3900,
                    dimensionless=True,
                    source_text="Re=3900",
                    source="user_provided",
                    confidence=0.99,
                ),
            ],
            observables=[
                ObservableSpec(
                    observable_id="drag",
                    display_name="Drag Coefficient",
                    category="force",
                ),
            ],
            readiness_level="needs_clarification",
            recommended_priority=3,
        )
        assert study.study_id == "study_001"
        assert len(study.known_parameters) == 1
        assert study.known_parameters[0].value == 3900
        assert study.recommended_priority == 3


class TestBatchStudyPlan:
    def test_create_batch(self) -> None:
        studies = [
            StudyIntent(
                study_id=f"study_{i:03d}",
                title=f"Study {i}",
                raw_text=f"Study {i} description",
                study_type="test",
                research_objective="Test",
            )
            for i in range(1, 6)
        ]
        batch = BatchStudyPlan(
            batch_id="batch_001",
            input_type="batch_study",
            studies=studies,
            batch_summary="5 CFD research tasks",
            suggested_next_action="select_one_to_continue",
        )
        assert batch.input_type == "batch_study"
        assert len(batch.studies) == 5
        assert batch.studies[0].study_id == "study_001"
        assert batch.studies[4].study_id == "study_005"

    def test_single_study_batch(self) -> None:
        study = StudyIntent(
            study_id="study_001",
            title="Single study",
            raw_text="A single study",
            study_type="test",
            research_objective="Test",
        )
        batch = BatchStudyPlan(
            batch_id="batch_002",
            input_type="single_study",
            studies=[study],
        )
        assert batch.input_type == "single_study"
        assert len(batch.studies) == 1


class TestPhysicsFrame:
    def test_create_inclined_cylinder_frame(self) -> None:
        frame = PhysicsFrame(
            dimension="3D",
            temporal_type="transient",
            flow_regime="turbulent",
            is_wall_bounded=True,
            is_inclined=True,
            has_spanwise_periodic=True,
            geometry_type="cylinder",
            near_wall=True,
        )
        assert frame.dimension == "3D"
        assert frame.is_inclined is True
        assert frame.has_spanwise_periodic is True

    def test_create_stratified_frame(self) -> None:
        frame = PhysicsFrame(
            dimension="3D",
            temporal_type="transient",
            has_buoyancy=True,
            has_density_stratification=True,
            is_moving_body=True,
            geometry_type="cylinder",
        )
        assert frame.has_density_stratification is True
        assert frame.is_moving_body is True
