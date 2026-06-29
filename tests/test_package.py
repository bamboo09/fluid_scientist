def test_package_exposes_version() -> None:
    import fluid_scientist

    assert fluid_scientist.__version__ == "0.1.0"
