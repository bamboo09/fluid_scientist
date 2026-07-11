"""Workstation auto-discovery service.

Discovers SSH workstation candidates by parsing ``~/.ssh/config`` and
resolving each host alias via ``ssh -G``.  Sensitive information such as
identity-file paths, private keys, and passwords is never collected or
returned.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Protocol

from fluid_scientist.workstations.models import (
    CandidateSource,
    ConnectionStatus,
    CredentialSource,
    WorkstationCandidate,
    WorkstationProfile,
)
from fluid_scientist.workstations.ssh_runner import SSHCommandRunner

logger = logging.getLogger(__name__)

# Matches wildcard, negation, or variable characters that disqualify an alias.
_WILDCARD_PATTERN = re.compile(r"[*?!$]")


class ProfileStoreProtocol(Protocol):
    """Protocol for loading persisted workstation profiles."""

    def list_profiles(self) -> list[WorkstationProfile]: ...


class WorkstationDiscoveryService:
    """Discovers workstation candidates from the local SSH configuration.

    The service reads ``~/.ssh/config``, extracts non-wildcard host aliases,
    resolves each via ``ssh -G``, checks ``known_hosts`` status, and merges
    any existing :class:`WorkstationProfile` data from a profile store.
    """

    def __init__(
        self,
        *,
        runner: SSHCommandRunner | None = None,
        profile_store: ProfileStoreProtocol | None = None,
        ssh_config_path: Path | None = None,
    ) -> None:
        self._runner = runner or SSHCommandRunner()
        self._profile_store = profile_store
        self._ssh_config_path = ssh_config_path or (Path.home() / ".ssh" / "config")

    def discover(self) -> list[WorkstationCandidate]:
        """Discover workstation candidates from the SSH config.

        Steps:

        1. Detect whether ``ssh`` is installed.
        2. Locate ``~/.ssh/config``.
        3. Parse ``Host`` aliases, excluding wildcards and variables.
        4. Resolve each alias with ``ssh -G``.
        5. Detect ``ssh-agent`` availability.
        6. Check ``known_hosts`` status for each alias.
        7. Merge data from existing profiles.
        8. Return the candidate list.
        """
        # Step 1 – detect ssh
        if not self._runner.check_ssh_installed():
            logger.warning("ssh is not installed on this system")
            return []

        # Step 2 – locate ssh config
        if not self._ssh_config_path.is_file():
            logger.info("ssh config not found at %s", self._ssh_config_path)
            return []

        # Step 3 – parse host aliases
        aliases = self._parse_ssh_config(self._ssh_config_path)
        if not aliases:
            logger.info("no host aliases found in ssh config")
            return []

        # Step 5 – check ssh-agent
        agent_info = self._runner.check_ssh_agent()
        agent_available = bool(agent_info.get("available", False))
        agent_has_identities = bool(agent_info.get("has_identities", False))

        # Step 7 – load existing profiles for merging
        existing_profiles = self._load_existing_profiles()

        # Steps 4, 6, 7 – resolve, check known_hosts, merge
        candidates: list[WorkstationCandidate] = []
        for alias in aliases:
            candidate = self._build_candidate(
                alias,
                agent_available=agent_available,
                agent_has_identities=agent_has_identities,
                existing_profiles=existing_profiles,
            )
            if candidate is not None:
                candidates.append(candidate)

        logger.info("discovered %d workstation candidates", len(candidates))
        return candidates

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_ssh_config(self, config_path: Path) -> list[str]:
        """Parse an SSH config file and return valid host aliases.

        Excludes aliases containing wildcards (``*``, ``?``), negation
        (``!``), or shell variables (``$``).  Duplicate aliases are removed
        while preserving first-seen order.
        """
        try:
            content = config_path.read_text(encoding="utf-8")
        except OSError as error:
            logger.warning("failed to read ssh config: %s", error)
            return []

        aliases: dict[str, None] = {}
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            if parts[0].lower() != "host":
                continue
            # A Host line may define multiple aliases: "Host foo bar baz"
            for name in parts[1:]:
                if _WILDCARD_PATTERN.search(name):
                    continue
                aliases.setdefault(name, None)

        return list(aliases.keys())

    def _build_candidate(
        self,
        alias: str,
        *,
        agent_available: bool,
        agent_has_identities: bool,
        existing_profiles: dict[str, WorkstationProfile],
    ) -> WorkstationCandidate | None:
        """Build a :class:`WorkstationCandidate` from a resolved alias.

        Returns ``None`` if the alias cannot be resolved.
        """
        # Step 4 – resolve via ssh -G
        resolved = self._runner.resolve_host(alias)
        if not resolved:
            logger.warning("failed to resolve host for alias '%s'", alias)
            return None

        hostname = str(resolved.get("hostname") or alias)
        user = str(resolved.get("user") or "")
        port_raw = resolved.get("port", 22)
        port = int(port_raw) if isinstance(port_raw, int) else 22
        proxyjump_raw = resolved.get("proxyjump")
        proxyjump = str(proxyjump_raw) if proxyjump_raw else None

        if not hostname:
            logger.warning("no hostname resolved for alias '%s'", alias)
            return None

        # Step 6 – check known_hosts
        known_host_status = self._runner.get_host_key_status(alias)

        # Determine credential source
        if agent_available and agent_has_identities:
            credential_source = CredentialSource.SSH_AGENT
        else:
            credential_source = CredentialSource.SSH_CONFIG

        # Step 7 – merge existing profile data
        last_success_at: str | None = None
        connection_status = ConnectionStatus.UNTESTED

        existing = existing_profiles.get(alias)
        if existing is not None:
            last_success_at = existing.last_success_at
            try:
                connection_status = ConnectionStatus(existing.connection_status)
            except ValueError:
                connection_status = ConnectionStatus.UNTESTED

        return WorkstationCandidate(
            candidate_id=f"ssh-config:{alias}",
            host_alias=alias,
            display_name=alias,
            resolved_host=hostname,
            resolved_user=user,
            resolved_port=port,
            proxy_jump=proxyjump,
            credential_source=credential_source,
            known_host_status=known_host_status,
            connection_status=connection_status,
            source=CandidateSource.SSH_CONFIG,
            last_success_at=last_success_at,
        )

    def _load_existing_profiles(self) -> dict[str, WorkstationProfile]:
        """Load existing profiles from the profile store.

        Returns an empty dict if no store is configured or loading fails.
        """
        if self._profile_store is None:
            return {}
        try:
            profiles = self._profile_store.list_profiles()
        except Exception as error:
            logger.warning("failed to load profiles: %s", error)
            return {}
        return {p.host_alias: p for p in profiles}
