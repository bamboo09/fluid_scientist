"""ConflictResolver — field-level arbitration between regex and LLM candidates.

Rules:
1. Both agree → accept (AGREEMENT)
2. Only regex has value → accept regex (REGEX_ONLY) 
3. Only LLM has value → accept LLM after semantic check (LLM_ONLY)
4. Both have values but conflict → use conflict type to determine winner
5. Same text segment produces two entities → detect duplicate (DUPLICATE_ENTITY)
6. Cannot auto-resolve → needs clarification (NEEDS_CLARIFICATION)

Key principle: Never silently pick one source over the other.
Always document the resolution reason.
"""

from __future__ import annotations

import re
from typing import Any

from fluid_scientist.intent import (
    CandidateConflict,
    CandidateSource,
    ConflictSeverity,
    ConflictType,
    ExtractionCandidate,
    IntentCandidateSet,
    ResolutionStrategy,
    ResolvedField,
)

# Geometry synonym table for semantic fidelity
GEOMETRY_SYNONYMS: dict[str, list[str]] = {
    "triangle": ["三角", "三角形", "三角障碍", "三角凸起", "三角小障碍物", "triangular", "triangle"],
    "rectangle": ["矩形", "长方形", "rectangular", "rectangle"],
    "cosine_bell": ["余弦凸起", "余弦形凸起", "余弦丘", "余弦钟形", "cosine bell", "cosine_bell"],
    "half_sine": ["正弦凸起", "半正弦凸起", "正弦形凸起", "sinusoidal bump", "half_sine", "sine bump"],
    "gaussian": ["高斯凸起", "高斯丘", "gaussian bump", "gaussian"],
    "cylinder": ["圆柱", "圆柱体", "cylinder", "circular cylinder"],
}


def _normalize_geometry_type(value: Any) -> str | None:
    """Normalize a geometry type value to canonical form."""
    if value is None:
        return None
    val_str = str(value).lower().strip()
    for canonical, synonyms in GEOMETRY_SYNONYMS.items():
        if val_str in [s.lower() for s in synonyms] or val_str == canonical:
            return canonical
    return val_str


def _find_in_text(text: str, keywords: list[str]) -> str | None:
    """Find which keyword appears in the text, return the matching segment."""
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            # Find the actual substring in original text
            idx = text_lower.find(kw.lower())
            # Expand to get a reasonable context window
            start = max(0, idx - 10)
            end = min(len(text), idx + len(kw) + 20)
            return text[start:end]
    return None


