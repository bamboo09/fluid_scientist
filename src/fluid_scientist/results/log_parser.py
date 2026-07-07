"""OpenFOAM 求解器日志解析器。"""

from __future__ import annotations

import re

from fluid_scientist.results.simulation_data import (
    ResidualData,
    SimulationData,
)


class OpenFOAMLogParser:
    """解析 OpenFOAM 求解器日志。"""

    # 残差正则: "Time = 0.1\nsmoothSolver: Solving for Ux, Initial residual = 0.123, ..."
    RESIDUAL_PATTERN = re.compile(
        r"Solving for (\w+),.*?Initial residual = ([\d.eE+-]+)"
    )
    TIME_PATTERN = re.compile(r"Time = ([\d.eE+-]+)")
    COURANT_PATTERN = re.compile(
        r"Courant Number mean: ([\d.eE+-]+) max: ([\d.eE+-]+)"
    )
    CONTINUITY_PATTERN = re.compile(
        r"continuity errors : sum local = ([\d.eE+-]+)"
    )

    def parse_log(self, log_text: str) -> SimulationData:
        """解析完整的求解器日志文本。"""
        lines = log_text.split("\n")

        times: list[float] = []
        residuals_by_var: dict[str, list[float]] = {}  # {"Ux": [0.1, 0.05, ...]}
        max_courants: list[float] = []
        continuity_errors: list[float] = []

        for line in lines:
            # 时间步
            time_match = self.TIME_PATTERN.search(line)
            if time_match:
                current_time = float(time_match.group(1))
                times.append(current_time)

            # 残差
            res_match = self.RESIDUAL_PATTERN.search(line)
            if res_match:
                var_name = res_match.group(1)
                residual = float(res_match.group(2))
                residuals_by_var.setdefault(var_name, []).append(residual)

            # Courant 数
            cour_match = self.COURANT_PATTERN.search(line)
            if cour_match:
                max_courants.append(float(cour_match.group(2)))

            # 连续性误差
            cont_match = self.CONTINUITY_PATTERN.search(line)
            if cont_match:
                continuity_errors.append(float(cont_match.group(1)))

        # 构造 ResidualData
        residual_data = ResidualData(
            time=times,
            ux=residuals_by_var.get("Ux", []),
            uy=residuals_by_var.get("Uy", []),
            uz=residuals_by_var.get("Uz", []),
            p=residuals_by_var.get("p", []),
        )

        return SimulationData(
            residuals=residual_data,
            max_courant=max_courants,
            continuity_errors=continuity_errors,
            time_steps=times,
        )


__all__ = ["OpenFOAMLogParser"]
