"""Prompt Registry -- loads, caches, and versions prompt templates.

Prompts are stored as Markdown (``.md``) files inside the package-level
``prompts`` directory (``src/fluid_scientist/prompts``).  The registry
caches loaded prompts together with their SHA-256 content hashes so that
repeated access does not incur file I/O.

A module-level :data:`PROMPT_VERSIONS` mapping supports explicit
versioning of individual prompts.  Consumers can use
:meth:`PromptRegistry.get_version` to look up the version string for a
prompt and :meth:`PromptRegistry.get_versioned_hash` to obtain a digest
that incorporates both the version and the content -- useful for cache
invalidation when either the prompt text or its declared version changes.

Typical usage::

    from fluid_scientist.research_ir.prompt_registry import PromptRegistry

    registry = PromptRegistry()
    prompt_text = registry.load("research_extraction")
    digest = registry.get_hash("research_extraction")
    for name in registry.list_prompts():
        print(name, registry.get_version(name))
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Directory containing prompt templates: <package_root>/prompts
#   __file__  -> .../src/fluid_scientist/research_ir/prompt_registry.py
#   parent   -> .../src/fluid_scientist/research_ir
#   parent.parent -> .../src/fluid_scientist
PROMPTS_DIR: Path = Path(__file__).parent.parent / "prompts"

# File extension for prompt templates.
PROMPT_EXTENSION: str = ".md"

# Prompt versioning -- map a prompt name (without extension) to a semantic
# version string.  Bump the version whenever the prompt content or its
# intended semantics change in a way that downstream consumers must be
# aware of (e.g. a required JSON field was added to the prompt's output
# schema).  Prompts absent from this mapping report version ``"0.0.0"``
# via :meth:`PromptRegistry.get_version`.
PROMPT_VERSIONS: dict[str, str] = {
    # "research_extraction": "1.0.0",
    # "mention_inventory": "1.0.0",
    # "source_coverage_check": "1.0.0",
}

# Default version reported for unversioned prompts.
DEFAULT_PROMPT_VERSION: str = "0.0.0"


class PromptRegistry:
    """Loads and caches prompt templates with hashing and versioning.

    The registry lazily reads prompt files from :data:`PROMPTS_DIR` (or a
    caller-supplied directory) and caches both the raw content and its
    SHA-256 hash.  Subsequent calls to :meth:`load` / :meth:`get_hash` for
    an already-loaded prompt are served from cache without disk access.

    Use :meth:`reload` to force a fresh read from disk, discarding any
    cached entry for the given prompt.

    Args:
        prompts_dir: Optional override for the prompts directory.  When
            ``None`` (the default) the module-level :data:`PROMPTS_DIR`
            is used.
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._prompts_dir: Path = (
            Path(prompts_dir) if prompts_dir is not None else PROMPTS_DIR
        )
        # prompt_name -> raw content
        self._content_cache: dict[str, str] = {}
        # prompt_name -> sha256 hex digest
        self._hash_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_path(self, prompt_name: str) -> Path:
        """Return the filesystem path for *prompt_name*."""
        return self._prompts_dir / f"{prompt_name}{PROMPT_EXTENSION}"

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Return the SHA-256 hex digest of *content*."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _read_from_disk(self, prompt_name: str) -> str:
        """Read a prompt from disk, cache it, and return the content.

        Logs a warning and raises :class:`FileNotFoundError` when the
        prompt file does not exist.
        """
        path = self._resolve_path(prompt_name)
        if not path.is_file():
            logger.warning(
                "Prompt file not found: %s (expected at %s)", prompt_name, path
            )
            raise FileNotFoundError(
                f"Prompt '{prompt_name}' not found at {path}"
            )
        content = path.read_text(encoding="utf-8")
        self._content_cache[prompt_name] = content
        self._hash_cache[prompt_name] = self._compute_hash(content)
        logger.debug("Loaded prompt '%s' from %s", prompt_name, path)
        return content

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, prompt_name: str) -> str:
        """Load a prompt by name (without the ``.md`` extension).

        The result is cached.  If the prompt has already been loaded the
        cached content is returned without disk access.  Use
        :meth:`reload` to force a fresh read.

        Args:
            prompt_name: Prompt name without extension, e.g.
                ``"research_extraction"``.

        Returns:
            The raw text content of the prompt file.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
        """
        if prompt_name in self._content_cache:
            return self._content_cache[prompt_name]
        return self._read_from_disk(prompt_name)

    def get_hash(self, prompt_name: str) -> str:
        """Return the SHA-256 hash of a prompt's content.

        The prompt is loaded (and cached) on first access if it has not
        been read yet.

        Args:
            prompt_name: Prompt name without extension.

        Returns:
            The SHA-256 hex digest of the prompt content.
        """
        if prompt_name not in self._hash_cache:
            self.load(prompt_name)
        return self._hash_cache[prompt_name]

    def list_prompts(self) -> list[str]:
        """List all available prompt names (without extension).

        Scans the prompts directory for files matching
        :data:`PROMPT_EXTENSION` and returns their stem names sorted
        alphabetically.  Logs a warning and returns an empty list when
        the directory is missing.
        """
        if not self._prompts_dir.is_dir():
            logger.warning(
                "Prompts directory does not exist: %s", self._prompts_dir
            )
            return []
        names: list[str] = [
            p.stem
            for p in sorted(self._prompts_dir.iterdir())
            if p.is_file() and p.suffix == PROMPT_EXTENSION
        ]
        return names

    def reload(self, prompt_name: str) -> str:
        """Force a reload of a prompt from disk.

        Discards any cached content and hash for *prompt_name* and reads
        the file again.

        Args:
            prompt_name: Prompt name without extension.

        Returns:
            The freshly-read prompt content.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
        """
        self._content_cache.pop(prompt_name, None)
        self._hash_cache.pop(prompt_name, None)
        return self._read_from_disk(prompt_name)

    # ------------------------------------------------------------------
    # Versioning helpers
    # ------------------------------------------------------------------

    def get_version(self, prompt_name: str) -> str:
        """Return the declared version string for a prompt.

        Looks up :data:`PROMPT_VERSIONS`.  Prompts without an explicit
        entry report :data:`DEFAULT_PROMPT_VERSION` (``"0.0.0"``).
        """
        return PROMPT_VERSIONS.get(prompt_name, DEFAULT_PROMPT_VERSION)

    def get_versioned_hash(self, prompt_name: str) -> str:
        """Return a SHA-256 digest that incorporates version and content.

        The digest is computed over ``"<name>@<version>\\n<content>"`` so
        that it changes whenever either the prompt's declared version or
        its text content changes.  This is convenient for cache keys.

        Args:
            prompt_name: Prompt name without extension.

        Returns:
            The versioned SHA-256 hex digest.
        """
        content = self.load(prompt_name)
        version = self.get_version(prompt_name)
        versioned_payload = f"{prompt_name}@{version}\n{content}"
        return self._compute_hash(versioned_payload)

    def is_cached(self, prompt_name: str) -> bool:
        """Return ``True`` if *prompt_name* is currently in the cache."""
        return prompt_name in self._content_cache

    def clear_cache(self) -> None:
        """Discard all cached prompts and hashes."""
        self._content_cache.clear()
        self._hash_cache.clear()


__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "PROMPT_EXTENSION",
    "PROMPT_VERSIONS",
    "PROMPTS_DIR",
    "PromptRegistry",
]
