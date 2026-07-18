"""Capability requirement graph for the Case IR.

This module extracts capability requirements from a
:class:`~fluid_scientist.study_spec.models.SimulationStudySpec` dict and
tracks their resolution status against a set of available capabilities.

A *capability requirement* is an atomic statement of the form
``"geometry.triangle_2d"`` or ``"physics.turbulence.LES"`` that the
simulation case needs in order to be fully realised.  The
:class:`CapabilityRequirementGraph` collects all such requirements from
the spec, checks them against a known capability set, and provides
query helpers for missing / unknown requirements.

Typical usage::

    from fluid_scientist.case_ir.capability_requirements import (
        CapabilityRequirementGraph,
    )

    graph = CapabilityRequirementGraph()
    requirements = graph.build_from_spec(spec_dict)
    available = {"geometry.cylinder_2d", "solver.pimpleFoam", ...}
    checked = graph.check_requirements(requirements, available)
    missing = graph.get_missing(checked)
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "AIRFOIL_2D_CAPABILITY_KEY",
    "CapabilityRequirement",
    "CapabilityRequirementGraph",
    "CUSTOM_POLYGON_2D_CAPABILITY_KEY",
    "RequirementStatus",
    "STL_IMPORT_CAPABILITY_KEY",
    "SUPERELLIPSE_2D_CAPABILITY_KEY",
    "detect_airfoil_capability",
    "detect_polygon_capability",
    "detect_stl_import_capability",
    "detect_superellipse_capability",
    "detect_unknown_geometry_capabilities",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

RequirementStatus = Literal[
    "satisfied",
    "missing",
    "unknown",
    "extension_requested",
]

#: Materials considered "standard" and therefore not requiring a capability.
_STANDARD_MATERIALS: set[str] = {"air", "water"}

# ---------------------------------------------------------------------------
# Well-known capability keys for extension-required geometries
# ---------------------------------------------------------------------------

#: Capability key for 2D airfoil geometry (NACA series, custom airfoil).
AIRFOIL_2D_CAPABILITY_KEY = "geometry.airfoil_2d"

#: Capability key for STL file import geometry.
STL_IMPORT_CAPABILITY_KEY = "geometry.stl_import"

#: Capability key for custom polygon geometry (supported via Phase E adapter).
CUSTOM_POLYGON_2D_CAPABILITY_KEY = "geometry.custom_polygon_2d"

#: Capability key for superellipse geometry (requires extension).
SUPERELLIPSE_2D_CAPABILITY_KEY = "geometry.superellipse_2d"


# ---------------------------------------------------------------------------
# CapabilityRequirement model
# ---------------------------------------------------------------------------


class CapabilityRequirement(BaseModel):
    """A single capability requirement extracted from the spec.

    Attributes:
        req_id: Unique identifier (e.g. ``"REQ-001"``).
        capability_key: Dotted capability key
            (e.g. ``"geometry.triangle_2d"``, ``"physics.turbulence.LES"``).
        required_by: What part of the spec requires this capability
            (e.g. ``"user_input"``, ``"physics_model"``, ``"geometry"``).
        status: Current resolution status.
        resolver: Name of the resolver that satisfied this requirement,
            or ``None``.
        resolved_artifact: Path or identifier of the artifact that
            satisfies this requirement, or ``None``.
    """

    req_id: str
    capability_key: str
    required_by: str
    status: RequirementStatus = "unknown"
    resolver: str | None = None
    resolved_artifact: str | None = None
    original_semantics: str | None = None
    extension_proposal: str | None = None


# ---------------------------------------------------------------------------
# CapabilityRequirementGraph
# ---------------------------------------------------------------------------


class CapabilityRequirementGraph:
    """Builds and queries the capability requirement graph.

    The graph is constructed by scanning a
    :class:`~fluid_scientist.study_spec.models.SimulationStudySpec`
    dict and extracting every atomic capability key implied by the
    spec's geometry, physics, numerics, boundaries, observations, and
    material definitions.
    """

    def __init__(self) -> None:
        """Initialise an empty graph.

        Use :meth:`build_from_spec` to populate requirements from a
        spec dict.
        """
        self._requirements: list[CapabilityRequirement] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def requirements(self) -> list[CapabilityRequirement]:
        """Return a copy of the current requirement list."""
        return list(self._requirements)

    # ------------------------------------------------------------------
    # Build from spec
    # ------------------------------------------------------------------

    def build_from_spec(self, spec_dict: dict[str, Any]) -> list[CapabilityRequirement]:
        """Extract all capability requirements from a spec dict.

        Args:
            spec_dict: A serialised
                :class:`~fluid_scientist.study_spec.models.SimulationStudySpec`
                dict.

        Returns:
            A list of :class:`CapabilityRequirement` objects, each with
            ``status="unknown"``.
        """
        requirements: list[CapabilityRequirement] = []
        counter = 0

        def _next_id() -> str:
            nonlocal counter
            counter += 1
            return f"REQ-{counter:03d}"

        # --- Geometry entities ---
        geometry = spec_dict.get("geometry", {})
        entities = geometry.get("entities", {})
        if isinstance(entities, dict):
            for _entity_id, entity in entities.items():
                semantic_type = entity.get("semantic_type", "")
                if semantic_type:
                    requirements.append(CapabilityRequirement(
                        req_id=_next_id(),
                        capability_key=f"geometry.{semantic_type}",
                        required_by="user_input",
                    ))

        # --- Turbulence model ---
        numerics = spec_dict.get("numerics", {})
        turbulence_model = numerics.get("turbulence_model")
        if turbulence_model and turbulence_model != "laminar":
            requirements.append(CapabilityRequirement(
                req_id=_next_id(),
                capability_key=f"physics.turbulence.{turbulence_model}",
                required_by="physics_model",
            ))

        # --- Solver ---
        solver = numerics.get("solver", "")
        if solver:
            requirements.append(CapabilityRequirement(
                req_id=_next_id(),
                capability_key=f"solver.{solver}",
                required_by="numerics",
            ))

        # --- Boundary condition types ---
        boundaries = spec_dict.get("boundaries", {})
        conditions = boundaries.get("conditions", [])
        if isinstance(conditions, list):
            for condition in conditions:
                bc_type = condition.get("bc_type", "")
                if bc_type:
                    requirements.append(CapabilityRequirement(
                        req_id=_next_id(),
                        capability_key=f"boundary.{bc_type}",
                        required_by="boundary",
                    ))

        # --- Observation metrics ---
        observations = spec_dict.get("observations", {})
        targets = observations.get("targets", [])
        if isinstance(targets, list):
            for target in targets:
                metric = target.get("metric", "")
                if metric:
                    requirements.append(CapabilityRequirement(
                        req_id=_next_id(),
                        capability_key=f"observation.{metric}",
                        required_by="observation",
                    ))

        # --- Material ---
        physics = spec_dict.get("physics", {})
        material = physics.get("material", {})
        if isinstance(material, dict):
            material_name = material.get("value", "")
        else:
            material_name = str(material)
        if material_name and material_name.lower() not in _STANDARD_MATERIALS:
            requirements.append(CapabilityRequirement(
                req_id=_next_id(),
                capability_key=f"material.{material_name}",
                required_by="physics_model",
            ))

        self._requirements = list(requirements)
        return requirements

    # ------------------------------------------------------------------
    # Check requirements
    # ------------------------------------------------------------------

    def check_requirements(
        self,
        requirements: list[CapabilityRequirement],
        available_capabilities: set[str],
    ) -> list[CapabilityRequirement]:
        """Mark each requirement as satisfied or missing.

        A requirement is ``satisfied`` if its ``capability_key`` is
        present in *available_capabilities*; otherwise it is ``missing``.
        Requirements already marked ``extension_requested`` are left
        unchanged.

        Args:
            requirements: The list of requirements to check.
            available_capabilities: A set of known capability keys.

        Returns:
            A new list of requirements with updated statuses.  The
            original list is not modified.
        """
        checked: list[CapabilityRequirement] = []
        for req in requirements:
            if req.status == "extension_requested":
                checked.append(req)
                continue
            if req.capability_key in available_capabilities:
                checked.append(req.model_copy(update={"status": "satisfied"}))
            else:
                checked.append(req.model_copy(update={"status": "missing"}))
        return checked

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_missing(
        self, requirements: list[CapabilityRequirement]
    ) -> list[CapabilityRequirement]:
        """Return only the requirements with ``status="missing"``."""
        return [r for r in requirements if r.status == "missing"]

    def get_unknown(
        self, requirements: list[CapabilityRequirement]
    ) -> list[CapabilityRequirement]:
        """Return only the requirements with ``status="unknown"``."""
        return [r for r in requirements if r.status == "unknown"]

    def get_satisfied(
        self, requirements: list[CapabilityRequirement]
    ) -> list[CapabilityRequirement]:
        """Return only the requirements with ``status="satisfied"``."""
        return [r for r in requirements if r.status == "satisfied"]


# ---------------------------------------------------------------------------
# Standalone detection functions for extension-required geometries
# ---------------------------------------------------------------------------

#: Keywords that indicate the user is requesting an airfoil geometry.
_AIRFOIL_KEYWORDS: list[str] = [
    "翼型", "airfoil", "naca", "NACA",
    "叶片", "机翼", "wing", "blade profile",
]

#: Keywords that indicate the user is requesting STL file import.
_STL_IMPORT_KEYWORDS: list[str] = [
    "导入stl", "import stl", "stl文件", "stl file",
    "导入stl文件", "导入模型", "import geometry",
    "加载stl", "load stl", "读取stl",
    "导入几何", "导入几何文件", "导入网格", "import mesh",
    "导入cad", "import cad", "读取模型",
]

#: Keywords that indicate the user is requesting a superellipse geometry.
_SUPERELLIPSE_KEYWORDS: list[str] = [
    "超椭圆", "superellipse", "super-ellipse", "super ellipse",
    "lamé curve", "lame curve", "超椭圆形", "超椭圆曲线",
    "superellipsoid", "超椭球",
]

#: Keywords that indicate the user is requesting a custom polygon geometry.
_POLYGON_KEYWORDS: list[str] = [
    "多边形", "polygon", "自定义多边形", "custom polygon",
    "不规则多边形", "irregular polygon", "多角形",
    "五边形", "六边形", "七边形", "八边形",
    "pentagon", "hexagon", "heptagon", "octagon",
]


def _extract_semantics_window(user_text: str, keyword: str, window: int = 60) -> str:
    """Extract a short text window around *keyword* for original_semantics."""
    pos = user_text.lower().find(keyword.lower())
    if pos < 0:
        return user_text[:window]
    start = max(0, pos - window // 2)
    end = min(len(user_text), pos + len(keyword) + window // 2)
    return user_text[start:end].strip()


def detect_airfoil_capability(user_text: str) -> CapabilityRequirement | None:
    """Detect whether the user is requesting an airfoil geometry.

    When the user mentions keywords such as ``"翼型"``, ``"airfoil"``,
    or ``"NACA"``, this function returns a
    :class:`CapabilityRequirement` with:

    - ``capability_key`` = ``"geometry.airfoil_2d"``
    - ``status`` = ``"extension_requested"``
    - ``original_semantics`` = the user's original text snippet
    - ``extension_proposal`` = a concrete proposal for extending the system

    Returns ``None`` if no airfoil-related keyword is found.
    """
    text_lower = user_text.lower()
    matched_keyword: str | None = None
    for kw in _AIRFOIL_KEYWORDS:
        if kw.lower() in text_lower:
            matched_keyword = kw
            break

    if matched_keyword is None:
        return None

    semantics = _extract_semantics_window(user_text, matched_keyword)

    # Try to extract a NACA designation (e.g. NACA0012, NACA 4412)
    naca_match = re.search(r'[Nn][Aa][Cc][Aa]\s*(\d{4,5})', user_text)
    if naca_match:
        semantics = f"NACA{naca_match.group(1)}翼型"

    return CapabilityRequirement(
        req_id="REQ-AIRFOIL",
        capability_key=AIRFOIL_2D_CAPABILITY_KEY,
        required_by="user_input",
        status="extension_requested",
        original_semantics=semantics,
        extension_proposal=(
            "需要添加airfoil生成器Skill和编译器hook："
            "1) 实现NACA 4位/5位翼型坐标生成器；"
            "2) 在compiler中添加airfoil STL生成方法；"
            "3) 在snappyHexMeshDict中注册airfoil surface；"
            "4) 添加airfoil阻力/升力观测量的function object配置"
        ),
    )


def detect_stl_import_capability(user_text: str) -> CapabilityRequirement | None:
    """Detect whether the user is requesting STL file import.

    When the user mentions keywords such as ``"导入STL"``,
    ``"import STL"``, or ``"STL文件"``, this function returns a
    :class:`CapabilityRequirement` with:

    - ``capability_key`` = ``"geometry.stl_import"``
    - ``status`` = ``"extension_requested"``
    - ``original_semantics`` = the user's original text snippet
    - ``extension_proposal`` = a concrete proposal for extending the system

    Returns ``None`` if no STL-import-related keyword is found.
    """
    text_lower = user_text.lower()
    matched_keyword: str | None = None
    for kw in _STL_IMPORT_KEYWORDS:
        if kw.lower() in text_lower:
            matched_keyword = kw
            break

    if matched_keyword is None:
        return None

    semantics = _extract_semantics_window(user_text, matched_keyword)

    # Try to extract the file path
    path_match = re.search(r'[\w./\\]+\.stl', user_text, re.IGNORECASE)
    if path_match:
        semantics = f"STL文件: {path_match.group(0)}"

    return CapabilityRequirement(
        req_id="REQ-STL-IMPORT",
        capability_key=STL_IMPORT_CAPABILITY_KEY,
        required_by="user_input",
        status="extension_requested",
        original_semantics=semantics,
        extension_proposal=(
            "需要添加STL处理器和导入流水线："
            "1) 实现STL文件解析与验证器（检查闭合性、法线方向）；"
            "2) 在compiler中添加STL文件拷贝/引用逻辑；"
            "3) 在snappyHexMeshDict中注册导入的STL surface；"
            "4) 添加STL几何边界自动识别与patch命名"
        ),
    )


def detect_superellipse_capability(user_text: str) -> CapabilityRequirement | None:
    """Detect whether the user is requesting a superellipse geometry.

    When the user mentions keywords such as ``"超椭圆"``,
    ``"superellipse"``, or ``"Lamé curve"``, this function returns a
    :class:`CapabilityRequirement` with:

    - ``capability_key`` = ``"geometry.superellipse_2d"``
    - ``status`` = ``"extension_requested"``
    - ``original_semantics`` = the user's original text snippet
    - ``extension_proposal`` = a concrete proposal for extending the system

    Returns ``None`` if no superellipse-related keyword is found.
    """
    text_lower = user_text.lower()
    matched_keyword: str | None = None
    for kw in _SUPERELLIPSE_KEYWORDS:
        if kw.lower() in text_lower:
            matched_keyword = kw
            break

    if matched_keyword is None:
        return None

    semantics = _extract_semantics_window(user_text, matched_keyword)

    # Try to extract superellipse parameters (a, b, n)
    param_match = re.search(r'[ab]\s*[=＝]\s*([\d.]+)', user_text)
    n_match = re.search(r'[nN]\s*[=＝]\s*(\d+(?:\.\d+)?)', user_text)
    param_parts: list[str] = []
    if param_match:
        param_parts.append(f"参数={param_match.group(0)}")
    if n_match:
        param_parts.append(f"指数n={n_match.group(1)}")
    if param_parts:
        semantics = f"超椭圆({', '.join(param_parts)})"

    return CapabilityRequirement(
        req_id="REQ-SUPERELLIPSE",
        capability_key=SUPERELLIPSE_2D_CAPABILITY_KEY,
        required_by="user_input",
        status="extension_requested",
        original_semantics=semantics,
        extension_proposal=(
            "需要添加超椭圆几何生成器Skill和编译器hook："
            "1) 实现超椭圆参数化坐标生成器（Lamé曲线: |x/a|^n + |y/b|^n = 1）；"
            "2) 在compiler中添加超椭圆STL生成方法（采样点→多边形近似→STL）；"
            "3) 在snappyHexMeshDict中注册超椭圆surface；"
            "4) 验证参数约束（a>0, b>0, n>0）并处理退化情况"
        ),
    )


def detect_polygon_capability(user_text: str) -> CapabilityRequirement | None:
    """Detect whether the user is requesting a custom polygon geometry.

    When the user mentions keywords such as ``"多边形"``, ``"polygon"``,
    or ``"六边形"``, this function returns a
    :class:`CapabilityRequirement` with:

    - ``capability_key`` = ``"geometry.custom_polygon_2d"``
    - ``status`` = ``"extension_requested"``
    - ``original_semantics`` = the user's original text snippet
    - ``extension_proposal`` = a concrete proposal for extending the system

    Returns ``None`` if no polygon-related keyword is found.
    """
    text_lower = user_text.lower()
    matched_keyword: str | None = None
    for kw in _POLYGON_KEYWORDS:
        if kw.lower() in text_lower:
            matched_keyword = kw
            break

    if matched_keyword is None:
        return None

    semantics = _extract_semantics_window(user_text, matched_keyword)

    # Try to extract vertex count or coordinates
    vertex_match = re.search(r'(\d+)\s*(?:个)?(?:顶点|vertex|vertices|点)', user_text, re.IGNORECASE)
    if vertex_match:
        semantics = f"{vertex_match.group(1)}顶点多边形"

    # Try to extract explicit vertex coordinates
    coord_matches = re.findall(r'\([\d.]+,\s*[\d.]+\)', user_text)
    if coord_matches:
        semantics = f"多边形(顶点: {', '.join(coord_matches[:4])}...)"

    return CapabilityRequirement(
        req_id="REQ-POLYGON",
        capability_key=CUSTOM_POLYGON_2D_CAPABILITY_KEY,
        required_by="user_input",
        status="extension_requested",
        original_semantics=semantics,
        extension_proposal=(
            "需要添加多边形几何编译器hook（Phase E已实现基础adapter）："
            "1) 解析用户提供的顶点坐标列表；"
            "2) 在compiler中通过_compile_polygon生成多边形STL（顶点→棱柱→STL）；"
            "3) 在snappyHexMeshDict中注册多边形surface；"
            "4) 验证顶点数>=3且多边形不自交"
        ),
    )


def detect_unknown_geometry_capabilities(
    user_text: str,
) -> list[CapabilityRequirement]:
    """Detect all extension-required geometry capabilities from user text.

    This is the main entry point for scanning user input for unsupported
    geometry types that require system extension.  It checks for airfoil,
    STL import, superellipse, and custom polygon requests, and returns a
    list of :class:`CapabilityRequirement` objects, each with a typed
    ``capability_key``, ``original_semantics``, and concrete
    ``extension_proposal``.

    The function does NOT silently fall back to known geometry templates —
    each detected capability is reported as ``"extension_requested"``.
    """
    requirements: list[CapabilityRequirement] = []

    airfoil_req = detect_airfoil_capability(user_text)
    if airfoil_req is not None:
        requirements.append(airfoil_req)

    stl_req = detect_stl_import_capability(user_text)
    if stl_req is not None:
        requirements.append(stl_req)

    superellipse_req = detect_superellipse_capability(user_text)
    if superellipse_req is not None:
        requirements.append(superellipse_req)

    polygon_req = detect_polygon_capability(user_text)
    if polygon_req is not None:
        requirements.append(polygon_req)

    return requirements
