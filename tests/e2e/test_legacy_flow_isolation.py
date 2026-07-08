"""Tests verifying legacy ExperimentPlan flow is isolated from Workflow V2.

Commit 6: The legacy plan-based flow (renderPlanCard, confirmAndSubmitPlan)
must be guarded by workflowMode === "legacy" so it never runs in V2 mode.
A workflow version footer must be displayed at the page bottom, and the
``/api/system/version`` endpoint must expose all expected fields.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app

ROOT = Path(__file__).resolve().parents[2]


def read_asset(relative: str) -> str:
    """Read a project file as UTF-8 text."""
    return (ROOT / relative).read_text(encoding="utf-8")


def function_source(script: str, function_name: str) -> str:
    """Extract the source of a JS function by its name."""
    start = script.index(f"function {function_name}(")
    following_functions = (
        position
        for marker in ("\nfunction ", "\nasync function ")
        if (position := script.find(marker, start + 1)) != -1
    )
    end = min(following_functions, default=len(script))
    return script[start:end]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repository():
    """Create an in-memory repository."""
    return SQLWorkflowRepository("sqlite:///:memory:")


@pytest.fixture
def client(repository):
    """Create a test client backed by *repository*."""
    app = create_app(repository=repository, execution_targets=[])
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Test 1: workflowMode variable is defined as "v2"
# ---------------------------------------------------------------------------


class TestWorkflowModeVariable:
    """Verify app.js defines workflowMode with value 'v2'."""

    def test_workflow_mode_variable_defined_as_v2(self):
        """app.js must define const workflowMode = 'v2' near the top."""
        script = read_asset("apps/web/app.js")
        assert 'const workflowMode = "v2";' in script

    def test_workflow_mode_variable_is_near_top(self):
        """workflowMode must be declared before the first function definition."""
        script = read_asset("apps/web/app.js")
        var_pos = script.index('const workflowMode = "v2";')
        first_func = script.index("function ")
        assert var_pos < first_func, (
            "workflowMode must be declared before any function definition"
        )

    def test_workflow_mode_has_explanatory_comment(self):
        """workflowMode must have a comment explaining its purpose."""
        script = read_asset("apps/web/app.js")
        assert 'Workflow mode: "v2"' in script
        assert "ExperimentSpec flow" in script
        assert "ExperimentPlan flow" in script


# ---------------------------------------------------------------------------
# Test 2: renderPlanCard calls are guarded by workflowMode === "legacy"
# ---------------------------------------------------------------------------


class TestRenderPlanCardGuarded:
    """Verify renderPlanCard calls are guarded by workflowMode === 'legacy'."""

    def test_onplan_callback_guards_renderplancard(self):
        """The onPlan callback must only call renderPlanCard in legacy mode."""
        script = read_asset("apps/web/app.js")
        guard = script.index('if (workflowMode === "legacy")')
        render_call = script.index("renderPlanCard(response)", guard)
        assert guard < render_call

    def test_onplan_callback_has_v2_else_branch(self):
        """The onPlan callback must have a V2 else branch with status message."""
        script = read_asset("apps/web/app.js")
        guard = script.index('if (workflowMode === "legacy")')
        else_branch = script.index("} else {", guard)
        v2_marker = "\u6b63\u5728\u521b\u5efa\u7ed3\u6784\u5316\u5b9e\u9a8c\u89c4\u683c"
        v2_status = script.index(v2_marker, else_branch)
        assert guard < else_branch < v2_status

    def test_session_restoration_guards_renderplancard(self):
        """Session restoration must guard renderPlanCard with workflowMode."""
        script = read_asset("apps/web/app.js")
        assert (
            'workflowMode === "legacy" && !renderedPlanRefs.has(planId)'
            in script
        )

    def test_no_unguarded_renderplancard_response_call(self):
        """There must be no bare renderPlanCard(response) call without guard."""
        script = read_asset("apps/web/app.js")
        pos = 0
        while True:
            pos = script.find("renderPlanCard(response)", pos)
            if pos == -1:
                break
            # Skip function definitions (e.g. "function renderPlanCard(response) {")
            prefix = script[max(0, pos - 20) : pos]
            if "function " in prefix:
                pos += 1
                continue
            window = script[max(0, pos - 500) : pos]
            assert 'workflowMode === "legacy"' in window, (
                "renderPlanCard(response) must be guarded by workflowMode"
            )
            pos += 1


# ---------------------------------------------------------------------------
# Test 3: confirmAndSubmitPlan has a workflowMode guard
# ---------------------------------------------------------------------------


class TestConfirmAndSubmitPlanGuarded:
    """Verify confirmAndSubmitPlan has a workflowMode guard."""

    def test_confirm_and_submit_plan_has_workflow_mode_guard(self):
        """confirmAndSubmitPlan must check workflowMode at the start."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "confirmAndSubmitPlan")
        assert 'if (workflowMode !== "legacy")' in source
        assert "console.warn" in source
        assert "deprecated" in source.lower()

    def test_confirm_and_submit_plan_guard_is_first_check(self):
        """The workflowMode guard must be the first check in the function."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "confirmAndSubmitPlan")
        guard_pos = source.index('if (workflowMode !== "legacy")')
        session_check = source.index("if (currentResearchSession)")
        assert guard_pos < session_check

    def test_confirm_and_submit_plan_guard_returns_early(self):
        """The workflowMode guard must return early in V2 mode."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "confirmAndSubmitPlan")
        guard_pos = source.index('if (workflowMode !== "legacy")')
        return_pos = source.index("return;", guard_pos)
        session_check = source.index("if (currentResearchSession)")
        assert guard_pos < return_pos < session_check


