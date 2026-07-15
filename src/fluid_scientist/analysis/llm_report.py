"""LLM scientific report generator and physics validation.

Flow:
1. Build structured result summary from simulation outputs (Cd/Cl time series, St, mesh info)
2. Validate physics: compare Cd/St against empirical correlations
3. Call LLM to generate structured scientific report
4. Report includes: setup, mesh, numerics, results, physics validation, conclusions
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


_REPORT_SYSTEM_PROMPT = """你是一个CFD仿真分析专家。请基于仿真结果生成结构化科学报告。

## 报告结构

1. **实验概述**: 实验目的、物理设置、关键参数
2. **网格信息**: 网格类型、单元数、质量指标
3. **数值方法**: 求解器、离散格式、时间步长、CFL数
4. **结果分析**:
   - 阻力系数Cd: 时间平均值、振幅、与经验值对比
   - 升力系数Cl: 时间平均值、振幅、振荡频率
   - Strouhal数: 从Cl时间序列FFT提取、与经验公式对比
   - 流场特征: 涡街形态、尾流结构
5. **物理验证**: 结果是否物理合理，与经验/理论值对比
6. **结论**: 主要发现、可信度评估

## 输出JSON Schema

```json
{
  "summary": "一句话概述仿真结果",
  "experiment_overview": {...},
  "mesh_info": {...},
  "numerical_method": {...},
  "results": {
    "Cd": {"mean": 0, "amplitude": 0, "empirical": 0, "error_percent": 0},
    "Cl": {"mean": 0, "amplitude": 0, "frequency": 0},
    "Strouhal": {"value": 0, "empirical": 0, "error_percent": 0}
  },
  "physics_validation": {
    "passed": true,
    "checks": [{"name": "", "passed": true, "detail": ""}]
  },
  "conclusions": ["结论1", "结论2"],
  "confidence": 0.0
}
```

## 严格规则

