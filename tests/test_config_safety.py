from pathlib import Path

import yaml


def test_example_environment_contains_no_real_secrets() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "sk-" not in text
    assert "BEGIN OPENSSH PRIVATE KEY" not in text
    assert "login.internal" not in text
    assert "/home/" not in text


def test_compose_declares_required_platform_services() -> None:
    compose = yaml.safe_load(Path("infra/compose.yaml").read_text(encoding="utf-8"))

    required = {"postgres", "redis", "qdrant", "minio"}
    assert required.issubset(set(compose["services"]))
    assert all("healthcheck" in compose["services"][name] for name in required)


def test_readme_documents_fake_mode_and_three_hpc_nodes() -> None:
    text = Path("README.md").read_text(encoding="utf-8")

    assert "Fake 模式" in text
    assert all(name in text for name in ("数据节点", "Login 节点", "计算节点"))
