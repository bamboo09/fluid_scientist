import json
import math

import pytest
from pydantic import ValidationError

from fluid_scientist.case_generation.models import (
    GeneratedCaseDraft,
    GeneratedCaseDraftView,
    GeneratedCaseFile,
    GeneratedCaseParameter,
)


def valid_draft_payload() -> dict[str, object]:
    return {
        "experiment_name": "Backward-facing step study",
        "objective": "Resolve reattachment length for a laminar step flow.",
        "solver": "incompressibleFluid",
        "preprocessing": ["blockMesh", "checkMesh"],
        "parameters": [],
        "files": [
            {"path": "0/U", "content": "FoamFile { class volVectorField; }"},
            {"path": "0/p", "content": "FoamFile { class volScalarField; }"},
            {"path": "constant/physicalProperties", "content": "nu 1e-5;"},
            {"path": "system/controlDict", "content": "solver incompressibleFluid;"},
            {"path": "system/fvSchemes", "content": "ddtSchemes { default steadyState; }"},
            {"path": "system/fvSolution", "content": "solvers {}"},
            {"path": "system/blockMeshDict", "content": "vertices ();"},
        ],
        "requested_outputs": ["reattachment_length", "residuals"],
        "assumptions": ["Two-dimensional incompressible flow"],
        "limitations": ["Pilot resolution is not grid independent"],
    }


def float_parameter() -> dict[str, object]:
    return {
        "name": "inlet_velocity_m_s",
        "kind": "float",
        "unit": "m/s",
        "minimum": 0.1,
        "maximum": 2.0,
        "default": 0.5,
        "regression_values": [0.25, 1.0],
    }


def test_generated_case_contract_rejects_commands_and_extra_fields() -> None:
    for forbidden in ("command", "remote_path", "binary", "archive", "api_key", "script"):
        with pytest.raises(ValidationError, match="Extra inputs"):
            GeneratedCaseDraft.model_validate(valid_draft_payload() | {forbidden: "secret"})


def test_generated_case_contract_bounds_files_and_total_utf8_bytes() -> None:
    payload = valid_draft_payload()
    payload["files"] = [{"path": "system/controlDict", "content": "x" * 1_000_001}]
    with pytest.raises(ValidationError):
        GeneratedCaseDraft.model_validate(payload)

    payload = valid_draft_payload()
    payload["files"] = [{"path": "0/U", "content": "\ud800"}]
    with pytest.raises(ValidationError):
        GeneratedCaseDraft.model_validate(payload)

    payload = valid_draft_payload()
    payload["files"] = [
        {"path": f"0/field_{index}", "content": "\U0001f40d" * 250_000}
        for index in range(9)
    ]
    with pytest.raises(ValidationError, match="8 MiB"):
        GeneratedCaseDraft.model_validate(payload)

    payload = valid_draft_payload()
    payload["files"] = [
        {"path": f"0/field_{index}", "content": "x"} for index in range(65)
    ]
    with pytest.raises(ValidationError):
        GeneratedCaseDraft.model_validate(payload)


def test_generated_case_file_limit_counts_utf8_bytes_not_characters() -> None:
    accepted = "\U0001f40d" * 250_000
    assert GeneratedCaseFile(path="0/U", content=accepted).content == accepted
    with pytest.raises(ValidationError, match="1,000,000 UTF-8 bytes"):
        GeneratedCaseFile(path="0/U", content=accepted + "\U0001f40d")


@pytest.mark.parametrize("field", ["path", "content"])
@pytest.mark.parametrize("value", [b"0/U", bytearray(b"0/U"), 123])
def test_generated_case_file_fields_require_real_strings(field: str, value: object) -> None:
    payload: dict[str, object] = {"path": "0/U", "content": "text"}
    payload[field] = value
    with pytest.raises(ValidationError, match="string"):
        GeneratedCaseFile.model_validate(payload)


