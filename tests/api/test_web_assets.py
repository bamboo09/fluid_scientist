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
