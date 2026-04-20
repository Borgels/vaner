from __future__ import annotations

import json

from typer.testing import CliRunner

from vaner.cli.commands import app

runner = CliRunner()


def test_doctor_release_probe_reports_outdated_install(temp_repo, monkeypatch) -> None:
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen2.5-coder:7b"
""".strip(),
        encoding="utf-8",
    )

    class _Resp:
        def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
            self.status_code = status_code
            self._payload = payload or {}

        def json(self) -> dict[str, object]:
            return self._payload

    def _fake_get(url: str, *args, **kwargs):  # noqa: ANN001
        if "pypi.org/pypi/vaner/json" in url:
            return _Resp(200, {"info": {"version": "999.0.0"}})
        if url.endswith("/health"):
            return _Resp(200)
        return _Resp(404)

    monkeypatch.setenv("VANER_DOCTOR_CHECK_UPDATES", "1")
    monkeypatch.setenv("VANER_SKIP_MCP_BOOT_PROBE", "1")
    monkeypatch.setattr(app.httpx, "get", _fake_get)

    result = runner.invoke(app.app, ["doctor", "--path", str(temp_repo), "--json"])
    payload = json.loads(result.stdout)
    release_probe = next(item for item in payload["checks"] if item["name"] == "vaner_release_probe")
    assert release_probe["ok"] is False
    assert "vaner upgrade" in release_probe["fix"]
