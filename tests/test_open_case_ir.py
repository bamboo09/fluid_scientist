from __future__ import annotations

import pytest

from fluid_scientist.case_ir import OpenCaseIRBuilder, RequestedCaseIR
from fluid_scientist.llm.structured_understanding import StructuredUnderstanding


def understanding_for(case_family: str, dimensionality: str, entity_type: str, *, heat_transfer: bool = False) -> StructuredUnderstanding:
    quote = f"create {case_family}"
    return StructuredUnderstanding.model_validate({
        "summary": f"Research {case_family}",
        "case_family": case_family,
        "dimensionality": dimensionality,
        "facts": [
            {
                "fact_id": "domain_length",
                "path": "/domain/length",
                "value": 6.0,
                "unit": "m",
                "origin": "USER_EXPLICIT",
                "evidence": [{"quote": quote, "source": "current_message"}],
            },
            {
                "fact_id": "time_mode",
                "path": "/physics/time_mode",
                "value": "transient",
                "origin": "MODEL_RECOMMENDED",
            },
            {
                "fact_id": "heat_transfer",
                "path": "/physics/heat_transfer",
                "value": heat_transfer,
                "origin": "MODEL_RECOMMENDED",
            },
            {
                "fact_id": "inlet_bc",
                "path": "/boundaries/inlet/semantic_role",
                "value": "periodic" if case_family == "periodic_channel" else "velocity_inlet",
                "origin": "MODEL_RECOMMENDED",
            },
            {
                "fact_id": "observable",
                "path": "/observables/primary/semantic_type",
                "value": "nusselt_number" if heat_transfer else "velocity_field",
                "origin": "MODEL_RECOMMENDED",
            },
        ],
        "entities": [{
            "entity_id": "geometry_1",
            "semantic_type": entity_type,
            "attributes": {"characteristic_length": {"value": 1.0, "unit": "m", "source": "USER_EXPLICIT"}},
            "evidence": [{"quote": quote, "source": "current_message"}],
        }],
        "relations": [],
        "ambiguities": [],
        "conflicts": [],
        "capability_requirements": [],
        "evidence_quotes": [{"quote": quote, "source": "current_message"}],
        "proposed_patch": {
            "patch_id": f"patch_{case_family}",
            "session_id": "session_open",
            "base_spec_id": "open_spec",
            "base_version": 1,
            "intent": "create_spec",
            "operations": [],
            "untouched_guarantee": True,
        },
    })


@pytest.mark.parametrize(
    ("family", "dimension", "entity_type", "heat_transfer"),
    [
        ("cylinder_flow", "2D", "cylinder", False),
        ("backward_facing_step", "2D", "backward_step", False),
        ("periodic_channel", "2D", "channel", False),
        ("lid_driven_cavity", "2D", "cavity", False),
        ("natural_convection_cavity", "2D", "cavity", True),
        ("external_flow_3d", "3D", "sphere", False),
    ],
)
def test_same_model_understanding_to_open_case_ir_chain(
    family: str, dimension: str, entity_type: str, heat_transfer: bool
) -> None:
    understanding = understanding_for(family, dimension, entity_type, heat_transfer=heat_transfer)
    case_ir = OpenCaseIRBuilder().build(
        understanding, study_id=f"study_{family}", case_id=f"case_{family}"
    )

    assert isinstance(case_ir, RequestedCaseIR)
    assert case_ir.case_family == family
    assert case_ir.dimensionality == dimension
    assert case_ir.domain["length"].value == 6.0
    assert case_ir.entities[0].parameters["semantic_type"].value == entity_type
    assert case_ir.physics.heat_transfer is heat_transfer
    assert case_ir.boundary_intents
    assert case_ir.observables


def test_non_cylinder_families_are_not_silently_rewritten() -> None:
    families = {
        "backward_facing_step", "periodic_channel", "lid_driven_cavity",
        "natural_convection_cavity", "external_flow_3d",
    }
    built = {
        OpenCaseIRBuilder().build(
            understanding_for(family, "3D" if family.endswith("3d") else "2D", "custom"),
            study_id=family,
            case_id=family,
        ).case_family
        for family in families
    }
    assert built == families
