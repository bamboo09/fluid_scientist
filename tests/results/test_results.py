"""Result Ingestor 模块测试。"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from fluid_scientist.results.ingestor import OpenFOAMResultIngestor
from fluid_scientist.results.log_parser import OpenFOAMLogParser
from fluid_scientist.results.metric_pipeline import execute_metric_pipeline
from fluid_scientist.results.postprocessing_parser import PostProcessingParser
from fluid_scientist.results.simulation_data import (
    ForceCoefficientsData,
    ResidualData,
    SimulationData,
)

# 测试日志样本
SAMPLE_LOG = """\
Time = 1
Courant Number mean: 0.123 max: 0.456
smoothSolver: Solving for Ux, Initial residual = 0.123, Final residual = 0.001
smoothSolver: Solving for Uy, Initial residual = 0.098, Final residual = 0.001
GAMG: Solving for p, Initial residual = 0.456, Final residual = 0.01
continuity errors : sum local = 1.23e-05
Time = 2
Courant Number mean: 0.089 max: 0.234
smoothSolver: Solving for Ux, Initial residual = 0.045, Final residual = 0.0005
"""


@pytest.fixture
def tmp_dir() -> Iterator[Path]:
    """提供可写临时目录（直接使用系统临时目录，规避 pytest basetemp 权限问题）。"""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestOpenFOAMLogParser:
    def test_log_parser_extracts_residuals(self):
        """解析日志提取残差。"""
        parser = OpenFOAMLogParser()
        data = parser.parse_log(SAMPLE_LOG)

        assert data.residuals.ux == [0.123, 0.045]
        assert data.residuals.uy == [0.098]
        assert data.residuals.p == [0.456]
        # 时间步应包含两个时刻
        assert data.time_steps == [1.0, 2.0]
        assert data.residuals.time == [1.0, 2.0]

    def test_log_parser_extracts_courant(self):
        """解析日志提取 Courant 数。"""
        parser = OpenFOAMLogParser()
        data = parser.parse_log(SAMPLE_LOG)

        assert data.max_courant == [0.456, 0.234]

    def test_log_parser_extracts_continuity(self):
        """解析日志提取连续性误差。"""
        parser = OpenFOAMLogParser()
        data = parser.parse_log(SAMPLE_LOG)

        assert data.continuity_errors == [1.23e-05]

    def test_empty_log_returns_empty_data(self):
        """空日志返回空数据。"""
        parser = OpenFOAMLogParser()
        data = parser.parse_log("")

        assert isinstance(data, SimulationData)
        assert data.residuals.ux == []
        assert data.residuals.p == []
        assert data.max_courant == []
        assert data.continuity_errors == []
        assert data.time_steps == []

    def test_malformed_log_does_not_crash(self):
        """畸形日志不崩溃，返回空数据。"""
        parser = OpenFOAMLogParser()
        malformed = "this is not a log\n%%%garbage%%%\nTime = abc\n"
        data = parser.parse_log(malformed)

        # 不应崩溃，且数据为空（Time = abc 无法解析为浮点数会被跳过）
        assert isinstance(data, SimulationData)
        assert data.residuals.ux == []


class TestPostProcessingParser:
    def test_force_coeffs_parser(self, tmp_dir: Path):
        """解析 forceCoeffs 输出。"""
        parser = PostProcessingParser()
        fc_file = tmp_dir / "coefficient.dat"
        fc_file.write_text(
            "# Time  Cd  Cl  Cm\n"
            "0.1  1.23  0.45  0.12\n"
            "0.2  1.25  0.46  0.13\n",
            encoding="utf-8",
        )

        result = parser.parse_force_coeffs(fc_file)

        assert isinstance(result, ForceCoefficientsData)
        assert result.time == [0.1, 0.2]
        assert result.cd == [1.23, 1.25]
        assert result.cl == [0.45, 0.46]
        assert result.cm == [0.12, 0.13]

    def test_force_coeffs_parser_skips_invalid_lines(self, tmp_dir: Path):
        """forceCoeffs 解析应跳过无效行。"""
        parser = PostProcessingParser()
        fc_file = tmp_dir / "coefficient.dat"
        fc_file.write_text(
            "# Time  Cd  Cl  Cm\n"
            "0.1  1.23  0.45  0.12\n"
            "not a number line\n"
            "0.2  1.25  0.46  0.13\n",
            encoding="utf-8",
        )

        result = parser.parse_force_coeffs(fc_file)
        assert result.cd == [1.23, 1.25]

    def test_surface_field_value_parser(self, tmp_dir: Path):
        """解析 surfaceFieldValue 输出。"""
        parser = PostProcessingParser()
        sv_file = tmp_dir / "surfaceFieldValue.dat"
        sv_file.write_text(
            "# Time  value\n"
            "0.1  100.5\n"
            "0.2  101.0\n",
            encoding="utf-8",
        )

        result = parser.parse_surface_field_value(sv_file, name="outlet_pressure")

        assert result.name == "outlet_pressure"
        assert result.time == [0.1, 0.2]
        assert result.values == [100.5, 101.0]

    def test_surface_field_value_default_name(self, tmp_dir: Path):
        """未提供 name 时使用文件名（不含扩展名）。"""
        parser = PostProcessingParser()
        sv_file = tmp_dir / "outlet.dat"
        sv_file.write_text("# Time  value\n0.1  50.0\n", encoding="utf-8")

        result = parser.parse_surface_field_value(sv_file)
        assert result.name == "outlet"


class TestOpenFOAMResultIngestor:
    def test_ingestor_combines_log_and_post_processing(self, tmp_dir: Path):
        """Ingestor 组合日志和后处理数据。"""
        # 构造后处理目录结构
        pp_dir = tmp_dir / "postProcessing"
        fc_dir = pp_dir / "forceCoeffs" / "0"
        fc_dir.mkdir(parents=True)
        (fc_dir / "coefficient.dat").write_text(
            "# Time  Cd  Cl  Cm\n"
            "0.1  1.23  0.45  0.12\n"
            "0.2  1.25  0.46  0.13\n",
            encoding="utf-8",
        )

        sv_dir = pp_dir / "surfaceFieldValue" / "0"
        sv_dir.mkdir(parents=True)
        (sv_dir / "surfaceFieldValue.dat").write_text(
            "# Time  value\n"
            "0.1  100.5\n"
            "0.2  101.0\n",
            encoding="utf-8",
        )

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(
            log_text=SAMPLE_LOG,
            post_processing_dir=pp_dir,
        )

        # 日志数据
        assert data.residuals.ux == [0.123, 0.045]
        assert data.max_courant == [0.456, 0.234]

        # 后处理数据 — 力系数
        assert data.forces is not None
        assert data.forces.cd == [1.23, 1.25]
        assert data.forces.cl == [0.45, 0.46]

        # 后处理数据 — 面场值
        assert len(data.surface_values) == 1
        assert data.surface_values[0].values == [100.5, 101.0]

    def test_ingestor_log_path(self, tmp_dir: Path):
        """Ingestor 通过 log_path 读取日志文件。"""
        log_file = tmp_dir / "solver.log"
        log_file.write_text(SAMPLE_LOG, encoding="utf-8")

        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest(log_path=log_file)

        assert data.residuals.ux == [0.123, 0.045]

    def test_ingestor_empty_inputs(self):
        """Ingestor 无输入时返回空数据。"""
        ingestor = OpenFOAMResultIngestor()
        data = ingestor.ingest()

        assert isinstance(data, SimulationData)
        assert data.residuals.ux == []
        assert data.forces is None


class TestMetricPipeline:
    def test_metric_pipeline_produces_report(self):
        """Metric pipeline 生成报告。"""
        data = SimulationData(
            residuals=ResidualData(ux=[1e-6], uy=[1e-6], p=[1e-7]),
            forces=ForceCoefficientsData(
                time=[0.1, 0.2],
                cd=[1.23, 1.25],
                cl=[0.45, 0.46],
                cm=[0.12, 0.13],
            ),
            max_courant=[0.456, 0.234],
        )

        result = execute_metric_pipeline(data)

        assert "overall_status" in result
        assert "summary" in result
        assert "metric_results" in result
        assert "quality_checks" in result
        # 残差 1e-6 低于阈值 1e-4，质量检查应通过
        assert result["overall_status"] == "passed"
        # 应提取到指标结果
        assert len(result["metric_results"]) > 0
        # 应执行了质量检查
        assert len(result["quality_checks"]) > 0

    def test_metric_pipeline_extracts_force_metrics(self):
        """Metric pipeline 应将力系数映射到对应 metric_id。"""
        data = SimulationData(
            residuals=ResidualData(ux=[1e-6], p=[1e-7]),
            forces=ForceCoefficientsData(
                time=[0.1],
                cd=[1.2],
                cl=[0.1],
                cm=[0.05],
            ),
        )

        result = execute_metric_pipeline(data, experiment_type="cylinder_flow")

        # 阻力系数与升力系数应被提取
        metric_by_id = {m["metric_id"]: m for m in result["metric_results"]}
        assert metric_by_id["drag_coefficient"]["value"] == 1.2
        assert metric_by_id["lift_coefficient"]["value"] == 0.1

    def test_metric_pipeline_empty_data(self):
        """空仿真数据仍能生成报告（无指标值）。"""
        data = SimulationData()

        result = execute_metric_pipeline(data)

        assert "overall_status" in result
        assert len(result["metric_results"]) > 0
        # 残差为空 → max_residual=0.0 ≤ 1e-4 → 通过；质量不平衡 0% → 通过
        assert result["overall_status"] == "passed"

    def test_metric_pipeline_unknown_experiment_type(self):
        """未知实验类型应返回错误信息而不崩溃。"""
        data = SimulationData()

        result = execute_metric_pipeline(data, experiment_type="nonexistent_type")

        assert "error" in result
