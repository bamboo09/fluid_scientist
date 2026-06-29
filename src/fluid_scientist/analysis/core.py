"""Statistical summaries computed outside the language model."""

from statistics import mean, stdev

from fluid_scientist.domain.models import AnalysisResult


def summarize_metric(
    *,
    project_id: str,
    metric_name: str,
    samples: list[float],
    artifact_ids: tuple[str, ...] = (),
) -> AnalysisResult:
    if not samples:
        raise ValueError("at least one sample is required")
    metrics = {
        f"{metric_name}_mean": mean(samples),
        f"{metric_name}_std": stdev(samples) if len(samples) > 1 else 0.0,
        f"{metric_name}_minimum": min(samples),
        f"{metric_name}_maximum": max(samples),
    }
    observations = (
        f"{metric_name} spans {min(samples):.6g} to {max(samples):.6g} "
        f"across {len(samples)} samples.",
    )
    return AnalysisResult(
        project_id=project_id,
        sample_count=len(samples),
        metrics=metrics,
        observations=observations,
        artifact_ids=artifact_ids,
    )

