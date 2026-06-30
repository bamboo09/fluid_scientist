import pytest
from pydantic import SecretStr, ValidationError

from fluid_scientist.settings import AppSettings


def test_fake_mode_requires_no_external_secrets() -> None:
    settings = AppSettings(app_mode="fake")

    assert settings.app_mode == "fake"
    assert settings.openai.api_key is None
    assert settings.database.url.startswith("sqlite:///")


def test_real_mode_rejects_missing_openai_and_hpc_configuration() -> None:
    with pytest.raises(ValidationError, match="real mode requires"):
        AppSettings(app_mode="real")


def test_real_mode_accepts_complete_configuration_and_redacts_key() -> None:
    settings = AppSettings(
        app_mode="real",
        openai={"api_key": SecretStr("not-a-real-key")},
        data_node={"host": "data.example", "username": "researcher"},
        login_node={"host": "login.example", "username": "researcher"},
        slurm={"partition": "compute"},
        openfoam={"module_name": "openfoam-v2312", "shared_root": "projects/fluid"},
    )

    assert settings.openai.api_key.get_secret_value() == "not-a-real-key"
    assert "not-a-real-key" not in repr(settings)
    assert "not-a-real-key" not in settings.model_dump_json()


def test_environment_uses_nested_delimiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLUID_APP_MODE", "fake")
    monkeypatch.setenv("FLUID_DATABASE__URL", "sqlite:///custom.db")

    settings = AppSettings()

    assert settings.database.url == "sqlite:///custom.db"
