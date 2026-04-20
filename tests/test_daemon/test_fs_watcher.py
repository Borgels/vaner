# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import errno

from vaner.daemon.signals import fs_watcher


def test_repo_change_watcher_falls_back_to_polling_on_enospc(monkeypatch, temp_repo) -> None:
    events: dict[str, int] = {"polling_started": 0}

    class _FakeObserver:
        def schedule(self, *_args, **_kwargs) -> None:
            return None

        def start(self) -> None:
            raise OSError(errno.ENOSPC, "watch limit reached")

        def stop(self) -> None:
            return None

        def join(self, timeout: float = 0.0) -> None:
            return None

    class _FakePollingObserver:
        def schedule(self, *_args, **_kwargs) -> None:
            return None

        def start(self) -> None:
            events["polling_started"] += 1

        def stop(self) -> None:
            return None

        def join(self, timeout: float = 0.0) -> None:
            return None

    monkeypatch.setattr(fs_watcher, "Observer", _FakeObserver)
    monkeypatch.setattr(fs_watcher, "PollingObserver", _FakePollingObserver)

    watcher = fs_watcher.RepoChangeWatcher(temp_repo)
    watcher.start()
    try:
        assert events["polling_started"] == 1
    finally:
        watcher.stop()
