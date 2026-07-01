import io
import tarfile

from fastapi.testclient import TestClient

from fluid_scientist.adapters.openai_provider import ExperimentDesign
from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.api.app import app, create_app


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
