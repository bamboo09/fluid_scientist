# External source adoption matrix

| Source | Commit/evidence lock | License | Level | Adopt | Reject |
|---|---|---|---|---|---|
| OpenAI Skills docs | official pages, accessed 2026-07-16 | docs | A | metadata, progressive disclosure, instruction-only, evals | CFD facts, runtime assumptions |
| OpenFOAM Foundation 13 | 18870c24 | GPL-3+ | A facts | source/tutorial behavior, locators | copied code |
| HPC-Skills hpc-openfoam | 4ea80691 | MIT | A/B | reference structure and checks | scripts/HPC/version recipes |
| Foam-Agent | cfde3847 | MIT | B/C | hierarchy, dependencies, bounded correction, eval ideas | runtime/prompts/v10 case generator |
| AI-CFD-Scientist | b7fa924c | unknown | A concepts/B | baseline, mesh gate, silent failure, artifacts | code/prompts/runtime |
| openfoam-claude-suite | 3863d6d9 | MIT | A/B | responsibility split, doctor gates | v2412/WSL/scripts |
| mechanical research skill | 07accf8d | MIT | A | rigor, assumptions, uncertainty | unrelated content |
| sim-cli/plugin | ec46ee4f/c53b4f36 | Apache-2.0 | B | typed surface, doctor, replay | runtime/dependencies |
| MetaOpenFOAM | 1b636576 | GPL-3 | C | historical comparison | code/current best practice |
| AutoCFD/NL2FOAM | 5f1196b2 | GPL-3 | C/D | eval diversity ideas | data, CoT, code, benchmark text |
| ChatCFD | paper only | unknown | B/D | structured extraction/validation/reflection | code/data/prompts |
| foamlib | 2f582563 | GPL-3 | B/D | structured parser principle | source/dependency |
| classy_blocks | a48d9e54 | MIT | A/B | parametric blockMesh concepts | universal-mesher claim |
| fluidfoam | 0ddf4065 | GPL-3+ | C/D | result organization ideas | implementation/dependency |
| openfoam-mcp-server | 8d14e203 | Apache-2.0 | C/D | schema/question patterns | execution/install/marketplace claims |

Exact entries, reviewed paths and prohibited content are in ../../third_party/skill_sources/sources.lock.yaml.

