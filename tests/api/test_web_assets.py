import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_asset(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def function_source(script: str, function_name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(function_name)}\s*\(", script
    )
    assert match is not None, f"missing function {function_name}"
    following = re.search(r"\n(?:async\s+)?function\s+", script[match.end() :])
    end = match.end() + following.start() if following else len(script)
    return script[match.start() : end]


def test_workbench_is_the_utf8_v5_conversation_interface() -> None:
    html = read_asset("apps/web/index.html")
    app = read_asset("apps/web/v5-app.js")
    cylinder = read_asset("apps/web/cylinder-flow.js")

    for element_id in (
        "session-list",
        "conversation-timeline",
        "composer-form",
        "research-input",
        "action-bar",
        "draft-version-badge",
        "draft-viewer",
    ):
        assert f'id="{element_id}"' in html

    assert "/assets/v5-app.js" in html
    assert "/assets/cylinder-flow.js" in html
    assert 'src="/assets/app.js' not in html
    assert "experiment-prompt" not in html
    assert "\ufffd" not in html + app + cylinder


def test_current_bundle_binds_the_only_research_composer() -> None:
    html = read_asset("apps/web/index.html")
    app = read_asset("apps/web/v5-app.js")

    assert html.count('id="composer-form"') == 1
    assert html.count('id="research-input"') == 1
    assert 'byId("composer-form").addEventListener("submit"' in app
    assert 'sendUserMessage(byId("research-input").value)' in app
    assert 'byId("new-session-btn").addEventListener("click", createNewSession)' in app


def test_model_credentials_are_password_only_and_not_browser_persisted() -> None:
    html = read_asset("apps/web/index.html")
    app = read_asset("apps/web/v5-app.js")

    assert 'id="model-api-key" type="password"' in html
    assert 'autocomplete="off"' in html
    assert 'api_key: key' in app
    assert 'api("/api/v5/model-config"' in app

    storage_calls = re.findall(r"localStorage\.setItem\((.*?)\);", app, re.DOTALL)
    assert storage_calls
    for call in storage_calls:
        lowered = call.lower()
        for forbidden in ("api_key", "api-key", "apikey", "password", "secret"):
            assert forbidden not in lowered


def test_session_identity_is_restored_without_recreating_a_valid_session() -> None:
    app = read_asset("apps/web/v5-app.js")
    init_source = function_source(app, "initSession")

    assert 'localStorage.getItem("v5_sid")' in app
    assert "API.getSession(state.sessionId)" in init_source
    assert "已恢复会话" in init_source
    assert 'localStorage.removeItem("v5_sid")' in init_source
    assert "await createNewSession()" in init_source
    assert "API.createSession()" not in init_source


def test_session_message_flow_uses_the_current_v5_endpoints() -> None:
    app = read_asset("apps/web/v5-app.js")
    send_source = function_source(app, "sendUserMessage")

    assert 'api("/api/v5/sessions"' in app
    assert 'api(`/api/v5/sessions/${id}/messages`' in app
    assert "API.sendMessage(state.sessionId, msg)" in send_source
    assert "API.getSession(state.sessionId)" in send_source
    assert "renderAll()" in send_source


def test_draft_validation_precedes_confirmation_in_the_user_flow() -> None:
    app = read_asset("apps/web/v5-app.js")
    render_actions = function_source(app, "updateActionBar")
    confirm_source = function_source(app, "confirmDraft")
    validate_source = function_source(app, "validateDraft")

    assert 'api(`/api/v5/drafts/${id}/validate`' in app
    assert 'api(`/api/v5/drafts/${id}/confirm`' in app
    assert "重新校验" in render_actions
    assert "确认草案" in render_actions
    assert "API.validateDraft(state.draft.draft_id)" in validate_source
    assert "API.confirmDraft(state.draft.draft_id, state.sessionId)" in confirm_source


def test_case_generation_compile_review_and_submit_are_distinct_steps() -> None:
    app = read_asset("apps/web/v5-app.js")

    for endpoint in (
        'api("/api/v5/case-plans/generate"',
        'api(`/api/v5/case-plans/${id}/compile`',
        'api(`/api/v5/case-plans/${id}/review`',
        'api(`/api/v5/cases/${casePlanId}/submit`',
    ):
        assert endpoint in app

    assert "API.generateCasePlan" in function_source(app, "generateCasePlan")
    assert "API.compileCasePlan" in function_source(app, "compileCase")
    assert "API.reviewCasePlan" in function_source(app, "reviewCase")
    assert "API.submitCase" in function_source(app, "submitToWorkstation")


def test_job_results_keep_postprocessing_and_visualizations_reachable() -> None:
    app = read_asset("apps/web/v5-app.js")
    postprocess_source = function_source(app, "fetchJobResults")
    render_source = function_source(app, "renderPostprocessPanel")

    assert 'api(`/api/v5/jobs/${jobId}/results`' in app
    assert 'api(`/api/v5/jobs/${jobId}/postprocess`' in app
    assert "/api/v5/jobs/${jobId}/visualizations/${filename}" in app
    assert "API.getPostprocessResults(jobId)" in postprocess_source
    assert "visualizations" in postprocess_source
    assert 'class="postprocess-panel"' in render_source


def test_workstation_configuration_keeps_host_key_confirmation_explicit() -> None:
    html = read_asset("apps/web/index.html")
    app = read_asset("apps/web/v5-app.js")

    for element_id in (
        "ws-input-host",
        "ws-input-user",
        "ws-input-port",
        "ws-input-key",
        "ws-input-knownhosts",
    ):
        assert f'id="{element_id}"' in html

    assert 'api(`/api/v5/workstations/${candidateId}/probe`' in app
    assert 'api(`/api/v5/workstations/${candidateId}/confirm-host-key`' in app
    assert 'api(`/api/v5/workstations/${candidateId}/save`' in app


def test_build_identity_is_visible_in_the_workbench_footer() -> None:
    html = read_asset("apps/web/index.html")
    app = read_asset("apps/web/v5-app.js")

    for element_id in ("system-version", "wf-git", "wf-schema", "wf-api"):
        assert f'id="{element_id}"' in html
    assert 'api("/api/system/version")' in app
    assert "loadSystemVersion" in app


def test_three_panel_layout_has_responsive_and_overflow_guards() -> None:
    css = read_asset("apps/web/styles.css")

    assert ".v5-workbench" in css
    assert ".panel-left" in css
    assert ".panel-center" in css
    assert ".panel-right" in css
    assert "min-width: 0" in css
    assert "overflow" in css
    assert "@media" in css
