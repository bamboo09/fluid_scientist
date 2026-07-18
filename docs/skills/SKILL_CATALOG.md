# Fluid Scientist CFD Skill catalog

The catalog follows progressive disclosure: concise routing metadata, one responsibility per Skill, instruction-only default, and topic references loaded only when needed.

| Skill | Trigger | Output | Must not do |
|---|---|---|---|
| fluid-research-workflow | end-to-end governed research workflow | stage/gate orchestration guidance | replace specialist reasoning or execute arbitrary shell |
| cfd-spec-understanding-and-editing | new request or current-spec edit | minimal SimulationSpecPatch | template fallback, execution |
| cfd-physics-review | physical sanity/applicability audit | blocker/warning/recommendation contract | compile, execute, confirm |
| openfoam-case-planning | approved spec to OF13 plan | OpenFOAMCaseBlueprint | final dictionaries or shell |
| openfoam-geometry-meshing | geometry semantics and mesh strategy | GeometryAST + Mesh Recipe | substitute shapes or run mesh |
| openfoam-boundaries-numerics | field BC and schemes review | coupled boundary/numerics plan | keyword-only mapping |
| openfoam-diagnostics | logs/artifact failure diagnosis | evidence-first bounded diagnosis | endless retries or physics drift |
| openfoam-postprocessing | objectives to observables/artifacts | objective mapping and post plan | unsupported execution or conclusion |
| cfd-validation-reporting | credibility gates/reporting | four-gate evidence report | equate exit with credibility |

Routing order for a changed study is spec edit → physics review when dependencies change → case/geometry/boundary/post plans → diagnostics only on evidence → validation/reporting. A user request may trigger several Skills, but each retains its own output contract.