# ---------------------------------------------------------------------------
# Test 4: loadSystemVersion populates footer fields
# ---------------------------------------------------------------------------


class TestLoadSystemVersionPopulatesFooter:
    """Verify loadSystemVersion populates footer fields."""

    def test_load_system_version_populates_wf_mode(self):
        """loadSystemVersion must populate wf-mode element."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "loadSystemVersion")
        assert "getElementById('wf-mode')" in source

    def test_load_system_version_populates_wf_git(self):
        """loadSystemVersion must populate wf-git element."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "loadSystemVersion")
        assert "getElementById('wf-git')" in source

    def test_load_system_version_populates_wf_schema(self):
        """loadSystemVersion must populate wf-schema element."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "loadSystemVersion")
        assert "getElementById('wf-schema')" in source

    def test_load_system_version_populates_wf_api(self):
        """loadSystemVersion must populate wf-api element."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "loadSystemVersion")
        assert "getElementById('wf-api')" in source

    def test_load_system_version_uses_template_literal_for_badge(self):
        """loadSystemVersion must use a proper template literal for the badge."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "loadSystemVersion")
        assert "`Workflow ${wf}" in source
        assert "${sha.substring(0, 7)}`" in source

    def test_load_system_version_populates_git_with_substring(self):
        """loadSystemVersion must truncate git commit to 12 chars in footer."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "loadSystemVersion")
        assert "info.git_commit.substring(0, 12)" in source

    def test_load_system_version_populates_schema_version(self):
        """loadSystemVersion must use schema_version from the API response."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "loadSystemVersion")
        assert "info.schema_version" in source

    def test_load_system_version_populates_api_version(self):
        """loadSystemVersion must use api_version from the API response."""
        script = read_asset("apps/web/app.js")
        source = function_source(script, "loadSystemVersion")
        assert "info.api_version" in source


# ---------------------------------------------------------------------------
# Test 5: index.html includes workflow-footer
# ---------------------------------------------------------------------------


class TestWorkflowFooterHtml:
    """Verify index.html includes the workflow-footer element."""

    def test_index_html_includes_workflow_footer(self):
        """index.html must include the workflow-footer element."""
        html = read_asset("apps/web/index.html")
        assert 'class="workflow-footer"' in html
        assert 'id="workflow-footer"' in html

    def test_index_html_includes_footer_element_ids(self):
        """index.html must include all footer element IDs."""
        html = read_asset("apps/web/index.html")
        for element_id in ("wf-mode", "wf-git", "wf-schema", "wf-api"):
            assert f'id="{element_id}"' in html, (
                f"index.html must include element with id={element_id}"
            )

    def test_workflow_footer_is_before_script_tag(self):
        """The workflow footer must appear before the script tag."""
        html = read_asset("apps/web/index.html")
        footer_pos = html.index('id="workflow-footer"')
        script_pos = html.index('src="/assets/app.js"')
        assert footer_pos < script_pos

    def test_workflow_footer_has_default_values(self):
        """The footer elements must have sensible default values."""
        html = read_asset("apps/web/index.html")
        assert "V2 Beta" in html
        assert html.count("\u2014") >= 3


# ---------------------------------------------------------------------------
# Test 6: CSS includes workflow-footer styles
# ---------------------------------------------------------------------------


class TestWorkflowFooterCss:
    """Verify styles.css includes workflow-footer styles."""

    def test_css_includes_workflow_footer_selector(self):
        """styles.css must include .workflow-footer selector."""
        css = read_asset("apps/web/styles.css")
        assert ".workflow-footer {" in css

    def test_css_includes_workflow_footer_code_selector(self):
        """styles.css must include .workflow-footer code selector."""
        css = read_asset("apps/web/styles.css")
        assert ".workflow-footer code {" in css

    def test_css_includes_flex_layout(self):
        """The footer CSS must use flex layout."""
        css = read_asset("apps/web/styles.css")
        rule_start = css.index(".workflow-footer {")
        rule_end = css.index("}", rule_start) + 1
        rule = css[rule_start:rule_end]
        assert "display: flex" in rule
        assert "flex-wrap: wrap" in rule

    def test_css_includes_border_top(self):
        """The footer CSS must include a top border."""
        css = read_asset("apps/web/styles.css")
        rule_start = css.index(".workflow-footer {")
        rule_end = css.index("}", rule_start) + 1
        rule = css[rule_start:rule_end]
        assert "border-top" in rule

    def test_css_includes_gap_and_padding(self):
        """The footer CSS must include gap and padding."""
        css = read_asset("apps/web/styles.css")
        rule_start = css.index(".workflow-footer {")
        rule_end = css.index("}", rule_start) + 1
        rule = css[rule_start:rule_end]
        assert "gap:" in rule
        assert "padding:" in rule

    def test_css_code_rule_includes_mono_font(self):
        """The footer code CSS must use a monospace font."""
        css = read_asset("apps/web/styles.css")
        rule_start = css.index(".workflow-footer code {")
        rule_end = css.index("}", rule_start) + 1
        rule = css[rule_start:rule_end]
        assert "monospace" in rule


# ---------------------------------------------------------------------------
# Test 7: /api/system/version returns expected fields
# ---------------------------------------------------------------------------


class TestSystemVersionEndpoint:
    """Verify /api/system/version returns expected fields."""

    def test_system_version_returns_all_expected_fields(self, client):
        """/api/system/version must return all expected version fields."""
        response = client.get("/api/system/version")
        assert response.status_code == 200
        info = response.json()
        for field in (
            "git_commit",
            "workflow",
            "api_version",
            "schema_version",
            "native_compile_enabled",
            "workflow_v2_enabled",
        ):
            assert field in info, f"Missing field: {field}"

    def test_system_version_workflow_is_v2(self, client):
        """The workflow field must be 'v2'."""
        response = client.get("/api/system/version")
        assert response.status_code == 200
        info = response.json()
        assert info["workflow"] == "v2"

    def test_system_version_v2_enabled_is_true_by_default(self, client):
        """workflow_v2_enabled must be True by default."""
        response = client.get("/api/system/version")
        assert response.status_code == 200
        info = response.json()
        assert info["workflow_v2_enabled"] is True

    def test_system_version_native_compile_enabled(self, client):
        """native_compile_enabled must be True."""
        response = client.get("/api/system/version")
        assert response.status_code == 200
        info = response.json()
        assert info["native_compile_enabled"] is True


# ---------------------------------------------------------------------------
# Test 8: Legacy plan endpoints have deprecation warning when V2 is enabled
# ---------------------------------------------------------------------------


class TestLegacyPlanEndpointDeprecation:
    """Verify legacy plan-based endpoints have deprecation warnings."""

    def test_submit_endpoint_has_v2_deprecation_warning(self):
        """The submit endpoint must log a warning when V2 is enabled."""
        api = read_asset("src/fluid_scientist/api/app.py")
        func_start = api.index("def submit_planned_experiment(")
        next_func = api.index("\n    @application.", func_start + 1)
        func_body = api[func_start:next_func]
        assert "runtime_settings.research_workflow_v2" in func_body
        assert "logger.warning" in func_body
        assert "Old plan-based endpoint called while V2 is enabled" in func_body

    def test_submit_endpoint_is_marked_deprecated(self):
        """The submit endpoint must be marked as deprecated in OpenAPI."""
        api = read_asset("src/fluid_scientist/api/app.py")
        endpoint_pos = api.index(
            "/api/projects/{project_id}/experiment-plans/{plan_id}/submit"
        )
        # The deprecated=True tag appears after the URL in the decorator block,
        # so we extract the full decorator-to-function-def region.
        decorator_start = api.rfind("@application.", 0, endpoint_pos)
        func_def = api.index("def submit_planned_experiment(", endpoint_pos)
        decorator_block = api[decorator_start:func_def]
        assert "deprecated=True" in decorator_block
        assert '"deprecated"' in decorator_block

    def test_results_endpoint_has_v2_deprecation_warning(self):
        """The results endpoint must log a warning when V2 is enabled."""
        api = read_asset("src/fluid_scientist/api/app.py")
        func_start = api.index("def planned_experiment_results(")
        next_func = api.index("\n    @application.", func_start + 1)
        func_body = api[func_start:next_func]
        assert "runtime_settings.research_workflow_v2" in func_body
        assert "logger.warning" in func_body

    def test_analysis_endpoint_has_v2_deprecation_warning(self):
        """The analysis endpoint must log a warning when V2 is enabled."""
        api = read_asset("src/fluid_scientist/api/app.py")
        func_start = api.index("def analyze_planned_experiment(")
        next_func = api.index("\n    @application.", func_start + 1)
        func_body = api[func_start:next_func]
        assert "runtime_settings.research_workflow_v2" in func_body
        assert "logger.warning" in func_body

    def test_legacy_compile_endpoint_exists(self):
        """The legacy plan compile endpoint must still exist for compatibility."""
        api = read_asset("src/fluid_scientist/api/app.py")
        assert "/api/experiment-plans/{plan_id}/compile" in api
        assert "def compile_experiment_plan(" in api
