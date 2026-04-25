# SPDX-License-Identifier: Apache-2.0

from vaner.integrations.injection import count_tokens, truncate_to_budget


def test_count_tokens_default_heuristic() -> None:
    # Four chars per token.
    assert count_tokens("") == 0
    assert count_tokens("a" * 4) == 1
    assert count_tokens("a" * 40) == 10


def test_count_tokens_respects_injected_tokenizer() -> None:
    def word_tokenizer(text: str) -> int:
        return len(text.split())

    assert count_tokens("one two three", tokenizer=word_tokenizer) == 3


def test_truncate_under_budget_returns_original() -> None:
    text = "a" * 40  # 10 tokens default
    assert truncate_to_budget(text, budget_tokens=100) == text


def test_truncate_respects_budget() -> None:
    text = "a" * 1000  # 250 tokens default
    out = truncate_to_budget(text, budget_tokens=50)
    assert count_tokens(out) <= 50


def test_truncate_preserves_marker() -> None:
    text = "a" * 400  # 100 tokens default
    out = truncate_to_budget(text, budget_tokens=20, marker="***")
    assert out.endswith("***")


def test_truncate_zero_budget_returns_empty() -> None:
    assert truncate_to_budget("hello", budget_tokens=0) == ""
