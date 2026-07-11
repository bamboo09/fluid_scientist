"""Tests for SQLite-backed workstation profile persistence.

Covers save/get round-trips, restart recovery, sensitive-field stripping,
default-profile switching, deletion, and test isolation via ``tmp_path``.
"""

from __future__ import annotations

import json

import pytest

from fluid_scientist.workstations.models import (
    PlatformStatus,
    SchedulerType,
    WorkstationProfile,
)
from fluid_scientist.workstations.profile_store import WorkstationProfileStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    profile_id: str = "ws_test001",
    host_alias: str = "hpc.example.com",
    display_name: str = "HPC Cluster",
    is_default: bool = False,
) -> WorkstationProfile:
    return WorkstationProfile(
        profile_id=profile_id,
        display_name=display_name,
        host_alias=host_alias,
        resolved_host="10.0.0.5",
        detected_username="researcher",
        detected_port=22,
        connection_method="SSH_CONFIG",
        known_host_fingerprint="SHA256:abc123",
        scheduler=SchedulerType.SLURM,
        openfoam_available=True,
        openfoam_version="13",
        openfoam_activation_method="ALREADY_ACTIVE",
        openfoam_activation_reference="login-shell",
        remote_base_dir="/scratch/researcher/fluid_scientist/runs",
        remote_os="Linux",
        cpu_count=64,
        memory_bytes=135291463680,
        disk_available_bytes=5368709120,
        connection_status="REACHABLE",
        platform_status=PlatformStatus.READY,
        last_probe_at="2026-01-01T00:00:00Z",
        last_success_at="2026-01-01T00:00:00Z",
        is_default=is_default,
    )


# ---------------------------------------------------------------------------
# Basic save / get / list
# ---------------------------------------------------------------------------


