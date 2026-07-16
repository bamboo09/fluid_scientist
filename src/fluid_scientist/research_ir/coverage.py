"""Source Coverage Guard - ensures 100% of user mentions are accounted for."""

from __future__ import annotations
import logging
from fluid_scientist.research_ir.models import (
    OpenWorldResearchIR, Mention, MentionInventory, SourceCoverage,
)

logger = logging.getLogger(__name__)


class CoverageError(Exception):
    """Raised when user mentions are not fully accounted for."""
    def __init__(self, unaccounted: list[Mention]):
        self.unaccounted = unaccounted
        texts = [m.text for m in unaccounted]
        super().__init__(
            f"Semantic coverage incomplete: {len(unaccounted)} mention(s) unaccounted: {texts}"
        )


class SourceCoverageGuard:
    """Hard gate: all user mentions must be accounted for before compilation."""

    def check(self, ir: OpenWorldResearchIR) -> CoverageError | None:
        """Check if all mentions are accounted for. Returns error or None if OK."""
        unaccounted = ir.source_coverage.unaccounted_mentions
        if unaccounted:
            return CoverageError(unaccounted)
        return None

    def enforce(self, ir: OpenWorldResearchIR) -> None:
        """Raise CoverageError if any mentions are unaccounted."""
        error = self.check(ir)
        if error:
            raise error

    def report(self, ir: OpenWorldResearchIR) -> dict:
        """Generate a coverage report."""
        inv = ir.source_coverage.mention_inventory
        total = len(inv.mentions)
        accounted = total - len(ir.source_coverage.unaccounted_mentions)
        return {
            "total_mentions": total,
            "accounted": accounted,
            "unaccounted": len(ir.source_coverage.unaccounted_mentions),
            "coverage_ratio": ir.source_coverage.coverage_ratio,
            "is_complete": ir.source_coverage.is_complete,
            "unaccounted_texts": [m.text for m in ir.source_coverage.unaccounted_mentions],
            "mention_details": [
                {
                    "mention_id": m.mention_id,
                    "text": m.text,
                    "category": m.category,
                    "status": m.status,
                    "mapped_to": m.mapped_to,
                }
                for m in inv.mentions
            ],
        }
