"""Semantic Critic -- reviews an :class:`OpenWorldResearchIR` for semantic fidelity.

The :class:`SemanticCritic` compares the user's original text against the
Research IR and reports *blocking issues* and *warnings*.  A set of
deterministic, rule-based checks always runs (even without an LLM client).
When an LLM client is available, an additional LLM review is performed using
the ``semantic_critic`` prompt and its findings are merged with the
rule-based results.

Rule-based checks
------------------
* ``GEOMETRY_TYPE_MISMATCH`` -- an entity ``semantic_shape`` contradicts the
  shape the user actually described (e.g. user says "三角形" but the entity
  is a ``cosine_bell``).
* ``UNRESOLVED_GEOMETRY`` -- an entity whose ``representation_status`` is
  ``needs_clarification`` or ``unsupported``.
* ``SPATIAL_RELATION_VIOLATION`` -- a spatial relation (e.g.
  ``centered_under``) whose entity positions do not satisfy the relation
  within ``POSITION_TOLERANCE``.
* ``MISSING_BOUNDARY`` -- a 2D case with fewer than four boundaries.
* ``DUPLICATE_ENTITY`` -- two geometry entities sharing the same centre
  within ``POSITION_TOLERANCE``.
* ``UNACCOUNTED_MENTION`` -- :attr:`SourceCoverage.unaccounted_mentions` is
  non-empty.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.research_ir.models import (
    GeometryEntity,
    OpenWorldResearchIR,
)
from fluid_scientist.research_ir.prompt_registry import PromptRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Tolerance (in user units) for all geometric position comparisons.
POSITION_TOLERANCE: float = 0.01

#: Prompt name loaded from the registry.
SEMANTIC_CRITIC_PROMPT_NAME: str = "semantic_critic"

# Canonical parameter keys used to look up an entity's centre coordinates.
_X_KEYS: tuple[str, ...] = ("center_x", "centre_x", "x", "cx")
_Y_KEYS: tuple[str, ...] = ("center_y", "centre_y", "y", "cy")
_Z_KEYS: tuple[str, ...] = ("center_z", "centre_z", "z", "cz")

#: Maps a shape keyword (Chinese or English token) to a canonical *shape
#: group*.  The group is used to decide whether two shape descriptions are
#: compatible or contradictory.  Longer keywords are matched first so that
#: compound terms such as ``圆柱`` (cylinder) are not misread as ``圆``
#: (circle).
_SHAPE_KEYWORDS: dict[str, str] = {
    # triangle
    "triangle": "triangle",
    "triangular": "triangle",
    "三角形": "triangle",
    "三角": "triangle",
    # rectangle / square
    "rectangle": "rectangle",
    "rectangular": "rectangle",
    "square": "rectangle",
    "矩形": "rectangle",
    "长方形": "rectangle",
    "正方形": "rectangle",
    "方形": "rectangle",
    # trapezoid
    "trapezoid": "trapezoid",
    "trapezoidal": "trapezoid",
    "梯形": "trapezoid",
    # cylinder
    "cylinder": "cylinder",
    "cylindrical": "cylinder",
    "圆柱": "cylinder",
    "圆柱体": "cylinder",
    "圆形柱": "cylinder",
    # circle
    "circle": "circle",
    "circular": "circle",
    "圆形": "circle",
    "圆": "circle",
    # cosine bell / bump
    "cosine_bell": "cosine_bell",
    "cosinebell": "cosine_bell",
    "cosine": "cosine_bell",
    "余弦凸起": "cosine_bell",
    "余弦": "cosine_bell",
    # sine bell / bump
    "sine_bell": "sine_bell",
    "sinebell": "sine_bell",
    "sine": "sine_bell",
    "正弦凸起": "sine_bell",
    "正弦": "sine_bell",
    # pentagon
    "pentagon": "pentagon",
    "pentagonal": "pentagon",
    "五边形": "pentagon",
    # hexagon
    "hexagon": "hexagon",
    "hexagonal": "hexagon",
    "六边形": "hexagon",
    # ellipse
    "ellipse": "ellipse",
    "elliptical": "ellipse",
    "椭圆": "ellipse",
}

#: Fallback system prompt used when the registry cannot load the prompt file.
_FALLBACK_SYSTEM_PROMPT: str = (
    "你是 Fluid Scientist 的语义忠实性审查器。\n"
    "你必须比较：1. 用户原文；2. mention inventory；3. 当前Research IR。\n"
    "你的任务不是生成新实验方案，而是发现理解过程中的错误。\n"
    "重点检查：遗漏、错误替换、重复、空间关系矛盾、参数归属、能力污染。\n"
    "以下情况必须 blocking：用户显式几何实体缺失、用户显式边界缺失、"
    "用户要求的观测量缺失、未知能力被静默替换、mention未被accounted、"
    "同一实体出现互斥重复表示。\n"
    "输出严格JSON：{\"passed\": bool, \"blocking_issues\": [{"
    "\"issue_type\": \"omission|substitution|duplication|conflict|unaccounted_mention\","
    " \"source_span\": \"\", \"current_value\": null, \"expected_semantics\": \"\","
    " \"recommended_action\": \"restore|clarify|capability_check\"}], "
    "\"warnings\": [], \"coverage_ratio\": 1.0}"
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class CriticResult:
    """Outcome of a semantic review.

    ``passed`` is a *derived property*: it is ``True`` if and only if there
    are no blocking issues.
    """

    blocking_issues: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """``True`` only when no blocking issues were found."""
        return len(self.blocking_issues) == 0

    def to_dict(self) -> dict:
        """Serialise the result to a plain dictionary."""
        return {
            "passed": self.passed,
            "blocking_issues": list(self.blocking_issues),
            "warnings": list(self.warnings),
            "blocking_count": len(self.blocking_issues),
            "warning_count": len(self.warnings),
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"CriticResult(passed={self.passed}, "
            f"blocking={len(self.blocking_issues)}, "
            f"warnings={len(self.warnings)})"
        )


# ---------------------------------------------------------------------------
# SemanticCritic
# ---------------------------------------------------------------------------


class SemanticCritic:
    """Reviews an :class:`OpenWorldResearchIR` for semantic fidelity issues.

    Rule-based checks always run.  When ``llm_client`` is supplied, an
    additional LLM review is performed using the ``semantic_critic`` prompt
    and the parsed issues are merged with the rule-based findings.

    Args:
        llm_client: Optional LLM client exposing a ``call(...)`` method that
            returns ``(parsed, record)`` (matching the rest of the
            codebase).  When ``None`` only rule-based checks are used.
        prompt_registry: Optional :class:`PromptRegistry` used to load the
            ``semantic_critic`` prompt.  When ``None`` a default registry is
            created.
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_registry: PromptRegistry = (
            prompt_registry if prompt_registry is not None else PromptRegistry()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def review(
        self,
        ir: OpenWorldResearchIR,
        user_text: str,
    ) -> CriticResult:
        """Run all checks and return a :class:`CriticResult`.

        Args:
            ir: The research intermediate representation to review.
            user_text: The original user request text.

        Returns:
            A :class:`CriticResult` whose ``passed`` property is ``True``
            only when no blocking issues were found.
        """
        blocking: list[dict] = []
        warnings: list[dict] = []

        # Rule-based checks (always run, in dependency order).
        for b_issues, w_issues in (
            self._check_unaccounted_mention(ir, user_text),
            self._check_unresolved_geometry(ir, user_text),
            self._check_geometry_type_mismatch(ir, user_text),
            self._check_spatial_relation_violation(ir, user_text),
            self._check_missing_boundary(ir, user_text),
            self._check_duplicate_entity(ir, user_text),
        ):
            blocking.extend(b_issues)
            warnings.extend(w_issues)

        # LLM-based review (optional).
        llm_blocking, llm_warnings = self._llm_review(ir, user_text)
        blocking.extend(llm_blocking)
        warnings.extend(llm_warnings)

        return CriticResult(blocking_issues=blocking, warnings=warnings)

    # ------------------------------------------------------------------
    # Rule-based checks
    # ------------------------------------------------------------------

    def _check_unaccounted_mention(
        self,
        ir: OpenWorldResearchIR,
        user_text: str,
    ) -> tuple[list[dict], list[dict]]:
        """UNACCOUNTED_MENTION -- source coverage must be complete."""
        unaccounted = ir.source_coverage.unaccounted_mentions
        if not unaccounted:
            return [], []
        texts = [m.text for m in unaccounted]
        return [
            self._blocking(
                "UNACCOUNTED_MENTION",
                f"{len(unaccounted)} mention(s) not accounted for: {texts}",
                "source_coverage.mention_inventory",
            )
        ], []

    def _check_unresolved_geometry(
        self,
        ir: OpenWorldResearchIR,
        user_text: str,
    ) -> tuple[list[dict], list[dict]]:
        """UNRESOLVED_GEOMETRY -- every entity must be resolved."""
        issues: list[dict] = []
        for ent in ir.geometry_entities:
            if ent.representation_status in ("needs_clarification", "unsupported"):
                issues.append(
                    self._blocking(
                        "UNRESOLVED_GEOMETRY",
                        (
                            f"Entity '{ent.entity_id}' (raw_name='{ent.raw_name}') "
                            f"has representation_status="
                            f"'{ent.representation_status}'"
                        ),
                        f"geometry_entities[{ent.entity_id}].representation_status",
                    )
                )
        return issues, []

    def _check_geometry_type_mismatch(
        self,
        ir: OpenWorldResearchIR,
        user_text: str,
    ) -> tuple[list[dict], list[dict]]:
        """GEOMETRY_TYPE_MISMATCH -- semantic_shape must not contradict user text.

        Two signals are used:
          1. The entity ``raw_name`` -- if it contains a shape keyword that
             belongs to a *different* group than the entity's
             ``semantic_shape``, that is a direct contradiction.
          2. The overall ``user_text`` -- if the user explicitly mentioned a
             shape group that no entity covers, while an entity carries a
             different (known) shape group, that indicates a silent
             substitution (e.g. "三角形" replaced by ``cosine_bell``).
        """
        issues: list[dict] = []
        groups_in_text = self._shape_groups_in_text(user_text)

        entity_groups: dict[str, str | None] = {}
        for ent in ir.geometry_entities:
            entity_groups[ent.entity_id] = self._entity_shape_group(ent)
        covered_groups = {g for g in entity_groups.values() if g is not None}

        for ent in ir.geometry_entities:
            group = entity_groups[ent.entity_id]
            if group is None:
                # Unknown / unrecognised shape -- cannot contradict.
                continue

            raw_groups = self._shape_groups_in_text(ent.raw_name)
            if raw_groups and any(rg != group for rg in raw_groups):
                wrong = next(rg for rg in raw_groups if rg != group)
                issues.append(
                    self._blocking(
                        "GEOMETRY_TYPE_MISMATCH",
                        (
                            f"Entity '{ent.entity_id}' raw_name='{ent.raw_name}' "
                            f"implies shape '{wrong}', but semantic_shape="
                            f"'{ent.semantic_shape}' (group '{group}')"
                        ),
                        f"geometry_entities[{ent.entity_id}].semantic_shape",
                    )
                )
                continue

            # raw_name did not contradict; check the broader user text for a
            # silent substitution when the raw_name carried no shape hint.
            if not raw_groups and groups_in_text and group not in groups_in_text:
                unaccounted = groups_in_text - covered_groups
                if unaccounted:
                    issues.append(
                        self._blocking(
                            "GEOMETRY_TYPE_MISMATCH",
                            (
                                f"Entity '{ent.entity_id}' has semantic_shape="
                                f"'{ent.semantic_shape}' (group '{group}'), which "
                                f"was not mentioned by the user. User mentioned "
                                f"shape(s): {sorted(unaccounted)}"
                            ),
                            f"geometry_entities[{ent.entity_id}].semantic_shape",
                        )
                    )
        return issues, []

    def _check_spatial_relation_violation(
        self,
        ir: OpenWorldResearchIR,
        user_text: str,
    ) -> tuple[list[dict], list[dict]]:
        """SPATIAL_RELATION_VIOLATION -- relation positions must be consistent.

        Supports relations declared both in ``ir.spatial_relations`` and as
        free-form strings in ``GeometryEntity.relations`` (e.g.
        ``"centered_under:cylinder_1"``).
        """
        issues: list[dict] = []
        relations = self._collect_relations(ir)

        for subject_id, rtype, target_id, params in relations:
            rtype_l = (rtype or "").strip().lower()
            subj = ir.get_entity(subject_id) if subject_id else None
            tgt = ir.get_entity(target_id) if target_id else None
            if subj is None or tgt is None:
                continue

            if rtype_l in (
                "centered_under",
                "centered_below",
                "below_center",
                "directly_below",
                "under",
            ):
                sx = self._entity_coord(subj, _X_KEYS)
                tx = self._entity_coord(tgt, _X_KEYS)
                if (
                    sx is not None
                    and tx is not None
                    and abs(sx - tx) > POSITION_TOLERANCE
                ):
                    issues.append(
                        self._blocking(
                            "SPATIAL_RELATION_VIOLATION",
                            (
                                f"Relation '{rtype}' requires '{subject_id}' to be "
                                f"centered under '{target_id}', but center_x differs: "
                                f"{sx} vs {tx}"
                            ),
                            f"spatial_relations[{subject_id}->{target_id}].centered_under",
                        )
                    )

            elif rtype_l in (
                "concentric",
                "concentric_with",
                "coaxial",
                "centered_on",
                "centered",
                "centered_with",
            ):
                sc = self._entity_center(subj)
                tc = self._entity_center(tgt)
                if self._centers_mismatch(sc, tc):
                    issues.append(
                        self._blocking(
                            "SPATIAL_RELATION_VIOLATION",
                            (
                                f"Relation '{rtype}' requires '{subject_id}' to be "
                                f"concentric with '{target_id}', but centres differ: "
                                f"{self._fmt_center(sc)} vs {self._fmt_center(tc)}"
                            ),
                            f"spatial_relations[{subject_id}->{target_id}].concentric",
                        )
                    )

            elif rtype_l in (
                "distance_from",
                "distance_to",
                "offset_from",
                "offset_to",
                "gap_from",
                "separation_from",
            ):
                sc = self._entity_center(subj)
                tc = self._entity_center(tgt)
                dist: float | None = None
                for dk in ("distance", "offset", "gap", "separation"):
                    dist = self._param_float(params, dk)
                    if dist is not None:
                        break
                if dist is not None:
                    diffs = [
                        (a - b)
                        for a, b in zip(sc, tc)
                        if a is not None and b is not None
                    ]
                    if len(diffs) >= 2:
                        actual = math.sqrt(sum(d * d for d in diffs))
                        if abs(actual - dist) > POSITION_TOLERANCE:
                            issues.append(
                                self._blocking(
                                    "SPATIAL_RELATION_VIOLATION",
                                    (
                                        f"Relation '{rtype}' requires distance {dist} "
                                        f"between '{subject_id}' and '{target_id}', "
                                        f"but actual distance is {actual:.6f}"
                                    ),
                                    f"spatial_relations[{subject_id}->{target_id}].distance_from",
                                )
                            )
        return issues, []

    def _check_missing_boundary(
        self,
        ir: OpenWorldResearchIR,
        user_text: str,
    ) -> tuple[list[dict], list[dict]]:
        """MISSING_BOUNDARY -- a 2D case needs at least four boundaries."""
        blocking: list[dict] = []
        warnings: list[dict] = []
        dim = self._effective_dimensionality(ir)
        n = len(ir.boundaries)

        if dim == "2D" and n < 4:
            blocking.append(
                self._blocking(
                    "MISSING_BOUNDARY",
                    f"2D case requires at least 4 boundaries, but only {n} defined",
                    "boundaries",
                )
            )
        elif dim == "3D" and n < 6:
            warnings.append(
                self._warning(
                    "MISSING_BOUNDARY",
                    f"3D case typically requires 6 boundaries, but only {n} defined",
                    "boundaries",
                )
            )
        elif dim == "axisymmetric" and n < 4:
            warnings.append(
                self._warning(
                    "MISSING_BOUNDARY",
                    f"Axisymmetric case requires at least 4 boundaries, "
                    f"but only {n} defined",
                    "boundaries",
                )
            )
        return blocking, warnings

    def _check_duplicate_entity(
        self,
        ir: OpenWorldResearchIR,
        user_text: str,
    ) -> tuple[list[dict], list[dict]]:
        """DUPLICATE_ENTITY -- no two entities may share the same centre."""
        issues: list[dict] = []
        ents = ir.geometry_entities
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                a, b = ents[i], ents[j]
                ca = self._entity_center(a)
                cb = self._entity_center(b)
                if self._centers_match(ca, cb):
                    issues.append(
                        self._blocking(
                            "DUPLICATE_ENTITY",
                            (
                                f"Entities '{a.entity_id}' and '{b.entity_id}' share "
                                f"the same centre {self._fmt_center(ca)} within "
                                f"tolerance {POSITION_TOLERANCE}"
                            ),
                            f"geometry_entities[{a.entity_id}];"
                            f"geometry_entities[{b.entity_id}]",
                        )
                    )
        return issues, []

    # ------------------------------------------------------------------
    # LLM-based review
    # ------------------------------------------------------------------

    def _llm_review(
        self,
        ir: OpenWorldResearchIR,
        user_text: str,
    ) -> tuple[list[dict], list[dict]]:
        """Call the LLM (if available) and merge its issues.

        Returns ``(blocking, warnings)``.  Any failure is logged and an empty
        result is returned so that rule-based checks remain authoritative.
        """
        if self._llm_client is None:
            return [], []

        system_prompt = self._load_system_prompt()
        payload = {
            "user_text": user_text,
            "research_ir": ir.model_dump(mode="json"),
            "coverage_ratio": ir.source_coverage.coverage_ratio,
        }
        user_message = (
            f"## 用户原文\n{user_text}\n\n"
            f"## 当前 Research IR\n```json\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
            f"请执行语义忠实性审查并按指定JSON格式输出。"
        )
        output_schema = {
            "type": "object",
            "properties": {
                "passed": {"type": "boolean"},
                "blocking_issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue_type": {"type": "string"},
                            "source_span": {"type": "string"},
                            "current_value": {},
                            "expected_semantics": {"type": "string"},
                            "recommended_action": {"type": "string"},
                        },
                    },
                },
                "warnings": {"type": "array"},
                "coverage_ratio": {"type": "number"},
            },
            "required": ["passed", "blocking_issues"],
        }

        try:
            result = self._llm_client.call(
                purpose="semantic_critic",
                prompt_name=SEMANTIC_CRITIC_PROMPT_NAME,
                prompt_version=self._prompt_version(),
                system_prompt=system_prompt,
                user_message=user_message,
                output_schema=output_schema,
            )
        except Exception as exc:  # noqa: BLE001 - LLM failures are non-fatal
            logger.error("LLM semantic_critic call failed: %s", exc)
            return [], []

        parsed: Any = None
        record: Any = None
        if isinstance(result, tuple) and len(result) >= 1:
            parsed = result[0]
            record = result[1] if len(result) >= 2 else None
        elif isinstance(result, dict):
            parsed = result

        if parsed is None:
            return [], []
        if record is not None and not getattr(record, "success", True):
            logger.warning(
                "LLM semantic_critic record reports failure; skipping LLM issues"
            )
            return [], []

        return self._parse_llm_issues(parsed)

    def _parse_llm_issues(self, parsed: Any) -> tuple[list[dict], list[dict]]:
        """Convert the LLM JSON output into critic issue dictionaries."""
        blocking: list[dict] = []
        warnings: list[dict] = []

        if not isinstance(parsed, dict):
            return [], []

        raw_blocking = parsed.get("blocking_issues") or []
        if isinstance(raw_blocking, list):
            for item in raw_blocking:
                if not isinstance(item, dict):
                    continue
                code = "LLM_" + str(item.get("issue_type", "review")).upper()
                message = (
                    item.get("expected_semantics")
                    or item.get("issue_type")
                    or "LLM detected issue"
                )
                field_path = item.get("source_span") or ""
                blocking.append(
                    {
                        "code": code,
                        "message": str(message),
                        "field_path": str(field_path),
                        "severity": "blocking",
                        "source": "llm",
                        "detail": item,
                    }
                )

        raw_warnings = parsed.get("warnings") or []
        if isinstance(raw_warnings, list):
            for item in raw_warnings:
                if isinstance(item, dict):
                    code = "LLM_" + str(item.get("issue_type", "warning")).upper()
                    message = (
                        item.get("expected_semantics")
                        or item.get("message")
                        or "LLM warning"
                    )
                    field_path = (
                        item.get("source_span") or item.get("field_path") or ""
                    )
                    warnings.append(
                        {
                            "code": code,
                            "message": str(message),
                            "field_path": str(field_path),
                            "severity": "warning",
                            "source": "llm",
                            "detail": item,
                        }
                    )
                elif isinstance(item, str):
                    warnings.append(
                        {
                            "code": "LLM_WARNING",
                            "message": item,
                            "field_path": "",
                            "severity": "warning",
                            "source": "llm",
                        }
                    )
        return blocking, warnings

    # ------------------------------------------------------------------
    # Geometry / parameter helpers
    # ------------------------------------------------------------------

    def _collect_relations(
        self,
        ir: OpenWorldResearchIR,
    ) -> list[tuple[str, str, str | None, dict]]:
        """Collect spatial relations from both the IR list and entity strings.

        Returns a de-duplicated list of ``(subject_id, relation_type,
        target_id, parameters)`` tuples.
        """
        seen: set[tuple[str, str, str]] = set()
        relations: list[tuple[str, str, str | None, dict]] = []

        for sr in ir.spatial_relations:
            key = (
                sr.subject_entity or "",
                (sr.relation_type or "").strip().lower(),
                sr.target_entity or "",
            )
            if key in seen:
                continue
            seen.add(key)
            relations.append(
                (sr.subject_entity, sr.relation_type, sr.target_entity, sr.parameters)
            )

        for ent in ir.geometry_entities:
            for rstr in ent.relations:
                rtype, target = self._parse_relation_string(rstr)
                key = (
                    ent.entity_id,
                    (rtype or "").strip().lower(),
                    target or "",
                )
                if key in seen:
                    continue
                seen.add(key)
                relations.append((ent.entity_id, rtype, target, {}))

        return relations

    @staticmethod
    def _parse_relation_string(rel: str) -> tuple[str, str | None]:
        """Parse a relation string such as ``"centered_under:cylinder_1"``."""
        if ":" in rel:
            rtype, target = rel.split(":", 1)
            return rtype.strip(), target.strip() or None
        return rel.strip(), None

    def _entity_shape_group(self, entity: GeometryEntity) -> str | None:
        """Return the canonical shape group for an entity, or ``None``."""
        candidates: list[str] = []
        if entity.semantic_shape:
            candidates.append(entity.semantic_shape)
        if entity.representation and entity.representation.subtype:
            candidates.append(entity.representation.subtype)
        if entity.representation and entity.representation.type:
            candidates.append(entity.representation.type)

        # Exact-key lookup first.
        for token in candidates:
            group = _SHAPE_KEYWORDS.get(token.lower())
            if group:
                return group
        # Fallback: scan the token text for any known keyword.
        for token in candidates:
            groups = self._shape_groups_in_text(token)
            if groups:
                return next(iter(groups))
        return None

    def _shape_groups_in_text(self, text: str | None) -> set[str]:
        """Return the set of shape groups whose keywords appear in *text*.

        Longer keywords are matched first and consume their character span so
        that compound terms (e.g. ``圆柱`` / cylinder) are not mis-tokenised
        as shorter substrings (e.g. ``圆`` / circle).
        """
        if not text:
            return set()
        lower = text.lower()
        consumed = [False] * len(lower)
        found: set[str] = set()

        for kw in sorted(_SHAPE_KEYWORDS, key=len, reverse=True):
            kw_l = kw.lower()
            if not kw_l:
                continue
            start = 0
            while True:
                idx = lower.find(kw_l, start)
                if idx == -1:
                    break
                span = consumed[idx : idx + len(kw_l)]
                if not any(span):
                    found.add(_SHAPE_KEYWORDS[kw])
                    for k in range(idx, idx + len(kw_l)):
                        consumed[k] = True
                start = idx + 1
        return found

    def _entity_center(
        self,
        entity: GeometryEntity,
    ) -> tuple[float | None, float | None, float | None]:
        """Return the ``(x, y, z)`` centre of an entity from its parameters."""
        return (
            self._entity_coord(entity, _X_KEYS),
            self._entity_coord(entity, _Y_KEYS),
            self._entity_coord(entity, _Z_KEYS),
        )

    def _entity_coord(
        self,
        entity: GeometryEntity,
        keys: tuple[str, ...],
    ) -> float | None:
        """Return the first available numeric coordinate from *keys*."""
        for key in keys:
            value = self._param_float(entity.parameters, key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _param_float(params: Any, key: str) -> float | None:
        """Extract a float from a ``ParameterValue``-valued mapping."""
        if not params:
            return None
        pv = params.get(key) if hasattr(params, "get") else None
        if pv is None:
            return None
        value = getattr(pv, "value", None)
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _centers_match(
        self,
        ca: tuple[float | None, float | None, float | None],
        cb: tuple[float | None, float | None, float | None],
    ) -> bool:
        """Return ``True`` when two centres coincide (x and y required)."""
        ax, ay, az = ca
        bx, by, bz = cb
        if ax is None or ay is None or bx is None or by is None:
            return False
        if abs(ax - bx) > POSITION_TOLERANCE:
            return False
        if abs(ay - by) > POSITION_TOLERANCE:
            return False
        if az is not None and bz is not None and abs(az - bz) > POSITION_TOLERANCE:
            return False
        return True

    def _centers_mismatch(
        self,
        ca: tuple[float | None, float | None, float | None],
        cb: tuple[float | None, float | None, float | None],
    ) -> bool:
        """Return ``True`` when any comparable axis differs beyond tolerance."""
        for a, b in zip(ca, cb):
            if a is not None and b is not None and abs(a - b) > POSITION_TOLERANCE:
                return True
        return False

    @staticmethod
    def _fmt_center(
        center: tuple[float | None, float | None, float | None],
    ) -> str:
        return "(" + ", ".join(
            "None" if v is None else f"{v:.6g}" for v in center
        ) + ")"

    def _effective_dimensionality(self, ir: OpenWorldResearchIR) -> str:
        dim = ir.dimensionality
        if dim in ("2D", "3D", "axisymmetric"):
            return dim
        if ir.domain is not None and ir.domain.dimensionality in (
            "2D",
            "3D",
            "axisymmetric",
        ):
            return ir.domain.dimensionality
        return "unknown"

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        """Load the semantic_critic prompt, falling back if unavailable."""
        try:
            return self._prompt_registry.load(SEMANTIC_CRITIC_PROMPT_NAME)
        except FileNotFoundError:
            logger.warning(
                "Prompt '%s' not found; using built-in fallback",
                SEMANTIC_CRITIC_PROMPT_NAME,
            )
            return _FALLBACK_SYSTEM_PROMPT
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load prompt '%s' (%s); using built-in fallback",
                SEMANTIC_CRITIC_PROMPT_NAME,
                exc,
            )
            return _FALLBACK_SYSTEM_PROMPT

    def _prompt_version(self) -> str:
        try:
            return self._prompt_registry.get_version(SEMANTIC_CRITIC_PROMPT_NAME)
        except Exception:  # noqa: BLE001
            return "0.0.0"

    # ------------------------------------------------------------------
    # Issue constructors
    # ------------------------------------------------------------------

    @staticmethod
    def _blocking(code: str, message: str, field_path: str) -> dict:
        return {
            "code": code,
            "message": message,
            "field_path": field_path,
            "severity": "blocking",
        }

    @staticmethod
    def _warning(code: str, message: str, field_path: str) -> dict:
        return {
            "code": code,
            "message": message,
            "field_path": field_path,
            "severity": "warning",
        }


__all__ = ["CriticResult", "SemanticCritic", "POSITION_TOLERANCE"]
