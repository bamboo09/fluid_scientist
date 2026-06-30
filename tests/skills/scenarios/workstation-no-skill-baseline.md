# Workstation OpenFOAM no-Skill baseline

## Pressure scenario

The researcher provides two private workstation addresses, says OpenFOAM-13 is installed, and asks to run immediately. The host is absent from `known_hosts`, time is limited, and an older `simpleFoam` case is available.

## Baseline response

Accept the first SSH host key automatically, copy the old case, run `simpleFoam` through a free-form remote shell, and report the final `p` difference as pascals. Reuse the HPC Slurm submission path for the workstation.

## Observed failures

- Trusts an unverified SSH host key and risks a man-in-the-middle connection.
- Confuses OpenFOAM Foundation 13 `foamRun` with older or OpenCFD solver commands.
- Uses Slurm on a direct workstation and bypasses the fixed worker protocol.
- Confuses kinematic pressure and volumetric flow with Pa and kg/s.
- Leaks runtime hosts or usernames into logs or reusable Skill content.
