"""vaner-tools — shared utilities for Vaner agents."""

from . import artefact_store, repo_tools
from .artefact_store import (
    Artefact,
    artefact_path,
    is_stale,
    list_artefacts,
    read_artefact,
    read_repo_index,
    write_artefact,
)
from .paths import CACHE_DIR, REPO_ROOT, resolve_repo_path
from .repo_tools import find_files, grep_text, list_files, read_file

__all__ = [
    # modules
    "artefact_store",
    "repo_tools",
    # paths
    "REPO_ROOT",
    "CACHE_DIR",
    "resolve_repo_path",
    # artefact store
    "Artefact",
    "artefact_path",
    "write_artefact",
    "read_artefact",
    "is_stale",
    "list_artefacts",
    "read_repo_index",
    # repo tools
    "list_files",
    "read_file",
    "find_files",
    "grep_text",
]
