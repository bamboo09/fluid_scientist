"""Scientific analysis — layered output from metric results.

The analysis output is structured in layers:
1. Direct calculation facts
2. Numerical credibility assessment
3. Comparison with benchmarks or reference
4. Physical interpretation
5. Unvalidated hypotheses
6. Next experiment recommendations

The LLM only interprets existing evidence — it never calculates replacement values.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from fluid_scientist.results.models import MetricResult, SimulationData


class AnalysisLayer(BaseModel):
    """A single layer of scientific analysis output."""
    layer_name: str
    content: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: str = "high"  # high, medium, low


class ScientificAnalysis(BaseModel):
    """Complete scientific analysis with layered output."""
    # Layer 1: Direct facts
    direct_facts: list[AnalysisLayer] = Field(default_factory=list)
    # Layer 2: Numerical credibility
    numerical_credibility: list[AnalysisLayer] = Field(default_factory=list)
    # Layer 3: Comparison
    comparisons: list[AnalysisLayer] = Field(default_factory=list)
    # Layer 4: Physical interpretation
    physical_interpretation: list[AnalysisLayer] = Field(default_factory=list)
    # Layer 5: Unvalidated hypotheses
    hypotheses: list[AnalysisLayer] = Field(default_factory=list)
    # Layer 6: Next experiment recommendations
    recommendations: list[AnalysisLayer] = Field(default_factory=list)

    # Summary
    overall_confidence: str = "high"
    key_findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ScientificAnalyzer:
    """Generates layered scientific analysis from metric results.

    LLM is only used for interpretation, never for calculation.
    """

    def analyze(
        self,
        metric_results: list[MetricResult],
        simulation_data: SimulationData,
        experiment_spec: Any | None = None,
        benchmark_values: dict[str, float] | None = None,
    ) -> ScientificAnalysis:
        """Generate scientific analysis from metric results.

        Args:
            metric_results: List of calculated MetricResults.
            simulation_data: The simulation data that was used.
            experiment_spec: Optional ExperimentSpec for context.
            benchmark_values: Optional benchmark values for comparison.

        Returns:
            ScientificAnalysis with all 6 layers.
        """
        benchmark_values = benchmark_values or {}
        analysis = ScientificAnalysis()

        # Layer 1: Direct calculation facts
        for result in metric_results:
            if result.value is not None and not result.data_missing:
                fact = AnalysisLayer(
                    layer_name="direct_calculation",
                    content=f"{result.metric_id} = {result.value} {result.unit}".strip(),
                    evidence_ids=[f"metric:{result.metric_id}"],
                    confidence=result.confidence,
                )
                analysis.direct_facts.append(fact)

        # Layer 2: Numerical credibility
        credibility_issues = []
        for result in metric_results:
            for check in result.quality_checks:
                if not check.get("passed", True):
                    credibility_issues.append(
                        f"{result.metric_id}: {check.get('name', 'unknown')} — {check.get('message', '')}"
                    )

        if simulation_data.max_courant and simulation_data.max_courant > 1.0:
            credibility_issues.append(f"Max Courant number {simulation_data.max_courant:.4f} exceeds 1.0")

        if simulation_data.final_continuity_error and abs(simulation_data.final_continuity_error) > 1e-4:
            credibility_issues.append(f"Continuity error {simulation_data.final_continuity_error:.2e} is high")

        if credibility_issues:
            analysis.numerical_credibility.append(AnalysisLayer(
                layer_name="credibility_issues",
                content="; ".join(credibility_issues),
                confidence="medium",
            ))
        else:
            analysis.numerical_credibility.append(AnalysisLayer(
                layer_name="credibility_check",
                content="All quality checks passed",
                confidence="high",
            ))

        # Layer 3: Comparison with benchmarks
        for metric_id, benchmark in benchmark_values.items():
            result = next((r for r in metric_results if r.metric_id == metric_id), None)
            if result and result.value is not None:
                relative_error = abs(result.value - benchmark) / max(abs(benchmark), 1e-10)
                comparison = AnalysisLayer(
                    layer_name="benchmark_comparison",
                    content=f"{metric_id}: calculated={result.value}, benchmark={benchmark}, "
                           f"relative_error={relative_error*100:.2f}%",
                    confidence="high" if relative_error < 0.05 else "medium",
                )
                analysis.comparisons.append(comparison)

        # Layer 4: Physical interpretation (based on metric values)
        for result in metric_results:
            if result.value is None or result.data_missing:
                continue
            interpretation = self._interpret_metric(result)
            if interpretation:
                analysis.physical_interpretation.append(AnalysisLayer(
                    layer_name="interpretation",
                    content=interpretation,
                    evidence_ids=[f"metric:{result.metric_id}"],
                    confidence=result.confidence,
                ))

        # Layer 5: Unvalidated hypotheses
        for result in metric_results:
            if result.confidence == "low":
                analysis.hypotheses.append(AnalysisLayer(
                    layer_name="hypothesis",
                    content=f"{result.metric_id} result ({result.value}) has low confidence — "
                           f"requires further validation",
                    confidence="low",
                ))

        # Layer 6: Next experiment recommendations
        if any(r.data_missing for r in metric_results):
            missing = [r.metric_id for r in metric_results if r.data_missing]
            analysis.recommendations.append(AnalysisLayer(
                layer_name="missing_data",
                content=f"重新编译和运行以获取缺失数据: {', '.join(missing)}",
                confidence="high",
            ))

        if any(r.confidence == "low" for r in metric_results):
            analysis.recommendations.append(AnalysisLayer(
                layer_name="refine_study",
                content="部分指标置信度低，建议增加采样时间或细化网格",
                confidence="high",
            ))

        if simulation_data.max_courant and simulation_data.max_courant > 1.0:
            analysis.recommendations.append(AnalysisLayer(
                layer_name="reduce_timestep",
                content="Courant数超限，建议减小时间步长后重新运行",
                confidence="high",
            ))

        # Overall confidence
        if all(r.confidence == "high" for r in metric_results if not r.data_missing):
            analysis.overall_confidence = "high"
        elif any(r.confidence == "low" for r in metric_results):
            analysis.overall_confidence = "low"
        else:
            analysis.overall_confidence = "medium"

        # Key findings
        for result in metric_results:
            if result.value is not None and not result.data_missing:
                analysis.key_findings.append(
                    f"{result.metric_id} = {result.value} {result.unit}".strip()
                )

        # Limitations
        for result in metric_results:
            if result.data_missing:
                analysis.limitations.append(f"{result.metric_id}: 数据缺失")
            for w in result.warnings:
                analysis.limitations.append(f"{result.metric_id}: {w}")

        return analysis

    def _interpret_metric(self, result: MetricResult) -> str | None:
        """Generate physical interpretation for a metric result."""
        if result.metric_id == "pressure_drop" and result.value is not None:
            return f"压降 {result.value:.2f} Pa 反映了流动阻力损失"
        elif result.metric_id == "drag_coefficient" and result.value is not None:
            return f"阻力系数 Cd = {result.value:.4f}"
        elif result.metric_id == "strouhal_number" and result.value is not None:
            st = result.value
            if 0.15 < st < 0.25:
                return f"Strouhal数 {st:.4f} 在典型涡脱范围 (0.15-0.25) 内"
            else:
                return f"Strouhal数 {st:.4f} 偏离典型范围"
        elif result.metric_id == "reynolds_number" and result.value is not None:
            re = result.value
            if re < 2300:
                return f"Reynolds数 {re:.0f}，流动为层流"
            elif re < 4000:
                return f"Reynolds数 {re:.0f}，流动为过渡区"
            else:
                return f"Reynolds数 {re:.0f}，流动为湍流"
        elif result.metric_id == "velocity_uniformity" and result.value is not None:
            cv = result.value
            if cv < 0.1:
                return f"速度均匀性 CV={cv:.4f}，分布均匀"
            elif cv < 0.3:
                return f"速度均匀性 CV={cv:.4f}，分布中等"
            else:
                return f"速度均匀性 CV={cv:.4f}，分布不均匀"
        return None


__all__ = ["AnalysisLayer", "ScientificAnalysis", "ScientificAnalyzer"]
