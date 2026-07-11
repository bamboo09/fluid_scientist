import io
import tarfile
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fluid_scientist.adapters.openai_provider import (
    ExperimentDesign,
    OpenAIPlanProvider,
    OpenAIResponsesProvider,
)
from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import app, create_app
from fluid_scientist.execution_targets.base import ExecutionTargetCapability
from fluid_scientist.experiment_planning import (
    ExperimentPlan,
    ProviderAuthenticationError,
    ProviderModelNotFoundError,
    ProviderOutputError,
    ProviderRequestError,
)
from fluid_scientist.ports import StoredExperimentPlan
from fluid_scientist.settings import AppSettings, OpenAISettings, ProviderSettings


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

    assert "V5 对话式科研工作台" in html
    assert "研究任务" in html
    assert "研究方案" in html
    assert "/assets/v5-app.js?v=" in html
    assert "__BUILD_SHA__" not in html
    assert "Skill 候选" not in html


def test_system_build_info_exposes_runtime_identity() -> None:
    response = client().get("/api/system/build-info")

    assert response.status_code == 200
    body = response.json()
    assert body["git_sha"]
    assert body["source_root"].endswith("AI FOR SCIENCE")
    assert body["package_path"].endswith("src\\fluid_scientist") or body[
        "package_path"
    ].endswith("src/fluid_scientist")
    assert body["frontend_root"].endswith("apps\\web") or body[
        "frontend_root"
    ].endswith("apps/web")
    assert len(body["frontend_index_hash"]) == 64
    assert body["runtime_mode"] in {"local", "docker"}


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
    assert secret not in repr(api.app.state.model_configuration)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "api_key": "   ",
            "planner_model": "gpt-5.4",
            "extractor_model": "gpt-5.4-mini",
        },
        {
            "api_key": "secret",
            "planner_model": "  ",
            "extractor_model": "gpt-5.4-mini",
        },
        {
            "api_key": "secret",
            "planner_model": "gpt-5.4",
            "extractor_model": "\t\n",
        },
        {
            "api_key": "secret",
            "planner_model": "gpt-5.4",
            "extractor_model": "gpt-5.4-mini",
            "base_url": "https://attacker.invalid",
        },
    ],
)
def test_legacy_openai_configuration_rejects_blank_or_extra_fields(
    payload: dict[str, str],
) -> None:
    api = TestClient(create_app())

    response = api.post("/api/settings/openai", json=payload)

    assert response.status_code == 422


def test_runtime_openai_fallback_preserves_explicit_legacy_injection() -> None:
    legacy = FakeExperimentDesigner()
    settings = AppSettings(
        openai=OpenAISettings(
            api_key=SecretStr("runtime-secret"),
            planner_model="gpt-runtime",
            extractor_model="gpt-extractor",
        )
    )

    api = create_app(settings=settings, experiment_designer=legacy)

    snapshot = api.state.model_configuration
    assert snapshot.legacy_designer is legacy
    assert isinstance(snapshot.plan_designer, OpenAIPlanProvider)
    assert snapshot.provider == "openai"
    assert snapshot.model == "gpt-runtime"