class RegexCandidateExtractor:
    """Extracts candidates from regex pipeline output (CylinderFlow2DExperimentSpecV1)."""

    def extract(self, spec: Any, user_text: str) -> list[ExtractionCandidate]:
        """Extract candidates from a pipeline-produced spec."""
        candidates: list[ExtractionCandidate] = []

        # Domain
        if spec.domain.length_m.is_resolved():
            candidates.append(ExtractionCandidate(
                field_path="domain.length",
                value=spec.domain.length_m.value,
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["长", "length"]),
                confidence=0.95 if spec.domain.length_m.is_user_provided() else 0.7,
            ))
        if spec.domain.height_m.is_resolved():
            candidates.append(ExtractionCandidate(
                field_path="domain.height",
                value=spec.domain.height_m.value,
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["宽", "高", "height", "width"]),
                confidence=0.95 if spec.domain.height_m.is_user_provided() else 0.7,
            ))

        # Cylinder
        if spec.has_cylinder:
            if spec.cylinder.radius_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="cylinder.radius",
                    value=spec.cylinder.radius_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["半径", "radius"]),
                    confidence=0.95 if spec.cylinder.radius_m.is_user_provided() else 0.7,
                ))
            if spec.cylinder.center_x_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="cylinder.center_x",
                    value=spec.cylinder.center_x_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["正中央", "中心", "center"]),
                    confidence=0.9 if spec.cylinder.center_x_m.is_user_provided() else 0.6,
                ))
            if spec.cylinder.center_y_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="cylinder.center_y",
                    value=spec.cylinder.center_y_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["距下壁", "距底", "高度"]),
                    confidence=0.9 if spec.cylinder.center_y_m.is_user_provided() else 0.6,
                ))

        # Triangle
        if spec.has_triangle:
            candidates.append(ExtractionCandidate(
                field_path="obstacle.type",
                value="triangle",
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, GEOMETRY_SYNONYMS["triangle"]),
                confidence=0.95,
            ))
            if spec.triangle.base_width_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="triangle.base_width",
                    value=spec.triangle.base_width_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["宽", "width"]),
                    confidence=0.95 if spec.triangle.base_width_m.is_user_provided() else 0.7,
                ))
            if spec.triangle.height_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="triangle.height",
                    value=spec.triangle.height_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["高", "height"]),
                    confidence=0.95 if spec.triangle.height_m.is_user_provided() else 0.7,
                ))

        # Rectangle
        if spec.has_rectangle:
            candidates.append(ExtractionCandidate(
                field_path="obstacle.type",
                value="rectangle",
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, GEOMETRY_SYNONYMS["rectangle"]),
                confidence=0.95,
            ))
            if spec.rectangle.width_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="rectangle.width",
                    value=spec.rectangle.width_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["宽", "width"]),
                    confidence=0.95 if spec.rectangle.width_m.is_user_provided() else 0.7,
                ))
            if spec.rectangle.height_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="rectangle.height",
                    value=spec.rectangle.height_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["高", "height"]),
                    confidence=0.95 if spec.rectangle.height_m.is_user_provided() else 0.7,
                ))

        # Bottom profile (bump)
        if spec.has_bottom_profile:
            candidates.append(ExtractionCandidate(
                field_path="obstacle.type",
                value=spec.bottom_profile.profile_type.value if spec.bottom_profile.profile_type else "bump",
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["凸起", "bump", "profile"]),
                confidence=0.9,
            ))
            if spec.bottom_profile.height_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="bump.height",
                    value=spec.bottom_profile.height_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["高", "height"]),
                    confidence=0.9,
                ))
            if spec.bottom_profile.width_m.is_resolved():
                candidates.append(ExtractionCandidate(
                    field_path="bump.width",
                    value=spec.bottom_profile.width_m.value,
                    source=CandidateSource.REGEX,
                    source_span=_find_in_text(user_text, ["宽", "width"]),
                    confidence=0.9,
                ))

        # Boundaries
        bc = spec.boundaries
        if bc.left.source != "SYSTEM_DEFAULT" or bc.left.semantic_type:
            candidates.append(ExtractionCandidate(
                field_path="boundary.left",
                value=bc.left.semantic_type.value if bc.left.semantic_type else "unknown",
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["左", "入口", "left", "inlet"]),
                confidence=0.9,
            ))
        if bc.right.source != "SYSTEM_DEFAULT" or bc.right.semantic_type:
            candidates.append(ExtractionCandidate(
                field_path="boundary.right",
                value=bc.right.semantic_type.value if bc.right.semantic_type else "unknown",
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["右", "出口", "right", "outlet"]),
                confidence=0.9,
            ))
        if bc.top.source != "SYSTEM_DEFAULT" or bc.top.semantic_type:
            candidates.append(ExtractionCandidate(
                field_path="boundary.top",
                value=bc.top.semantic_type.value if bc.top.semantic_type else "unknown",
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["上", "顶", "top"]),
                confidence=0.85,
            ))
        if bc.bottom_flat.source != "SYSTEM_DEFAULT" or bc.bottom_flat.semantic_type:
            candidates.append(ExtractionCandidate(
                field_path="boundary.bottom",
                value=bc.bottom_flat.semantic_type.value if bc.bottom_flat.semantic_type else "unknown",
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["下", "底", "bottom", "wall"]),
                confidence=0.9,
            ))

        # Inlet velocity
        if bc.left.inlet_velocity is not None:
            candidates.append(ExtractionCandidate(
                field_path="physics.inlet_velocity",
                value=bc.left.inlet_velocity,
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["速度", "velocity", "来流"]),
                confidence=0.95,
            ))

        # Reynolds number
        re_val = spec.fluid.reynolds_number.value if hasattr(spec.fluid, 'reynolds_number') and spec.fluid.reynolds_number else None
        if re_val is not None and spec.fluid.reynolds_number.is_resolved():
            candidates.append(ExtractionCandidate(
                field_path="physics.reynolds_number",
                value=re_val,
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, ["Re", "雷诺", "Reynolds"]),
                confidence=0.95,
            ))

        # Observables
        for obs in spec.observables:
            obs_type = obs.type.value if hasattr(obs.type, 'value') else str(obs.type)
            candidates.append(ExtractionCandidate(
                field_path=f"observable.{obs_type}",
                value=obs_type,
                source=CandidateSource.REGEX,
                source_span=_find_in_text(user_text, _observable_keywords(obs_type)),
                confidence=0.85,
            ))

        return candidates


