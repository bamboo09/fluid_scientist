import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_asset(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def function_source(script: str, function_name: str) -> str:
    start = script.index(f"function {function_name}(")
    following_functions = (
        position
        for marker in ("\nfunction ", "\nasync function ")
        if (position := script.find(marker, start + 1)) != -1
    )
    end = min(following_functions, default=len(script))
    return script[start:end]


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


def test_polling_failures_show_a_visible_chinese_auto_retry_warning() -> None:
    script = read_asset("apps/web/app.js")
    render_source = function_source(script, "renderTaskCard")

    assert 'warning.textContent = task.warning || ""' in render_source
    assert "warning.hidden = !task.warning" in render_source
    assert "body.append(state, identity, detail, warning, meta)" in render_source

    for function_name in ("pollPlannedExperiment", "pollCustomCase"):
        poll_source = function_source(script, function_name)
        catch_source = poll_source[poll_source.index("} catch (error)") :]
        assert "warning:" in catch_source
        assert "状态查询暂时失败" in catch_source
        assert "将自动重试" in catch_source


def test_polling_uses_capped_backoff_without_resetting_between_attempts() -> None:
    script = read_asset("apps/web/app.js")

    for function_name in ("pollPlannedExperiment", "pollCustomCase"):
        poll_source = function_source(script, function_name)
        assert "pollDelay = 1500" not in poll_source
        assert "schedulePoll(" in poll_source

    schedule_source = function_source(script, "schedulePoll")
    assert re.search(r"Math\.min\([^;]*,\s*10000\)", schedule_source)


def test_deterministic_case_id_is_scoped_to_the_plan_identity() -> None:
    script = read_asset("apps/web/app.js")
    source = function_source(script, "deterministicCaseId")

    assert "currentPlan.plan_id" in source
    assert "currentPlan.plan_version" in source


def test_task_card_visibly_renders_the_submission_timestamp() -> None:
    script = read_asset("apps/web/app.js")
    source = function_source(script, "renderTaskCard")

    assert "task.submittedAt" in source


def test_plan_response_exposes_owning_project_for_safe_restore() -> None:
    """The browser cannot reject stale plan IDs without this server identity."""
    api = read_asset("src/fluid_scientist/api/app.py")
    view_start = api.index("class ExperimentPlanView(BaseModel):")
    view_end = api.index("\n\nclass ", view_start + 1)
    view_source = api[view_start:view_end]
    assert re.search(r"^\s+project_id:\s*str\s*\|\s*None", view_source, re.MULTILINE)

    create_start = api.index("def create_experiment_plan(")
    create_end = api.index("\n    @application.", create_start + 1)
    create_source = api[create_start:create_end]
    assert "project_id=stored.project_id" in create_source

    get_start = api.index("def get_experiment_plan(")
    get_end = api.index("\n    @application.", get_start + 1)
    get_source = api[get_start:get_end]
    assert "project_id=stored.project_id" in get_source


def test_restore_discards_cross_project_plan_and_case_identifiers() -> None:
    script = read_asset("apps/web/app.js")
    restore_source = function_source(script, "restoreActiveExperiment")

    assert "currentPlan.project_id" in restore_source
    assert "currentProject.project_id" in restore_source
    assert "localStorage.removeItem(storageKeys.planId)" in restore_source
    assert "localStorage.removeItem(storageKeys.caseId)" in restore_source
    assert re.search(r"currentPlan\s*=\s*null", restore_source)


def test_new_experiments_are_blocked_while_an_existing_task_is_active() -> None:
    script = read_asset("apps/web/app.js")

    design_source = function_source(script, "designExperimentFromPrompt")
    custom_source = function_source(script, "submitCustomCase")
    for source in (design_source, custom_source):
        assert "canStartExperiment(activeTask)" in source
        assert "已有实验正在运行" in source


def test_old_plan_confirmation_cannot_steal_an_active_experiment_poll() -> None:
    script = read_asset("apps/web/app.js")
    source = function_source(script, "confirmAndSubmitPlan")

    guard = source.index("canStartExperiment(activeTask)")
    warning = source.index("已有实验正在运行", guard)
    preparing = source.index('phase: "preparing"')
    start_polling = source.index("startPolling(")

    assert guard < warning < preparing
    assert warning < start_polling


def test_custom_openfoam_plan_routes_to_reviewed_archive_upload() -> None:
    script = read_asset("apps/web/app.js")
    render_source = function_source(script, "renderPlanCard")
    submit_source = function_source(script, "confirmAndSubmitPlan")

    assert 'plan.experiment_type === "custom_openfoam"' in render_source
    assert "上传并审核算例归档" in render_source
    assert 'openDialog("custom-case-drawer")' in render_source
    custom_render_guard = render_source.index(
        'plan.experiment_type === "custom_openfoam"'
    )
    custom_open = render_source.index('openDialog("custom-case-drawer")')
    custom_return = render_source.index("return;", custom_open)
    built_in_confirm = render_source.index("confirmAndSubmitPlan(confirm)")
    assert custom_render_guard < custom_open < custom_return < built_in_confirm

    assert 'currentPlan.plan.experiment_type === "custom_openfoam"' in submit_source
    assert 'openDialog("custom-case-drawer")' in submit_source
    custom_guard = submit_source.index(
        'currentPlan.plan.experiment_type === "custom_openfoam"'
    )
    compile_call = submit_source.index("/compile`")
    assert custom_guard < compile_call


def test_planning_payload_omits_an_unselected_target() -> None:
    script = read_asset("apps/web/app.js")
    design_source = function_source(script, "designExperimentFromPrompt")

    assert "buildPlanRequest(question, currentProject.project_id, selectedTarget)" in design_source
    assert "target_id: selectedTarget" not in design_source
