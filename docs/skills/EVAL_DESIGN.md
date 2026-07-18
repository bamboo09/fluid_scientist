# Skill Eval design

The suite defines success before authoring: routing, schema outcome, minimality, dependency reasoning, scientific safety and efficiency. JSONL cases are original and may name an external concept in `inspired_by`; they do not copy source tests, prompts, datasets or chain-of-thought.

## Allocation

- spec editing 70
- physics review 25
- case planning 25
- geometry/meshing 20
- boundaries/numerics 20
- diagnostics 15
- postprocessing 15
- validation/reporting 10

Total single-turn: 200. Additional multi-turn conversations: 20 in `tests/skill_evals/multi_turn`.

## Grading

Deterministic checks cover valid JSON, required keys/operations/paths, forbidden changes, v13-unverified labeling, no shell/final dictionary, and evidence IDs. Rubrics cover ambiguity, minimality, physics reasoning, risk classification, stop behavior and overclaiming. Anti-template tests include triangle≠cosine bell, unknown polygon, plot deletion≠field deletion, start=5 time ambiguity and solver exit≠credibility.

Eval artifacts are specifications, not claims that a model run has passed. A future harness should capture prompt → trace/artifacts → deterministic/rubric checks → score, preserve model/version/Skill commit, and report failures.