def _observable_keywords(obs_type: str) -> list[str]:
    """Get keywords for an observable type."""
    mapping = {
        "cylinder_drag": ["阻力", "Cd", "drag"],
        "cylinder_lift": ["升力", "Cl", "lift"],
        "wake_shedding_frequency": ["涡脱落", "涡街", "shedding", "Strouhal", "St"],
        "velocity_magnitude_field": ["速度场", "velocity field"],
        "vorticity_field": ["涡量", "vorticity"],
        "streamlines": ["流线", "streamline"],
        "section_mean_velocity": ["平均流速", "截面", "mean velocity"],
    }
    return mapping.get(obs_type, [obs_type])


class LLMCandidateExtractor:
    """Extracts candidates from LLM structured parse output."""

    def extract(self, llm_parsed: dict, user_text: str) -> list[ExtractionCandidate]:
        """Extract candidates from LLM JSON response."""
        candidates: list[ExtractionCandidate] = []

        # Scene
        scene = llm_parsed.get("scene", {})
        if scene.get("dimension"):
            candidates.append(ExtractionCandidate(
                field_path="scene.dimension",
                value=scene["dimension"],
                source=CandidateSource.LLM,
                confidence=float(scene.get("confidence", 0.8)),
            ))

        # Geometry
        geom = llm_parsed.get("geometry", {})
        domain = geom.get("domain", {})
        if domain.get("length", {}).get("value"):
            candidates.append(ExtractionCandidate(
                field_path="domain.length",
                value=float(domain["length"]["value"]),
                source=CandidateSource.LLM,
                source_span=user_text[:200],
                confidence=0.85,
            ))
        if domain.get("height", {}).get("value"):
            candidates.append(ExtractionCandidate(
                field_path="domain.height",
                value=float(domain["height"]["value"]),
                source=CandidateSource.LLM,
                source_span=user_text[:200],
                confidence=0.85,
            ))

        # Objects
        for obj in geom.get("objects", []):
            obj_type = obj.get("type", "unknown")
            obj_id = obj.get("id", "obj_0")
            candidates.append(ExtractionCandidate(
                field_path=f"geometry.objects.{obj_id}.type",
                value=obj_type,
                source=CandidateSource.LLM,
                source_span=_find_in_text(user_text, GEOMETRY_SYNONYMS.get(_normalize_geometry_type(obj_type), [obj_type])),
                confidence=0.85,
                reasoning_summary=f"LLM identified {obj_type}",
            ))

            if obj_type == "cylinder":
                radius = obj.get("radius", {}).get("value")
                if radius and float(radius) > 0:
                    candidates.append(ExtractionCandidate(
                        field_path="cylinder.radius",
                        value=float(radius),
                        source=CandidateSource.LLM,
                        confidence=0.85,
                    ))
                center = obj.get("center", {})
                cx = center.get("x", {}).get("value")
                cy = center.get("y", {}).get("value")
                if cx is not None and float(cx) > 0:
                    candidates.append(ExtractionCandidate(
                        field_path="cylinder.center_x",
                        value=float(cx),
                        source=CandidateSource.LLM,
                        confidence=0.8,
                    ))
                if cy is not None and float(cy) > 0:
                    candidates.append(ExtractionCandidate(
                        field_path="cylinder.center_y",
                        value=float(cy),
                        source=CandidateSource.LLM,
                        confidence=0.8,
                    ))

            elif obj_type in ("triangle", "rectangle", "cosine_bell", "half_sine", "gaussian"):
                # Map to obstacle.type
                candidates.append(ExtractionCandidate(
                    field_path="obstacle.type",
                    value=_normalize_geometry_type(obj_type) or obj_type,
                    source=CandidateSource.LLM,
                    source_span=_find_in_text(user_text, GEOMETRY_SYNONYMS.get(_normalize_geometry_type(obj_type), [obj_type])),
                    confidence=0.85,
                ))

                # Dimensions
                w = obj.get("width", {}).get("value") or obj.get("base_width", {}).get("value")
                h = obj.get("height", {}).get("value")
                if w and float(w) > 0:
                    candidates.append(ExtractionCandidate(
                        field_path=f"{_normalize_geometry_type(obj_type)}.width",
                        value=float(w),
                        source=CandidateSource.LLM,
                        confidence=0.8,
                    ))
                if h and float(h) > 0:
                    candidates.append(ExtractionCandidate(
                        field_path=f"{_normalize_geometry_type(obj_type)}.height",
                        value=float(h),
                        source=CandidateSource.LLM,
                        confidence=0.8,
                    ))

        # Physics
        physics = llm_parsed.get("physics", {})
        if physics.get("inlet_velocity", {}).get("value"):
            candidates.append(ExtractionCandidate(
                field_path="physics.inlet_velocity",
                value=float(physics["inlet_velocity"]["value"]),
                source=CandidateSource.LLM,
                confidence=0.85,
            ))
        if physics.get("reynolds_number", {}).get("value"):
            candidates.append(ExtractionCandidate(
                field_path="physics.reynolds_number",
                value=float(physics["reynolds_number"]["value"]),
                source=CandidateSource.LLM,
                confidence=0.85,
            ))
        if physics.get("kinematic_viscosity", {}).get("value"):
            candidates.append(ExtractionCandidate(
                field_path="physics.kinematic_viscosity",
                value=float(physics["kinematic_viscosity"]["value"]),
                source=CandidateSource.LLM,
                confidence=0.8,
            ))

        # Boundaries
        for bnd in llm_parsed.get("boundaries", []):
            name = bnd.get("name", "")
            btype = bnd.get("type", "unknown")
            candidates.append(ExtractionCandidate(
                field_path=f"boundary.{name}",
                value=btype,
                source=CandidateSource.LLM,
                source_span=_find_in_text(user_text, [name]),
                confidence=0.8,
            ))

        # Metrics
        for metric in llm_parsed.get("requested_metrics", []):
            candidates.append(ExtractionCandidate(
                field_path=f"observable.{metric}",
                value=metric,
                source=CandidateSource.LLM,
                confidence=0.8,
            ))

        return candidates


