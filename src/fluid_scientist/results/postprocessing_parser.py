"""OpenFOAM functionObject 输出解析器。"""

from __future__ import annotations

from pathlib import Path

from fluid_scientist.results.simulation_data import (
    ForceCoefficientsData,
    SurfaceFieldValueData,
)


class PostProcessingParser:
    """解析 OpenFOAM functionObject 输出文件。"""

    def parse_force_coeffs(self, file_path: str | Path) -> ForceCoefficientsData:
        """解析 forceCoeffs 输出文件。

        文件格式通常为：
        # Time  Cd  Cl  Cm
        0.1  1.23  0.45  0.12
        """
        content = Path(file_path).read_text(encoding="utf-8")
        lines = [
            line.strip()
            for line in content.split("\n")
            if line.strip() and not line.startswith("#")
        ]

        times: list[float] = []
        cds: list[float] = []
        cls: list[float] = []
        cms: list[float] = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 4:
                try:
                    times.append(float(parts[0]))
                    cds.append(float(parts[1]))
                    cls.append(float(parts[2]))
                    cms.append(float(parts[3]))
                except ValueError:
                    continue

        return ForceCoefficientsData(time=times, cd=cds, cl=cls, cm=cms)

    def parse_surface_field_value(
        self,
        file_path: str | Path,
        name: str = "",
    ) -> SurfaceFieldValueData:
        """解析 surfaceFieldValue 输出文件。"""
        content = Path(file_path).read_text(encoding="utf-8")
        lines = [
            line.strip()
            for line in content.split("\n")
            if line.strip() and not line.startswith("#")
        ]

        times: list[float] = []
        values: list[float] = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    times.append(float(parts[0]))
                    values.append(float(parts[1]))
                except ValueError:
                    continue

        return SurfaceFieldValueData(
            name=name or Path(file_path).stem,
            time=times,
            values=values,
        )


__all__ = ["PostProcessingParser"]
