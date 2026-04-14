# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


def scan_repo_files(repo_root: Path, max_files: int = 200) -> list[Path]:
    skipped_parts = {".git", ".venv", "__pycache__", ".vaner"}
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if len(files) >= max_files:
            break
        rel_parts = path.relative_to(repo_root).parts
        if any(part in skipped_parts for part in rel_parts):
            continue
        if path.is_file():
            files.append(path)
    return files


class _RepoEventHandler(FileSystemEventHandler):
    def __init__(self, repo_root: Path, changed: set[Path], lock: threading.Lock) -> None:
        super().__init__()
        self.repo_root = repo_root
        self.changed = changed
        self.lock = lock
        self.skipped_parts = {".git", ".venv", "__pycache__", ".vaner"}

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