def neutral_pipe_plan(experiment_name: str = "Neutral Pipe Study") -> ExperimentPlan:
    return ExperimentPlan.model_validate(
        {
            "experiment_type": "laminar_pipe",
            "experiment_name": experiment_name,
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


def test_model_configuration_rejects_inconsistent_injection() -> None:
    with pytest.raises(ValueError, match="plan_designer"):
        create_app(
            plan_designer=None,
            plan_provider_name="glm",
            plan_model_name="glm-4.5",
        )


def test_openai_legacy_configuration_updates_neutral_provider_coherently() -> None:
    neutral_settings: list[ProviderSettings] = []
    legacy_settings: list[OpenAISettings] = []
    neutral = FakePlanDesigner()
    legacy = FakeExperimentDesigner()

    def plan_factory(settings: ProviderSettings) -> FakePlanDesigner:
        neutral_settings.append(settings)
        return neutral

    def legacy_factory(settings: OpenAISettings) -> FakeExperimentDesigner:
        legacy_settings.append(settings)
        return legacy

    api = TestClient(
        create_app(
            plan_provider_factory=plan_factory,
            legacy_provider_factory=legacy_factory,
        )
    )

    response = api.post(
        "/api/settings/openai",
        json={
            "api_key": "coherent-secret",
            "planner_model": "gpt-coherent",
            "extractor_model": "gpt-extractor",
        },
    )

    assert response.status_code == 200
    assert neutral_settings[0].model == "gpt-coherent"
    assert legacy_settings[0].planner_model == "gpt-coherent"
    assert api.get("/api/model-configurations").json() == {
        "configured": True,
        "provider": "openai",
        "model": "gpt-coherent",
    }
    assert api.app.state.model_configuration.plan_designer is neutral
    assert api.app.state.model_configuration.legacy_designer is legacy
    assert "coherent-secret" not in repr(api.app.state.model_configuration)


def test_openai_neutral_configuration_prepares_matching_legacy_provider() -> None:
    legacy_settings: list[OpenAISettings] = []

    def legacy_factory(settings: OpenAISettings) -> FakeExperimentDesigner:
        legacy_settings.append(settings)
        return FakeExperimentDesigner()

    api = TestClient(
        create_app(
            plan_provider_factory=lambda settings: FakePlanDesigner(),
            legacy_provider_factory=legacy_factory,
        )
    )

    response = api.post(
        "/api/model-configurations",
        json={"provider": "openai", "model": "gpt-neutral", "api_key": "secret"},
    )

    assert response.status_code == 200
    assert legacy_settings[0].planner_model == "gpt-neutral"
    assert legacy_settings[0].extractor_model == "gpt-neutral"
    legacy_response = api.post(
        "/api/experiment-designs",
        json={"question": "Design a laminar pipe pressure-loss experiment."},
    )
    assert legacy_response.status_code == 200


@pytest.mark.parametrize("provider", ["glm", "deepseek"])
def test_non_openai_configuration_disables_stale_legacy_designer(provider: str) -> None:
    api = TestClient(
        create_app(
            experiment_designer=FakeExperimentDesigner(),
            plan_provider_factory=lambda settings: FakePlanDesigner(),
        )
    )
    configured = api.post(
        "/api/model-configurations",
        json={"provider": provider, "model": "chosen-model", "api_key": "secret"},
    )

    response = api.post(
        "/api/experiment-designs",
        json={"question": "Design a laminar pipe pressure-loss experiment."},
    )

    assert configured.status_code == 200
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Selected provider supports /api/experiment-plans only"
    }


def test_real_openai_factories_construct_coherent_snapshot_without_network() -> None:
    api = TestClient(create_app())

    response = api.post(
        "/api/model-configurations",
        json={"provider": "openai", "model": "gpt-real", "api_key": "secret"},
    )

    snapshot = api.app.state.model_configuration
    assert response.status_code == 200
    assert isinstance(snapshot.plan_designer, OpenAIPlanProvider)
    assert isinstance(snapshot.legacy_designer, OpenAIResponsesProvider)
    assert snapshot.provider == "openai"
    assert snapshot.model == "gpt-real"


def test_startup_openai_settings_initialize_coherent_snapshot() -> None:
    settings = AppSettings(
        openai=OpenAISettings(
            api_key=SecretStr("startup-secret"),
            planner_model="gpt-startup",
            extractor_model="gpt-extractor",
        )
    )

    api = create_app(settings=settings)

    snapshot = api.state.model_configuration
    assert snapshot.provider == "openai"
    assert snapshot.model == "gpt-startup"
    assert isinstance(snapshot.plan_designer, OpenAIPlanProvider)
    assert isinstance(snapshot.legacy_designer, OpenAIResponsesProvider)
    assert "startup-secret" not in repr(snapshot)


def test_openai_reconfiguration_is_atomically_visible_to_readers() -> None:
    block_new_legacy = Event()
    legacy_factory_entered = Event()

    def plan_factory(settings: ProviderSettings) -> FakePlanDesigner:
        return FakePlanDesigner(neutral_pipe_plan(f"{settings.provider}-{settings.model}"))

    def legacy_factory(settings: OpenAISettings) -> FakeExperimentDesigner:
        if settings.planner_model == "model-b":
            legacy_factory_entered.set()
            assert block_new_legacy.wait(timeout=5)
        return FakeExperimentDesigner()

    api = TestClient(
        create_app(
            plan_provider_factory=plan_factory,
            legacy_provider_factory=legacy_factory,
        )
    )
    initial = api.post(
        "/api/model-configurations",
        json={"provider": "openai", "model": "model-a", "api_key": "secret-a"},
    )
    assert initial.status_code == 200

    with ThreadPoolExecutor(max_workers=2) as executor:
        updating = executor.submit(
            api.post,
            "/api/model-configurations",
            json={"provider": "openai", "model": "model-b", "api_key": "secret-b"},
        )
        assert legacy_factory_entered.wait(timeout=5)
        during_metadata = api.get("/api/model-configurations").json()
        during_plan = api.post(
            "/api/experiment-plans",
            json={"question": "Design a laminar pressure-loss benchmark."},
        ).json()
        block_new_legacy.set()
        assert updating.result(timeout=5).status_code == 200

    assert during_metadata == {
        "configured": True,
        "provider": "openai",
        "model": "model-a",
    }
    assert during_plan["model"] == "model-a"
    assert during_plan["plan"]["experiment_name"] == "openai-model-a"
    after_plan = api.post(
        "/api/experiment-plans",
        json={"question": "Design a laminar pressure-loss benchmark."},
    ).json()
    assert after_plan["model"] == "model-b"
    assert after_plan["plan"]["experiment_name"] == "openai-model-b"


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "anthropic", "model": "m", "api_key": "key"},
        {"provider": "glm", "model": "m", "api_key": "   "},
    ],
)
def test_model_configuration_rejects_invalid_or_extra_fields(payload: dict[str, str]) -> None:
    api = TestClient(create_app(plan_provider_factory=lambda settings: FakePlanDesigner()))

    response = api.post("/api/model-configurations", json=payload)

    assert response.status_code == 422


