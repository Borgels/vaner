from __future__ import annotations

import pytest

from vaner.intent.adapter import ReasonerContext
from vaner.intent.reasoner import CorpusReasoner


@pytest.mark.asyncio
async def test_reasoner_fallback_without_llm():
    reasoner = CorpusReasoner()
    context = ReasonerContext(corpus_type="code_repo", summary="recent edits in auth.py")

    hypotheses = await reasoner.generate(context=context, llm_output=None, fallback_items=["file:auth.py", "file:tests.py"])

    assert hypotheses
    assert hypotheses[0].relevant_keys


@pytest.mark.asyncio
async def test_reasoner_parses_json_wrapped_in_markdown():
    reasoner = CorpusReasoner()
    context = ReasonerContext(corpus_type="code_repo", summary="context")
    llm_output = """
Planning notes before output:
```json
[
  {
    "question": "Where should retries be added?",
    "confidence": 0.82,
    "evidence": ["recent timeout failures"],
    "relevant_keys": ["file:network.py"],
    "category": "debugging",
    "response_format": "checklist",
    "follow_ups": ["Should we add jitter?"]
  }
]
```
"""

    hypotheses = await reasoner.generate(context=context, llm_output=llm_output, fallback_items=[])

    assert len(hypotheses) == 1
    assert hypotheses[0].question == "Where should retries be added?"
    assert hypotheses[0].category == "debugging"


@pytest.mark.asyncio
async def test_reasoner_filters_existing_questions_and_respects_limit():
    reasoner = CorpusReasoner()
    context = ReasonerContext(corpus_type="code_repo", summary="context")
    llm_output = """
[
  {"question": "Already known question", "confidence": 0.6},
  {"question": "New question A", "confidence": 0.7},
  {"question": "New question B", "confidence": 0.8}
]
"""
    hypotheses = await reasoner.generate(
        context=context,
        llm_output=llm_output,
        fallback_items=[],
        existing_questions=["Already known question"],
        limit=1,
    )

    assert len(hypotheses) == 1
    assert hypotheses[0].question == "New question A"


@pytest.mark.asyncio
async def test_reasoner_normalizes_string_fields_from_llm():
    reasoner = CorpusReasoner()
    context = ReasonerContext(corpus_type="code_repo", summary="context")
    llm_output = """
[
  {
    "question": "Where should retries be added?",
    "confidence": "0.8",
    "evidence": "recent timeout failures",
    "relevant_keys": "file:network.py",
    "follow_ups": "Should we add jitter?"
  }
]
"""
    hypotheses = await reasoner.generate(context=context, llm_output=llm_output, fallback_items=[])
    assert len(hypotheses) == 1
    assert hypotheses[0].evidence == ["recent timeout failures"]
    assert hypotheses[0].relevant_keys == ["file:network.py"]
    assert hypotheses[0].follow_ups == ["Should we add jitter?"]
