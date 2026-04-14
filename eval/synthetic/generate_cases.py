#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from vaner.cli.commands.config import load_config
from vaner.models.artefact import ArtefactKind
from vaner.store.artefacts import ArtefactStore

QUESTION_TYPES = ["origin", "mechanism", "structure", "conditional", "wiring", "error_path", "cross_file"]

GENERATOR_PROMPT = """Given the file summary below, generate {count} developer questions for these types: {question_types}.

Return ONLY a JSON array. Each item must include:
- question
- question_type
- expected_points (4-6 concise factual bullets)

File path: {source_path}
Summary:
{summary}
"""


def _extract_message_text(payload: dict) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content
    return ""


def _extract_json_array(text: str) -> list[dict]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = "\n".join(line for line in stripped.splitlines() if not line.startswith("```"))
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1:
        return []
    loaded = json.loads(stripped[start : end + 1])
    return loaded if isinstance(loaded, list) else []


async def _list_file_summaries(store: ArtefactStore) -> list:
    await store.initialize()
    return await store.list(kind=ArtefactKind.FILE_SUMMARY, limit=400)


async def _generate_questions(prompt: str, *, base_url: str, model: str, api_key_env: str) -> list[dict]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv(api_key_env, "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
    return _extract_json_array(_extract_message_text(response.json()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic benchmark cases from cached file summaries.")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None, help="Output JSON path")
    parser.add_argument("--questions-per-file", type=int, default=1)
    parser.add_argument("--types", nargs="+", default=QUESTION_TYPES)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    config = load_config(repo_root)
    store = ArtefactStore(config.store_path)
    artefacts = asyncio.run(_list_file_summaries(store))
    if not artefacts:
        raise SystemExit("No file summaries found. Run `vaner prepare` first.")

    output = args.output or (repo_root / "eval" / "synthetic" / "cases" / "raw.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    cases: list[dict] = []

    for idx, artefact in enumerate(artefacts):
        prompt = GENERATOR_PROMPT.format(
            count=args.questions_per_file,
            question_types=", ".join(args.types),
            source_path=artefact.source_path,
            summary=artefact.content,
        )
        questions = asyncio.run(
            _generate_questions(
                prompt,
                base_url=config.backend.base_url,
                model=config.generation.generation_model or config.backend.model,
                api_key_env=config.backend.api_key_env,
            )
        )
        for q in questions:
            if not isinstance(q, dict) or "question" not in q:
                continue
            case_id = f"syn-{idx:04d}-{len(cases):03d}"
            cases.append(
                {
                    "case_id": case_id,
                    "question": q.get("question", ""),
                    "question_type": q.get("question_type", "understanding"),
                    "expected_paths": [artefact.source_path],
                    "expected_points": [p for p in q.get("expected_points", []) if isinstance(p, str)],
                }
            )

    output.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"Wrote {len(cases)} synthetic cases to {output}")


if __name__ == "__main__":
    main()