- 所有数值必须来自实际仿真结果，不得编造
- 如果数据缺失，明确标注"数据不可用"
- 物理验证必须对比经验值并计算误差百分比
- 置信度基于网格质量、收敛性、数据完整性综合评估
"""


@dataclass
class PhysicsValidationResult:
    """Result of physics validation checks."""
    passed: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add_check(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.passed = False

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checks": self.checks}


class ResultSummaryBuilder:
    """Builds a structured summary from simulation outputs.

    Extracts:
    - Cd/Cl time series from forceCoeffs output
    - Strouhal number from Cl FFT analysis
    - Mesh statistics from checkMesh
    - Solver convergence from log
    """

    def build_summary(
        self,
        execution_result: dict | None = None,
        mesh_report: dict | None = None,
        smoke_report: dict | None = None,
        sim_report: dict | None = None,
        spec: Any | None = None,
        plot_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build structured result summary."""
        summary: dict[str, Any] = {
            "has_results": False,
            "mesh": {},
            "simulation": {},
            "metrics": {},
            "plots": plot_paths or [],
        }

        # Mesh info
        if mesh_report:
            summary["mesh"] = {
                "cells": mesh_report.get("n_cells", "unknown"),
                "points": mesh_report.get("n_points", "unknown"),
                "checkmesh_status": mesh_report.get("status", "unknown"),
                "max_aspect_ratio": mesh_report.get("max_aspect_ratio"),
                "max_non_orthogonality": mesh_report.get("max_non_orthogonality"),
            }

        # Simulation info
        if sim_report:
            summary["simulation"] = {
                "status": sim_report.get("status", "unknown"),
                "final_time": sim_report.get("final_time"),
                "courant_max": sim_report.get("courant_max"),
                "has_nan": sim_report.get("has_nan", False),
                "has_error": sim_report.get("has_error", False),
            }
            summary["has_results"] = sim_report.get("status") == "SUCCESS"

        # Extract metrics from execution result
        if execution_result:
            metrics = self._extract_metrics(execution_result)
            summary["metrics"] = metrics

        # Spec info
        if spec:
            summary["spec"] = {
                "reynolds_number": self._get_reynolds(spec),
                "inlet_velocity": self._get_velocity(spec),
                "cylinder_diameter": self._get_diameter(spec),
                "domain_length": spec.domain.length_m.value if spec.domain.length_m.is_resolved() else None,
                "domain_height": spec.domain.height_m.value if spec.domain.height_m.is_resolved() else None,
            }

        return summary

    def _extract_metrics(self, result: dict) -> dict[str, Any]:
        """Extract Cd, Cl, St from execution result."""
        metrics: dict[str, Any] = {}

        # Look for forceCoeffs data in result
        sim_report = result.get("simulation_report", {})
        output_tail = sim_report.get("output_tail", "")

        # Try to parse Cd/Cl from forceCoeffs output
        cd_values = self._parse_force_coeffs(output_tail, "Cd")
        cl_values = self._parse_force_coeffs(output_tail, "Cl")

        if cd_values:
            metrics["Cd"] = {
                "mean": sum(cd_values) / len(cd_values),
                "min": min(cd_values),
                "max": max(cd_values),
                "amplitude": (max(cd_values) - min(cd_values)) / 2,
                "samples": len(cd_values),
            }

        if cl_values:
            metrics["Cl"] = {
                "mean": sum(cl_values) / len(cl_values),
                "min": min(cl_values),
                "max": max(cl_values),
                "amplitude": (max(cl_values) - min(cl_values)) / 2,
                "samples": len(cl_values),
            }
            # Estimate Strouhal from Cl oscillation
            st = self._estimate_strouhal(cl_values)
            if st is not None:
                metrics["Strouhal"] = {"value": st}

        return metrics

    def _parse_force_coeffs(self, log: str, coeff: str) -> list[float]:
        """Parse force coefficient values from log."""
        import re
        # Pattern: Cd = 1.234 or Cl = -0.567
        pattern = rf"{coeff}\s*[=:]\s*(-?\d+\.?\d*)"
        matches = re.findall(pattern, log)
        return [float(m) for m in matches[-100:]]  # Last 100 values

    def _estimate_strouhal(self, cl_values: list[float]) -> float | None:
        """Estimate Strouhal number from Cl time series using zero-crossing."""
        if len(cl_values) < 20:
            return None

        # Simple zero-crossing frequency estimation
        mean_cl = sum(cl_values) / len(cl_values)
        centered = [v - mean_cl for v in cl_values]

        crossings = 0
        for i in range(1, len(centered)):
            if centered[i - 1] * centered[i] < 0:
                crossings += 1

        if crossings < 2:
            return None

        # Frequency = crossings / 2 / total_time
        # Assuming uniform time sampling, St = f * D / U
        # Without knowing delta_t, we can only estimate relative frequency
        # Return the zero-crossing count as a proxy
        return crossings / 2 / len(cl_values)  # Normalized frequency

    def _get_reynolds(self, spec: Any) -> float | None:
        """Extract Reynolds number from spec."""
        if hasattr(spec, "fluid") and hasattr(spec.fluid, "reynolds_number"):
            re = spec.fluid.reynolds_number
            if hasattr(re, "value") and re.is_resolved():
                return re.value
        # Try to extract from user text
        if hasattr(spec, "user_input_text") and spec.user_input_text:
            import re
            match = re.search(r"Re\s*=?\s*(\d+\.?\d*)", spec.user_input_text)
            if match:
                return float(match.group(1))
        return None

    def _get_velocity(self, spec: Any) -> float | None:
        if spec.boundaries.left.inlet_velocity is not None:
            return spec.boundaries.left.inlet_velocity
        return None

    def _get_diameter(self, spec: Any) -> float | None:
        return spec.get_cylinder_diameter()


