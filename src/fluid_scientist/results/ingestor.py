"""OpenFOAM 结果摄入器 — 从仿真输出提取 SimulationData。"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fluid_scientist.results.log_parser import OpenFOAMLogParser
from fluid_scientist.results.postprocessing_parser import PostProcessingParser
from fluid_scientist.results.simulation_data import SimulationData


class OpenFOAMResultIngestor:
    """从 OpenFOAM 运行结果中摄入数据。"""

    def __init__(self) -> None:
        self._log_parser = OpenFOAMLogParser()
        self._post_parser = PostProcessingParser()

    def ingest(
        self,
        log_text: str | None = None,
        log_path: str | Path | None = None,
        post_processing_dir: str | Path | None = None,
    ) -> SimulationData:
        """从 OpenFOAM 输出摄入仿真数据。

        Args:
            log_text: 求解器日志文本（与 log_path 二选一）
            log_path: 求解器日志文件路径
            post_processing_dir: 后处理目录路径（包含 forceCoeffs、surfaceFieldValue 等）

        Returns:
            SimulationData 包含解析得到的所有数据
        """
        # 1. 解析日志
        if log_text is None and log_path is not None:
            log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")

        data = SimulationData()
        if log_text:
            data = self._log_parser.parse_log(log_text)

        # 2. 解析后处理文件
        if post_processing_dir is not None:
            pp_dir = Path(post_processing_dir)
            self._parse_post_processing(pp_dir, data)

        return data

    def _parse_post_processing(self, pp_dir: Path, data: SimulationData) -> None:
        """解析后处理目录。"""
        # forceCoeffs
        for fc_dir in pp_dir.rglob("forceCoeffs*"):
            if fc_dir.is_dir():
                # 找最新的时间步文件
                files = sorted(fc_dir.glob("*/coefficient.dat"), reverse=True)
                if not files:
                    files = sorted(fc_dir.glob("*.dat"), reverse=True)
                if files:
                    with contextlib.suppress(Exception):
                        data.forces = self._post_parser.parse_force_coeffs(files[0])

        # surfaceFieldValue
        for sv_dir in pp_dir.rglob("surfaceFieldValue*"):
            if sv_dir.is_dir():
                files = sorted(sv_dir.glob("*/surfaceFieldValue.dat"), reverse=True)
                if not files:
                    files = sorted(sv_dir.glob("*.dat"), reverse=True)
                if files:
                    with contextlib.suppress(Exception):
                        sv_data = self._post_parser.parse_surface_field_value(
                            files[0],
                            name=sv_dir.name,
                        )
                        data.surface_values.append(sv_data)


__all__ = ["OpenFOAMResultIngestor"]