def test_model_configuration_accepts_base_url() -> None:
    api = TestClient(create_app(plan_provider_factory=lambda settings: FakePlanDesigner()))

    response = api.post(
        "/api/model-configurations",
        json={
            "provider": "glm",
            "model": "glm-4-flash",
            "api_key": "key",
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "glm-4-flash"


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
    project_id = api.post(
        "/api/projects",
        json={"question": "Measure laminar pressure loss in a pipe."},
    ).json()["project_id"]

    response = api.post(
        "/api/experiment-plans",
        json={
            "question": "Design a laminar pressure-loss benchmark.",
            "project_id": project_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "deepseek"
    assert response.json()["model"] == "deepseek-chat"
    assert response.json()["project_id"] == project_id
    assert response.json()["plan"]["experiment_type"] == "laminar_pipe"
    capabilities = designer.calls[0][1]
    assert set(
        ("laminar_pipe", "cylinder_flow", "lid_driven_cavity", "custom_openfoam")
    ).issubset(capabilities)
    assert "OpenFOAM-13" in capabilities
    assert "workstation_openfoam" in capabilities


def test_experiment_plan_uses_the_selected_execution_target_capability() -> None:
    class KnownTarget:
        target_id = "known-target"
        kind = "hpc_slurm"
        declared_capabilities = ("OpenFOAM-13", "slurm")

        def doctor(self) -> ExecutionTargetCapability:
            raise AssertionError("planning must not call doctor")

    designer = FakePlanDesigner()
    api = TestClient(
        create_app(
            execution_targets=(KnownTarget(),),
            plan_designer=designer,
            plan_provider_name="deepseek",
            plan_model_name="deepseek-chat",
        )
    )

    response = api.post(
        "/api/experiment-plans",
        json={
            "question": "Design a laminar pressure-loss benchmark.",
            "target_id": "known-target",
        },
    )

    assert response.status_code == 200
    capabilities = designer.calls[0][1]
    assert "hpc_slurm" in capabilities
    assert "known-target" in capabilities
    assert "slurm" in capabilities

    unknown = api.post(
        "/api/experiment-plans",
        json={
            "question": "Design a laminar pressure-loss benchmark.",
            "target_id": "missing-target",
        },
    )
    assert unknown.status_code == 404


def test_stored_experiment_plan_can_be_recovered_by_id(tmp_path) -> None:
    repository = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'plans.db'}")
    plan = neutral_pipe_plan("Recovered Pipe Study")
    repository.store_experiment_plan(
        StoredExperimentPlan(
            plan_id="plan-recovery-1",
            project_id=None,
            version=3,
            provider="glm",
            model="glm-4.5-air",
            plan_json=plan.model_dump_json(),
        )
    )
    api = TestClient(create_app(repository=repository))

    response = api.get("/api/experiment-plans/plan-recovery-1")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "glm",
        "model": "glm-4.5-air",
        "plan_id": "plan-recovery-1",
        "plan_version": 3,
        "project_id": None,
        "plan": plan.model_dump(mode="json"),
    }


def test_missing_experiment_plan_recovery_returns_404(tmp_path) -> None:
    repository = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'plans.db'}")
    api = TestClient(create_app(repository=repository))

    response = api.get("/api/experiment-plans/missing-plan")

    assert response.status_code == 404
    assert response.json() == {"detail": "experiment plan not found"}


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
            "模型生成的实验计划未通过严格参数校验；请重试或补充研究条件。",
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