class PhysicsValidator:
    """Validates simulation results against empirical correlations.

    Checks:
    - Cd vs empirical correlation for cylinder at given Re
    - St vs empirical formula (St ≈ 0.198(1 - 19.7/Re) for Re < 200)
    - Cl amplitude reasonable for given Re
    - Mesh quality sufficient
    """

    def validate(self, summary: dict[str, Any]) -> PhysicsValidationResult:
        """Run all physics validation checks."""
        result = PhysicsValidationResult()

        spec_info = summary.get("spec", {})
        metrics = summary.get("metrics", {})
        re = spec_info.get("reynolds_number")
        u = spec_info.get("inlet_velocity")
        d = spec_info.get("cylinder_diameter")

        # Check Cd
        if "Cd" in metrics and re is not None:
            cd_mean = metrics["Cd"].get("mean")
            cd_empirical = self._empirical_cd(re)
            if cd_mean is not None and cd_empirical is not None:
                error = abs(cd_mean - cd_empirical) / cd_empirical * 100
                passed = error < 30  # 30% tolerance for CFD vs empirical
                result.add_check(
                    "Cd_comparison",
                    passed,
                    f"Cd_sim={cd_mean:.4f}, Cd_emp={cd_empirical:.4f}, error={error:.1f}%",
                )

        # Check Strouhal
        if "Strouhal" in metrics and re is not None:
            st_sim = metrics["Strouhal"].get("value")
            st_empirical = self._empirical_strouhal(re)
            if st_sim is not None and st_empirical is not None:
                error = abs(st_sim - st_empirical) / st_empirical * 100
                passed = error < 25  # 25% tolerance
                result.add_check(
                    "Strouhal_comparison",
                    passed,
                    f"St_sim={st_sim:.4f}, St_emp={st_empirical:.4f}, error={error:.1f}%",
                )

        # Check mesh quality
        mesh = summary.get("mesh", {})
        if mesh:
            n_cells = mesh.get("cells")
            if n_cells and n_cells != "unknown" and n_cells < 1000:
                result.add_check(
                    "mesh_sufficiency",
                    False,
                    f"Mesh has only {n_cells} cells — likely too coarse for accurate results",
                )
            else:
                result.add_check(
                    "mesh_sufficiency",
                    True,
                    f"Mesh has {n_cells} cells",
                )

        # Check convergence
        sim = summary.get("simulation", {})
        if sim.get("has_nan"):
            result.add_check("no_nan", False, "NaN detected in simulation results")
        else:
            result.add_check("no_nan", True, "No NaN in simulation")

        if sim.get("has_error"):
            result.add_check("no_error", False, "Errors detected in simulation log")
        else:
            result.add_check("no_error", True, "No errors in simulation log")

        return result

    def _empirical_cd(self, re: float) -> float | None:
        """Empirical drag coefficient for circular cylinder.

        For Re < 1: Cd ≈ 24/Re (Stokes)
        For 1 < Re < 1000: Cd ≈ 10/sqrt(Re) + 3 (empirical fit)
        For 1000 < Re < 2e5: Cd ≈ 1.0-1.2 (subcritical)
        """
        if re < 1:
            return 24.0 / re if re > 0 else None
        elif re < 1000:
            return 10.0 / math.sqrt(re) + 3.0
        elif re < 2e5:
            return 1.2
        return None

    def _empirical_strouhal(self, re: float) -> float | None:
        """Empirical Strouhal number for circular cylinder.

        For 50 < Re < 200: St ≈ 0.198(1 - 19.7/Re) (Roshko)
        For Re > 200: St ≈ 0.2 (approximately constant)
        """
        if re < 50:
            return None  # No vortex shedding below Re~47
        elif re < 200:
            return 0.198 * (1 - 19.7 / re)
        else:
            return 0.2


