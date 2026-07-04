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


def function_css_rule(css: str, selector: str) -> str:
    start = css.index(f"{selector} {{")
    end = css.index("}", start) + 1
    return css[start:end]


def test_workbench_is_a_utf8_conversation_first_interface() -> None:
    assets = {
        path: read_asset(path)
        for path in (
            "apps/web/index.html",
            "apps/web/app.js",
            "apps/web/operation-lifecycle.js",
            "apps/web/operation-state.js",
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

    plan = script.index('"/api/plan-operations"')
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
        f"storageKeys.{key}"
        for key in ("projectId", "planId", "caseId", "targetId", "operationId")
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


def test_planning_uses_recoverable_operation_endpoint_and_identity() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")
    state = read_asset("apps/web/operation-state.js")

    assert 'id="active-operation"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-busy="false"' in html
    assert '"/api/plan-operations"' in script
    assert "storageKeys.operationId" in script
    assert "/api/operations/${operationId}" in script
    assert "elapsed" in state
    assert "model_planning" in state
    assert "schema_correction" in state
    assert "storing_plan" in state


def test_operation_controls_and_polling_are_stale_response_safe() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")
    lifecycle = read_asset("apps/web/operation-lifecycle.js")

    for element_id in (
        "operation-stage",
        "operation-status",
        "operation-elapsed",
        "cancel-operation",
        "retry-operation",
    ):
        assert f'id="{element_id}"' in html

    assert 'byId("cancel-operation")?.addEventListener' in script
    assert 'byId("retry-operation")?.addEventListener' in script
    assert 'method: "DELETE"' in script
    assert "this.generation" in lifecycle
    assert "AbortController" in lifecycle
    assert "Math.min" in lifecycle
    assert 'window.addEventListener("beforeunload"' in script


def test_operation_progress_accessibility_avoids_elapsed_chatter() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")
    operation_tag = re.search(r'<article id="active-operation"[^>]*>', html)
    progress_tag = re.search(r'<div class="operation-progress"[^>]*>', html)

    assert operation_tag is not None
    assert 'aria-live=' not in operation_tag.group(0)
    assert 'aria-busy="false"' in operation_tag.group(0)
    assert 'id="operation-announcement"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-atomic="true"' in html
    assert progress_tag is not None
    assert "aria-valuenow" not in progress_tag.group(0)

    render_source = function_source(script, "renderOperation")
    assert 'progress?.removeAttribute("aria-valuenow")' in render_source
    assert 'progress?.setAttribute("aria-valuenow", String(view.percent))' in render_source
    assert "announceOperation(operation, message)" in render_source
    announce_source = function_source(script, "announceOperation")
    assert "lastOperationAnnouncement" in announce_source
    assert "operation-elapsed" not in announce_source

    stream_tag = re.search(r'<section id="conversation-stream"[^>]*>', html)
    assert stream_tag is not None
    assert 'aria-live=' not in stream_tag.group(0)
    stream_start = html.index(stream_tag.group(0))
    stream_end = html.index("</section>", stream_start)
    operation_position = html.index('id="active-operation"')
    assert stream_start < operation_position < stream_end
    assert 'aria-live=' not in html[stream_start:html.index('id="operation-announcement"')]


def test_operation_recovery_starts_independently_from_target_discovery() -> None:
    script = read_asset("apps/web/app.js")
    init_source = function_source(script, "init")

    bind = init_source.index("bindEvents()")
    model = init_source.index("loadModelConfiguration()")
    recover = init_source.index("restoreActiveOperation()")
    target = init_source.index("loadExecutionTargets()")
    assert bind < model
    assert bind < recover
    assert bind < target
    assert "await loadExecutionTargets()" not in init_source
    assert "Promise.allSettled" not in init_source
    assert "experimentRecovery = restoreActiveExperiment()" in init_source
    assert "Promise.all([operationRecovery, modelLoad, experimentRecovery])" in init_source

    recovery_source = function_source(script, "restoreActiveOperation")
    assert "localStorage.getItem(storageKeys.operationId)" in recovery_source
    assert "startOperationPolling" in recovery_source
    missing_source = function_source(script, "clearMissingOperation")
    assert "localStorage.removeItem(storageKeys.operationId)" in missing_source
    assert "error?.status === 404" in read_asset("apps/web/operation-lifecycle.js")


def test_operation_card_only_displays_server_safe_error_text() -> None:
    script = read_asset("apps/web/app.js")
    render_source = function_source(script, "renderOperation")

    assert "operation.safe_error" in render_source
    assert "operation.message" not in render_source


def test_persisted_target_allows_planning_before_target_discovery_returns() -> None:
    script = read_asset("apps/web/app.js")
    prefix = script[: script.index("function text(")]
    composer = function_source(script, "refreshComposer")

    assert "selectedTarget = localStorage.getItem(storageKeys.targetId)" in prefix
    assert "targetSelected: Boolean(selectedTarget)" in composer
    assert "loadExecutionTargets" not in composer


def test_paused_terminal_operation_retry_does_not_submit_a_new_plan() -> None:
    script = read_asset("apps/web/app.js")
    retry_source = function_source(script, "retryActiveOperation")

    assert "operationPoller?.paused && activeOperationId" in retry_source
    assert "!operationView(activeOperation || {}).terminal" not in retry_source
    paused_branch = retry_source[: retry_source.index("const question")]
    assert "operationPoller.resume()" in paused_branch
    assert "submitPlanOperation" not in paused_branch
    assert "return;" in paused_branch


def test_edit_mode_hides_the_top_question_to_avoid_duplicate_presentation() -> None:
    script = read_asset("apps/web/app.js")
    restore_source = function_source(script, "restoreResearchComposer")

    assert "researchQuestionCard.hidden = true" in restore_source
    assert 'researchQuestionText.textContent = ""' in restore_source
    assert "researchForm.hidden = false" in restore_source


def test_new_research_cancels_active_planning_before_clearing_identity() -> None:
    script = read_asset("apps/web/app.js")
    lifecycle = read_asset("apps/web/operation-lifecycle.js")
    reset_source = function_source(script, "resetResearchSession")
    clear_source = function_source(script, "clearResearchSession")

    assert "async function resetResearchSession()" in script
    assert "await cancelPlanningBeforeReset" in reset_source
    assert 'method: "DELETE"' in reset_source
    assert "resumePolling: startOperationPolling" in reset_source
    assert "clearSession: clearResearchSession" in reset_source
    assert "localStorage.removeItem" not in reset_source
    assert "localStorage.removeItem(key)" in clear_source
    cancel = lifecycle.index("await cancelOperation(operationId)")
    clear = lifecycle.index("clearSession();", cancel)
    assert cancel < clear


def test_question_and_provider_data_are_never_persisted_during_planning() -> None:
    script = read_asset("apps/web/app.js")
    design_source = function_source(script, "designExperimentFromPrompt")
    submit_source = function_source(script, "submitPlanOperation")

    assert "localStorage.setItem" not in design_source
    assert "localStorage.setItem" not in submit_source
    assert "persist(storageKeys.operationId" in submit_source
    assert "persist(storageKeys.planId" not in submit_source
    for forbidden in ("api_key", "provider_payload", "plan_json"):
        assert forbidden not in design_source + submit_source


def test_submitted_question_has_one_top_level_presentation_not_a_second_dialogue() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")

    assert html.index('id="research-question-card"') < html.index('id="active-operation"')
    assert html.index('id="active-operation"') < html.index('id="research-form"')
    design_source = function_source(script, "designExperimentFromPrompt")
    assert "showResearchQuestion(question)" in design_source
    assert 'appendConversation("user", question)' not in design_source


def test_structured_results_postprocessing_and_analysis_remain_reachable() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")
    controller = read_asset("apps/web/postprocess.js")
    state = read_asset("apps/web/result-state.js")

    assert 'id="postprocess-results"' in html
    assert "plannedResultUrl" in script
    assert "/experiment-plans/${encodedPlanId}/${safeAction}?" in state
    assert "renderPostprocessResults" in controller
    assert "renderExperimentAnalysis" in script
    for result_field in (
        "mesh",
        "residuals",
        "numeric_times",
        "observables",
        "paraview_file",
    ):
        assert result_field in controller


def test_both_postprocess_buttons_use_one_reveal_controller() -> None:
    html = read_asset("apps/web/index.html")
    app = read_asset("apps/web/app.js")
    controller = read_asset("apps/web/postprocess.js")

    assert 'id="view-postprocess"' in html
    assert 'const staticPostprocessButton = byId("view-postprocess")' in app
    assert 'from "./postprocess.js"' in app
    assert "revealPostprocess" in controller
    assert "bindPostprocessButton" in app
    assert "bindPostprocessReveal" in app
    assert "postButton.addEventListener" not in app
    bind_source = function_source(app, "bindPostprocessButton")
    assert "const sessionKey = postprocessSessionKey()" in bind_source
    assert "fetchCurrentPostprocessResults(sessionKey)" in bind_source
    render_source = function_source(app, "renderResultsCard")
    assert "renderPostprocessResults(results)" not in render_source
    assert 'analyzeButton.textContent = "实验结果分析与报告"' in render_source
    for behavior in ("scrollIntoView", "focus", "aria-busy", "fetchResults"):
        assert behavior in controller
    assert "renderCavityCenterlineProfile" in controller
    assert "renderCylinderForceHistory" in controller


def test_result_analysis_is_bound_to_complete_identity_and_stale_safe() -> None:
    app = read_asset("apps/web/app.js")
    state = read_asset("apps/web/result-state.js")
    analyze = function_source(app, "analyzeExperimentResults")

    for key in ("projectId", "planId", "caseId", "targetId"):
        assert key in state
    assert "boundIdentity" in state
    assert "AnalysisRequestController" in app
    assert "latestResults === resultContext" in analyze
    assert "postprocessSessionVersion === generation" in analyze
    assert "const analyzeButton = event?.currentTarget" in analyze
    assert "plannedResultUrl({ ...identity" in analyze


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


def test_expanded_plan_parameters_cannot_overlap_the_context_rail() -> None:
    css = read_asset("apps/web/styles.css")

    assert ".work-card {" in css
    assert "min-width: 0;" in function_css_rule(css, ".work-card")
    details_rule = function_css_rule(css, ".work-card.plan-card > details")
    assert "max-width: 100%;" in details_rule
    assert "overflow: hidden;" in details_rule
    pre_rule = function_css_rule(css, ".work-card.plan-card > details pre")
    for declaration in (
        "width: 100%;",
        "max-width: 100%;",
        "box-sizing: border-box;",
        "white-space: pre-wrap;",
        "overflow-wrap: anywhere;",
    ):
        assert declaration in pre_rule


def test_custom_plan_explains_why_an_openfoam_case_file_is_required() -> None:
    assets = read_asset("apps/web/index.html") + read_asset("apps/web/app.js")

    assert "OpenFOAM Case 文件夹" in assets
    assert "模型生成的是实验计划" in assets
    assert "不能直接作为可执行算例" in assets


def test_planning_waits_for_a_declared_target_without_checking_reachability() -> None:
    script = read_asset("apps/web/app.js")
    design_source = function_source(script, "submitPlanOperation")

    assert "buildPlanRequest(question, currentProject.project_id, selectedTarget)" in design_source
    assert "target_id: selectedTarget" not in design_source
    assert "if (!selectedTarget)" in design_source
    assert "loadExecutionTargets" not in design_source


def test_submitted_research_question_becomes_the_session_heading() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")

    for element_id in (
        "welcome-message",
        "research-question-card",
        "research-question-text",
        "start-new-experiment",
    ):
        assert f'id="{element_id}"' in html

    source = function_source(script, "showResearchQuestion")
    assert "welcomeMessage.hidden = true" in source
    assert "researchQuestionText.textContent = question" in source
    assert "researchQuestionCard.hidden = false" in source
    assert "researchForm.hidden = true" in source
    assert "startNewExperiment.hidden = false" in source

    design_source = function_source(script, "designExperimentFromPrompt")
    assert "showResearchQuestion(question)" in design_source
    assert 'appendConversation("user", question)' not in design_source


def test_stale_restore_warning_is_friendly_and_hides_internal_ids() -> None:
    script = read_asset("apps/web/app.js")
    restore_source = function_source(script, "restoreActiveExperiment")

    assert "已检测到上次实验的过期草稿" in restore_source
    assert "stalePlanId" not in restore_source
    assert "recoveredProjectId" not in restore_source
