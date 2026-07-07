"""指标执行管道 — 从仿真数据到指标报告。"""

from __future__ import annotations

from typing import Any

from fluid_scientist.results.simulation_data import SimulationData


def _to_metric_sim_data(simulation_data: SimulationData) -> Any:
    """将 results.SimulationData 转换为 metric_spec.analysis.SimulationData。

    metric_spec 中的 SimulationData 是严格模型（标量字典），因此需要从
    时间序列中提取代表性的标量值：
      - 残差：取每个变量最终时刻的残差值
      - 力系数：取最终时刻的值，并映射到 metric_id 命名（cd→drag_coefficient 等）
      - 最大 Courant 数：取整个仿真过程中的最大值
    """
    from fluid_scientist.metric_spec.analysis import (
        SimulationData as MetricSimData,
    )

    # 残差：取每个变量的最终残差值（列表最后一个）
    residuals: dict[str, float] = {}
    res = simulation_data.residuals
    for key, values in (
        ("Ux", res.ux),
        ("Uy", res.uy),
        ("Uz", res.uz),
        ("p", res.p),
    ):
        if values:
            residuals[key] = float(values[-1])

    # 力系数：取最终值，并映射到 metric_spec 中使用的 metric_id
    forces: dict[str, float] = {}
    if simulation_data.forces is not None:
        fc = simulation_data.forces
        for src_key, dst_key in (
            ("cd", "drag_coefficient"),
            ("cl", "lift_coefficient"),
            ("cm", "moment_coefficient"),
        ):
            values = getattr(fc, src_key)
            if values:
                forces[dst_key] = float(values[-1])

    # 最大 Courant 数：取整个仿真过程中的最大值
    max_courant: float | None = None
    if simulation_data.max_courant:
        max_courant = float(max(simulation_data.max_courant))

    return MetricSimData(
        residuals=residuals,
        forces=forces,
        fluxes={},
        max_courant=max_courant,
        probes={},
        gci_value=None,
        custom={},
    )


def execute_metric_pipeline(
    simulation_data: SimulationData,
    metric_spec: Any | None = None,
    experiment_type: str = "cylinder_flow",
) -> dict[str, Any]:
    """执行指标计算管道。

    将 SimulationData 传入 Metric Engine，生成指标报告。
    """
    from fluid_scientist.metric_spec.analysis import analyze_simulation

    # 获取 metric spec
    if metric_spec is None:
        from fluid_scientist.metric_spec.registry import get_metric_spec

        try:
            metric_spec = get_metric_spec(experiment_type)
        except Exception:
            return {"error": "no metric spec available", "results": []}

    # 将 results.SimulationData 转换为 metric_spec 分析模块所需的格式
    metric_sim_data = _to_metric_sim_data(simulation_data)

    # 执行分析
    report = analyze_simulation(metric_sim_data, metric_spec)

    # quality_check_outcomes 是 dataclass（非 pydantic 模型），需手动序列化
    return {
        "overall_status": report.overall_status.value,
        "summary": report.summary,
        "metric_results": [
            r.model_dump(mode="json") for r in report.metric_results
        ],
        "quality_checks": [
            {
                "check_type": q.check_type.value,
                "status": q.status.value,
                "value": q.value,
                "threshold": q.threshold,
                "message": q.message,
            }
            for q in report.quality_check_outcomes
        ],
    }


__all__ = ["execute_metric_pipeline"]