class TestSaveAndGet:
    def test_save_and_get_roundtrip(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        profile = _make_profile()
        store.save(profile)

        loaded = store.get("ws_test001")
        assert loaded is not None
        assert loaded.profile_id == "ws_test001"
        assert loaded.host_alias == "hpc.example.com"
        assert loaded.display_name == "HPC Cluster"
        assert loaded.openfoam_available is True
        assert loaded.openfoam_version == "13"
        assert loaded.scheduler == SchedulerType.SLURM
        assert loaded.cpu_count == 64
        assert loaded.platform_status == PlatformStatus.READY

    def test_get_returns_none_for_missing(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        assert store.get("nonexistent") is None

    def test_list_all_returns_saved_profiles(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        store.save(_make_profile("ws_a", "host-a"))
        store.save(_make_profile("ws_b", "host-b"))

        profiles = store.list_all()
        assert len(profiles) == 2
        aliases = {p.host_alias for p in profiles}
        assert aliases == {"host-a", "host-b"}

    def test_list_all_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        assert store.list_all() == []

    def test_save_updates_existing(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        profile = _make_profile()
        store.save(profile)

        updated = _make_profile(display_name="Updated Name")
        store.save(updated)

        loaded = store.get("ws_test001")
        assert loaded is not None
        assert loaded.display_name == "Updated Name"
        assert len(store.list_all()) == 1


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


class TestRestartRecovery:
    def test_profile_survives_store_recreation(self, tmp_path):
        """A new store pointing at the same DB file recovers all profiles."""
        db = str(tmp_path / "test.db")
        store1 = WorkstationProfileStore(db_path=db)
        store1.save(_make_profile("ws_persist", "persistent-host"))
        store1.save(_make_profile("ws_persist2", "persistent-host-2"))

        # Simulate a service restart by creating a new store with the same path
        store2 = WorkstationProfileStore(db_path=db)
        profiles = store2.list_all()
        assert len(profiles) == 2

        loaded = store2.get("ws_persist")
        assert loaded is not None
        assert loaded.host_alias == "persistent-host"
        assert loaded.openfoam_available is True

    def test_default_survives_restart(self, tmp_path):
        db = str(tmp_path / "test.db")
        store1 = WorkstationProfileStore(db_path=db)
        store1.save(_make_profile("ws_default", "default-host", is_default=True))

        store2 = WorkstationProfileStore(db_path=db)
        default = store2.get_default()
        assert default is not None
        assert default.profile_id == "ws_default"


# ---------------------------------------------------------------------------
# Sensitive field filtering
# ---------------------------------------------------------------------------


class TestSensitiveFields:
    def test_profile_has_no_sensitive_fields(self, tmp_path):
        """WorkstationProfile must not model private_key, password, etc."""
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        profile = _make_profile()
        store.save(profile)

        loaded = store.get("ws_test001")
        assert loaded is not None
        data = loaded.model_dump()
        for key in ("private_key", "private_key_path", "password", "passphrase", "raw_credential"):
            assert key not in data, f"sensitive field '{key}' must not be in profile"

    def test_sanitizer_strips_sensitive_keys_from_json(self, tmp_path):
        """The _sanitize method must strip sensitive keys even if injected."""
        payload = json.dumps({
            "profile_id": "ws_evil",
            "display_name": "evil",
            "host_alias": "host",
            "private_key": "-----BEGIN PRIVATE KEY-----\n...",
            "password": "s3cr3t",
            "passphrase": "passphrase123",
            "private_key_path": "/home/user/.ssh/id_rsa",
            "raw_credential": "token-abc",
        })
        sanitized = WorkstationProfileStore._sanitize(payload)
        data = json.loads(sanitized)
        for key in ("private_key", "password", "passphrase", "private_key_path", "raw_credential"):
            assert key not in data

    def test_raw_db_has_no_sensitive_data(self, tmp_path):
        """Even the raw SQLite TEXT column must not contain sensitive data."""
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        store.save(_make_profile())

        import sqlite3
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT data FROM workstation_profiles WHERE profile_id = ?",
            ("ws_test001",),
        ).fetchone()
        conn.close()

        raw_json = row[0]
        for key in ("private_key", "password", "passphrase", "raw_credential"):
            assert key not in raw_json


# ---------------------------------------------------------------------------
# Default profile management
# ---------------------------------------------------------------------------


class TestDefaultProfile:
    def test_set_default(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        store.save(_make_profile("ws_a", "host-a"))
        store.save(_make_profile("ws_b", "host-b"))

        store.set_default("ws_b")
        default = store.get_default()
        assert default is not None
        assert default.profile_id == "ws_b"

    def test_set_default_clears_previous(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        store.save(_make_profile("ws_a", "host-a", is_default=True))
        store.save(_make_profile("ws_b", "host-b"))

        store.set_default("ws_b")
        default = store.get_default()
        assert default is not None
        assert default.profile_id == "ws_b"

    def test_set_default_raises_for_missing(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        with pytest.raises(KeyError):
            store.set_default("nonexistent")

    def test_get_default_returns_none_when_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        assert store.get_default() is None

    def test_save_with_is_default_true(self, tmp_path):
        """Saving a profile with is_default=True marks it as the default."""
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        profile = _make_profile("ws_first", "first-host", is_default=True)
        store.save(profile)

        default = store.get_default()
        assert default is not None
        assert default.profile_id == "ws_first"

    def test_save_without_is_default_does_not_auto_set(self, tmp_path):
        """The store itself does not auto-set the first profile as default;
        that is the responsibility of WorkstationConnectionService."""
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        store.save(_make_profile("ws_first", "first-host", is_default=False))

        assert store.get_default() is None


# ---------------------------------------------------------------------------
# Delete and clear_all
# ---------------------------------------------------------------------------


class TestDeleteAndClear:
    def test_delete_removes_profile(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        store.save(_make_profile("ws_a", "host-a"))
        store.save(_make_profile("ws_b", "host-b"))

        store.delete("ws_a")
        assert store.get("ws_a") is None
        assert len(store.list_all()) == 1

    def test_delete_nonexistent_is_silent(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        store.delete("nonexistent")  # should not raise

    def test_clear_all_removes_everything(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = WorkstationProfileStore(db_path=db)
        store.save(_make_profile("ws_a", "host-a"))
        store.save(_make_profile("ws_b", "host-b"))

        store.clear_all()
        assert store.list_all() == []
        assert store.get_default() is None


# ---------------------------------------------------------------------------
# Test isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_each_tmp_db_is_independent(self, tmp_path):
        db1 = str(tmp_path / "db1.db")
        db2 = str(tmp_path / "db2.db")
        store1 = WorkstationProfileStore(db_path=db1)
        store2 = WorkstationProfileStore(db_path=db2)

        store1.save(_make_profile("ws_only_in_db1", "host-1"))

        assert len(store1.list_all()) == 1
        assert len(store2.list_all()) == 0
