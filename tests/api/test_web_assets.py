from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_workbench_assets_are_valid_utf8_without_replacement_characters() -> None:
    for relative in ("apps/web/index.html", "apps/web/app.js"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "\ufffd" not in text


def test_workbench_javascript_keeps_chinese_execution_messages() -> None:
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert "工作站 OpenFOAM" in script
    assert "尚未配置真实执行平台" in script
    assert "等待 ${gate} 人工审批" in script
    assert "闭环完成" in script


def test_skill_governance_is_not_exposed_in_the_workbench() -> None:
    assets = "".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in ("apps/web/index.html", "apps/web/app.js")
    )

    assert "候选 Skill" not in assets
    assert "Skill 治理" not in assets


def test_workbench_exposes_real_workstation_submission_and_result_polling() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    for field_id in (
        "benchmark-form",
        "experiment-name",
        "pipe-diameter",
        "pipe-length",
        "pipe-velocity",
        "pipe-nu",
        "pipe-density",
        "axial-cells",
        "radial-cells",
        "submit-benchmark",
    ):
        assert f'id="{field_id}"' in html

    assert "/benchmarks" in script
    assert "/results" in script
    assert "pollBenchmark" in script
    assert "validation.passed" in script
    assert 'PILOT_READY: "SUBMIT_PILOT"' not in script


def test_workbench_persists_and_resumes_the_active_project() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert 'id="job-id"' in html
    assert "fluid-scientist-project-id" in script
    assert "localStorage.setItem" in script
    assert "resumeProject" in script
    assert "/api/projects/recent" in script
    assert 'workflow_state === "PILOT_RUNNING"' in script
    assert 'workflow_state === "PILOT_VERIFIED"' in script


def test_workbench_shows_reproducible_paraview_instructions() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert 'id="postprocess-command"' in html
    assert "collection.post_processing" in script
    assert "postProcessing.case_path" in script
    assert "postProcessing.paraview_file" in script
    assert "paraFoam" in script


def test_workbench_can_request_and_apply_model_designed_experiment() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert 'id="design-experiment"' in html
    assert 'id="design-rationale"' in html
    assert '"/api/experiment-plans"' in script
    assert "renderPlanReview" in script
    assert "response.plan" in script


def test_workbench_can_validate_custom_openfoam_archive_without_submitting_it() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert 'id="custom-case-file"' in html
    assert 'id="validate-custom-case"' in html
    assert 'id="custom-case-result"' in html
    assert '"/api/custom-cases/validate"' in script
    assert "尚未提交" in html


def test_workbench_opens_postprocessing_results_in_browser() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert 'id="view-postprocess"' in html
    assert 'id="postprocess-results"' in html
    assert "renderPostprocessResults" in script
    assert "latestBenchmarkResults" in script
    assert "后处理 Case 已准备" not in html
    assert "后处理结果" in html


def test_workbench_can_configure_model_without_persisting_api_key() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert 'id="model-api-key"' in html
    assert 'id="configure-model"' in html
    assert 'type="password"' in html
    assert '"/api/model-configurations"' in script
    assert "localStorage.setItem(targetStorageKey" in script
    assert "localStorage.setItem(\"openai" not in script


def test_workbench_can_submit_and_poll_a_validated_custom_case() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert 'id="custom-experiment-name"' in html
    assert 'id="submit-custom-case"' in html
    assert '"/api/custom-cases/submit"' in script
    assert "pollCustomCase" in script
    assert 'design.experiment_type === "custom_openfoam"' in script
    assert "VALIDATION ONLY" not in html


def test_workbench_exposes_provider_neutral_compiled_plan_flow() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    for marker in (
        'id="model-provider"',
        'value="openai"',
        'value="glm"',
        'value="deepseek"',
        'id="model-api-key"',
        'id="model-id"',
        'id="experiment-plan-review"',
        'id="plan-geometry"',
        'id="plan-physics"',
        'id="plan-boundaries"',
        'id="plan-mesh"',
        'id="plan-numerics"',
        'id="plan-sweeps"',
        'id="plan-outputs"',
        'id="plan-assumptions"',
        'id="plan-limitations"',
        'id="compile-experiment"',
        'id="compile-preview"',
        'id="submit-planned-experiment"',
    ):
        assert marker in html

    assert '"/api/model-configurations"' in script
    assert '"/api/experiment-plans"' in script
    assert "/compile`" in script
    assert "/experiment-plans/${currentPlan.plan_id}/submit`" in script
    assert "archive_sha256" in script
    assert "plan_version" in script
    assert 'localStorage.setItem("api' not in script
    assert "modelApiKey.value = \"\"" in script


def test_gate_two_approval_includes_the_visible_compiled_digest() -> None:
    script = (ROOT / "apps/web/app.js").read_text(encoding="utf-8")

    assert "plan_id: currentPlan.plan_id" in script
    assert "plan_version: currentPlan.plan_version" in script
    assert "archive_sha256: currentCompilation.archive_sha256" in script
    assert "textContent" in script