@pytest.mark.parametrize("bad_value", [b"text", bytearray(b"text"), 42])
def test_all_contract_string_fields_reject_non_strings(bad_value: object) -> None:
    for field in ("name", "unit"):
        payload = float_parameter()
        payload[field] = bad_value
        with pytest.raises(ValidationError):
            GeneratedCaseParameter.model_validate(payload)

    enum_payload = {
        "name": "scheme",
        "kind": "enum",
        "default": "linear",
        "regression_values": ["linear", "upwind"],
        "allowed_values": ["linear", bad_value],
    }
    with pytest.raises(ValidationError):
        GeneratedCaseParameter.model_validate(enum_payload)

    for field in ("experiment_name", "objective"):
        payload = valid_draft_payload()
        payload[field] = bad_value
        with pytest.raises(ValidationError):
            GeneratedCaseDraft.model_validate(payload)
    for field in ("requested_outputs", "assumptions", "limitations"):
        payload = valid_draft_payload()
        payload[field] = [bad_value]
        with pytest.raises(ValidationError):
            GeneratedCaseDraft.model_validate(payload)

    draft = GeneratedCaseDraft.model_validate(valid_draft_payload())
    view_payload: dict[str, object] = {
        "draft_id": "draft-1",
        "project_id": "project-1",
        "plan_id": "plan-1",
        "plan_version": 1,
        "version": 1,
        "provider": "glm",
        "model": "glm-5.1",
        "digest": "sha256:" + "a" * 64,
        "draft": draft,
    }
    for field in ("draft_id", "project_id", "plan_id", "provider", "model", "digest"):
        with pytest.raises(ValidationError):
            GeneratedCaseDraftView.model_validate(view_payload | {field: bad_value})


def test_preprocessing_rejects_oversized_input_at_field_boundary() -> None:
    payload = valid_draft_payload()
    payload["preprocessing"] = ["blockMesh", "checkMesh", *("blockMesh" for _ in range(10_000))]
    with pytest.raises(ValidationError):
        GeneratedCaseDraft.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("files", [{"path": "0/U", "content": "x"}, {"path": "0/U", "content": "y"}]),
        ("parameters", [float_parameter(), float_parameter()]),
        ("requested_outputs", ["residuals", "residuals"]),
    ],
)
def test_generated_case_contract_rejects_duplicate_identity_fields(
    field: str, value: object
) -> None:
    payload = valid_draft_payload()
    payload[field] = value
    with pytest.raises(ValidationError, match="unique"):
        GeneratedCaseDraft.model_validate(payload)


def test_draft_requires_ordered_preprocessing_and_nonempty_metadata() -> None:
    for preprocessing in (
        ["checkMesh", "blockMesh"],
        ["blockMesh", "blockMesh", "checkMesh"],
        ["blockMesh"],
        ["checkMesh"],
    ):
        with pytest.raises(ValidationError):
            GeneratedCaseDraft.model_validate(
                valid_draft_payload() | {"preprocessing": preprocessing}
            )
    for field in ("requested_outputs", "assumptions", "limitations"):
        with pytest.raises(ValidationError):
            GeneratedCaseDraft.model_validate(valid_draft_payload() | {field: []})


@pytest.mark.parametrize("name", ["Residuals", "residual-rate", "a.b", "_hidden", "two__x"])
def test_lower_snake_case_names_are_enforced(name: str) -> None:
    with pytest.raises(ValidationError):
        GeneratedCaseDraft.model_validate(
            valid_draft_payload() | {"requested_outputs": [name]}
        )
    with pytest.raises(ValidationError):
        GeneratedCaseParameter.model_validate(float_parameter() | {"name": name})


def test_float_parameter_requires_finite_bounded_values() -> None:
    parameter = GeneratedCaseParameter.model_validate(float_parameter())
    assert parameter.minimum <= parameter.default <= parameter.maximum

    bad_values = [
        {"minimum": 1.0, "maximum": 0.5},
        {"default": 3.0},
        {"regression_values": [0.2, 3.0]},
        {"minimum": math.nan},
        {"default": math.inf},
        {"regression_values": [0.2]},
    ]
    for change in bad_values:
        with pytest.raises(ValidationError):
            GeneratedCaseParameter.model_validate(float_parameter() | change)


def test_integer_bounds_preserve_large_values_exactly() -> None:
    large = 10**40
    parameter = GeneratedCaseParameter.model_validate(
        {
            "name": "sample_count",
            "kind": "integer",
            "minimum": large,
            "maximum": large + 3,
            "default": large + 1,
            "regression_values": [large + 2, large + 3],
        }
    )
    assert parameter.default == large + 1
    with pytest.raises(ValidationError, match="supported magnitude"):
        GeneratedCaseParameter.model_validate(
            {
                "name": "sample_count",
                "kind": "integer",
                "minimum": 10**101,
                "maximum": 10**101 + 3,
                "default": 10**101 + 1,
                "regression_values": [10**101 + 2, 10**101 + 3],
            }
        )


def test_huge_finite_float_is_rejected_as_validation_error() -> None:
    with pytest.raises(ValidationError, match="supported magnitude"):
        GeneratedCaseParameter.model_validate(float_parameter() | {"maximum": 1e308})


