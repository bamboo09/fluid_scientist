import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_asset(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_workbench_is_a_utf8_conversation_first_interface() -> None:
    assets = {
        path: read_asset(path)
        for path in (
            "apps/web/index.html",
            "apps/web/app.js",
            "apps/web/workbench-state.js",
        )
    }
    html = assets["apps/web/index.html"]
    combined = "".join(assets.values())

    for element_id in (
        "conversation-stream",
        "experiment-prompt",
        "design-experiment",
        "task-context",
        "model-settings",
        "custom-case-drawer",
    ):
        assert f'id="{element_id}"' in html

    assert "\ufffd" not in combined
    for broken in (
        "璁捐瀹為獙",
        "鐎圭偤鐛",
        "缁夋垹鐖",
        "瀹搞儰缍旂粩",
        "宸ヤ綔绔",
        "灏氭湭",
    ):
        assert broken not in combined


def test_workbench_controller_preserves_the_approved_operation_order() -> None:
    script = read_asset("apps/web/app.js")

    for function_name in (
        "designExperimentFromPrompt",
        "confirmAndSubmitPlan",
        "renderTaskCard",
        "restoreActiveExperiment",
    ):
        assert function_name in script

    plan = script.index('"/api/experiment-plans"')
    compile_plan = script.index("/compile`", plan)
    approval_endpoint = script.index("/approvals", compile_plan)
    gate_two = script.index('gate: "GATE_2"', approval_endpoint)
    plan_id = script.index("plan_id: currentPlan.plan_id", approval_endpoint)
    plan_version = script.index(
        "plan_version: currentPlan.plan_version", approval_endpoint
    )
    archive_sha256 = script.index(
        "archive_sha256: currentCompilation.archive_sha256", approval_endpoint
    )
    submit = script.index("/submit`", approval_endpoint)

    assert plan < compile_plan < approval_endpoint < gate_two < submit
    for binding_field in (plan_id, plan_version, archive_sha256):
        assert approval_endpoint < binding_field < submit


def test_submitted_state_requires_an_external_job_identity() -> None:
    script = read_asset("apps/web/app.js")

    external_job = script.index("response.external_job_id ?? response.job_id")
    guard = script.index("if (!externalJobId)", external_job)
    submitted_marker = 'phase: "submitted"'
    assert script.count(submitted_marker) == 1
    submitted = script.index(submitted_marker)
    assert external_job < guard < submitted


def test_model_credentials_are_never_persisted_in_the_browser() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")

    assert 'id="model-api-key"' in html
    assert 'type="password"' in html
    assert '"/api/model-configurations"' in script
    assert 'modelApiKey.value = ""' in script

    approved_storage_keys = {
        f"storageKeys.{key}" for key in ("projectId", "planId", "caseId", "targetId")
    }
    storage_calls = re.findall(
        r"localStorage\.setItem\((.*?)\);", script, flags=re.DOTALL
    )
    for call in storage_calls:
        normalized = " ".join(call.split())
        first_argument = normalized.split(",", maxsplit=1)[0]
        assert first_argument in approved_storage_keys
        lowered = normalized.lower()
        for secret_marker in (
            "modelapikey",
            "api_key",
            "api-key",
            "password",
            "credential",
            "secret",
        ):
            assert secret_marker not in lowered


def test_custom_case_is_validated_before_submit_and_then_polled() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")

    for element_id in (
        "custom-case-file",
        "validate-custom-case",
        "submit-custom-case",
    ):
        assert f'id="{element_id}"' in html

    validate = script.index('"/api/custom-cases/validate"')
    validation_guard = script.index("if (!validatedCustomCase)", validate)
    submit = script.index('"/api/custom-cases/submit"', validation_guard)
    poll = script.index("pollCustomCase", submit)
    assert validate < validation_guard < submit < poll


def test_active_experiment_restores_all_identifiers_without_resubmitting() -> None:
    script = read_asset("apps/web/app.js")
    state = read_asset("apps/web/workbench-state.js")

    for key in ("projectId", "planId", "caseId", "targetId"):
        assert f"localStorage.getItem(storageKeys.{key})" in script
        assert f"{key}: \"fluid-scientist-" in state

    restore_start = script.index("async function restoreActiveExperiment")
    restore_end = script.index("\nasync function ", restore_start + 1)
    restore_source = script[restore_start:restore_end]
    assert "/submit" not in restore_source
    assert "confirmAndSubmitPlan(" not in restore_source


def test_structured_results_postprocessing_and_analysis_remain_reachable() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")

    assert 'id="postprocess-results"' in html
    assert "/experiment-plans/${planId}/results?" in script
    assert "/experiment-plans/${planId}/analysis?" in script
    assert "renderPostprocessResults" in script
    assert "renderExperimentAnalysis" in script
    for result_field in (
        "mesh",
        "residuals",
        "numeric_times",
        "observables",
        "paraview_file",
    ):
        assert result_field in script
