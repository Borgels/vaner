# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

_SKIPPED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".vaner",
    "node_modules",
    ".next",
    ".turbo",
    ".nuxt",
    ".output",
    "dist",
    "build",
    ".svelte-kit",
}


def scan_repo_files(
    repo_root: Path,
    max_files: int = 500,
    include_paths: list[str] | None = None,
) -> list[Path]:
    """Scan repo for files, optionally focusing on specific sub-paths.

    When ``include_paths`` is provided (e.g. ``["sympy", "tests"]``), only
    those subdirectories are traversed. This avoids wasting the ``max_files``
    budget on unrelated directories (node_modules, docs, etc.) in large repos.
    When ``include_paths`` is provided and ``max_files`` is at its default (500),
    the limit is automatically raised to 4 000 so that large single-language
    repos (sympy, django, …) are fully indexed in one pass.
    """
    if include_paths:
        scan_roots = [repo_root / p for p in include_paths if (repo_root / p).is_dir()]
        if not scan_roots:
            scan_roots = [repo_root]
        effective_max = max_files if max_files != 500 else 4000
    else:
        scan_roots = [repo_root]
        effective_max = max_files

    files: list[Path] = []
    for scan_root in scan_roots:
        for path in scan_root.rglob("*"):
            if len(files) >= effective_max:
                break
            rel_parts = path.relative_to(repo_root).parts
            if any(part in _SKIPPED_PARTS for part in rel_parts):
                continue
            if path.is_file():
                files.append(path)
        if len(files) >= effective_max:
            break
    return files


class _RepoEventHandler(FileSystemEventHandler):
    def __init__(self, repo_root: Path, changed: set[Path], lock: threading.Lock) -> None:
        super().__init__()
        self.repo_root = repo_root
        self.changed = changed
        self.lock = lock
        self.skipped_parts = _SKIPPED_PARTS

    def _record(self, path_str: str) -> None:
        path = Path(path_str)
        if path.is_dir() or not path.exists():
            return
        try:
            rel_parts = path.relative_to(self.repo_root).parts
        except ValueError:
            return
        if any(part in self.skipped_parts for part in rel_parts):
            return
        with self.lock:
            self.changed.add(path.resolve())

    def on_created(self, event):  # type: ignore[no-untyped-def]
        self._record(event.src_path)

    def on_modified(self, event):  # type: ignore[no-untyped-def]
        self._record(event.src_path)

    def on_moved(self, event):  # type: ignore[no-untyped-def]
        self._record(event.dest_path)


class RepoChangeWatcher:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._changed: set[Path] = set()
        self._lock = threading.Lock()
        self._observer = Observer()
        self._handler = _RepoEventHandler(repo_root, self._changed, self._lock)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._observer.schedule(self._handler, str(self.repo_root), recursive=True)
        self._observer.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._observer.stop()
        self._observer.join(timeout=2)
        self._started = False

    def drain_changes(self, max_files: int = 200) -> list[Path]:
        with self._lock:
            items = list(self._changed)[:max_files]
            for item in items:
                self._changed.discard(item)
        return items
