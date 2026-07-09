"""Tests for StudyTypeClassifier."""

from __future__ import annotations

import pytest

from fluid_scientist.study_decomposition.study_type_classifier import StudyTypeClassifier


@pytest.fixture
def classifier() -> StudyTypeClassifier:
    return StudyTypeClassifier()


class TestStudyTypeClassification:
    """Test classification for all supported study types."""

    def test_cylinder_chinese(self, classifier: StudyTypeClassifier) -> None:
        study_type, confidence, evidence = classifier.classify("圆柱绕流 Re=3900")
        assert study_type == "cylinder"
        assert confidence > 0.5
        assert evidence["matched_keyword"] is not None
        assert evidence["source"] == "keyword_match"

    def test_cylinder_english(self, classifier: StudyTypeClassifier) -> None:
        study_type, confidence, evidence = classifier.classify("flow over a cylinder")
        assert study_type == "cylinder"
        assert confidence > 0.5
        assert evidence["matched_keyword"] == "cylinder"

    def test_elliptic_cylinder_specificity(
        self, classifier: StudyTypeClassifier
    ) -> None:
        # "椭圆柱" (elliptic cylinder) must NOT be classified as plain cylinder.
        study_type, confidence, _evidence = classifier.classify("椭圆柱绕流")
        assert study_type == "elliptic"
        assert confidence > 0.5

    def test_elliptic_english(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("elliptic cylinder flow")
        assert study_type == "elliptic"

    def test_jet_chinese(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("射流冲击平板")
        assert study_type == "jet"

    def test_jet_english(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, evidence = classifier.classify("turbulent jet impingement")
        assert study_type == "jet"
        assert evidence["matched_keyword"] == "jet"

    def test_backward_facing_step_chinese(
        self, classifier: StudyTypeClassifier
    ) -> None:
        study_type, _conf, _ev = classifier.classify("后台阶流动分离再附")
        assert study_type == "step"

    def test_backward_facing_step_english(
        self, classifier: StudyTypeClassifier
    ) -> None:
        study_type, _conf, _ev = classifier.classify("backward-facing step flow")
        assert study_type == "step"

    def test_step_generic_keyword(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("step flow with separation")
        assert study_type == "step"

    def test_pipe_flow_chinese(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("管道内湍流流动")
        assert study_type == "pipe"

    def test_pipe_flow_english(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("turbulent pipe flow Re=10000")
        assert study_type == "pipe"

    def test_tube_keyword(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("flow in a tube")
        assert study_type == "pipe"

    def test_duct_keyword(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("duct flow")
        assert study_type == "pipe"

    def test_cavity_chinese(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("方腔驱动流")
        assert study_type == "cavity"

    def test_cavity_english(self, classifier: StudyTypeClassifier) -> None:
        study_type, _conf, _ev = classifier.classify("lid-driven cavity flow")
        assert study_type == "cavity"

    def test_empty_text_returns_unknown(
        self, classifier: StudyTypeClassifier
    ) -> None:
        study_type, confidence, evidence = classifier.classify("")
        assert study_type == "unknown"
        assert confidence == 0.0
        assert evidence["matched_keyword"] is None
        assert evidence["source"] == "no_match"

    def test_unrelated_text_returns_unknown(
        self, classifier: StudyTypeClassifier
    ) -> None:
        study_type, confidence, _ev = classifier.classify(
            "这是一段不包含任何几何关键词的文本"
        )
        assert study_type == "unknown"
        assert confidence == 0.0

    def test_case_insensitive_english(
        self, classifier: StudyTypeClassifier
    ) -> None:
        study_type, _conf, _ev = classifier.classify("CYLINDER wake")
        assert study_type == "cylinder"

    def test_returns_tuple_of_three(self, classifier: StudyTypeClassifier) -> None:
        result = classifier.classify("cylinder flow")
        assert isinstance(result, tuple)
        assert len(result) == 3
        study_type, confidence, evidence = result
        assert isinstance(study_type, str)
        assert isinstance(confidence, float)
        assert isinstance(evidence, dict)
