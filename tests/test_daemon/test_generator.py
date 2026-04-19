# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio

from vaner.daemon.engine import generator as generator_mod
from vaner.daemon.engine.generator import agenerate_file_summary, generate_artefact, generate_diff_summary
from vaner.models.config import GenerationConfig, VanerConfig


def test_generate_artefact_for_normal_file(temp_repo):
    source = temp_repo / "normal.py"
    source.write_text(
        "MAX_ITEMS = 10\n\nclass Service:\n    pass\n\ndef f(limit: int = 5):\n    return list(range(limit))[:MAX_ITEMS]\n",
        encoding="utf-8",
    )
    artefact = generate_artefact(source, temp_repo)
    assert artefact.source_path == "normal.py"
    assert "Functions:" in artefact.content
    assert "Constants:" in artefact.content
    assert "Limits:" in artefact.content


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
