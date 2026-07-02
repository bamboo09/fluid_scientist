import io
import tarfile

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fluid_scientist.adapters.openai_provider import ExperimentDesign
from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.api.app import app, create_app
from fluid_scientist.experiment_planning import (
    ExperimentPlan,
    ProviderAuthenticationError,
    ProviderModelNotFoundError,
    ProviderOutputError,
    ProviderRequestError,
)
from fluid_scientist.settings import ProviderSettings


def client() -> TestClient:
    return TestClient(app)


def test_health_endpoint_is_credential_free() -> None:
    response = client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "fake"}


def test_demo_endpoint_returns_reported_project() -> None:
    response = client().post(
        "/api/demo",
        json={"question": "How does bend curvature affect pressure drop?"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["workflow_state"] == "REPORTED"
    assert body["report"]["claims"]

    stored = client().get(f"/api/projects/{body['project_id']}")
    assert stored.status_code == 200
    assert stored.json()["project_id"] == body["project_id"]


def test_demo_endpoint_rejects_short_question() -> None:
    response = client().post("/api/demo", json={"question": "bend"})

    assert response.status_code == 422


def test_static_workbench_does_not_expose_skill_navigation() -> None:
    html = client().get("/").text

    assert "实验结果分析与报告" in html
    assert "Skill 候选" not in html


class FakeExperimentDesigner:
    def design_experiment(self, question, *, capabilities):
        assert question.startswith("Design")
        assert "laminar_pipe" in capabilities
        return ExperimentDesign(
            experiment_name="Model Designed Pipe Study",
            experiment_type="laminar_pipe",
            objective="Validate pressure loss against an analytical benchmark.",
            assumptions=("Single-phase incompressible laminar flow",),
            rationale="Use the verified workstation template before broader experiments.",
            requested_outputs=("pressure_drop_pa", "mass_imbalance_percent"),
            case=LaminarPipeCase(
                diameter_m=0.02,
                length_m=2,
                mean_velocity_m_s=0.08,
                kinematic_viscosity_m2_s=1e-6,
            ),
        )


def test_model_experiment_design_endpoint_returns_typed_plan() -> None:
    api = TestClient(create_app(experiment_designer=FakeExperimentDesigner()))

    response = api.post(
        "/api/experiment-designs",
        json={"question": "Design a laminar pipe pressure-loss experiment."},
    )

    assert response.status_code == 200
    assert response.json()["experiment_name"] == "Model Designed Pipe Study"
    assert response.json()["case"]["mean_velocity_m_s"] == 0.08


def test_model_experiment_design_requires_configured_provider() -> None:
    api = TestClient(create_app(experiment_designer=None))

    response = api.post(
        "/api/experiment-designs",
        json={"question": "Design a laminar pipe pressure-loss experiment."},
    )

    assert response.status_code == 503
    assert "OpenAI" in response.json()["detail"]


def test_model_can_be_configured_in_memory_without_echoing_api_key() -> None:
    secret = "sk-test-runtime-only-secret"
    api = TestClient(create_app(experiment_designer=None))

    response = api.post(
        "/api/settings/openai",
        json={
            "api_key": secret,
            "planner_model": "gpt-5.4",
            "extractor_model": "gpt-5.4-mini",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "planner_model": "gpt-5.4",
        "extractor_model": "gpt-5.4-mini",
    }
    assert secret not in response.text
    assert secret not in repr(api.app.state.experiment_designer)


def neutral_pipe_plan() -> ExperimentPlan:
    return ExperimentPlan.model_validate(
        {
            "experiment_type": "laminar_pipe",
            "experiment_name": "Neutral Pipe Study",
            "objective": "Validate pressure loss against the analytical solution.",
            "rationale": "A laminar benchmark provides a deterministic first experiment.",
            "assumptions": ["Steady incompressible Newtonian flow"],
            "limitations": ["The benchmark excludes turbulent effects"],
            "requested_outputs": ["pressure_drop", "mass_imbalance", "residuals"],
            "convergence_targets": {
                "residual_tolerance": 1e-6,
                "mass_imbalance_percent": 0.1,
            },
            "case": {
                "diameter_m": 0.02,
                "length_m": 2.0,
                "mean_velocity_m_s": 0.08,
                "kinematic_viscosity_m2_s": 1e-6,
                "density_kg_m3": 998.2,
                "axial_cells": 80,
                "radial_cells": 10,
            },
            "parameter_sweeps": [],
        }
    )


class FakePlanDesigner:
    def __init__(self, outcome: ExperimentPlan | Exception | None = None) -> None:
        self.outcome = outcome or neutral_pipe_plan()
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def design_experiment(self, question, *, capabilities):
        self.calls.append((question, capabilities))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize("provider", ["openai", "glm", "deepseek"])
def test_model_configuration_is_ephemeral_and_credential_free(provider: str) -> None:
    secret = f"{provider}-runtime-secret"
    received: list[ProviderSettings] = []
    designer = FakePlanDesigner()

    def factory(settings: ProviderSettings) -> FakePlanDesigner:
        received.append(settings)
        return designer

    api = TestClient(create_app(plan_provider_factory=factory))
    response = api.post(
        "/api/model-configurations",
        json={"provider": provider, "model": "chosen-model", "api_key": secret},
    )

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "provider": provider,
        "model": "chosen-model",
    }
    assert received[0].provider == provider
    assert received[0].api_key == SecretStr(secret)
    assert secret not in response.text
    assert secret not in repr(vars(api.app.state))

    stored = api.get("/api/model-configurations")
    assert stored.json() == response.json()


def test_model_configuration_reports_unconfigured_state() -> None:
    api = TestClient(create_app())

    response = api.get("/api/model-configurations")

    assert response.status_code == 200
    assert response.json() == {"configured": False, "provider": None, "model": None}


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "anthropic", "model": "m", "api_key": "key"},
        {"provider": "glm", "model": "m", "api_key": "   "},
        {"provider": "glm", "model": "m", "api_key": "key", "base_url": "x"},
    ],
)
def test_model_configuration_rejects_invalid_or_extra_fields(payload: dict[str, str]) -> None:
    api = TestClient(create_app(plan_provider_factory=lambda settings: FakePlanDesigner()))

    response = api.post("/api/model-configurations", json=payload)

    assert response.status_code == 422


