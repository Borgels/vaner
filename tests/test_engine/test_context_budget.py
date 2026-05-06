# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.engine import _adaptive_selection_top_n, _effective_context_budget
from vaner.models.config import BackendConfig, VanerConfig


def _config(tmp_path, *, max_context_tokens: int = 8192, num_ctx: int | None = None) -> VanerConfig:
    runtime_options = {"num_ctx": num_ctx} if num_ctx is not None else {}
    return VanerConfig(
        repo_root=tmp_path,
        store_path=tmp_path / ".vaner" / "store.db",
        telemetry_path=tmp_path / ".vaner" / "telemetry.db",
        max_context_tokens=max_context_tokens,
        backend=BackendConfig(name="ollama", runtime_options=runtime_options),
    )


def test_effective_context_budget_uses_explicit_request(tmp_path):
    assert _effective_context_budget(_config(tmp_path, num_ctx=131_072), requested=2048) == 2048


def test_effective_context_budget_expands_for_large_local_context(tmp_path):
    assert _effective_context_budget(_config(tmp_path, max_context_tokens=4096, num_ctx=131_072)) == 43_690


def test_effective_context_budget_expands_for_million_token_runtime(tmp_path):
    assert _effective_context_budget(_config(tmp_path, max_context_tokens=4096, num_ctx=1_048_576)) == 262_144


def test_adaptive_selection_top_n_expands_for_multifacet_large_context():
    prompt = "How does the ArtefactStore persist packages? What schema does it use and how are rows retrieved?"
    assert _adaptive_selection_top_n(prompt, requested=8, max_context_tokens=43_690) > 8
