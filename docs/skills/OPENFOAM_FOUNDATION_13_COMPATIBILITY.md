# OpenFOAM Foundation 13 compatibility

Locked authority: OpenFOAM/OpenFOAM-13 commit `18870c24d21c6b982e2cdec27b2f59738cca5f90`. External projects are not authorities.

| Concept | External project/version | External wording | Foundation 13 result | Official source/tutorial evidence | Skill wording |
|---|---|---|---|---|---|
| solver invocation | Foam-Agent v10; suite v2412 | legacy executables/wrappers | `foamRun` loads module from controlDict `solver` or `-solver` | applications/solvers/foamRun/foamRun.C | blueprint selects module; no shell |
| incompressible solver | mixed | simpleFoam/pimpleFoam | verified module `incompressibleFluid` supports steady/transient PIMPLE-family control | applications/modules/incompressibleFluid; tutorials/incompressibleFluid/cylinder | `solver_module: incompressibleFluid` when physics matches |
| application entry | old tutorials | `application` | v13 release notes say application entry removed where possible; tutorial uses `solver incompressibleFluid` | tutorials/incompressibleFluid/cylinder/system/controlDict | do not mandate `application` |
| transport properties | v10/v2412 recipes | transportProperties | verified incompressible v13 tutorial uses `constant/physicalProperties` with `transportModel Newtonian`, `nu` | cylinder/constant/physicalProperties | plan physicalProperties |
| turbulence selection | older forks | turbulenceProperties | verified tutorial uses `constant/momentumTransport`, `simulationType`, RAS/LES subdicts | cylinder/constant/momentumTransport | model→field/wall dependency |
| pressure | mixed | p or p_rgh | cylinder incompressible uses kinematic `p`; other modules may use p_rgh and require module-specific verification | cylinder/0/p and forceCoeffsIncompressible description | explicit convention, conversion |
| PIMPLE controls | mixed | solver-specific | cylinder fvSolution uses PIMPLE and branches on ddt steadyState/transient | cylinder/system/fvSolution | dependency on time scheme |
| schemes | mixed | copied presets | cylinder demonstrates bounded steady advection and different transient form; not universal | cylinder/system/fvSchemes | evidence-backed plan, sensitivity |
| function objects | mixed libraries | forces/probes/etc. | cylinder includes `forcesIncompressible`, `forceCoeffsIncompressible`, residuals, streamlines; exact configs remain case-specific | cylinder/system/functions and forceCoeffsIncompressible | consumer dependencies |
| decomposition | mixed | scotch and rank scripts | v13 annotated dictionary verifies numberOfSubdomains and multiple methods | etc/caseDicts/annotated/decomposeParDict | blueprint only, no MPI command |
| postProcess | mixed | wrapper commands | command flags/function compatibility not exhaustively source-verified in this audit | UNVERIFIED_FOR_FOUNDATION_13 | no mandatory command |
| ParaView | suite recipes | paraFoam/pvbatch | v13 download page demonstrates paraFoam and v13 release notes decomposed-case improvements | openfoam.org/download/13-ubuntu; openfoam.org/release/13 | visualization plan only |
| snappy/Gmsh/import | external references | detailed commands/keys | not exhaustively verified here | UNVERIFIED_FOR_FOUNDATION_13 | recipe-level, block uncertain keys |
| convective/open BC variants | mixed | vendor names | not exhaustively verified | UNVERIFIED_FOR_FOUNDATION_13 | preserve physical intent and block keyword mandate |

Actual workstation execution was not performed because this task excludes workstation/runtime changes and no controlled worker session was placed in scope. Therefore compatibility is source/tutorial verified, not runtime-certified. Any future workstation result must be appended as evidence; it must not overwrite this distinction.

