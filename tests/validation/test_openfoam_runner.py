"""OpenFOAM validation runner tests."""

from __future__ import annotations

import pytest

from fluid_scientist.validation.openfoam import (
    OpenFOAMValidationRequest,
    RemoteOpenFOAMValidationRunner,
    TypedCommandBuilder,
)
from fluid_scientist.workstations.profile_store import WorkstationProfileStore


def test_typed_command_builder_rejects_unapproved_command() -> None:
    builder = TypedCommandBuilder()

    with pytest.raises(ValueError):
        builder.build("touch /tmp/nope", case_dir="/case")


def test_remote_runner_reports_missing_default_profile(tmp_path) -> None:
    store = WorkstationProfileStore(db_path=str(tmp_path / "profiles.db"))
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    report = RemoteOpenFOAMValidationRunner(store=store).validate(
        OpenFOAMValidationRequest(case_dir=str(case_dir))
    )

    assert report.runner == "none"
    assert not report.passed
    assert report.error_code == "WORKSTATION_PROFILE_REQUIRED"