@pytest.mark.parametrize("value", [True, 1.5, "1"])
def test_integer_parameter_rejects_bool_fraction_and_coercion(value: object) -> None:
    payload = {
        "name": "cell_count",
        "kind": "integer",
        "unit": None,
        "minimum": 10,
        "maximum": 100,
        "default": value,
        "regression_values": [20, 40],
    }
    with pytest.raises(ValidationError):
        GeneratedCaseParameter.model_validate(payload)


def test_enum_parameter_requires_coherent_unique_allowed_values() -> None:
    valid = {
        "name": "scheme",
        "kind": "enum",
        "unit": None,
        "minimum": None,
        "maximum": None,
        "default": "linear",
        "regression_values": ["linear", "upwind"],
        "allowed_values": ["linear", "upwind"],
    }
    assert GeneratedCaseParameter.model_validate(valid).default == "linear"
    for change in (
        {"allowed_values": []},
        {"allowed_values": ["linear", "linear"]},
        {"default": "cubic"},
        {"regression_values": ["linear", "cubic"]},
        {"minimum": 0},
        {"unit": "m/s"},
    ):
        with pytest.raises(ValidationError):
            GeneratedCaseParameter.model_validate(valid | change)


def test_enum_allowed_value_metadata_is_bounded() -> None:
    base = {
        "name": "scheme",
        "kind": "enum",
        "default": "a",
        "regression_values": ["a", "b"],
    }
    with pytest.raises(ValidationError):
        GeneratedCaseParameter.model_validate(
            base | {"allowed_values": ["a", "b", "x" * 121]}
        )
    with pytest.raises(ValidationError):
        GeneratedCaseParameter.model_validate(
            base | {"allowed_values": ["a", "b", *(f"v{i}" for i in range(63))]}
        )
    with pytest.raises(ValidationError, match="4 KiB"):
        GeneratedCaseParameter.model_validate(
            base
            | {
                "allowed_values": [
                    "a",
                    "b",
                    *(f"v{i}_" + "\U0001f40d" * 29 for i in range(62)),
                ]
            }
        )


def test_assumption_and_limitation_metadata_is_bounded() -> None:
    payload = valid_draft_payload()
    payload["assumptions"] = ["\U0001f40d" * 513]
    with pytest.raises(ValidationError, match="2 KiB"):
        GeneratedCaseDraft.model_validate(payload)

    payload = valid_draft_payload()
    payload["limitations"] = [f"limit {index}" for index in range(65)]
    with pytest.raises(ValidationError):
        GeneratedCaseDraft.model_validate(payload)

    payload = valid_draft_payload()
    payload["assumptions"] = ["\U0001f40d" * 500 for _ in range(33)]
    with pytest.raises(ValidationError, match="64 KiB"):
        GeneratedCaseDraft.model_validate(payload)


def test_parameter_rejects_kind_incoherence_and_non_scalar_values() -> None:
    with pytest.raises(ValidationError):
        GeneratedCaseParameter.model_validate(float_parameter() | {"allowed_values": [1, 2]})
    with pytest.raises(ValidationError):
        GeneratedCaseParameter.model_validate(float_parameter() | {"default": {"x": 1}})
    with pytest.raises(ValidationError):
        GeneratedCaseParameter.model_validate(float_parameter() | {"default": "0.5"})


def test_json_roundtrip_is_strict_and_immutable() -> None:
    draft = GeneratedCaseDraft.model_validate(valid_draft_payload())
    assert GeneratedCaseDraft.model_validate_json(draft.model_dump_json()) == draft
    with pytest.raises(ValidationError):
        GeneratedCaseDraft.model_validate_json(json.dumps(valid_draft_payload() | {"solver": 13}))
    with pytest.raises(ValidationError):
        draft.experiment_name = "changed"  # type: ignore[misc]


def test_generated_case_draft_view_has_only_api_safe_fields() -> None:
    draft = GeneratedCaseDraft.model_validate(valid_draft_payload())
    view = GeneratedCaseDraftView(
        draft_id="draft-1",
        project_id="project-1",
        plan_id="plan-1",
        plan_version=2,
        version=1,
        provider="glm",
        model="glm-5.1",
        digest="sha256:" + "a" * 64,
        draft=draft,
    )
    assert set(view.model_dump()) == {
        "draft_id",
        "project_id",
        "plan_id",
        "plan_version",
        "version",
        "provider",
        "model",
        "digest",
        "draft",
    }
    serialized = view.model_dump_json()
    assert "api_key" not in serialized
    assert "raw_response" not in serialized
    with pytest.raises(ValidationError):
        GeneratedCaseDraftView.model_validate(view.model_dump() | {"raw_response": "secret"})
    with pytest.raises(ValidationError):
        GeneratedCaseDraftView.model_validate(view.model_dump() | {"digest": "bad"})