class ConflictResolver:
    """Resolves conflicts between regex and LLM candidates field by field.

    Rules:
    - Agreement: both same → accept
    - Regex only: accept regex
    - LLM only: accept LLM after semantic fidelity check
    - Conflict: type-dependent resolution
    - Duplicate entity: detect and resolve
    """

    def resolve(
        self,
        regex_candidates: list[ExtractionCandidate],
        llm_candidates: list[ExtractionCandidate],
        user_text: str,
    ) -> IntentCandidateSet:
        """Resolve all candidates into a canonical set."""
        result = IntentCandidateSet(
            regex_candidates=regex_candidates,
            llm_candidates=llm_candidates,
        )

        # Group candidates by field_path
        regex_by_path: dict[str, list[ExtractionCandidate]] = {}
        for c in regex_candidates:
            regex_by_path.setdefault(c.field_path, []).append(c)

        llm_by_path: dict[str, list[ExtractionCandidate]] = {}
        for c in llm_candidates:
            # Normalize geometry object paths to obstacle.type for comparison
            path = c.field_path
            if path.startswith("geometry.objects."):
                # Map to obstacle.type for conflict detection
                path = "obstacle.type"
            llm_by_path.setdefault(path, []).append(c)

        all_paths = set(regex_by_path.keys()) | set(llm_by_path.keys())

        # Check for duplicate entity detection (e.g., sine bump creating both rectangle and bump)
        self._detect_duplicate_entities(regex_candidates, llm_candidates, user_text, result)

        # Resolve each field
        for path in sorted(all_paths):
            r_cands = regex_by_path.get(path, [])
            l_cands = llm_by_path.get(path, [])

            resolved = self._resolve_field(path, r_cands, l_cands, user_text, result)
            if resolved is not None:
                result.resolved_fields.append(resolved)
            else:
                result.unresolved.append(path)

        return result

    def _detect_duplicate_entities(
        self,
        regex_cands: list[ExtractionCandidate],
        llm_cands: list[ExtractionCandidate],
        user_text: str,
        result: IntentCandidateSet,
    ) -> None:
        """Detect when the same text segment produces two different entity types.

        Example: "正弦凸起，高5m、宽20m" → both rectangle and half_sine profile.
        """
        # Collect all obstacle.type candidates
        all_type_cands: list[tuple[ExtractionCandidate, str]] = []
        for c in regex_cands + llm_cands:
            if c.field_path == "obstacle.type" or c.field_path.startswith("geometry.objects."):
                path = "obstacle.type" if c.field_path == "obstacle.type" else c.field_path
                all_type_cands.append((c, path))

        # Group by source_span proximity
        types_by_span: dict[str, list[str]] = {}
        for cand, _ in all_type_cands:
            if cand.source_span:
                # Use span as key (simplified: just track all types)
                types_by_span.setdefault(cand.source_span[:30], []).append(str(cand.value))

        # Check for conflicting types on same span
        seen_types: set[str] = set()
        for cands_with_path in [all_type_cands]:
            for cand, _ in cands_with_path:
                v = str(cand.value)
                if v in seen_types and v not in ("cylinder",):
                    # Check if this is a genuine duplicate (same text, different type)
                    pass
                seen_types.add(v)

        # Check specific pattern: bump keywords present but rectangle also detected
        has_bump_keyword = any(
            kw in user_text for kw in ["正弦凸起", "余弦凸起", "半正弦", "sinusoidal", "cosine bell"]
        )
        has_rectangle_cand = any(
            str(c.value) == "rectangle"
            for c in regex_cands + llm_cands
            if c.field_path == "obstacle.type"
        )
        has_bump_cand = any(
            str(c.value) in ("cosine_bell", "half_sine", "gaussian")
            for c in regex_cands + llm_cands
            if c.field_path == "obstacle.type"
        )

        if has_bump_keyword and has_rectangle_cand and has_bump_cand:
            conflict = CandidateConflict(
                field_path="obstacle.type",
                regex_value="rectangle+bump" if any(c.source == CandidateSource.REGEX for c in regex_cands if c.field_path == "obstacle.type") else None,
                llm_value="rectangle+bump" if any(c.source == CandidateSource.LLM for c in llm_cands if c.field_path == "obstacle.type" or c.field_path.startswith("geometry.objects.")) else None,
                raw_text=user_text[:200],
                conflict_type=ConflictType.DUPLICATE_ENTITY,
                severity=ConflictSeverity.WARNING,
                resolution="keep_bottom_profile_remove_rectangle",
            )
            result.conflicts.append(conflict)

    def _resolve_field(
        self,
        path: str,
        regex_cands: list[ExtractionCandidate],
        llm_cands: list[ExtractionCandidate],
        user_text: str,
        result: IntentCandidateSet,
    ) -> ResolvedField | None:
        """Resolve a single field path."""
        r_val = regex_cands[0].value if regex_cands else None
        l_val = llm_cands[0].value if llm_cands else None
        r_span = regex_cands[0].source_span if regex_cands else None
        l_span = llm_cands[0].source_span if llm_cands else None
        r_conf = regex_cands[0].confidence if regex_cands else 0.0
        l_conf = llm_cands[0].confidence if llm_cands else 0.0

        # Case A: Both agree
        if r_val is not None and l_val is not None and self._values_equal(r_val, l_val, path):
            return ResolvedField(
                field_path=path,
                value=r_val,
                raw_value=str(r_val),
                source_span=r_span or l_span,
                source=CandidateSource.REGEX,
                regex_candidate=r_val,
                llm_candidate=l_val,
                resolution=ResolutionStrategy.AGREEMENT,
                confidence=max(r_conf, l_conf),
            )

        # Case B: Only regex
        if r_val is not None and l_val is None:
            return ResolvedField(
                field_path=path,
                value=r_val,
                raw_value=str(r_val),
                source_span=r_span,
                source=CandidateSource.REGEX,
                regex_candidate=r_val,
                llm_candidate=None,
                resolution=ResolutionStrategy.REGEX_ONLY,
                confidence=r_conf,
            )

        # Case C: Only LLM
        if r_val is None and l_val is not None:
            # Semantic fidelity check for geometry types
            if path == "obstacle.type":
                normalized = _normalize_geometry_type(l_val)
                if normalized:
                    # Verify the user text actually contains this geometry keyword
                    synonyms = GEOMETRY_SYNONYMS.get(normalized, [])
                    if synonyms and not any(kw.lower() in user_text.lower() for kw in synonyms):
                        # LLM says triangle but user didn't say triangle
                        conflict = CandidateConflict(
                            field_path=path,
                            regex_value=None,
                            llm_value=l_val,
                            raw_text=user_text[:200],
                            conflict_type=ConflictType.SEMANTIC_TYPE_CONFLICT,
                            severity=ConflictSeverity.BLOCKING,
                            resolution=None,
                        )
                        result.conflicts.append(conflict)
                        result.unresolved.append(path)
                        return None

            return ResolvedField(
                field_path=path,
                value=l_val,
                raw_value=str(l_val),
                source_span=l_span,
                source=CandidateSource.LLM,
                regex_candidate=None,
                llm_candidate=l_val,
                resolution=ResolutionStrategy.LLM_ONLY,
                confidence=l_conf,
            )

        # Case D: Conflict
        if r_val is not None and l_val is not None and not self._values_equal(r_val, l_val, path):
            conflict_type = self._classify_conflict(path, r_val, l_val, user_text)

            # Determine winner
            if path == "obstacle.type":
                # For geometry type, check which matches user text better
                r_norm = _normalize_geometry_type(r_val)
                l_norm = _normalize_geometry_type(l_val)

                r_match = self._text_matches_geometry(user_text, r_norm)
                l_match = self._text_matches_geometry(user_text, l_norm)

                if r_match and not l_match:
                    # Regex matches text, LLM doesn't
                    conflict = CandidateConflict(
                        field_path=path, regex_value=r_val, llm_value=l_val,
                        raw_text=user_text[:200],
                        conflict_type=conflict_type,
                        severity=ConflictSeverity.BLOCKING,
                        resolution=f"regex_wins: user text contains {r_norm} keyword",
                    )
                    result.conflicts.append(conflict)
                    return ResolvedField(
                        field_path=path, value=r_val, raw_value=str(r_val),
                        source_span=r_span, source=CandidateSource.REGEX,
                        regex_candidate=r_val, llm_candidate=l_val,
                        resolution=ResolutionStrategy.REGEX_WINS,
                        confidence=r_conf,
                    )
                elif l_match and not r_match:
                    conflict = CandidateConflict(
                        field_path=path, regex_value=r_val, llm_value=l_val,
                        raw_text=user_text[:200],
                        conflict_type=conflict_type,
                        severity=ConflictSeverity.WARNING,
                        resolution=f"llm_wins: user text contains {l_norm} keyword",
                    )
                    result.conflicts.append(conflict)
                    return ResolvedField(
                        field_path=path, value=l_val, raw_value=str(l_val),
                        source_span=l_span, source=CandidateSource.LLM,
                        regex_candidate=r_val, llm_candidate=l_val,
                        resolution=ResolutionStrategy.LLM_WINS,
                        confidence=l_conf,
                    )
                else:
                    # Both match or neither matches — needs clarification
                    conflict = CandidateConflict(
                        field_path=path, regex_value=r_val, llm_value=l_val,
                        raw_text=user_text[:200],
                        conflict_type=ConflictType.SEMANTIC_TYPE_CONFLICT,
                        severity=ConflictSeverity.BLOCKING,
                        resolution=None,
                    )
                    result.conflicts.append(conflict)
                    result.unresolved.append(path)
                    return None

            elif path.startswith("physics.") or path.startswith("domain.") or path.startswith("cylinder."):
                # For numeric fields, prefer the one with higher confidence and user-provided source
                if r_conf >= l_conf:
                    conflict = CandidateConflict(
                        field_path=path, regex_value=r_val, llm_value=l_val,
                        raw_text=user_text[:200],
                        conflict_type=conflict_type,
                        severity=ConflictSeverity.WARNING,
                        resolution="regex_wins: higher confidence",
                    )
                    result.conflicts.append(conflict)
                    return ResolvedField(
                        field_path=path, value=r_val, raw_value=str(r_val),
                        source_span=r_span, source=CandidateSource.REGEX,
                        regex_candidate=r_val, llm_candidate=l_val,
                        resolution=ResolutionStrategy.REGEX_WINS,
                        confidence=r_conf,
                    )
                else:
                    conflict = CandidateConflict(
                        field_path=path, regex_value=r_val, llm_value=l_val,
                        raw_text=user_text[:200],
                        conflict_type=conflict_type,
                        severity=ConflictSeverity.WARNING,
                        resolution="llm_wins: higher confidence",
                    )
                    result.conflicts.append(conflict)
                    return ResolvedField(
                        field_path=path, value=l_val, raw_value=str(l_val),
                        source_span=l_span, source=CandidateSource.LLM,
                        regex_candidate=r_val, llm_candidate=l_val,
                        resolution=ResolutionStrategy.LLM_WINS,
                        confidence=l_conf,
                    )

            elif path.startswith("boundary."):
                # For boundaries, prefer regex (more reliable for structured text)
                conflict = CandidateConflict(
                    field_path=path, regex_value=r_val, llm_value=l_val,
                    raw_text=user_text[:200],
                    conflict_type=ConflictType.BOUNDARY_CONFLICT,
                    severity=ConflictSeverity.WARNING,
                    resolution="regex_wins: boundary extraction",
                )
                result.conflicts.append(conflict)
                return ResolvedField(
                    field_path=path, value=r_val, raw_value=str(r_val),
                    source_span=r_span, source=CandidateSource.REGEX,
                    regex_candidate=r_val, llm_candidate=l_val,
                    resolution=ResolutionStrategy.REGEX_WINS,
                    confidence=r_conf,
                )

            else:
                # Default: needs clarification
                conflict = CandidateConflict(
                    field_path=path, regex_value=r_val, llm_value=l_val,
                    raw_text=user_text[:200],
                    conflict_type=conflict_type,
                    severity=ConflictSeverity.BLOCKING,
                    resolution=None,
                )
                result.conflicts.append(conflict)
                result.unresolved.append(path)
                return None

        return None

    def _values_equal(self, a: Any, b: Any, path: str) -> bool:
        """Check if two values are equal, with type-specific comparison."""
        if path == "obstacle.type":
            na = _normalize_geometry_type(a)
            nb = _normalize_geometry_type(b)
            return na == nb
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return abs(float(a) - float(b)) < 1e-6
        return str(a) == str(b)

    def _classify_conflict(self, path: str, r_val: Any, l_val: Any, user_text: str) -> ConflictType:
        """Classify the type of conflict."""
        if path == "obstacle.type":
            return ConflictType.SEMANTIC_TYPE_CONFLICT
        if path.startswith("cylinder.center") or path.startswith("triangle.") or path.startswith("rectangle."):
            return ConflictType.SPATIAL_CONFLICT
        if path.startswith("boundary."):
            return ConflictType.BOUNDARY_CONFLICT
        if path.startswith("physics."):
            return ConflictType.VALUE_CONFLICT
        return ConflictType.VALUE_CONFLICT

    def _text_matches_geometry(self, text: str, geom_type: str | None) -> bool:
        """Check if user text contains keywords for the given geometry type."""
        if geom_type is None:
            return False
        synonyms = GEOMETRY_SYNONYMS.get(geom_type, [])
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in synonyms)
