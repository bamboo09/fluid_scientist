from fluid_scientist.api.build_info import (
    TRAE_MERGE_BASELINE_SHA,
    get_build_info,
)


def test_build_info_reports_current_branch_and_required_ancestry() -> None:
    info = get_build_info()

    assert info["current_sha"]
    assert info["branch"] == "codex/v6-model-native-fluid-scientist"
    assert info["required_baseline_sha"] == TRAE_MERGE_BASELINE_SHA
    assert info["contains_required_baseline"] is True