def test_experiment_capabilities_are_ui_safe_and_complete() -> None:
    response = TestClient(create_app()).get("/api/experiment-capabilities")

    assert response.status_code == 200
    body = response.json()
    assert [item["experiment_type"] for item in body] == [
        "laminar_pipe",
        "cylinder_flow",
        "lid_driven_cavity",
        "custom_openfoam",
    ]
    assert all(item["label"] and item["required_outputs"] for item in body)
    assert "host" not in response.text.lower()
    assert "command" not in response.text.lower()


def test_experiment_plan_requires_configured_neutral_provider() -> None:
    api = TestClient(create_app())

    response = api.post(
        "/api/experiment-plans",
        json={"question": "Design a laminar pressure-loss benchmark."},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Experiment plan provider is not configured"}


def test_experiment_plan_returns_provider_neutral_typed_response() -> None:
    designer = FakePlanDesigner()
    api = TestClient(
        create_app(
            plan_designer=designer,
            plan_provider_name="deepseek",
            plan_model_name="deepseek-chat",
        )
    )

    response = api.post(
        "/api/experiment-plans",
        json={"question": "Design a laminar pressure-loss benchmark."},
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "deepseek"
    assert response.json()["model"] == "deepseek-chat"
    assert response.json()["plan"]["experiment_type"] == "laminar_pipe"
    capabilities = designer.calls[0][1]
    assert set(
        ("laminar_pipe", "cylinder_flow", "lid_driven_cavity", "custom_openfoam")
    ).issubset(capabilities)
    assert "OpenFOAM-13" in capabilities
    assert "workstation_openfoam" in capabilities


@pytest.mark.parametrize("question", ["short", "x" * 2001])
def test_experiment_plan_question_is_strict(question: str) -> None:
    api = TestClient(
        create_app(
            plan_designer=FakePlanDesigner(),
            plan_provider_name="glm",
            plan_model_name="glm-4.5",
        )
    )

    response = api.post("/api/experiment-plans", json={"question": question})

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (
            ProviderAuthenticationError(
                "raw secret", provider="glm", model="model"
            ),
            401,
            "Provider authentication failed",
        ),
        (
            ProviderOutputError("raw secret", provider="glm", model="model"),
            422,
            "Provider returned an invalid experiment plan",
        ),
        (
            ProviderModelNotFoundError(
                "raw secret", provider="glm", model="model"
            ),
            422,
            "Provider model was not found",
        ),
        (
            ProviderRequestError("raw secret", provider="glm", model="model"),
            502,
            "Experiment plan provider request failed",
        ),
    ],
)
def test_experiment_plan_maps_provider_errors_without_leaking_details(
    error: Exception, expected_status: int, expected_detail: str
) -> None:
    api = TestClient(
        create_app(
            plan_designer=FakePlanDesigner(error),
            plan_provider_name="glm",
            plan_model_name="model",
        )
    )

    response = api.post(
        "/api/experiment-plans",
        json={"question": "Design a laminar pressure-loss benchmark."},
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "raw secret" not in response.text


def test_custom_openfoam_case_can_be_validated_before_submission() -> None:
    files = {
        "0/U": "internalField uniform (0 0 0);",
        "constant/physicalProperties": "nu 1e-6;",
        "system/controlDict": "solver incompressibleFluid; endTime 100;",
        "system/fvSchemes": "ddtSchemes {}",
        "system/fvSolution": "solvers {}",
        "system/blockMeshDict": "vertices ();",
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, text in files.items():
            payload = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))

    response = client().post(
        "/api/custom-cases/validate",
        content=output.getvalue(),
        headers={"Content-Type": "application/gzip"},
    )

    assert response.status_code == 200
    assert response.json()["solver"] == "incompressibleFluid"
    assert response.json()["needs_block_mesh"] is True
