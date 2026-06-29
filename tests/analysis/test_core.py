import pytest

from fluid_scientist.analysis.core import summarize_metric


def test_summarize_metric_computes_values_deterministically() -> None:
    result = summarize_metric(
        project_id="project-1",
        metric_name="pressure_drop_pa",
        samples=[100.0, 110.0, 112.5],
        artifact_ids=("case:coarse", "case:medium", "case:fine"),
    )

    assert result.sample_count == 3
    assert result.metrics["pressure_drop_pa_mean"] == pytest.approx(107.5)
    assert result.metrics["pressure_drop_pa_std"] == pytest.approx(6.6143782777)
    assert result.artifact_ids == ("case:coarse", "case:medium", "case:fine")


def test_summarize_metric_rejects_empty_samples() -> None:
    with pytest.raises(ValueError, match="at least one"):
        summarize_metric(
            project_id="project-1",
            metric_name="pressure_drop_pa",
            samples=[],
        )

