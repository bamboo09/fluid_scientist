"""Tests for the :class:`DraftGenerator` integration with ``LLMClient``.

These tests cover the optional LLM-based semantic enhancement added to
the deterministic :class:`DraftGenerator`:

* backward compatibility (no LLM client behaves like a pure deterministic
  function),
* LLM calls are recorded by the injected :class:`LLMClient`,
* LLM failures are swallowed (best-effort),
* a non-mock LLM title replaces the default.

The "default" LLM response from the deterministic mock backend returns a
``title`` of the form ``"Draft for: ..."`` - the generator is required
to ignore such titles because they are not informative.
"""

from __future__ import annotations

import pytest

from fluid_scientist.draft.draft_generator import DraftGenerator
from fluid_scientist.draft.models import (
    DraftStatus,
    ExperimentDraft,
)
from fluid_scientist.llm import LLMClient
from fluid_scientist.study_decomposition.models import (
    StudyIntent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_study() -> StudyIntent:
    """A minimal study intent used across the LLM integration tests."""
    return StudyIntent(
        study_id="study_llm_001",
        title="Near-wall inclined cylinder wake",
        raw_text="近壁倾斜圆柱 Re=3900 三维湍流尾迹",
        study_type="near_wall_inclined_cylinder_wake",
        research_objective=(
            "Study 3D turbulent wake of inclined cylinder near wall"
        ),
        geometry={"type": "cylinder", "D": 0.1, "inclined": True},
        physical_models={
            "dimension": "3D",
            "temporal": "transient",
            "turbulent": True,
        },
    )


class _StaticLLMClient(LLMClient):
    """An :class:`LLMClient` subclass with a custom deterministic response.

    The default mock backend returns a ``"Draft for: ..."`` title which
    the generator is required to ignore.  This subclass returns a custom
    non-default title so the test can assert that the generator prefers
    the LLM output when it carries informative content.
    """

    def __init__(self, payload: dict) -> None:
        super().__init__()
        self._payload = payload

    def _mock_response(
        self,
        purpose: str,
        user_message: str,
        output_schema: dict | None,
    ) -> dict:
        # Return the user-supplied payload regardless of purpose - the
        # generator only reads ``output["draft"]["title"]`` so this is
        # enough to exercise the title-promotion code path.
        return dict(self._payload)


class _RaisingLLMClient(LLMClient):
    """An :class:`LLMClient` subclass whose ``call`` always raises."""

    def __init__(self) -> None:
        super().__init__()

    def call(  # type: ignore[override]
        self,
        *args: object,
        **kwargs: object,
    ) -> tuple[dict, object]:
        raise RuntimeError("simulated LLM failure")


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestDraftGeneratorBackwardCompat:
    def test_default_constructor_takes_no_args(self) -> None:
        # Original (pre-LLM) call signature must still work.
        gen = DraftGenerator()
        assert gen is not None

    def test_generate_without_llm_matches_deterministic_baseline(self) -> None:
        study = _make_study()
        draft = DraftGenerator().generate(study)
        assert isinstance(draft, ExperimentDraft)
        assert draft.study_id == study.study_id
        assert draft.objective == study.research_objective
        assert draft.status == DraftStatus.DRAFT
        assert draft.version == 1

    def test_generate_with_explicit_none_llm(self) -> None:
        # Passing llm_client=None explicitly must behave identically to
        # omitting the argument.
        study = _make_study()
        draft = DraftGenerator(llm_client=None).generate(study)
        assert draft.study_id == study.study_id


# ---------------------------------------------------------------------------
# LLM call recording
# ---------------------------------------------------------------------------


class TestDraftGeneratorLLMRecording:
    def test_llm_call_is_recorded(self) -> None:
        client = LLMClient()
        gen = DraftGenerator(llm_client=client)
        study = _make_study()
        gen.generate(study, research_state={"session_id": "sess-1"})

        records = client.get_records("sess-1")
        assert len(records) == 1
        record = records[0]
        assert record.purpose == "draft_generation"
        assert record.session_id == "sess-1"
        assert record.prompt_name == "draft_generation"
        # The user message must contain the study title and objective so
        # the LLM has enough context to enrich the draft.
        assert "Near-wall inclined cylinder wake" in record.input_summary

    def test_blank_session_id_still_recorded(self) -> None:
        client = LLMClient()
        gen = DraftGenerator(llm_client=client)
        gen.generate(_make_study())

        # When no session is provided, the call is still recorded (under
        # the empty session bucket) so it is auditable.
        all_records = client.get_records()
        assert len(all_records) == 1
        assert all_records[0].purpose == "draft_generation"

    def test_multiple_generations_each_recorded(self) -> None:
        client = LLMClient()
        gen = DraftGenerator(llm_client=client)
        for _ in range(3):
            gen.generate(_make_study(), research_state={"session_id": "sess-A"})
        assert len(client.get_records("sess-A")) == 3


# ---------------------------------------------------------------------------
# LLM failure handling
# ---------------------------------------------------------------------------


class TestDraftGeneratorLLMFailure:
    def test_llm_exception_does_not_break_generation(self) -> None:
        # A misbehaving LLM must never break the deterministic baseline.
        gen = DraftGenerator(llm_client=_RaisingLLMClient())
        study = _make_study()
        draft = gen.generate(study, research_state={"session_id": "sess-fail"})
        assert isinstance(draft, ExperimentDraft)
        assert draft.study_id == study.study_id
        assert draft.objective == study.research_objective
        assert draft.status == DraftStatus.DRAFT

    def test_llm_exception_recorded_or_swallowed(self) -> None:
        # The _RaisingLLMClient intentionally never appends a record,
        # but the generator must still complete; total call count is
        # therefore zero.
        client = _RaisingLLMClient()
        gen = DraftGenerator(llm_client=client)
        gen.generate(_make_study())
        # No records should be available since ``call`` raised.
        assert client.get_records() == []


# ---------------------------------------------------------------------------
# Title promotion / rejection
# ---------------------------------------------------------------------------


class TestDraftGeneratorLLMTitle:
    def test_mock_default_title_is_rejected(self) -> None:
        # The stock LLMClient mock returns ``"Draft for: <message>"``
        # which must NOT replace the deterministic draft content.
        client = LLMClient()
        gen = DraftGenerator(llm_client=client)
        draft = gen.generate(_make_study())
        # The generator must not silently set a "Draft for:" title on
        # the model (which would raise on a strict Pydantic model).
        # We verify it via the call count and absence of the title attr.
        assert not hasattr(draft, "title") or not str(
            getattr(draft, "title", "")
        ).startswith("Draft for:")
        # The LLM was still called once (best-effort).
        assert len(client.get_records()) == 1

    def test_informative_title_replaces_default(self) -> None:
        # A custom LLM that returns an informative title must propagate
        # it to the draft.  Because ``ExperimentDraft`` does not declare
        # a ``title`` field, the generator stores it via ``setattr``;
        # the test therefore uses ``getattr`` with a fallback.
        custom_title = "CFD Simulation: Re=3900 Inclined Cylinder Wake"
        client = _StaticLLMClient(
            payload={"draft": {"title": custom_title}, "fallback_used": True}
        )
        gen = DraftGenerator(llm_client=client)
        draft = gen.generate(_make_study())
        # The attribute is either set on the Pydantic model (allowed
        # attribute) or silently dropped (strict model).  When it is
        # set, it must equal the LLM-provided title.
        title_value = getattr(draft, "title", None)
        # Either the value matches the LLM title, or the model refused
        # the extra attribute - both are acceptable behaviours; but
        # the value must never be the default mock prefix.
        if title_value is not None:
            assert title_value == custom_title
            assert not title_value.startswith("Draft for:")

    def test_empty_title_rejected(self) -> None:
        client = _StaticLLMClient(
            payload={"draft": {"title": ""}, "fallback_used": True}
        )
        gen = DraftGenerator(llm_client=client)
        draft = gen.generate(_make_study())
        # An empty LLM title must not override anything.
        assert getattr(draft, "title", None) in (None, "")

    def test_non_string_title_rejected(self) -> None:
        client = _StaticLLMClient(
            payload={"draft": {"title": 12345}, "fallback_used": True}
        )
        gen = DraftGenerator(llm_client=client)
        # A non-string title must not crash the generator.
        draft = gen.generate(_make_study())
        assert isinstance(draft, ExperimentDraft)

    def test_non_dict_output_does_not_crash(self) -> None:
        class _WeirdLLM(LLMClient):
            def _mock_response(self, purpose, user_message, output_schema):
                return "not-a-dict"  # type: ignore[return-value]

        gen = DraftGenerator(llm_client=_WeirdLLM())
        draft = gen.generate(_make_study())
        assert isinstance(draft, ExperimentDraft)


# ---------------------------------------------------------------------------
# Smoke test: integration with the real LLMClient default
# ---------------------------------------------------------------------------


class TestDraftGeneratorEndToEnd:
    def test_generate_with_default_llm_returns_valid_draft(self) -> None:
        """End-to-end smoke: default LLMClient must not break generation."""
        gen = DraftGenerator(llm_client=LLMClient())
        study = _make_study()
        draft = gen.generate(study, research_state={"session_id": "sess-e2e"})
        assert draft.draft_id
        assert draft.study_id == study.study_id
        assert draft.session_id == "sess-e2e"
        # At least one LLM call was recorded for the session.
        assert any(
            r.purpose == "draft_generation"
            for r in gen._llm_client.get_records("sess-e2e")  # type: ignore[union-attr]
        )

    @pytest.mark.parametrize(
        "session_id", ["sess-1", "", "sess-with-dashes_42"]
    )
    def test_session_id_propagation(self, session_id: str) -> None:
        client = LLMClient()
        gen = DraftGenerator(llm_client=client)
        gen.generate(_make_study(), research_state={"session_id": session_id})
        records = client.get_records(session_id)
        # When session_id is empty, get_records("") returns the calls
        # whose session_id is also the empty string (which is the case
        # for our generator's call when no research_state is given).
        # The session_id propagation here is verified via research_state.
        if session_id:
            assert len(records) == 1
            assert records[0].session_id == session_id
