from pathlib import Path


def test_package_exposes_version() -> None:
    import fluid_scientist

    assert fluid_scientist.__version__ == "0.1.0"


def test_worker_runtime_supports_python_310() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in pyproject

    direct_strenum_imports = [
        path
        for path in Path("src/fluid_scientist").rglob("*.py")
        if path.name != "compat.py"
        if "from enum import StrEnum" in path.read_text(encoding="utf-8")
    ]
    assert direct_strenum_imports == []

    direct_utc_imports = [
        path
        for path in Path("src/fluid_scientist").rglob("*.py")
        if path.name != "compat.py"
        if "from datetime import UTC" in path.read_text(encoding="utf-8")
    ]
    assert direct_utc_imports == []
