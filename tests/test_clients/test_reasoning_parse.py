# SPDX-License-Identifier: Apache-2.0
"""Tests for thinking-block strippers used by LLMResponse adapters.

Covers Qwen3's ``"Thinking Process:"`` preamble, Claude's ``<thinking>``
block, DeepSeek's ``<think>``, and fenced ``reasoning`` blocks. Also covers
the no-op path for responses without any reasoning preamble.
"""

from __future__ import annotations

from vaner.clients.llm_response import LLMResponse, approx_tokens, split_thinking_and_content


def test_no_thinking_preamble_returns_raw_content():
    raw = '{"key": "value"}'
    resp = split_thinking_and_content(raw)
    assert resp.thinking == ""
    assert resp.content == '{"key": "value"}'
    assert resp.raw == raw


def test_claude_thinking_block_extracted():
    raw = '<thinking>Let me reason about this.</thinking>\n{"answer": 42}'
    resp = split_thinking_and_content(raw)
    assert resp.thinking == "Let me reason about this."
    assert resp.content == '{"answer": 42}'


def test_claude_thinking_multiline():
    raw = """<thinking>
step 1: understand the input
step 2: produce JSON
</thinking>
{"result": "ok"}"""
    resp = split_thinking_and_content(raw)
    assert "step 1" in resp.thinking
    assert "step 2" in resp.thinking
    assert resp.content.startswith("{")


def test_deepseek_think_block_extracted():
    raw = '<think>Short internal chain-of-thought here.</think>\n{"k":"v"}'
    resp = split_thinking_and_content(raw)
    assert resp.thinking == "Short internal chain-of-thought here."
    assert resp.content == '{"k":"v"}'


def test_qwen3_thinking_process_preamble_extracted():
    raw = 'Thinking Process: first I analyse the prompt, then I produce JSON.\n\n{"ok": true}'
    resp = split_thinking_and_content(raw)
    assert "analyse the prompt" in resp.thinking
    assert resp.content == '{"ok": true}'


def test_qwen3_thought_process_alias():
    raw = 'Thought Process: considering A vs B.\n\n{"choice":"A"}'
    resp = split_thinking_and_content(raw)
    assert "A vs B" in resp.thinking
    assert resp.content.startswith("{")


def test_qwen3_reasoning_label_alias():
    raw = 'Reasoning: I will pick the shortest path.\n\n["a","b"]'
    resp = split_thinking_and_content(raw)
    assert "shortest path" in resp.thinking
    assert resp.content == '["a","b"]'


def test_fenced_reasoning_block_extracted():
    raw = """```reasoning
several lines
of thought
```
{"done": true}"""
    resp = split_thinking_and_content(raw)
    assert "several lines" in resp.thinking
    assert resp.content == '{"done": true}'


def test_case_insensitive_tag():
    raw = "<THINKING>mixed case</THINKING>\n[]"
    resp = split_thinking_and_content(raw)
    assert resp.thinking == "mixed case"
    assert resp.content == "[]"


def test_response_str_returns_content_for_legacy_callers():
    raw = '<thinking>reason</thinking>\n{"v":1}'
    resp = split_thinking_and_content(raw)
    assert str(resp) == '{"v":1}'


def test_empty_raw_response_handled():
    resp = split_thinking_and_content("")
    assert resp.thinking == ""
    assert resp.content == ""
    assert resp.raw == ""


def test_approx_tokens_rough_estimate():
    assert approx_tokens("") == 0
    assert approx_tokens("abcd") >= 1
    # Roughly 4 chars per token
    assert approx_tokens("a" * 40) in range(8, 12)


def test_llm_response_is_hashable_and_immutable():
    resp = LLMResponse(thinking="t", content="c", raw="c")
    assert isinstance(hash(resp), int)
