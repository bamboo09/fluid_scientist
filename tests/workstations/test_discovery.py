"""Tests for workstation discovery: SSH config parsing and candidate generation."""

from __future__ import annotations

from pathlib import Path

from fluid_scientist.workstations.discovery import WorkstationDiscoveryService
from fluid_scientist.workstations.models import (
    CandidateSource,
    ConnectionStatus,
    CredentialSource,
    KnownHostStatus,
)

# ---------------------------------------------------------------------------
# Mock runner for discovery tests
# ---------------------------------------------------------------------------


class MockDiscoveryRunner:
    """Mock SSHCommandRunner for discovery tests.

    Returns canned responses for every SSH-related call.  resolve_host
    returns a different hostname per alias so each candidate is distinct.
    """

    def __init__(
        self,
        *,
        ssh_installed: bool = True,
        agent_available: bool = True,
        agent_has_identities: bool = True,
        host_key_status: KnownHostStatus = KnownHostStatus.KNOWN,
        resolve_map: dict[str, dict] | None = None,
    ) -> None:
        self._ssh_installed = ssh_installed
        self._agent_available = agent_available
        self._agent_has_identities = agent_has_identities
        self._host_key_status = host_key_status
        self._resolve_map = resolve_map or {}

    def check_ssh_installed(self) -> bool:
        return self._ssh_installed

    def resolve_host(self, host_alias: str) -> dict:
        if host_alias in self._resolve_map:
            return self._resolve_map[host_alias]
        return {
            "hostname": f"{host_alias}.example.com",
            "user": "researcher",
            "port": 22,
            "proxyjump": None,
            "identityagent": None,
        }

    def check_ssh_agent(self) -> dict:
        return {
            "available": self._agent_available,
            "has_identities": self._agent_has_identities,
        }

    def get_host_key_status(self, host_alias: str) -> KnownHostStatus:
        return self._host_key_status


# ---------------------------------------------------------------------------
# SSH config parsing
# ---------------------------------------------------------------------------


