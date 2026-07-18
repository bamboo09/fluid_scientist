# Source audit

## Method

Public repositories were cloned with partial blobs into `third_party/skill_sources/checkouts`, HEAD commit and last commit metadata were recorded, LICENSE/COPYING files were inspected, and relevant README/SKILL/reference/test inventories were read. No third-party script, installer, shell asset, MCP server, model runtime, or dataset was executed. Marketplace pages were not used as facts.

## Results and risks

- Permissive MIT/Apache sources: concepts were independently rewritten; `copied_files: []`.
- GPL sources (OpenFOAM, MetaOpenFOAM, AutoCFD, foamlib, fluidfoam): facts/interfaces were studied, but no implementation, template, dataset or long documentation passage was copied.
- AI-CFD-Scientist: no LICENSE/COPYING file was found at the locked commit; only paper/README-level concepts were independently expressed.
- ChatCFD: no official code repository was identified from the paper/search; it remains paper-only, unknown-license, D for code/data/prompts.
- AutoCFD README says NL2FOAM was expanded from 16 curated cases; its dataset and CoT were not copied. Repository LICENSE is GPLv3.
- MetaOpenFOAM locked HEAD explicitly deprecates the project in favor of sim-cli; it is historical only.
- Foam-Agent locked README targets Foundation v10 and openfoam-claude-suite targets v2412; neither is an OF13 authority.
- fluidfoam cannot be fully checked out on Windows because its history contains a colon-bearing path; README/LICENSE were read with `git show` only.

## Per-source record

Each source record includes repository, commit, license, relevant paths, version assumptions, scripts/network risk, adapted/rejected content in `sources.lock.yaml`. No credentials were accessed and checkouts are audit evidence, not production dependencies.