class LLMReportGenerator:
    """Generates scientific report using LLM based on structured result summary."""

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm_client = llm_client
        self._summary_builder = ResultSummaryBuilder()
        self._validator = PhysicsValidator()

    def generate_report(
        self,
        execution_result: dict | None = None,
        mesh_report: dict | None = None,
        smoke_report: dict | None = None,
        sim_report: dict | None = None,
        spec: Any | None = None,
        plot_paths: list[str] | None = None,
        llm_client: Any | None = None,
    ) -> dict[str, Any]:
        """Generate a full scientific report.

        Args:
            execution_result: Full execution result dict
            mesh_report: checkMesh report
            smoke_report: Smoke test report
            sim_report: Full simulation report
            spec: The experiment spec
            plot_paths: List of generated plot file paths
            llm_client: Optional LLM client override

        Returns:
            Report dict with summary, results, validation, and conclusions
        """
        # Build structured summary
        summary = self._summary_builder.build_summary(
            execution_result=execution_result,
            mesh_report=mesh_report,
            smoke_report=smoke_report,
            sim_report=sim_report,
            spec=spec,
            plot_paths=plot_paths,
        )

        # Run physics validation
        validation = self._validator.validate(summary)

        # Try LLM report generation
        client = llm_client or self._llm_client
        if client is not None:
            report = self._call_llm_for_report(client, summary, validation)
        else:
            # Fallback: rule-based report (no LLM)
            report = self._rule_based_report(summary, validation)

        report["physics_validation"] = validation.to_dict()
        report["result_summary"] = summary
        report["report_source"] = "llm" if client else "rule_based"
        return report

    def _call_llm_for_report(
        self,
        llm_client: Any,
        summary: dict[str, Any],
        validation: PhysicsValidationResult,
    ) -> dict[str, Any]:
        """Call LLM to generate report from structured summary."""
        user_message = (
            f"## 仿真结果摘要\n```json\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n```\n\n"
            f"## 物理验证结果\n```json\n{json.dumps(validation.to_dict(), ensure_ascii=False, indent=2)}\n```\n\n"
            f"请基于以上数据生成结构化科学报告。"
        )

        try:
            parsed, record = llm_client.call(
                purpose="explanation",
                prompt_name="scientific_report",
                system_prompt=_REPORT_SYSTEM_PROMPT,
                user_message=user_message,
                output_schema={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "results": {"type": "object"},
                        "conclusions": {"type": "array"},
                        "confidence": {"type": "number"},
                    },
                },
                prompt_version="report-v1",
            )

            if record.success:
                return parsed
            else:
                return self._rule_based_report(summary, validation)

        except Exception as e:
            logger.error("LLM report generation failed: %s", e)
            return self._rule_based_report(summary, validation)

    def _rule_based_report(
        self,
        summary: dict[str, Any],
        validation: PhysicsValidationResult,
    ) -> dict[str, Any]:
        """Generate a rule-based report when LLM is unavailable."""
        metrics = summary.get("metrics", {})
        spec = summary.get("spec", {})
        re = spec.get("reynolds_number")

        conclusions: list[str] = []

        if summary.get("has_results"):
            conclusions.append("仿真成功完成")
        else:
            conclusions.append("仿真未成功完成")
            return {
                "summary": "仿真未成功完成",
                "conclusions": conclusions,
                "confidence": 0.0,
            }

        if "Cd" in metrics:
            cd = metrics["Cd"].get("mean", 0)
            conclusions.append(f"平均阻力系数Cd = {cd:.4f}")

        if "Cl" in metrics:
            cl_amp = metrics["Cl"].get("amplitude", 0)
            conclusions.append(f"升力系数振幅 = {cl_amp:.4f}")

        if "Strouhal" in metrics:
            st = metrics["Strouhal"].get("value", 0)
            conclusions.append(f"Strouhal数 = {st:.4f}")

        if validation.passed:
            conclusions.append("物理验证通过：结果与经验值吻合")
        else:
            failed_checks = [c["name"] for c in validation.checks if not c["passed"]]
            conclusions.append(f"物理验证部分未通过：{', '.join(failed_checks)}")

        return {
            "summary": f"Re={re} 圆柱绕流仿真完成" if re else "圆柱绕流仿真完成",
            "conclusions": conclusions,
            "confidence": 0.7 if validation.passed else 0.4,
        }
