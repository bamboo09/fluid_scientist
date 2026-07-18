"""Model capability evaluation and admission thresholds.

Before a model may serve as the :attr:`~fluid_scientist.model_runtime.models.ModelRole.PRIMARY_REASONER`
it must pass a battery of capability evaluations.  This module defines:

* :class:`CapabilityEvalResult` - the measured capability scores for a
  candidate model.
* :class:`ModelAdmissionThresholds` - the class-level pass/fail
  thresholds every primary reasoner must meet.
* :func:`evaluate_model` - a function (also exposed as
  :meth:`ModelAdmissionThresholds.evaluate`) that checks a result
  against the thresholds.

A model that fails any threshold is *not* admitted; the runtime never
silently degrades to a weaker model or a template fallback.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = ["CapabilityEvalResult", "ModelAdmissionThresholds", "evaluate_model"]


class CapabilityEvalResult(BaseModel):
    """Measured capability scores for a candidate model.

    Each field is a rate/accuracy in ``[0.0, 1.0]`` (except the two
    ``*_rate`` failure metrics where lower is better).  Together they
    characterise whether a model is trustworthy enough to drive the
    primary reasoning loop of the CFD agent.
    """

    model_config = ConfigDict(extra="forbid")

    model_id: str
    eval_version: str = "cap-eval-v1"
    structured_output_parse_rate: float
    single_field_edit_accuracy: float
    consecutive_8turn_retention: float
    geometry_type_accuracy: float
    unit_accuracy: float
    conflict_recall: float
    unknown_capability_recall: float
    template_misuse_rate: float
    fabricated_success_rate: float
    pass_fail: Literal["pass", "fail"] = "fail"


class ModelAdmissionThresholds:
    """Class-level pass/fail thresholds for primary reasoner admission.

    Metrics that are *accuracy/recall* must be ``>=`` the threshold;
    metrics that are *error rates* must be ``<=`` (or ``==`` for
    :attr:`fabricated_success_rate`) the threshold.
    """

    # accuracy / recall metrics (>= required)
    structured_output_parse_rate: float = 0.98
    single_field_edit_accuracy: float = 0.95
    consecutive_8turn_retention: float = 0.90
    geometry_type_accuracy: float = 0.95
    unit_accuracy: float = 0.98
    conflict_recall: float = 0.90
    unknown_capability_recall: float = 0.95

    # error-rate metrics (<= required)
    template_misuse_rate: float = 0.02
    fabricated_success_rate: float = 0.0  # must be exactly zero

    @classmethod
    def evaluate(cls, results: CapabilityEvalResult) -> bool:
        """Return ``True`` iff *results* meets every admission threshold."""
        return evaluate_model(results)


def evaluate_model(results: CapabilityEvalResult) -> bool:
    """Check *results* against :class:`ModelAdmissionThresholds`.

    Returns ``True`` only when every accuracy/recall metric is at or
    above its threshold and every error-rate metric is at or below its
    threshold (with :attr:`fabricated_success_rate` required to be
    exactly ``0.0``).
    """
    t = ModelAdmissionThresholds
    return (
        results.structured_output_parse_rate >= t.structured_output_parse_rate
        and results.single_field_edit_accuracy >= t.single_field_edit_accuracy
        and results.consecutive_8turn_retention >= t.consecutive_8turn_retention
        and results.geometry_type_accuracy >= t.geometry_type_accuracy
        and results.unit_accuracy >= t.unit_accuracy
        and results.conflict_recall >= t.conflict_recall
        and results.unknown_capability_recall >= t.unknown_capability_recall
        and results.template_misuse_rate <= t.template_misuse_rate
        and results.fabricated_success_rate == t.fabricated_success_rate
    )
