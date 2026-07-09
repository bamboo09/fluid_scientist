"""LLM integration package.

Provides :class:`LLMClient`, a simple wrapper around LLM providers that
records every call as an :class:`~fluid_scientist.draft_session.models.LLMCallRecord`
for debugging, replay and quality analysis.  In its default ``"mock"``
configuration it returns deterministic structured responses so the rest
of the pipeline can be exercised without external API keys.
"""

from fluid_scientist.llm.client import LLMClient

__all__ = ["LLMClient"]
