"""Clarification question planning for the draft session workflow.

The :class:`ClarificationPlanner` converts detected ambiguities and blocking
issues into a prioritised list of :class:`ClarificationQuestion` objects that
the conversational orchestrator can present to the user.  It enforces the
product rule of at most three questions per turn, skips non-blocking
assumptions, and orders questions so that geometry-defining issues are asked
before solver/physics questions, which in turn come before boundary-condition
and postprocess-capability questions.
"""

from __future__ import annotations

from fluid_scientist.draft_session.models import ClarificationQuestion
from fluid_scientist.study_decomposition.models import AmbiguityItem

# ---------------------------------------------------------------------------
# Category detection helpers
# ---------------------------------------------------------------------------

# Keywords that map an AmbiguityItem.field to a question category.
# Order matters: the first match wins.
_GEOMETRY_KEYWORDS: tuple[str, ...] = (
    "geometry",
    "dimension",
    "characteristic_length",
    "diameter",
    "length",
    "height",
    "width",
    "radius",
    "domain_size",
    "domain_length",
    "domain_width",
    "mesh",
    "oscillation",
    "moving_body",
    "angle_of_attack",
    "aspect_ratio",
)

_SOLVER_PHYSICS_KEYWORDS: tuple[str, ...] = (
    "turbulence_model",
    "solver",
    "physics",
    "temporal",
    "steady",
    "transient",
    "reynolds",
    "froude",
    "mach",
    "prandtl",
    "density_stratification",
    "buoyancy",
    "thermal",
    "heat_transfer",
    "compressible",
    "multiphase",
    "viscosity",
    "flow_regime",
)

_BOUNDARY_CONDITION_KEYWORDS: tuple[str, ...] = (
    "boundary",
    "inlet",
    "outlet",
    "wall",
    "bc_",
    "pressure",
    "velocity_profile",
    "heat_flux_role",
    "wall_function",
    "far_field",
    "symmetry",
    "periodic",
)

_POSTPROCESS_KEYWORDS: tuple[str, ...] = (
    "observable",
    "postprocess",
    "measurement",
    "sampling",
    "force_coefficient",
    "drag",
    "lift",
    "spectral",
    "probe",
    "monitor",
    "statistic",
)

_NUMERICAL_KEYWORDS: tuple[str, ...] = (
    "numerical",
    "discretisation",
    "discretization",
    "scheme",
    "relaxation",
    "convergence",
    "cfl",
    "time_step",
    "residual",
    "iteration",
)


def _categorise_field(field: str) -> str:
    """Heuristically map a field name to a category label."""
    lower = field.lower()
    for kw in _GEOMETRY_KEYWORDS:
        if kw in lower:
            return "geometry"
    for kw in _SOLVER_PHYSICS_KEYWORDS:
        if kw in lower:
            return "solver_physics"
    for kw in _BOUNDARY_CONDITION_KEYWORDS:
        if kw in lower:
            return "boundary_condition"
    for kw in _POSTPROCESS_KEYWORDS:
        if kw in lower:
            return "postprocess_capability"
    for kw in _NUMERICAL_KEYWORDS:
        if kw in lower:
            return "numerical_setting"
    return "other"


# Severity rank (lower = higher priority).
# v5 ClarificationQuestion only supports needs_confirmation and
# blocking_for_case_generation; non_blocking_assumption items are filtered
# out before sorting.
_SEVERITY_RANK: dict[str, int] = {
    "blocking_for_case_generation": 0,
    "needs_confirmation": 1,
}

# Category rank within the same severity band (lower = higher priority)
_CATEGORY_RANK: dict[str, int] = {
    "geometry": 0,
    "solver_physics": 1,
    "boundary_condition": 2,
    "postprocess_capability": 3,
    "numerical_setting": 4,
    "other": 5,
}


# ---------------------------------------------------------------------------
# ClarificationPlanner
# ---------------------------------------------------------------------------


class ClarificationPlanner:
    """Plan clarification questions from ambiguities and unknowns.

    Rules:
    - Max 3 questions per turn
    - Priority order:
      1. Geometry-defining issues (blocking)
      2. Solver/physics model issues (blocking)
      3. Boundary condition mapping issues (blocking)
      4. Observable postprocess capability issues (needs_confirmation)
      5. Other low-risk numerical settings (non_blocking_assumption — skipped)
    - Only ask blocking and needs_confirmation items, skip non_blocking_assumption
    - blocking items take priority over needs_confirmation
    """

    MAX_QUESTIONS_PER_TURN: int = 3

    def __init__(self) -> None:
        self._question_counter: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        ambiguities: list[AmbiguityItem],
        blocking_issues: list[dict] | None = None,
    ) -> list[ClarificationQuestion]:
        """Generate up to MAX_QUESTIONS_PER_TURN clarification questions.

        Parameters
        ----------
        ambiguities:
            Ambiguity items produced by the ambiguity detector (and any
            downstream capability checks that append to the intent's
            ``ambiguity_report``).
        blocking_issues:
            Optional list of blocking-issue dicts (e.g. from
            :attr:`ResearchState.blocking_issues`).  Each dict should at
            least contain an ``issue`` key; ``field`` and ``reason`` are
            honoured when present.
        """
        candidates: list[ClarificationQuestion] = []

        # 1. Convert AmbiguityItems, skipping non_blocking_assumption.
        for amb in ambiguities:
            if amb.severity == "non_blocking_assumption":
                continue
            candidates.append(self._ambiguity_to_question(amb))

        # 2. Convert external blocking issues (always treated as blocking).
        for issue in blocking_issues or []:
            candidates.append(self._blocking_issue_to_question(issue))

        # 3. Sort: severity first (blocking > needs_confirmation), then by
        #    category priority derived from field name.
        candidates.sort(
            key=lambda q: (
                _SEVERITY_RANK.get(q.severity, 99),
                _CATEGORY_RANK.get(_categorise_field(q.field), 99),
                q.field,
            )
        )

        # 4. Assign unique question_ids after sorting so that higher-priority
        #    questions get lower-numbered ids.
        for _idx, q in enumerate(candidates, start=1):
            q.question_id = self._next_id()

        # 5. Limit to MAX_QUESTIONS_PER_TURN.
        return candidates[: self.MAX_QUESTIONS_PER_TURN]

    def should_clarify(self, questions: list[ClarificationQuestion]) -> bool:
        """Return True if any question is blocking (i.e. must be answered)."""
        return any(q.severity == "blocking_for_case_generation" for q in questions)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        self._question_counter += 1
        return f"q-{self._question_counter:03d}"

    def _ambiguity_to_question(
        self, amb: AmbiguityItem
    ) -> ClarificationQuestion:
        question_text = amb.suggested_question or (
            f"Please clarify: {amb.issue}"
        )
        recommended = None
        if amb.recommended_default is not None:
            recommended = {"default": str(amb.recommended_default)}
        return ClarificationQuestion(
            question_id="",  # assigned after sorting
            field=amb.field,
            question=question_text,
            severity=amb.severity,  # type: ignore[arg-type]
            reason=amb.reason,
            recommended_answer=recommended,
        )

    def _blocking_issue_to_question(
        self, issue: dict
    ) -> ClarificationQuestion:
        field = str(issue.get("field", issue.get("issue", "unknown")))
        issue_text = str(issue.get("issue", "Blocking issue detected"))
        reason = str(issue.get("reason", ""))
        return ClarificationQuestion(
            question_id="",  # assigned after sorting
            field=field,
            question=f"Please resolve blocking issue: {issue_text}",
            severity="blocking_for_case_generation",
            reason=reason,
        )