class TestSSHConfigParsing:
    def _write_config(self, tmp_path: Path, content: str) -> Path:
        config = tmp_path / "config"
        config.write_text(content, encoding="utf-8")
        return config

    def test_parses_simple_host_entries(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host alpha\n  HostName alpha.example.com\n"
            "Host beta\n  HostName beta.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        aliases = svc._parse_ssh_config(config)
        assert "alpha" in aliases
        assert "beta" in aliases

    def test_excludes_wildcard_hosts(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host *\n  User wildcard\n"
            "Host *.example.com\n  User domain\n"
            "Host real-host\n  HostName real.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        aliases = svc._parse_ssh_config(config)
        assert "real-host" in aliases
        assert "*" not in aliases
        assert "*.example.com" not in aliases

    def test_excludes_question_mark_wildcards(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host host?\n  User wildcard\n"
            "Host real-host\n  HostName real.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        aliases = svc._parse_ssh_config(config)
        assert "real-host" in aliases
        assert "host?" not in aliases

    def test_excludes_negation_hosts(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host *.example.com\n  User domain\n"
            "Host !bad.example.com\n  User neg\n"
            "Host good-host\n  HostName good.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        aliases = svc._parse_ssh_config(config)
        assert "good-host" in aliases
        assert "!bad.example.com" not in aliases

    def test_excludes_variable_hosts(self, tmp_path):
        config = self._write_config(
            tmp_path,
            'Host $HOSTNAME\n  User var\n'
            "Host real-host\n  HostName real.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        aliases = svc._parse_ssh_config(config)
        assert "real-host" in aliases
        assert "$HOSTNAME" not in aliases

    def test_preserves_order_and_deduplicates(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host alpha beta\n  User u\n"
            "Host alpha\n  User u2\n"
            "Host gamma\n  User u3\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        aliases = svc._parse_ssh_config(config)
        assert aliases == ["alpha", "beta", "gamma"]

    def test_skips_comments_and_blank_lines(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "# This is a comment\n"
            "\n"
            "Host real-host\n  HostName real.example.com\n"
            "  # nested comment\n"
            "\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        aliases = svc._parse_ssh_config(config)
        assert aliases == ["real-host"]

    def test_multiple_aliases_on_one_line(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host host1 host2 host3\n  User shared\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        aliases = svc._parse_ssh_config(config)
        assert set(aliases) == {"host1", "host2", "host3"}


# ---------------------------------------------------------------------------
# Discovery flow
# ---------------------------------------------------------------------------


class TestDiscoveryFlow:
    def _write_config(self, tmp_path: Path, content: str) -> Path:
        config = tmp_path / "config"
        config.write_text(content, encoding="utf-8")
        return config

    def test_discover_returns_candidates(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host alpha\n  HostName alpha.example.com\n"
            "Host beta\n  HostName beta.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        candidates = svc.discover()
        assert len(candidates) == 2
        aliases = {c.host_alias for c in candidates}
        assert aliases == {"alpha", "beta"}

    def test_candidate_has_correct_fields(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host hpc-cluster\n  HostName hpc.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        candidates = svc.discover()
        assert len(candidates) == 1
        c = candidates[0]
        assert c.host_alias == "hpc-cluster"
        assert c.display_name == "hpc-cluster"
        assert c.resolved_host == "hpc-cluster.example.com"
        assert c.resolved_user == "researcher"
        assert c.resolved_port == 22
        assert c.candidate_id == "ssh-config:hpc-cluster"
        assert c.source == CandidateSource.SSH_CONFIG

    def test_returns_empty_when_ssh_not_installed(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host alpha\n  HostName alpha.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(ssh_installed=False),
            ssh_config_path=config,
        )
        assert svc.discover() == []

    def test_returns_empty_when_config_missing(self, tmp_path):
        missing = tmp_path / "nonexistent"
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=missing,
        )
        assert svc.discover() == []

    def test_returns_empty_when_no_hosts_in_config(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "# Just a comment\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            ssh_config_path=config,
        )
        assert svc.discover() == []

    def test_skips_alias_that_cannot_be_resolved(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host good\n  HostName good.example.com\n"
            "Host bad\n  HostName bad.example.com\n",
        )
        resolve_map = {
            "good": {"hostname": "good.example.com", "user": "u", "port": 22},
            "bad": {},  # empty dict = unresolvable
        }
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(resolve_map=resolve_map),
            ssh_config_path=config,
        )
        candidates = svc.discover()
        assert len(candidates) == 1
        assert candidates[0].host_alias == "good"

    def test_credential_source_ssh_agent(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host alpha\n  HostName alpha.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(
                agent_available=True,
                agent_has_identities=True,
            ),
            ssh_config_path=config,
        )
        candidates = svc.discover()
        assert candidates[0].credential_source == CredentialSource.SSH_AGENT

    def test_credential_source_ssh_config_when_no_agent(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host alpha\n  HostName alpha.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(
                agent_available=False,
                agent_has_identities=False,
            ),
            ssh_config_path=config,
        )
        candidates = svc.discover()
        assert candidates[0].credential_source == CredentialSource.SSH_CONFIG

    def test_known_host_status_propagated(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host alpha\n  HostName alpha.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(
                host_key_status=KnownHostStatus.UNKNOWN,
            ),
            ssh_config_path=config,
        )
        candidates = svc.discover()
        assert candidates[0].known_host_status == KnownHostStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Profile store merging
# ---------------------------------------------------------------------------


class MockProfileStore:
    """Mock profile store implementing ProfileStoreProtocol.

    The discovery service calls ``list_profiles()`` (per
    :class:`ProfileStoreProtocol`), so this mock exposes that name.
    """

    def __init__(self, profiles=None):
        self._profiles = profiles or []

    def list_profiles(self):
        return list(self._profiles)


class TestProfileMerging:
    def _write_config(self, tmp_path: Path, content: str) -> Path:
        config = tmp_path / "config"
        config.write_text(content, encoding="utf-8")
        return config

    def test_merges_existing_profile_connection_status(self, tmp_path):
        from fluid_scientist.workstations.models import WorkstationProfile

        config = self._write_config(
            tmp_path,
            "Host alpha\n  HostName alpha.example.com\n",
        )
        existing = WorkstationProfile(
            profile_id="ws_abc",
            display_name="alpha",
            host_alias="alpha",
            resolved_host="alpha.example.com",
            detected_username="researcher",
            connection_status="REACHABLE",
            last_success_at="2026-01-01T00:00:00Z",
        )
        store = MockProfileStore([existing])
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            profile_store=store,
            ssh_config_path=config,
        )
        candidates = svc.discover()
        assert len(candidates) == 1
        c = candidates[0]
        assert c.connection_status == ConnectionStatus.REACHABLE
        assert c.last_success_at == "2026-01-01T00:00:00Z"

    def test_no_profile_store_does_not_crash(self, tmp_path):
        config = self._write_config(
            tmp_path,
            "Host alpha\n  HostName alpha.example.com\n",
        )
        svc = WorkstationDiscoveryService(
            runner=MockDiscoveryRunner(),
            profile_store=None,
            ssh_config_path=config,
        )
        candidates = svc.discover()
        assert len(candidates) == 1
        assert candidates[0].connection_status == ConnectionStatus.UNTESTED
