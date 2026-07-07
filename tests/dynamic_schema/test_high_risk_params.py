"""Test that high-risk physics parameters are not silently defaulted."""

from fluid_scientist.experiment_spec.models import PhysicsFieldStatus, PhysicsSpec


def test_physics_spec_defaults_to_none():
    """PhysicsSpec high-risk fields should default to None, not hardcoded values."""
    spec = PhysicsSpec()
    assert spec.dimensions is None
    assert spec.phases is None
    assert spec.compressibility is None
    assert spec.flow_regime is None
    assert spec.temporal_type is None
    assert spec.gravity_enabled is None


def test_physics_field_status_tracks_unknown():
    """field_status should track unknown fields."""
    spec = PhysicsSpec()
    # All high-risk fields should be unknown by default
    for field_name in [
        "dimensions",
        "phases",
        "compressibility",
        "flow_regime",
        "temporal_type",
        "gravity_enabled",
    ]:
        meta = spec.field_status.get(field_name)
        if meta is None:
            # field_status is optional, but if present should be UNKNOWN
            continue
        assert meta.status == PhysicsFieldStatus.UNKNOWN


def test_generate_schema_warns_on_missing_physics():
    """generate_schema should warn when high-risk physics fields are missing."""
    from fluid_scientist.dynamic_schema.schema_engine import generate_schema

    spec = PhysicsSpec()  # All None
    result = generate_schema(spec)
    # Should have warnings about unknown physics
    assert len(result.warnings) > 0
    # Check that at least one warning mentions unknown physics
    warning_text = " ".join(result.warnings)
    assert "unknown" in warning_text.lower() or "missing" in warning_text.lower()
