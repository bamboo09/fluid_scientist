"""Command-line entry point installed on OpenFOAM workstations."""

import argparse
import json
import os
from pathlib import Path

from pydantic import ValidationError

from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.worker.service import WorkerJobService, system_doctor


def main(
    argv: list[str] | None = None,
    *,
    service: WorkerJobService | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="fluid-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--json", action="store_true", dest="as_json")

    submit = subparsers.add_parser("submit")
    submit.add_argument("--job-id", required=True)
    submit.add_argument("--diameter", required=True, type=float)
    submit.add_argument("--length", required=True, type=float)
    submit.add_argument("--velocity", required=True, type=float)
    submit.add_argument("--nu", required=True, type=float)
    submit.add_argument("--density", type=float, default=998.2)
    submit.add_argument("--axial-cells", type=int, default=80)
    submit.add_argument("--radial-cells", type=int, default=10)
    submit.add_argument("--json", action="store_true", dest="as_json")

    submit_custom = subparsers.add_parser("submit-custom")
    submit_custom.add_argument("--job-id", required=True)
    submit_custom.add_argument("--archive", required=True)
    submit_custom.add_argument("--json", action="store_true", dest="as_json")

    for command in ("status", "cancel", "collect"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("job_id")
        command_parser.add_argument("--json", action="store_true", dest="as_json")

    private_run = subparsers.add_parser("_run", help=argparse.SUPPRESS)
    private_run.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)

    root = Path(os.environ.get("FLUID_WORKER_ROOT", "~/.local/share/fluid-scientist")).expanduser()

    if args.command == "doctor":
        root.mkdir(parents=True, exist_ok=True)
        try:
            report = system_doctor(root)
        except RuntimeError as error:
            if args.as_json:
                print(json.dumps({"error": str(error)}))
            else:
                print(f"not ready: {error}")
            return 2
        print(report.model_dump_json() if args.as_json else str(report))
        return 0

    jobs = service or WorkerJobService(root)
    try:
        if args.command == "submit":
            spec = LaminarPipeCase(
                diameter_m=args.diameter,
                length_m=args.length,
                mean_velocity_m_s=args.velocity,
                kinematic_viscosity_m2_s=args.nu,
                density_kg_m3=args.density,
                axial_cells=args.axial_cells,
                radial_cells=args.radial_cells,
            )
            output = jobs.submit(args.job_id, spec)
        elif args.command == "submit-custom":
            output = jobs.submit_custom(args.job_id, args.archive)
        elif args.command == "status":
            output = jobs.status(args.job_id)
        elif args.command == "cancel":
            output = jobs.cancel(args.job_id)
        elif args.command == "collect":
            collected = jobs.collect(args.job_id)
            print(json.dumps(collected) if args.as_json else str(collected))
            return 0
        elif args.command == "_run":
            output = jobs.execute(args.job_id)
            return 0 if output.state.value == "succeeded" else 1
        else:
            return 2
    except (KeyError, RuntimeError, ValueError, ValidationError) as error:
        if getattr(args, "as_json", False):
            print(json.dumps({"error": str(error)}))
        else:
            print(f"error: {error}")
        return 2

    print(output.model_dump_json() if args.as_json else str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
