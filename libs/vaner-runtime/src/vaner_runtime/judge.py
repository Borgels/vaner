from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """You are evaluating whether injected context helped an LLM response.

Injected context:
{context}

User prompt:
{prompt}

Model response:
{response}

Rate helpfulness of the injected context on a scale of 0.0 to 1.0:
- 1.0: Response directly uses injected context, clearly improves answer
- 0.5: Response partially references context
- 0.0: Context was irrelevant or response ignores it

Reply with ONLY a float between 0.0 and 1.0. Nothing else."""


async def judge_helpfulness(
    context: str,
    prompt: str,
    response: str,
    model: str = "qwen2.5-coder:32b",
    ollama_url: str = "http://localhost:11434",
) -> Optional[float]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": JUDGE_PROMPT.format(
                    context=context[:2000],
                    prompt=prompt[:500],
                    response=response[:1000],
                ),
            }
        ],
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(f"{ollama_url}/api/chat", json=payload) as r:
                data = await r.json()
                text = data["message"]["content"].strip()
                return max(0.0, min(1.0, float(text)))
    except Exception as e:
        logger.warning("judge_helpfulness failed: %s", e)
        return None
