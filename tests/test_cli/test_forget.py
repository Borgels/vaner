# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.cli.commands.forget import forget_state


def test_forget_state_removes_databases_and_runtime(temp_repo):
    vaner_dir = temp_repo / ".vaner"
    runtime = vaner_dir / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (vaner_dir / "store.db").write_text("x", encoding="utf-8")
    (vaner_dir / "telemetry.db").write_text("y", encoding="utf-8")
    (runtime / "last_context.md").write_text("z", encoding="utf-8")

    removed = forget_state(temp_repo)

    assert removed == 3
    assert not (vaner_dir / "store.db").exists()
    assert not (vaner_dir / "telemetry.db").exists()
    assert not (runtime / "last_context.md").exists()
