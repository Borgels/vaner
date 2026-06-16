# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json

import httpx

from vaner.daemon.engine import generator as generator_mod
from vaner.daemon.engine.generator import (
    DIFF_SUMMARY_PROMPT,
    FILE_SUMMARY_PROMPT,
    _llm_summarize,
    agenerate_file_summary,
    generate_artefact,
    generate_diff_summary,
)
from vaner.models.config import BackendConfig, GenerationConfig, VanerConfig

_real_async_client = httpx.AsyncClient


def _stub_async_client(handler):
    def _factory(**_kwargs):
        return _real_async_client(transport=httpx.MockTransport(handler))

    return _factory


def test_generate_artefact_for_normal_file(temp_repo):
    source = temp_repo / "normal.py"
    source.write_text(
        "MAX_ITEMS = 10\n\nclass Service:\n    pass\n\ndef f(limit: int = 5):\n    return list(range(limit))[:MAX_ITEMS]\n",
        encoding="utf-8",
    )
    artefact = generate_artefact(source, temp_repo)
    assert artefact.source_path == "normal.py"
    assert "Functions:" in artefact.content
    assert "f(limit: int)" in artefact.content
    assert "Constants:" in artefact.content
    assert "Limits:" in artefact.content


def test_generate_artefact_preserves_sql_schema_anchors(temp_repo):
    source = temp_repo / "store.py"
    source.write_text(
        """
import aiosqlite

class Store:
    async def initialize(self) -> None:
        await self.db.execute(\"\"\"
            CREATE TABLE artefacts (
                key TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
        \"\"\")
        await self.db.execute("CREATE INDEX idx_artefacts_source_path ON artefacts(source_path)")

    async def list_by_keys(self, keys: list[str]) -> list[str]:
        return keys
""",
        encoding="utf-8",
    )

    artefact = generate_artefact(source, temp_repo)

    assert "Schema:" in artefact.content
    assert "CREATE TABLE artefacts" in artefact.content
    assert "CREATE INDEX idx_artefacts_source_path" in artefact.content
    assert "list_by_keys(self, keys: list[str]) -> list[str]" in artefact.content


def test_generate_artefact_for_empty_file(temp_repo):
    source = temp_repo / "empty.py"
    source.write_text("\n", encoding="utf-8")
    artefact = generate_artefact(source, temp_repo)
    assert artefact.content == "Empty or whitespace-only file."


def test_generate_artefact_for_binary_file(temp_repo):
    source = temp_repo / "binary.bin"
    source.write_bytes(b"\x00\xff\x10\x11")
    artefact = generate_artefact(source, temp_repo)
    assert artefact.source_path == "binary.bin"


def test_generate_diff_summary_redacts_patterns(temp_repo):
    diff = "+ password = secret123"
    artefact = generate_diff_summary(
        temp_repo,
        "sample.py",
        diff,
        redact_patterns=[r"secret\d+"],
    )
    assert "REDACTED" in artefact.content


def test_llm_summary_prompts_include_internal_evidence_policy():
    for prompt in (FILE_SUMMARY_PROMPT, DIFF_SUMMARY_PROMPT):
        assert "Vaner internal LLM policy" in prompt
        assert "Evidence summary policy" in prompt
        assert "Preserve implementation anchors" in prompt
        assert "Do not let predictions or likely intent become factual behavior" in prompt


def test_agenerate_file_summary_uses_llm_when_enabled(temp_repo, monkeypatch):
    source = temp_repo / "llm.py"
    source.write_text("def x():\n    return 1\n", encoding="utf-8")
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
        generation=GenerationConfig(use_llm=True, generation_model="gpt-test"),
    )

    async def _fake_llm(text: str, prompt_template: str, config_obj: VanerConfig, source_label: str) -> str:
        return "LLM precise summary"

    monkeypatch.setattr(generator_mod, "_llm_summarize", _fake_llm)
    artefact = asyncio.run(
        agenerate_file_summary(
            source,
            temp_repo,
            model_name="gpt-test",
            config=config,
        )
    )
    assert artefact.content == "LLM precise summary"
    assert artefact.metadata["summary_mode"] == "llm"


def test_llm_summarize_uses_native_ollama_chat_with_thinking_disabled(temp_repo, monkeypatch):
    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["body"] = json.loads(req.content or b"{}")
        return httpx.Response(200, json={"message": {"content": "native summary"}})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
        backend=BackendConfig(name="ollama", base_url="http://127.0.0.1:11434/v1", model="qwen3.5:35b"),
        generation=GenerationConfig(use_llm=True, summary_max_tokens=64),
    )

    result = asyncio.run(_llm_summarize("def x(): pass", "summarize", config, "x.py"))

    assert result == "native summary"
    assert captured["path"] == "/api/chat"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["think"] is False
    assert body["options"] == {"num_predict": 64}


def test_agenerate_file_summary_falls_back_to_heuristic_when_llm_fails(temp_repo, monkeypatch):
    source = temp_repo / "fallback.py"
    source.write_text("MAX_A = 10\n\ndef x():\n    return MAX_A\n", encoding="utf-8")
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
        generation=GenerationConfig(use_llm=True, generation_model="gpt-test"),
    )

    async def _fake_llm_none(text: str, prompt_template: str, config_obj: VanerConfig, source_label: str) -> str | None:
        return None

    monkeypatch.setattr(generator_mod, "_llm_summarize", _fake_llm_none)
    artefact = asyncio.run(
        agenerate_file_summary(
            source,
            temp_repo,
            model_name="gpt-test",
            config=config,
        )
    )
    assert "Functions:" in artefact.content
    assert artefact.metadata["summary_mode"] == "heuristic"
