"""Dynamic Schema Builder for OpenWorldResearchIR.

Generates dynamic JSON schemas and form layouts for frontend display.
Can show ANY entity from the :class:`OpenWorldResearchIR`, not just
predefined types.  Each entity gets a schema generated on the fly
based on its representation and parameters, so new or unusual geometry
shapes are handled gracefully without code changes.

Three public entry points are provided on :class:`DynamicSchemaBuilder`:

* :meth:`build_schema`            -- a JSON-Schema-style description of every
  component in the IR, with per-entity dynamic property definitions.
* :meth:`build_form_layout`       -- a frontend-ready form layout with
  Chinese-labeled fields, typed inputs, and source provenance.
* :meth:`serialize_ir_for_display` -- the full IR serialised to a plain
  ``dict`` augmented with a source-coverage report and a list of
  blocking issues derived from ``representation_status``.
"""

from __future__ import annotations

import logging
from typing import Any

from fluid_scientist.research_ir.models import (
    GeometryEntity,
    OpenWorldResearchIR,
    ParameterValue,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chinese label mapping for common parameter names
# ---------------------------------------------------------------------------

PARAMETER_LABELS: dict[str, str] = {
    "radius": "半径",
    "diameter": "直径",
    "center_x": "中心X坐标",
    "center_y": "中心Y坐标",
    "width": "宽度",
    "height": "高度",
    "length": "长度",
    "base_width": "底边宽度",
    "top_width": "上底宽度",
    "bottom_width": "下底宽度",
    "semi_axis_a": "长半轴",
    "semi_axis_b": "短半轴",
    "inlet_velocity": "入口速度",
    "reynolds_number": "雷诺数",
    "density": "密度",
    "viscosity": "粘度",
    "temperature": "温度",
    "pressure": "压力",
    "velocity": "速度",
}

# Default unit inference for common parameter names.  An empty string
# means "dimensionless" or "no unit".
PARAMETER_UNITS: dict[str, str] = {
    "radius": "m",
    "diameter": "m",
    "center_x": "m",
    "center_y": "m",
    "width": "m",
    "height": "m",
    "length": "m",
    "base_width": "m",
    "top_width": "m",
    "bottom_width": "m",
    "semi_axis_a": "m",
    "semi_axis_b": "m",
    "inlet_velocity": "m/s",
    "velocity": "m/s",
    "reynolds_number": "",
    "density": "kg/m^3",
    "viscosity": "Pa*s",
    "temperature": "K",
    "pressure": "Pa",
}

# Chinese display names for common semantic shapes.
SHAPE_DISPLAY_NAMES: dict[str, str] = {
    "circle": "圆形",
    "cylinder": "圆柱",
    "rectangle": "矩形",
    "triangle": "三角形",
    "trapezoid": "梯形",
    "cosine_bell": "余弦钟形",
    "half_sine": "半正弦",
    "gaussian": "高斯形",
    "ellipse": "椭圆",
    "unknown": "未知形状",
}

# Select options for enum-like fields.  These mirror the ``Literal`` types
# declared on the corresponding model fields so the frontend can render a
# dropdown instead of free text.
SELECT_OPTIONS: dict[str, list[str]] = {
    "dimensionality": ["2D", "3D", "axisymmetric", "unknown"],
    "role": [
        "domain",
        "immersed_obstacle",
        "wall_attached_obstacle",
        "solid_body",
        "porous_region",
        "inlet_geometry",
        "outlet_geometry",
        "unknown",
    ],
    "phase": ["gas", "liquid", "solid", "multiphase", "unknown"],
    "model": [
        "incompressible_newtonian",
        "compressible_newtonian",
        "non_newtonian",
        "multiphase",
        "custom",
        "unknown",
    ],
    "physical_role": [
        "velocity_inlet",
        "mass_flow_inlet",
        "pressure_inlet",
        "pressure_outlet",
        "open_boundary",
        "no_slip_wall",
        "slip_wall",
        "moving_wall",
        "symmetry",
        "periodic",
        "shear_stress",
        "heat_flux",
        "convective_outlet",
        "custom",
        "unknown",
    ],
    "representation_status": [
        "resolved",
        "needs_clarification",
        "unsupported",
    ],
}

# Extended Chinese labels for entity-level fields that are NOT parameters
# (i.e. metadata fields like entity_id, raw_name, etc.).
_FIELD_LABELS: dict[str, str] = {
    "entity_id": "实体ID",
    "raw_name": "名称",
    "semantic_shape": "语义形状",
    "role": "角色",
    "material_id": "材料ID",
    "phase": "相态",
    "model": "本构模型",
    "boundary_id": "边界ID",
    "target": "目标边界",
    "physical_role": "物理角色",
    "model_id": "模型ID",
    "model_type": "模型类型",
    "observable_id": "观测ID",
    "physical_quantity": "物理量",
    "target_entity": "目标实体",
    "statistic": "统计方式",
    "measurement_plan": "测量方案",
    "dimensionality": "维度",
    "capability_status": "能力状态",
    "semantic_status": "语义状态",
    "representation_status": "表示状态",
    "missing_required_properties": "缺失属性",
    "confidence": "置信度",
    "raw_text": "原始文本",
    "spatial_scope": "空间范围",
    "temporal_scope": "时间范围",
    "field": "场变量",
    "region": "区域",
    "description": "描述",
}

# Representation statuses that are considered "blocking" -- i.e. the entity
# cannot be compiled until the issue is resolved.
_BLOCKING_REP_STATUSES: frozenset[str] = frozenset(
    {"needs_clarification", "unsupported"}
)


class DynamicSchemaBuilder:
    """Builds dynamic JSON schemas and form layouts for frontend display.

    The builder is designed to handle *any* entity type from the
    :class:`OpenWorldResearchIR`.  Rather than hard-coding schemas for
    predefined shapes (circle, rectangle, ...), it inspects each entity's
    ``representation`` and ``parameters`` at runtime and generates an
    appropriate schema dynamically.  Unknown or unresolved entities
    receive a generic schema that still exposes their ``raw_name`` and
    all available parameters, so nothing is hidden from the user.

    Typical usage::

        builder = DynamicSchemaBuilder()
        schema = builder.build_schema(ir)
        layout = builder.build_form_layout(ir)
        display = builder.serialize_ir_for_display(ir)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_schema(self, ir: OpenWorldResearchIR) -> dict:
        """Build a JSON schema describing all entities in the IR.

        The returned dict follows JSON-Schema conventions (``type``,
        ``properties``, ``items``) so it can be consumed by standard
        frontend schema libraries.  Each geometry entity gets a
        dynamically generated sub-schema based on its representation
        and parameters.  Unknown entities receive a generic schema with
        their ``raw_name`` and available parameters.

        Args:
            ir: The open-world research intermediate representation.

        Returns:
            A JSON-Schema-style ``dict`` with top-level ``properties``
            for every IR component (domain, geometry, materials,
            boundaries, physics, observables, etc.).
        """
        schema: dict[str, Any] = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "OpenWorldResearchIR",
            "description": "Dynamic schema for research IR frontend display",
            "type": "object",
            "ir_version": ir.ir_version,
            "study_type": ir.study_type,
            "dimensionality": ir.dimensionality,
            "properties": {},
        }

        props = schema["properties"]

        # -- Domain -------------------------------------------------------
        props["domain"] = self._build_domain_schema(ir.domain)

        # -- Geometry entities (ALL entities, regardless of type) --------
        props["geometry_entities"] = {
            "type": "array",
            "title": "几何实体",
            "items": [
                self._build_entity_schema(entity)
                for entity in ir.geometry_entities
            ],
        }

        # -- Materials ----------------------------------------------------
        props["materials"] = {
            "type": "array",
            "title": "材料",
            "items": [
                self._build_material_schema(material)
                for material in ir.materials
            ],
        }

        # -- Boundaries ---------------------------------------------------
        props["boundaries"] = {
            "type": "array",
            "title": "边界条件",
            "items": [
                self._build_boundary_schema(boundary)
                for boundary in ir.boundaries
            ],
        }

        # -- Initial conditions -------------------------------------------
        props["initial_conditions"] = {
            "type": "array",
            "title": "初始条件",
            "items": [
                self._build_initial_condition_schema(ic)
                for ic in ir.initial_conditions
            ],
        }

        # -- Physics models -----------------------------------------------
        props["physics_models"] = {
            "type": "array",
            "title": "物理模型",
            "items": [
                self._build_physics_schema(physics)
                for physics in ir.physics_models
            ],
        }

        # -- Observables --------------------------------------------------
        props["observables"] = {
            "type": "array",
            "title": "观测目标",
            "items": [
                self._build_observable_schema(observable)
                for observable in ir.observables
            ],
        }

        # -- Spatial relations --------------------------------------------
        props["spatial_relations"] = {
            "type": "array",
            "title": "空间关系",
            "items": [
                self._build_spatial_relation_schema(rel)
                for rel in ir.spatial_relations
            ],
        }

        return schema

    def build_form_layout(self, ir: OpenWorldResearchIR) -> dict:
        """Build a form layout for the frontend.

        Returns a dict with the following sections:
        ``"domain"``, ``"geometry"``, ``"materials"``, ``"boundaries"``,
        ``"physics"``, ``"observables"``.

        * ``domain`` is a flat list of form fields.
        * ``geometry`` is a list of per-entity forms, each labelled with
          ``entity_id`` and ``raw_name``.  Fields are generated
          dynamically from the entity's parameters.
        * The remaining sections are lists of per-item forms, each with
          a title, a ``fields`` list, and metadata.

        Each form field has: ``key``, ``label``, ``type``
        (``text``/``number``/``select``), ``value``, ``unit``,
        ``editable``, and ``source_span``.

        Args:
            ir: The open-world research intermediate representation.

        Returns:
            A dict keyed by section name, where each value is a list of
            form fields or per-entity form dicts.
        """
        layout: dict[str, Any] = {
            "domain": self._build_domain_form(ir.domain),
            "geometry": [
                self._build_entity_form(entity)
                for entity in ir.geometry_entities
            ],
            "materials": [
                self._build_material_form(material)
                for material in ir.materials
            ],
            "boundaries": [
                self._build_boundary_form(boundary)
                for boundary in ir.boundaries
            ],
            "physics": [
                self._build_physics_form(physics)
                for physics in ir.physics_models
            ],
            "observables": [
                self._build_observable_form(observable)
                for observable in ir.observables
            ],
        }
        return layout

    def serialize_ir_for_display(self, ir: OpenWorldResearchIR) -> dict:
        """Serialize the full IR to a display-friendly dict.

        Converts the entire :class:`OpenWorldResearchIR` to a plain
        ``dict`` suitable for a JSON API response, then augments it
        with:

        * ``source_coverage_report`` -- a summary of how many user
          mentions have been accounted for.
        * ``blocking_issues`` -- a list of issues that prevent
          compilation, derived from entity ``representation_status``
          values and blocking ambiguities.

        Args:
            ir: The open-world research intermediate representation.

        Returns:
            A JSON-serialisable ``dict`` representation of the IR
            with additional display metadata.
        """
        ir_dict = ir.model_dump(mode="json")

        # Source coverage report
        ir_dict["source_coverage_report"] = self._build_coverage_report(ir)

        # Blocking issues from representation_status and ambiguities
        ir_dict["blocking_issues"] = self._collect_blocking_issues(ir)

        return ir_dict

    # ------------------------------------------------------------------
    # Schema builders (for build_schema)
    # ------------------------------------------------------------------

    def _build_domain_schema(self, domain: Any) -> dict:
        """Build a JSON-schema fragment for the domain intent."""
        properties: dict[str, Any] = {
            "dimensionality": {
                "type": "string",
                "label": self._get_label("dimensionality"),
                "enum": SELECT_OPTIONS["dimensionality"],
            },
        }

        for attr in ("length", "width", "height"):
            pv = getattr(domain, attr, None)
            if pv is not None:
                properties[attr] = self._build_parameter_schema(attr, pv)

        return {
            "type": "object",
            "title": "计算域",
            "properties": properties,
        }

    def _build_entity_schema(self, entity: GeometryEntity) -> dict:
        """Build a dynamically generated schema for a single geometry entity.

        Known entities (resolved representation) get a structured schema
        that includes the representation type, subtype, and definition.
        Unknown entities get a generic schema with their ``raw_name`` and
        all available parameters.
        """
        rep = entity.representation
        is_unknown = (
            rep.type == "unknown"
            or entity.representation_status in _BLOCKING_REP_STATUSES
        )

        properties: dict[str, Any] = {
            "entity_id": {
                "type": "string",
                "label": self._get_label("entity_id"),
            },
            "raw_name": {
                "type": "string",
                "label": self._get_label("raw_name"),
            },
            "semantic_shape": {
                "type": "string",
                "label": self._get_label("semantic_shape"),
            },
            "role": {
                "type": "string",
                "label": self._get_label("role"),
                "enum": SELECT_OPTIONS["role"],
            },
            "representation": {
                "type": "object",
                "label": "表示",
                "properties": {
                    "type": {"type": "string", "label": "类型"},
                    "subtype": {"type": "string", "label": "子类型"},
                    "definition": {
                        "type": "object",
                        "label": "定义",
                        "properties": {
                            k: {"type": "string", "label": k}
                            for k in rep.definition
                        },
                    },
                },
            },
            "representation_status": {
                "type": "string",
                "label": self._get_label("representation_status"),
                "enum": SELECT_OPTIONS["representation_status"],
            },
            "confidence": {
                "type": "number",
                "label": self._get_label("confidence"),
            },
        }

        # Build parameter properties dynamically from the entity's
        # actual parameters -- this is what makes the schema "dynamic"
        # and able to handle ANY entity type.
        param_properties: dict[str, Any] = {}
        for key, pv in entity.parameters.items():
            param_properties[key] = self._build_parameter_schema(key, pv)

        properties["parameters"] = {
            "type": "object",
            "label": "参数",
            "properties": param_properties,
        }

        entity_schema: dict[str, Any] = {
            "entity_id": entity.entity_id,
            "type": "object",
            "title": self._entity_title(entity),
            "properties": properties,
            "representation": {
                "type": rep.type,
                "subtype": rep.subtype,
            },
            "status": entity.representation_status,
            "is_unknown": is_unknown,
        }

        # For unknown entities, prominently include the raw_name so the
        # frontend can display it even if parameters are sparse.
        if is_unknown:
            entity_schema["raw_name"] = entity.raw_name
            entity_schema["available_parameters"] = list(
                entity.parameters.keys()
            )

        return entity_schema

    def _build_material_schema(self, material: Any) -> dict:
        """Build a schema for a single material intent."""
        properties: dict[str, Any] = {
            "material_id": {
                "type": "string",
                "label": self._get_label("material_id"),
            },
            "raw_name": {
                "type": "string",
                "label": self._get_label("raw_name"),
            },
            "phase": {
                "type": "string",
                "label": self._get_label("phase"),
                "enum": SELECT_OPTIONS["phase"],
            },
            "model": {
                "type": "string",
                "label": self._get_label("model"),
                "enum": SELECT_OPTIONS["model"],
            },
            "capability_status": {
                "type": "string",
                "label": self._get_label("capability_status"),
            },
        }

        for key, pv in material.properties.items():
            properties[key] = self._build_parameter_schema(key, pv)

        return {
            "material_id": material.material_id,
            "type": "object",
            "title": material.raw_name or material.material_id,
            "properties": properties,
            "capability_status": material.capability_status,
        }

    def _build_boundary_schema(self, boundary: Any) -> dict:
        """Build a schema for a single boundary intent."""
        properties: dict[str, Any] = {
            "boundary_id": {
                "type": "string",
                "label": self._get_label("boundary_id"),
            },
            "target": {
                "type": "string",
                "label": self._get_label("target"),
            },
            "physical_role": {
                "type": "string",
                "label": self._get_label("physical_role"),
                "enum": SELECT_OPTIONS["physical_role"],
            },
            "capability_status": {
                "type": "string",
                "label": self._get_label("capability_status"),
            },
            "semantic_status": {
                "type": "string",
                "label": self._get_label("semantic_status"),
            },
        }

        for key, pv in boundary.quantities.items():
            properties[key] = self._build_parameter_schema(key, pv)

        return {
            "boundary_id": boundary.boundary_id,
            "type": "object",
            "title": f"边界 {boundary.boundary_id}",
            "properties": properties,
            "capability_status": boundary.capability_status,
        }

    def _build_initial_condition_schema(self, ic: Any) -> dict:
        """Build a schema for a single initial condition."""
        properties: dict[str, Any] = {
            "ic_id": {"type": "string", "label": "初始条件ID"},
            "field": {
                "type": "string",
                "label": self._get_label("field"),
            },
            "region": {
                "type": "string",
                "label": self._get_label("region"),
            },
        }

        if ic.value is not None:
            properties["value"] = self._build_parameter_schema(
                "value", ic.value
            )

        return {
            "ic_id": ic.ic_id,
            "type": "object",
            "title": f"初始条件 {ic.ic_id}",
            "properties": properties,
        }

    def _build_physics_schema(self, physics: Any) -> dict:
        """Build a schema for a single physics model intent."""
        properties: dict[str, Any] = {
            "model_id": {
                "type": "string",
                "label": self._get_label("model_id"),
            },
            "raw_name": {
                "type": "string",
                "label": self._get_label("raw_name"),
            },
            "model_type": {
                "type": "string",
                "label": self._get_label("model_type"),
            },
            "capability_status": {
                "type": "string",
                "label": self._get_label("capability_status"),
            },
        }

        for key, pv in physics.parameters.items():
            properties[key] = self._build_parameter_schema(key, pv)

        return {
            "model_id": physics.model_id,
            "type": "object",
            "title": physics.raw_name or physics.model_id,
            "properties": properties,
            "capability_status": physics.capability_status,
        }

    def _build_observable_schema(self, observable: Any) -> dict:
        """Build a schema for a single observable intent."""
        properties: dict[str, Any] = {
            "observable_id": {
                "type": "string",
                "label": self._get_label("observable_id"),
            },
            "raw_name": {
                "type": "string",
                "label": self._get_label("raw_name"),
            },
            "physical_quantity": {
                "type": "string",
                "label": self._get_label("physical_quantity"),
            },
            "target_entity": {
                "type": "string",
                "label": self._get_label("target_entity"),
            },
            "statistic": {
                "type": "string",
                "label": self._get_label("statistic"),
            },
            "measurement_plan": {
                "type": "string",
                "label": self._get_label("measurement_plan"),
            },
            "capability_status": {
                "type": "string",
                "label": self._get_label("capability_status"),
            },
        }

        return {
            "observable_id": observable.observable_id,
            "type": "object",
            "title": observable.raw_name or observable.observable_id,
            "properties": properties,
            "capability_status": observable.capability_status,
        }

    def _build_spatial_relation_schema(self, relation: Any) -> dict:
        """Build a schema for a single spatial relation."""
        properties: dict[str, Any] = {
            "relation_id": {"type": "string", "label": "关系ID"},
            "subject_entity": {
                "type": "string",
                "label": "主体实体",
            },
            "relation_type": {
                "type": "string",
                "label": "关系类型",
            },
            "target_entity": {
                "type": "string",
                "label": self._get_label("target_entity"),
            },
            "target_boundary": {
                "type": "string",
                "label": "目标边界",
            },
        }

        for key, pv in relation.parameters.items():
            properties[key] = self._build_parameter_schema(key, pv)

        return {
            "relation_id": relation.relation_id,
            "type": "object",
            "title": f"空间关系 {relation.relation_id}",
            "properties": properties,
        }

    def _build_parameter_schema(
        self, key: str, pv: ParameterValue
    ) -> dict:
        """Build a JSON-schema property for a single parameter value."""
        field_type = self._infer_field_type(pv.value)
        unit = self._infer_unit(key, pv)

        schema: dict[str, Any] = {
            "type": field_type,
            "label": self._get_label(key),
            "unit": unit,
        }

        if pv.value is not None:
            schema["default"] = pv.value

        if pv.confidence is not None and pv.confidence < 1.0:
            schema["confidence"] = pv.confidence

        return schema

    # ------------------------------------------------------------------
    # Form builders (for build_form_layout)
    # ------------------------------------------------------------------

    def _build_domain_form(self, domain: Any) -> list[dict]:
        """Build form fields for the domain section.

        The domain is a single object, so this returns a flat list of
        form fields rather than a list of per-entity forms.
        """
        fields: list[dict] = []

        # Dimensionality (select)
        fields.append(
            self._build_field_from_value(
                "dimensionality", domain.dimensionality, editable=True
            )
        )

        # Length / width / height (ParameterValue objects)
        for attr in ("length", "width", "height"):
            pv = getattr(domain, attr, None)
            if pv is not None:
                fields.append(self._build_field(attr, pv))

        return fields

    def _build_entity_form(self, entity: GeometryEntity) -> dict:
        """Build a form dict for a single geometry entity.

        The returned dict matches the example layout::

            {
                "entity_id": "geo_1",
                "title": "梯形凸起 (trapezoid)",
                "fields": [ ... ],
                "representation": {"type": "...", "subtype": "..."},
                "status": "resolved"
            }
        """
        fields: list[dict] = []

        # Entity metadata fields (non-editable)
        fields.append(
            self._build_field_from_value(
                "entity_id", entity.entity_id, editable=False
            )
        )
        fields.append(
            self._build_field_from_value(
                "raw_name", entity.raw_name, editable=True
            )
        )
        fields.append(
            self._build_field_from_value(
                "semantic_shape", entity.semantic_shape, editable=True
            )
        )
        fields.append(
            self._build_field_from_value(
                "role", entity.role, editable=True
            )
        )

        # Parameter fields (editable) -- dynamically generated from the
        # entity's actual parameters.  This handles ANY entity type.
        for key, pv in entity.parameters.items():
            fields.append(self._build_field(key, pv))

        return {
            "entity_id": entity.entity_id,
            "title": self._entity_title(entity),
            "fields": fields,
            "representation": {
                "type": entity.representation.type,
                "subtype": entity.representation.subtype,
            },
            "status": entity.representation_status,
        }

    def _build_material_form(self, material: Any) -> dict:
        """Build a form dict for a single material."""
        fields: list[dict] = [
            self._build_field_from_value(
                "material_id", material.material_id, editable=False
            ),
            self._build_field_from_value(
                "raw_name", material.raw_name, editable=True
            ),
            self._build_field_from_value(
                "phase", material.phase, editable=True
            ),
            self._build_field_from_value(
                "model", material.model, editable=True
            ),
        ]

        for key, pv in material.properties.items():
            fields.append(self._build_field(key, pv))

        return {
            "material_id": material.material_id,
            "title": material.raw_name or material.material_id,
            "fields": fields,
            "capability_status": material.capability_status,
        }

    def _build_boundary_form(self, boundary: Any) -> dict:
        """Build a form dict for a single boundary."""
        fields: list[dict] = [
            self._build_field_from_value(
                "boundary_id", boundary.boundary_id, editable=False
            ),
            self._build_field_from_value(
                "target", boundary.target, editable=True
            ),
            self._build_field_from_value(
                "physical_role", boundary.physical_role, editable=True
            ),
        ]

        for key, pv in boundary.quantities.items():
            fields.append(self._build_field(key, pv))

        return {
            "boundary_id": boundary.boundary_id,
            "title": f"边界 {boundary.boundary_id}",
            "fields": fields,
            "capability_status": boundary.capability_status,
            "semantic_status": boundary.semantic_status,
        }

    def _build_physics_form(self, physics: Any) -> dict:
        """Build a form dict for a single physics model."""
        fields: list[dict] = [
            self._build_field_from_value(
                "model_id", physics.model_id, editable=False
            ),
            self._build_field_from_value(
                "raw_name", physics.raw_name, editable=True
            ),
            self._build_field_from_value(
                "model_type", physics.model_type, editable=True
            ),
        ]

        for key, pv in physics.parameters.items():
            fields.append(self._build_field(key, pv))

        return {
            "model_id": physics.model_id,
            "title": physics.raw_name or physics.model_id,
            "fields": fields,
            "capability_status": physics.capability_status,
        }

    def _build_observable_form(self, observable: Any) -> dict:
        """Build a form dict for a single observable."""
        fields: list[dict] = [
            self._build_field_from_value(
                "observable_id", observable.observable_id, editable=False
            ),
            self._build_field_from_value(
                "raw_name", observable.raw_name, editable=True
            ),
            self._build_field_from_value(
                "physical_quantity", observable.physical_quantity,
                editable=True,
            ),
            self._build_field_from_value(
                "target_entity", observable.target_entity, editable=True
            ),
            self._build_field_from_value(
                "statistic", observable.statistic, editable=True
            ),
            self._build_field_from_value(
                "measurement_plan", observable.measurement_plan,
                editable=True,
            ),
        ]

        return {
            "observable_id": observable.observable_id,
            "title": observable.raw_name or observable.observable_id,
            "fields": fields,
            "capability_status": observable.capability_status,
        }

    # ------------------------------------------------------------------
    # Field construction helpers
    # ------------------------------------------------------------------

    def _build_field(self, key: str, pv: ParameterValue) -> dict:
        """Build a form field dict from a :class:`ParameterValue`.

        Each field has: ``key``, ``label``, ``type``, ``value``,
        ``unit``, ``editable``, ``source_span``.  Select fields also
        include ``options``.
        """
        field_type = "select" if key in SELECT_OPTIONS else self._infer_field_type(pv.value)

        unit = self._infer_unit(key, pv)

        field: dict[str, Any] = {
            "key": key,
            "label": self._get_label(key),
            "type": field_type,
            "value": pv.value,
            "unit": unit,
            "editable": True,
            "source_span": pv.source_span,
        }

        if field_type == "select":
            field["options"] = SELECT_OPTIONS[key]

        return field

    def _build_field_from_value(
        self,
        key: str,
        value: Any,
        unit: str = "",
        editable: bool = True,
        source_span: str | None = None,
    ) -> dict:
        """Build a form field dict from a raw scalar value.

        Used for entity-level metadata fields that are not
        :class:`ParameterValue` objects (e.g. ``entity_id``,
        ``dimensionality``, ``phase``).
        """
        field_type = "select" if key in SELECT_OPTIONS else self._infer_field_type(value)

        inferred_unit = unit or PARAMETER_UNITS.get(key, "")

        field: dict[str, Any] = {
            "key": key,
            "label": self._get_label(key),
            "type": field_type,
            "value": value,
            "unit": inferred_unit,
            "editable": editable,
            "source_span": source_span,
        }

        if field_type == "select":
            field["options"] = SELECT_OPTIONS[key]

        return field

    # ------------------------------------------------------------------
    # Coverage and blocking issues
    # ------------------------------------------------------------------

    def _build_coverage_report(self, ir: OpenWorldResearchIR) -> dict:
        """Build a source-coverage report dict.

        Mirrors the structure produced by
        :class:`SourceCoverageGuard.report` in ``coverage.py`` so the
        frontend receives a consistent report shape.
        """
        inv = ir.source_coverage.mention_inventory
        total = len(inv.mentions)
        unaccounted = ir.source_coverage.unaccounted_mentions
        accounted = total - len(unaccounted)

        return {
            "total_mentions": total,
            "accounted": accounted,
            "unaccounted": len(unaccounted),
            "coverage_ratio": ir.source_coverage.coverage_ratio,
            "is_complete": ir.source_coverage.is_complete,
            "unaccounted_texts": [m.text for m in unaccounted],
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

    def _collect_blocking_issues(
        self, ir: OpenWorldResearchIR
    ) -> list[dict]:
        """Collect all blocking issues from the IR.

        Blocking issues are derived from:

        1. Geometry entities whose ``representation_status`` is
           ``"needs_clarification"`` or ``"unsupported"``.
        2. Semantic ambiguities with ``blocking=True``.
        3. Unresolved mentions that have no mapping.
        """
        issues: list[dict] = []

        # 1. Representation status issues
        for entity in ir.geometry_entities:
            if entity.representation_status in _BLOCKING_REP_STATUSES:
                display_name = entity.raw_name or entity.entity_id
                issues.append(
                    {
                        "type": "representation_status",
                        "entity_id": entity.entity_id,
                        "raw_name": entity.raw_name,
                        "semantic_shape": entity.semantic_shape,
                        "status": entity.representation_status,
                        "message": (
                            f"实体 '{display_name}' 的表示状态为 "
                            f"'{entity.representation_status}'，"
                            f"需要进一步处理。"
                        ),
                    }
                )

        # 2. Blocking ambiguities
        for ambiguity in ir.ambiguities:
            if ambiguity.blocking:
                issues.append(
                    {
                        "type": "ambiguity",
                        "ambiguity_id": ambiguity.ambiguity_id,
                        "description": ambiguity.description,
                        "affected_entities": ambiguity.affected_entities,
                        "source_span": ambiguity.source_span,
                        "message": f"存在歧义: {ambiguity.description}",
                    }
                )

        # 3. Unresolved mentions
        for mention in ir.unresolved_mentions:
            issues.append(
                {
                    "type": "unresolved_mention",
                    "text": mention.text,
                    "category": mention.category,
                    "reason": mention.reason,
                    "message": (
                        f"无法解析的用户输入: '{mention.text}' "
                        f"(类别: {mention.category}, 原因: {mention.reason})"
                    ),
                }
            )

        return issues

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_field_type(value: Any) -> str:
        """Infer the form field type from a value.

        Returns ``"number"`` for ints and floats, ``"text"`` for
        everything else (including ``None``).
        """
        if value is None:
            return "text"
        if isinstance(value, bool):
            return "text"
        if isinstance(value, (int, float)):
            return "number"
        return "text"

    @staticmethod
    def _infer_unit(key: str, pv: ParameterValue) -> str:
        """Infer the unit for a parameter.

        Uses the ``ParameterValue.unit`` if present, otherwise falls
        back to :data:`PARAMETER_UNITS`.
        """
        if pv.unit:
            return pv.unit
        return PARAMETER_UNITS.get(key, "")

    def _get_label(self, key: str) -> str:
        """Get a Chinese label for a parameter or field key.

        Checks :data:`PARAMETER_LABELS` first, then
        :data:`_FIELD_LABELS`, and finally falls back to the raw key.
        """
        return (
            PARAMETER_LABELS.get(key)
            or _FIELD_LABELS.get(key)
            or key
        )

    def _entity_title(self, entity: GeometryEntity) -> str:
        """Build a human-readable title for a geometry entity.

        Format: ``"{raw_name} ({semantic_shape})"`` when ``raw_name``
        is non-empty, otherwise ``"{shape_display_name} ({semantic_shape})"``.
        """
        semantic_shape = entity.semantic_shape or "unknown"
        if entity.raw_name:
            return f"{entity.raw_name} ({semantic_shape})"
        shape_display = SHAPE_DISPLAY_NAMES.get(
            semantic_shape, semantic_shape
        )
        return f"{shape_display} ({semantic_shape})"


__all__ = [
    "DynamicSchemaBuilder",
    "PARAMETER_LABELS",
    "PARAMETER_UNITS",
    "SHAPE_DISPLAY_NAMES",
    "SELECT_OPTIONS",
]
