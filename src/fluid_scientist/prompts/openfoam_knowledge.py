"""OpenFOAM v2406 knowledge base for LLM prompts.

This module provides a comprehensive knowledge base about OpenFOAM v2406
syntax, workstation security restrictions, and valid parameter values.
It is injected into LLM prompts to prevent common errors at the source.

Usage:
    from fluid_scientist.prompts.openfoam_knowledge import OPENFOAM_KNOWLEDGE
    system_prompt = base_prompt + "\\n\\n" + OPENFOAM_KNOWLEDGE
"""

OPENFOAM_KNOWLEDGE = r"""
=====================================================================
OPENFOAM v2406 KNOWLEDGE BASE — READ CAREFULLY BEFORE GENERATING PLANS
=====================================================================

This system targets OpenFOAM v2406 running on a secure workstation via
'foamRun -solver incompressibleFluid'. All plans MUST comply with the
restrictions below. Violations will cause runtime failures.

----------------------------------------------------------------------
1. SOLVER REQUIREMENT (CRITICAL)
----------------------------------------------------------------------
- The controlDict MUST contain: solver incompressibleFluid;
- Do NOT use 'application pimpleFoam;' or any other application name.
- The workstation runs cases via: foamRun -solver incompressibleFluid
- This is the OpenFOAM v2406 unified solver interface.
- Valid solver values: incompressibleFluid, incompressibleFluidDyM
- For the case plan 'solver' field, always use: incompressibleFluid

----------------------------------------------------------------------
2. SECURITY RESTRICTIONS (CRITICAL — violation = job failure)
----------------------------------------------------------------------
The workstation security policy FORBIDS the following:
- 'libs (...);' directive in any file (no dynamic library loading)
- '$variable' references in any file (no variable interpolation)
- '#include' or '#includeIfPresent' directives
- 'codeStream' or 'coded' anything (no dynamic code execution)
- 'system()' or shell script calls
- Allrun/Allclean scripts (removed automatically)
- Any file with executable permissions

All values must be LITERAL — no macros, no variables, no includes.

----------------------------------------------------------------------
3. FILE STRUCTURE
----------------------------------------------------------------------
Each OpenFOAM case has this directory structure:
  case/
  ├── 0/              # Initial/boundary conditions
  │   ├── U           # Velocity field [m/s]
  │   └── p           # Pressure field [m²/s²]
  ├── constant/
  │   ├── transportProperties    # Fluid properties
  │   └── turbulenceProperties   # Turbulence model selection
  └── system/
      ├── controlDict   # Run control (time, output, function objects)
      ├── blockMeshDict # Mesh definition
      ├── fvSchemes     # Discretization schemes
      └── fvSolution    # Linear solvers and algorithm control

----------------------------------------------------------------------
4. controlDict FORMAT
----------------------------------------------------------------------
Example (CORRECT):
```
solver incompressibleFluid;
startFrom  latestTime;
startTime  0;
stopAt     endTime;
endTime    100;
deltaT     0.01;
writeControl    timeStep;
writeInterval   10;
purgeWrite      3;
writeFormat     ascii;
writePrecision  6;
timeFormat      general;
runTimeModifiable true;

functions
{
    // function objects go here
}
```
Rules:
- First entry MUST be 'solver incompressibleFluid;'
- function object names in 'functions {}' MUST be valid OpenFOAM
  keywords: alphanumeric + underscore only, NO spaces.
  CORRECT: forces_drag, probes_velocity, fieldAverage_U
  WRONG:   forces drag, probes velocity, field average U
- Do NOT add 'libs (...);' anywhere.

----------------------------------------------------------------------
5. blockMeshDict FORMAT
----------------------------------------------------------------------
Example (CORRECT):
```
vertices
(
    (0 0 0)      // vertex 0
    (1 0 0)      // vertex 1
    (1 1 0)      // vertex 2
    (0 1 0)      // vertex 3
    (0 0 0.1)    // vertex 4
    (1 0 0.1)    // vertex 5
    (1 1 0.1)    // vertex 6
    (0 1 0.1)    // vertex 7
);

blocks
(
    hex (0 1 2 3 4 5 6 7) (20 20 1) simpleGrading (1 1 1)
);

boundary
(
    inlet
    {
        type patch;
        faces
        (
            (0 1 5 4)
        );
    }
    outlet
    {
        type patch;
        faces
        (
            (2 3 7 6)
        );
    }
    walls
    {
        type wall;
        faces
        (
            (1 2 6 5)
            (3 0 4 7)
        );
    }
    frontAndBack
    {
        type empty;   // For 2D cases
        faces
        (
            (0 3 2 1)
            (4 5 6 7)
        );
    }
);
```
Rules:
- vertices: each is (x y z) — exactly 3 coordinates, space-separated.
- blocks: hex (8 vertex indices) (nx ny nz) simpleGrading (sx sy sz)
- For 2D: use 1 cell in z-direction, frontAndBack as type empty.
- boundary faces: (v1 v2 v3 v4) — 4 vertex indices, counter-clockwise.
- Patch types: patch, wall, symmetry, symmetryPlane, empty, cyclic.

----------------------------------------------------------------------
6. 0/U (Velocity) FORMAT
----------------------------------------------------------------------
Example (CORRECT):
```
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);
boundaryField
{
    inlet
    {
        type            fixedValue;
        value           uniform (1 0 0);
    }
    outlet
    {
        type            zeroGradient;
    }
    wall
    {
        type            noSlip;
    }
    frontAndBack
    {
        type            empty;
    }
}
```
Rules:
- dimensions: [0 1 -1 0 0 0 0] (velocity)
- internalField: uniform (vx vy vz) — vector with 3 components
- Integer values: use '0' NOT '0.0'
- Valid BC types: fixedValue, zeroGradient, noSlip, slip, inletOutlet,
  pressureInletOutletVelocity, movingWallVelocity, empty, cyclic,
  symmetry, symmetryPlane

----------------------------------------------------------------------
7. 0/p (Pressure) FORMAT
----------------------------------------------------------------------
Example (CORRECT):
```
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    inlet
    {
        type            zeroGradient;
    }
    outlet
    {
        type            fixedValue;
        value           uniform 0;
    }
    wall
    {
        type            zeroGradient;
    }
    frontAndBack
    {
        type            empty;
    }
}
```
Rules:
- dimensions: [0 2 -2 0 0 0 0] (kinematic pressure)
- internalField: uniform 0 — SCALAR, not a vector!
  CORRECT: uniform 0;
  WRONG:   uniform (0 0 0);   ← this is a vector, p is scalar!
  WRONG:   uniform 0.0;        ← use 0, not 0.0
- Valid BC types: fixedValue, zeroGradient, totalPressure,
  inletOutlet, empty, cyclic, symmetry, symmetryPlane

----------------------------------------------------------------------
8. transportProperties FORMAT
----------------------------------------------------------------------
Example (CORRECT):
```
transportModel  Newtonian;
nu              [0 2 -1 0 0 0 0] 1e-06;
rho             [1 -3 0 0 0 0 0] 1000;
```
Rules:
- transportModel: must be Newtonian
- nu: MUST include dimensions: nu [0 2 -1 0 0 0 0] <value>;
  WRONG: nu 1e-06;  (missing dimensions)
- rho: MUST include dimensions: rho [1 -3 0 0 0 0 0] <value>;
- nu = mu / rho (kinematic viscosity)
- For water at 20°C: nu ≈ 1.004e-6, rho ≈ 998.2
- For air at 20°C: nu ≈ 1.516e-5, rho ≈ 1.204

----------------------------------------------------------------------
9. turbulenceProperties FORMAT
----------------------------------------------------------------------
For laminar:
```
simulationType  laminar;
```
For RANS (kOmegaSST):
```
simulationType  RAS;
RAS
{
    model           kOmegaSST;
    turbulence      on;
    printCoeffs     on;
}
```
For LES:
```
simulationType  LES;
LES
{
    model           Smagorinsky;
    turbulence      on;
    printCoeffs     on;
}
```
Valid RAS models: kOmegaSST, kEpsilon, realizableKE, RNGkEpsilon,
  LienCubicKE, LRR, SSG, LaunderSharmaKE
Valid LES models: Smagorinsky, WALE, dynamicLagrangian, kOmegaSSTDES

----------------------------------------------------------------------
10. fvSchemes FORMAT
----------------------------------------------------------------------
Example (CORRECT for transient):
```
ddtSchemes
{
    default         backward;
}
gradSchemes
{
    default         Gauss linear;
    grad(U)         Gauss linear;
}
divSchemes
{
    default         none;
    div(phi,U)      Gauss linearUpwind grad(U);
    div(phi,k)      Gauss limitedLinear 1;
    div(phi,omega)  Gauss limitedLinear 1;
}
laplacianSchemes
{
    default         Gauss linear corrected;
}
```
Rules:
- All 4 blocks MUST exist: ddtSchemes, gradSchemes, divSchemes, laplacianSchemes
- For steady-state: ddtSchemes default steadyState;
- For transient: ddtSchemes default backward; (or Euler for robustness)
- For laminar: omit div(phi,k) and div(phi,omega) entries
- For turbulence: include div(phi,k) and div(phi,omega) if using kOmegaSST

----------------------------------------------------------------------
11. fvSolution FORMAT
----------------------------------------------------------------------
Example (CORRECT for transient PIMPLE):
```
solvers
{
    p
    {
        solver          GAMG;
        tolerance       1e-6;
        relTol          0.05;
        smoother        GaussSeidel;
    }
    pFinal
    {
        $p;             // WRONG! $ is forbidden!
        relTol          0;
    }
    U
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
    }
}

PIMPLE
{
    momentumPredictor yes;
    nOuterCorrectors  1;
    nCorrectors       2;
    nNonOrthogonalCorrectors 1;
}

relaxationFactors
{
    equations
    {
        U               0.9;
        p               0.3;
    }
}
```
Rules:
- solvers block MUST exist with p and U solvers
- Do NOT use '$p;' — write out the full solver entry for pFinal
- PIMPLE block for transient, SIMPLE block for steady-state
- relaxationFactors MUST exist
- Typical relaxation: U 0.7-0.9, p 0.3-0.5
- For divergence: reduce to U 0.3, p 0.1

----------------------------------------------------------------------
12. FUNCTION OBJECTS (measurement_plan)
----------------------------------------------------------------------
Valid function object types and required fields:

forces:
  type forces;
  patches (wall1 wall2);
  fields (U p);
  rhoInf 1.0;           // Required for incompressible

forceCoeffs:
  type forceCoeffs;
  patches (wall1);
  fields (U p);
  rhoInf 1.0;
  liftDir (0 1 0);
  dragDir (1 0 0);
  pitchAxis (0 0 1);
  magUInf 1.0;
  lRef 1.0;
  Aref 1.0;

probes:
  type probes;
  fields (U p);
  probeLocations
  (
      (0.5 0.5 0)
      (1.0 0.5 0)
  );

fieldAverage:
  type fieldAverage;
  fields (U p);
  functionObject type fieldAverage;
  fields
  (
      U
      {
          mean        on;
          prime2Mean  on;
          base        time;
      }
  );

surfaces:
  type surfaceFieldValue;
  fields (U p);
  surfaceFormat raw;

Rules:
- Function object names MUST be valid OpenFOAM keywords (no spaces!)
- Use snake_case: forces_drag, probes_velocity, fieldAverage_U
- Do NOT add 'libs (...);' — function objects load automatically
- probeLocations MUST have at least one point, or omit the field entirely

----------------------------------------------------------------------
13. NUMBER FORMATTING
----------------------------------------------------------------------
- Integer values: use '0' NOT '0.0', '1' NOT '1.0'
- Scientific notation: use '1e-06' NOT '1.0e-6' or '0.000001'
- Dimensions: always in square brackets [M L T K mol A cd]
  velocity:  [0 1 -1 0 0 0 0]
  pressure:  [0 2 -2 0 0 0 0]
  nu:        [0 2 -1 0 0 0 0]
  rho:       [1 -3 0 0 0 0 0]

----------------------------------------------------------------------
14. COMMON ERRORS TO AVOID
----------------------------------------------------------------------
1. Using 'application pimpleFoam' instead of 'solver incompressibleFluid'
2. Forgetting dimensions on nu/rho in transportProperties
3. Using vector format for pressure: 'uniform (0 0 0)' instead of 'uniform 0'
4. Using '0.0' instead of '0' for integer-valued floats
5. Function object names with spaces: 'field average' instead of 'fieldAverage'
6. Adding 'libs (...);' directive
7. Using '$' variable references
8. Missing relaxationFactors in fvSolution
9. Missing PIMPLE block for transient simulations
10. Using divSchemes that reference turbulence fields for laminar cases
11. Using #include directives
12. Using codeStream or codedFixedValue

----------------------------------------------------------------------
15. BOUNDARY CONDITION PATCH NAME MAPPING
----------------------------------------------------------------------
Standard patch names and their typical BC types:

| Patch Name | U (velocity)          | p (pressure)     |
|------------|----------------------|------------------|
| inlet      | fixedValue           | zeroGradient     |
| outlet     | zeroGradient         | fixedValue (0)   |
| wall       | noSlip               | zeroGradient     |
| symmetry   | symmetry             | symmetry         |
| frontAndBack (2D) | empty       | empty            |
| cyclic     | cyclic               | cyclic           |

For moving walls: movingWallVelocity instead of noSlip
For pressure outlets: totalPressure for p at outlet

----------------------------------------------------------------------
END OF KNOWLEDGE BASE
----------------------------------------------------------------------
"""
