import io
import json
import tarfile
from pathlib import Path

from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.worker.service import (
    CommandResult,
    JobState,
    OpenFOAM13JobRunner,
    WorkerJobService,
    build_doctor_report,
    extract_surface_metrics,
    system_doctor,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []

    def run(self, argv, *, cwd, timeout):
        self.calls.append((argv, cwd, timeout))
        return CommandResult(returncode=0, stdout=f"completed {argv[0]}", stderr="")


def test_doctor_report_identifies_foundation_13_and_fixed_commands(tmp_path) -> None:
    commands = {
        name: f"/opt/openfoam13/platforms/linux64GccDPInt32Opt/bin/{name}"
        for name in ("blockMesh", "checkMesh", "foamRun", "postProcess")
    }

    report = build_doctor_report(
        command_paths=commands,
        foam_version_output="OpenFOAM-13\n",
        cpu_count=32,
        memory_gb=125.5,
        disk_free_gb=430.25,
    )

    payload = json.loads(report.model_dump_json())
    assert payload == {
        "protocol_version": 1,
        "foam_version": "OpenFOAM-13",
        "cpu_count": 32,
        "memory_gb": 125.5,
        "disk_free_gb": 430.25,
        "commands": ["blockMesh", "checkMesh", "foamRun", "postProcess"],
    }


def test_system_doctor_uses_foundation_version_environment(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("WM_PROJECT_VERSION", "13")
    monkeypatch.setattr(
        "fluid_scientist.worker.service.shutil.which",
        lambda command: f"/opt/openfoam13/bin/{command}",
    )

    report = system_doctor(tmp_path)

    assert report.foam_version == "OpenFOAM-13"


def test_openfoam_job_runner_uses_only_fixed_argv(tmp_path) -> None:
    runner = RecordingRunner()
    job = OpenFOAM13JobRunner(runner=runner, command_timeout=600)

    result = job.run(tmp_path)

    assert [call[0] for call in runner.calls] == [
        ("blockMesh",),
        ("checkMesh", "-allGeometry", "-allTopology"),
        ("foamRun", "-solver", "incompressibleFluid"),
    ]
    assert all(call[1] == tmp_path for call in runner.calls)
    assert result.mesh_log == "completed checkMesh"
    assert result.solver_log == "completed foamRun"


def test_openfoam_job_runner_stops_after_failed_mesh_check(tmp_path) -> None:
    class FailedMeshRunner(RecordingRunner):
        def run(self, argv, *, cwd, timeout):
            result = super().run(argv, cwd=cwd, timeout=timeout)
            if argv[0] == "checkMesh":
                return CommandResult(1, "Failed 1 mesh checks", "")
            return result

    runner = FailedMeshRunner()

    try:
        OpenFOAM13JobRunner(runner=runner).run(tmp_path)
    except RuntimeError as error:
        assert "checkMesh" in str(error)
    else:
        raise AssertionError("mesh failure must stop the job")

    assert [call[0][0] for call in runner.calls] == ["blockMesh", "checkMesh"]


class FakeLauncher:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    def launch(self, job_id: str) -> int:
        self.job_ids.append(job_id)
        return 4321


def pipe_spec(velocity: float = 0.1) -> LaminarPipeCase:
    return LaminarPipeCase(
        diameter_m=0.02,
        length_m=2.0,
        mean_velocity_m_s=velocity,
        kinematic_viscosity_m2_s=1.0e-6,
        density_kg_m3=1000.0,
    )


def custom_archive() -> bytes:
    files = {
        "0/U": "internalField uniform (0 0 0);",
        "0/p": "internalField uniform 0;",
        "constant/physicalProperties": "nu 1e-6;",
        "system/controlDict": "solver incompressibleFluid; endTime 100;",
        "system/fvSchemes": "ddtSchemes {}",
        "system/fvSolution": "solvers {}",
        "system/blockMeshDict": "vertices ();",
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, text in files.items():
            payload = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def test_worker_submits_validated_custom_case_and_extracts_it_safely(tmp_path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "cylinder.tar.gz").write_bytes(custom_archive())
    launcher = FakeLauncher()
    service = WorkerJobService(tmp_path, launcher=launcher)

    job = service.submit_custom("cylinder-001", "cylinder.tar.gz")

    assert job.state == JobState.RUNNING
    assert job.spec.kind == "custom_openfoam"
    assert job.spec.needs_block_mesh is True
    assert (tmp_path / "jobs/cylinder-001/case/system/controlDict").is_file()
    assert launcher.job_ids == ["cylinder-001"]


def test_custom_runner_uses_fixed_commands_and_optional_block_mesh(tmp_path) -> None:
    runner = RecordingRunner()
    job = OpenFOAM13JobRunner(runner=runner)

    job.run(tmp_path, needs_block_mesh=False)

    assert [call[0] for call in runner.calls] == [
        ("checkMesh", "-allGeometry", "-allTopology"),
        ("foamRun", "-solver", "incompressibleFluid"),
    ]


def test_worker_submit_is_persistent_and_idempotent(tmp_path) -> None:
    launcher = FakeLauncher()
    service = WorkerJobService(tmp_path, launcher=launcher)

    first = service.submit("benchmark-001", pipe_spec())
    replay = WorkerJobService(tmp_path, launcher=launcher).submit("benchmark-001", pipe_spec())

    assert first.state == JobState.RUNNING
    assert first.pid == 4321
    assert replay == first
    assert launcher.job_ids == ["benchmark-001"]
    assert (tmp_path / "jobs/benchmark-001/case/system/controlDict").is_file()


def test_worker_rejects_same_job_id_with_different_parameters(tmp_path) -> None:
    service = WorkerJobService(tmp_path, launcher=FakeLauncher())
    service.submit("benchmark-001", pipe_spec())

    try:
        service.submit("benchmark-001", pipe_spec(velocity=0.11))
    except ValueError as error:
        assert "different parameters" in str(error)
    else:
        raise AssertionError("job id must be immutable")


def test_worker_execute_and_collect_persist_credibility_outputs(tmp_path) -> None:
    class CredibleRunner(RecordingRunner):
        def run(self, argv, *, cwd, timeout):
            self.calls.append((argv, cwd, timeout))
            if argv[0] == "blockMesh":
                (cwd / "constant/polyMesh").mkdir(parents=True)
                return CommandResult(0, "ok", "")
            if argv[0] == "checkMesh":
                return CommandResult(
                    0,
                    "cells: 8000\nMax aspect ratio = 2 OK.\n"
                    "Mesh non-orthogonality Max: 3 average: 0.5\n"
                    "Max skewness = 0.2 OK.\nMesh OK.\n",
                    "",
                )
            if argv[0] == "foamRun":
                (cwd / "2000").mkdir()
                for name, value in (
                    ("pressureDrop", "0.016"),
                    ("inletFlow", "-3.14159e-5"),
                    ("outletFlow", "3.14158e-5"),
                ):
                    output = cwd / "postProcessing" / name / "0"
                    output.mkdir(parents=True)
                    (output / "surfaceFieldValue.dat").write_text(
                        f"# Time value\n2000 {value}\n", encoding="utf-8"
                    )
                return CommandResult(
                    0,
                    "Solving for Ux, Initial residual = 0.1, Final residual = 1e-8\n"
                    "time step continuity errors : sum local = 1e-9, global = 2e-10, "
                    "cumulative = 3e-9\nEnd\n",
                    "",
                )
            return CommandResult(0, "ok", "")

    service = WorkerJobService(tmp_path, launcher=FakeLauncher())
    service.submit("benchmark-001", pipe_spec())

    completed = service.execute(
        "benchmark-001", runner=OpenFOAM13JobRunner(runner=CredibleRunner())
    )
    collected = service.collect("benchmark-001")

    assert completed.state == JobState.SUCCEEDED
    assert collected["mesh"]["cells"] == 8000
    assert collected["solver"]["completed"] is True
    assert collected["solver"]["pressure_drop_pa"] == 16.0
    assert collected["solver"]["inlet_mass_flow"] == 0.0314159
    assert collected["solver"]["outlet_mass_flow"] == -0.0314158
    assert collected["case_manifest"]["system/controlDict"]
    assert collected["post_processing"] == {
        "case_path": "jobs/benchmark-001/case",
        "paraview_file": "benchmark-001.foam",
        "time_directories": ["0", "2000"],
    }
    assert (tmp_path / "jobs/benchmark-001/case/benchmark-001.foam").is_file()


def test_surface_metrics_use_latest_time_and_convert_openfoam_units(tmp_path) -> None:
    for function_name, rows in (
        ("pressureDrop", "0 0.01\n2000 0.016\n"),
        ("inletFlow", "0 -3e-5\n2000 -3.14159e-5\n"),
        ("outletFlow", "0 3e-5\n2000 3.14158e-5\n"),
    ):
        output = tmp_path / "postProcessing" / function_name / "0"
        output.mkdir(parents=True)
        (output / "surfaceFieldValue.dat").write_text("# Time value\n" + rows, encoding="utf-8")

    metrics = extract_surface_metrics(tmp_path, density_kg_m3=1000.0)

    assert metrics.pressure_drop_pa == 16.0
    assert metrics.inlet_mass_flow == 0.0314159
    assert metrics.outlet_mass_flow == -0.0314158
