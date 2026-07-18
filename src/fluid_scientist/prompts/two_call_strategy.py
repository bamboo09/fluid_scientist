"""Two-call strategy: primary_reasoner + critic with bounded retries.

The :class:`TwoCallStrategy` implements the core model-driven spec
editing loop described in the plan (Section 10.3):

1. Build the Spec Editor prompt and call the model
   (``primary_reasoner``) to produce a candidate patch.
2. Build the Critic prompt with the candidate patch and call the model
   (``critic``) to review it.
3. If the Critic accepts, return the candidate patch.
4. If the Critic rejects, retry (up to 2 retries = 3 total attempts).
5. Never loop infinitely.
6. On model failure, return an explicit ``MODEL_FAILED`` error.

The ``model_client`` is a **simple callable** ``model_client(prompt: str)
-> dict`` so that tests can easily mock it without depending on a real
LLM provider.  In production, a thin adapter wraps the actual LLM
client to conform to this interface.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import ValidationError

from .critic import CriticResult

__all__ = ["TwoCallStrategy"]

#: Type alias for the model client callable.
ModelClient = Callable[[str], dict]


class TwoCallStrategy:
    """Two-call strategy with primary_reasoner + critic and bounded retries.

    Parameters
    ----------
    system_prompt_builder:
        Callable that builds the Spec Editor prompt.  Expected
        signature::

            system_prompt_builder(
                context: dict,
                patch_schema: dict,
                current_spec: dict,
                user_message: str,
                confirmed_facts: list,
                unresolved_conflicts: list,
                skills: list,
                openfoam_env: dict,
            ) -> str

        In practice this is :func:`~fluid_scientist.prompts.spec_editor.build_spec_editor_prompt`.
    critic_prompt_builder:
        Callable that builds the Critic prompt.  Expected signature::

            critic_prompt_builder(
                candidate_patch: dict,
                current_spec: dict,
                user_message: str,
            ) -> str

        In practice this is
        :func:`~fluid_scientist.prompts.critic.build_critic_prompt`.

    Attributes
    ----------
    MAX_RETRIES:
        Maximum number of retries after the first attempt.  A value of
        ``2`` means up to 3 total calls to ``primary_reasoner``.
    """

    #: Maximum retries (2 means 3 total primary_reasoner calls).
    MAX_RETRIES: int = 2

    def __init__(
        self,
        system_prompt_builder: Callable[..., str],
        critic_prompt_builder: Callable[..., str],
    ) -> None:
        self._system_prompt_builder = system_prompt_builder
        self._critic_prompt_builder = critic_prompt_builder

    def execute(
        self,
        model_client: ModelClient,
        context: dict,
        user_message: str,
        current_spec: dict,
        patch_schema: dict,
    ) -> tuple[dict | None, CriticResult | None, list[str]]:
        """Execute the two-call strategy.

        Parameters
        ----------
        model_client:
            A callable ``model_client(prompt: str) -> dict`` that
            sends the prompt to the model and returns the parsed JSON
            output as a dict.
        context:
            Session context dict.  Must (or may) contain the following
            keys, which are extracted and forwarded to the prompt
            builder:

            * ``"confirmed_facts"`` — list of confirmed facts.
            * ``"unresolved_conflicts"`` — list of conflicts.
            * ``"skills"`` — list of skill descriptors.
            * ``"openfoam_env"`` — OpenFOAM environment dict.
            * ``"workflow_phase"`` — current workflow phase string.

            On retries, ``"prior_critic_feedback"`` is added so the
            Spec Editor can see what went wrong.
        user_message:
            The user's raw message for this turn.
        current_spec:
            The current :class:`SimulationStudySpec` as a dict.
        patch_schema:
            The JSON Schema for ``SimulationSpecPatch``.

        Returns
        -------
        A tuple ``(candidate_patch, critic_result, errors)``:

        * **Critic accepts** (any attempt):
          ``(patch, CriticResult(accepted=True), [])``
        * **Critic rejects after exhausting retries**:
          ``(None, CriticResult(accepted=False, ...), required_corrections)``
        * **Model failure** (exception, non-dict, or validation error):
          ``(None, None, ["MODEL_FAILED: ..."])``
        """
        # Extract context fields for the prompt builder.
        confirmed_facts = context.get("confirmed_facts", [])
        unresolved_conflicts = context.get("unresolved_conflicts", [])
        skills = context.get("skills", [])
        openfoam_env = context.get("openfoam_env", {})

        last_critic_result: CriticResult | None = None
        last_required_corrections: list[str] = []

        # 1 initial attempt + MAX_RETRIES retries = MAX_RETRIES + 1 total.
        total_attempts = self.MAX_RETRIES + 1

        for attempt in range(1, total_attempts + 1):
            # --- Step 1: Build the Spec Editor prompt ---
            try:
                spec_editor_prompt = self._system_prompt_builder(
                    context,
                    patch_schema,
                    current_spec,
                    user_message,
                    confirmed_facts,
                    unresolved_conflicts,
                    skills,
                    openfoam_env,
                )
            except Exception as exc:
                return (
                    None,
                    None,
                    [f"MODEL_FAILED: spec editor prompt build error: {exc}"],
                )

            # --- Step 2: Call model (primary_reasoner) -> candidate patch ---
            try:
                candidate_patch = model_client(spec_editor_prompt)
            except Exception as exc:
                return (None, None, [f"MODEL_FAILED: {exc}"])

            if not isinstance(candidate_patch, dict):
                return (
                    None,
                    None,
                    [
                        "MODEL_FAILED: primary_reasoner returned non-dict "
                        f"(got {type(candidate_patch).__name__})"
                    ],
                )

            # --- Step 3: Build the Critic prompt ---
            try:
                critic_prompt = self._critic_prompt_builder(
                    candidate_patch,
                    current_spec,
                    user_message,
                )
            except Exception as exc:
                return (
                    None,
                    None,
                    [f"MODEL_FAILED: critic prompt build error: {exc}"],
                )

            # --- Step 4: Call model (critic) -> CriticResult ---
            try:
                critic_output = model_client(critic_prompt)
            except Exception as exc:
                return (
                    None,
                    None,
                    [f"MODEL_FAILED: critic call error: {exc}"],
                )

            try:
                critic_result = CriticResult.model_validate(critic_output)
            except ValidationError as exc:
                return (
                    None,
                    None,
                    [f"MODEL_FAILED: critic output validation error: {exc}"],
                )

            # --- Step 5: If critic accepts, return immediately ---
            if critic_result.accepted:
                return (candidate_patch, critic_result, [])

            # --- Step 6: Critic rejected — prepare for retry ---
            last_critic_result = critic_result
            last_required_corrections = list(critic_result.required_corrections)

            # Inject the critic feedback into the context so the next
            # attempt's prompt includes it (the prompt builder reads
            # context["prior_critic_feedback"]).
            context["prior_critic_feedback"] = {
                "attempt": attempt,
                "violations": critic_result.violations,
                "required_corrections": critic_result.required_corrections,
            }

        # --- Exhausted all retries ---
        return (None, last_critic_result, last_required_corrections)
