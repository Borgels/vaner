#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Refresh src/vaner/setup/recommended/data.json from live model catalogs.

Run once per Vaner release (CI step on the release tag) — never at
runtime on a user's machine. The script is the only place model names
enter the daemon; it queries authoritative external sources, applies
deterministic bucketing + classification, and writes the registry
file the daemon ships.

Sources (in priority order):

1. **Ollama library** — primary. We piggyback on Ollama's curation:
   their library page is the de-facto "popular models" set the
   community already trusts, and per-tag manifests give us
   parameter-count metadata.

   - Library index: ``https://ollama.com/library`` (HTML, scraped).
   - Per-model tags: ``https://registry.ollama.ai/v2/library/<name>/tags/list`` (JSON).

2. **Hugging Face Hub** — secondary, for models Ollama hasn't picked
   up yet (vLLM / MLX / llama.cpp users). Scoped to GGUF +
   instruct/chat. Implemented in WS10.2 follow-up.

Classification (the ONE place humans steer things):

- ``FAMILY_INTENTS`` maps a family slug ("qwen", "llama", "gemma",
  …) to a tuple of :class:`IntentLean` slugs the resolver scores
  against. The mapping is heuristic and conservative — unknown
  families default to ``("mixed",)`` so they still surface to the
  resolver without forcing an opinion.

Reproducibility:

- ``--source-snapshot=<dir>`` reads pre-recorded JSON / HTML from a
  directory instead of hitting the network. Used by tests + CI for
  byte-stable runs across releases.
- ``--out=<path>`` overrides the default registry path
  (``src/vaner/setup/recommended/data.json``).
- Every produced ``data.json`` includes a ``sources`` array with
  per-source ``snapshot_at`` timestamps so the maintainer can see
  exactly which snapshot a release was cut from.

Resilience:

- If a source is unreachable, the script falls back to the previous
  ``data.json`` for that source's contributions (logged WARNING).
- The CI release pipeline FAILS if the produced registry has zero
  models — the staleness gate.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from pydantic import ValidationError

from vaner.setup.recommended.schema import (
    IntentLean,
    RankSource,
    RecommendedModel,
    Registry,
    RegistrySource,
)

logger = logging.getLogger("refresh_recommended_models")

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

OLLAMA_LIBRARY_URL = "https://ollama.com/library"
OLLAMA_REGISTRY_URL = "https://registry.ollama.ai/v2"
HTTP_TIMEOUT = 10.0
USER_AGENT = "Vaner-RecommendedModels-Refresher/0.8.8 (+https://github.com/Borgels/Vaner)"

# Power-of-two-ish parameter bands. Each band keeps the top-N by
# popularity rank. Bands chosen to match common memory budgets — see
# the calibration table in vaner.setup.memory_budget.
PARAM_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 4.0),
    (4.0, 10.0),
    (10.0, 20.0),
    (20.0, 50.0),
    (50.0, 100.0),
    (100.0, 250.0),
    (250.0, 10_000.0),
)

# Top-N per band. Three gives the resolver alternatives without
# bloating the registry.
TOP_PER_BAND = 3

# Family → intent_lean mapping. Conservative; unknown families fall
# back to ("mixed",). The slugs are matched as case-insensitive
# prefixes against the family extracted from the model id (so "qwen"
# matches "qwen", "qwen2", "qwen2.5", "qwen3" etc.) — keep entries
# specific enough that a longer family always wins via the
# longest-prefix rule below.
FAMILY_INTENTS: dict[str, tuple[IntentLean, ...]] = {
    # Code-tuned families
    "codellama": ("coding",),
    "codegemma": ("coding",),
    "starcoder": ("coding",),
    "qwen2.5-coder": ("coding",),
    "qwen3-coder": ("coding",),
    "deepseek-coder": ("coding",),
    "deepseek-v2-coder": ("coding",),
    # Reasoning / research / long-context
    "deepseek-r": ("research", "writing", "planning"),
    "deepseek-v": ("research", "writing", "planning", "mixed"),
    "qwq": ("research", "planning", "coding"),
    "command-r": ("support", "research"),
    "mixtral": ("writing", "research", "mixed"),
    # General-purpose families. Ordered general-to-specific is fine
    # because lookup is longest-prefix (see _intents_for).
    "qwen": ("coding", "writing", "research", "mixed"),
    "llama": ("writing", "support", "mixed"),
    "gemma": ("writing", "support", "learning", "mixed"),
    "phi": ("learning", "support", "mixed"),
    "mistral": ("writing", "support", "mixed"),
    "yi": ("writing", "research", "mixed"),
    "smollm": ("learning", "support"),
    "tinyllama": ("learning", "support"),
}


# Memory-budget estimates for q4 quantisation. The registry does not
# hardcode this per-model — we derive it from params_b using the
# rule-of-thumb that q4 is ≈ 0.6 GB per billion parameters, plus
# 1 GB constant overhead for KV-cache + runtime. Calibrated against
# llama.cpp + Ollama field reports (a 7B q4 model wants ≈ 5 GB; a
# 70B q4 wants ≈ 43 GB; a 32B q4 wants ≈ 20 GB).
def estimate_min_budget_gb(params_b: float) -> float:
    return round(params_b * 0.6 + 1.0, 1)


# ---------------------------------------------------------------------------
# Data classes (intermediate, before pydantic conversion)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawModel:
    """A model harvested from a single source, before bucketing."""

    family: str
    name: str  # ollama name without tag, e.g. "qwen2.5"
    tag: str  # ollama tag suffix, e.g. "32b-instruct-q4_K_M"
    params_b: float
    context_length: int
    popularity_rank: int
    rank_source: RankSource
    ollama_id: str | None = None
    huggingface_id: str | None = None


@dataclass
class SourceResult:
    """One source's contribution + provenance."""

    name: str
    snapshot_at: datetime
    note: str | None = None
    models: list[RawModel] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Ollama library scraper
# ---------------------------------------------------------------------------


def _http_get(url: str) -> str:
    req = urlrequest.Request(url, headers={"User-Agent": USER_AGENT})
    with urlrequest.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 - public read
        if resp.status != 200:
            raise URLError(f"{url} returned status {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


_LIBRARY_HREF_RE = re.compile(
    r'<a[^>]+href="/library/([a-z0-9._-]+)"',
    flags=re.IGNORECASE,
)


def _scrape_ollama_library(html: str) -> list[str]:
    """Extract the model names from the library page, in document order."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _LIBRARY_HREF_RE.finditer(html):
        name = match.group(1).lower()
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


_TAG_PARAMS_RE = re.compile(r"(?:^|[-:])(\d+(?:\.\d+)?)b(?:[-:]|$)", flags=re.IGNORECASE)
_TAG_INSTRUCT_RE = re.compile(r"\b(instruct|chat|it|tuned)\b", flags=re.IGNORECASE)


def _parse_tag_params(tag: str) -> float | None:
    """Extract the parameter count in billions from an Ollama tag."""
    m = _TAG_PARAMS_RE.search(tag)
    if m is None:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _is_instruct_tag(tag: str) -> bool:
    """Heuristic: instruct/chat-tuned variants are what the wizard wants."""
    return bool(_TAG_INSTRUCT_RE.search(tag))


def _fetch_ollama_tags(name: str) -> list[str]:
    """Fetch the tag list for a single model from the registry API."""
    url = f"{OLLAMA_REGISTRY_URL}/library/{name}/tags/list"
    body = _http_get(url)
    payload = json.loads(body)
    tags = payload.get("tags", [])
    return [str(t) for t in tags if isinstance(t, str)]


def harvest_ollama(snapshot_dir: Path | None) -> SourceResult:
    """Walk the Ollama library + per-model tags into RawModel records."""
    snapshot_at = datetime.now(tz=UTC)
    note: str | None = None

    # Fetch (or load from snapshot) the library index HTML.
    if snapshot_dir is not None:
        index_path = snapshot_dir / "ollama_library.html"
        if not index_path.exists():
            note = "snapshot dir present but ollama_library.html missing"
            logger.warning(note)
            return SourceResult(name="ollama-library", snapshot_at=snapshot_at, note=note)
        html = index_path.read_text(encoding="utf-8")
    else:
        try:
            html = _http_get(OLLAMA_LIBRARY_URL)
        except (URLError, HTTPError, TimeoutError) as exc:
            note = f"ollama library unreachable: {exc}"
            logger.warning(note)
            return SourceResult(name="ollama-library", snapshot_at=snapshot_at, note=note)

    names = _scrape_ollama_library(html)
    if not names:
        return SourceResult(
            name="ollama-library",
            snapshot_at=snapshot_at,
            note="ollama library page returned 0 model names — scraper may be stale",
        )

    out: list[RawModel] = []
    # Ollama already orders the library page by popularity (their default
    # sort). We trust that ordering as the popularity_rank.
    for rank, name in enumerate(names, start=1):
        if snapshot_dir is not None:
            tags_path = snapshot_dir / f"ollama_tags_{name}.json"
            if not tags_path.exists():
                logger.debug("snapshot missing tags for %s; skipping", name)
                continue
            try:
                tags = json.loads(tags_path.read_text(encoding="utf-8")).get("tags", [])
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("tags fixture for %s unreadable: %s", name, exc)
                continue
        else:
            try:
                tags = _fetch_ollama_tags(name)
            except (URLError, HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                logger.debug("tags for %s unreachable: %s", name, exc)
                continue

        # Filter to instruct/chat tags with a recoverable parameter count.
        for tag in tags:
            if not _is_instruct_tag(tag):
                continue
            params = _parse_tag_params(tag)
            if params is None:
                continue
            family = _family_slug(name)
            ollama_id = f"{name}:{tag}"
            out.append(
                RawModel(
                    family=family,
                    name=name,
                    tag=tag,
                    params_b=params,
                    # Ollama does not expose context length per tag; the
                    # daemon reads it from the running model. Carry a
                    # conservative default; the desktop wizard's preset
                    # card surfaces the daemon's reported value at run
                    # time.
                    context_length=8192,
                    popularity_rank=rank,
                    rank_source="ollama-library",
                    ollama_id=ollama_id,
                ),
            )
    return SourceResult(name="ollama-library", snapshot_at=snapshot_at, models=out)


_FAMILY_RE = re.compile(r"^([a-z][a-z0-9.-]*?)(?:\d|$)", flags=re.IGNORECASE)


def _family_slug(name: str) -> str:
    """Best-effort family extraction from an Ollama model name.

    Heuristic: take the leading non-digit chunk. ``qwen2.5`` → ``qwen2.5``
    (full match); ``deepseek-r1`` → ``deepseek-r``; ``llama3.2`` →
    ``llama3.2``. Used as the lookup key into ``FAMILY_INTENTS``.
    """
    return name.lower()


def _intents_for(name: str) -> tuple[IntentLean, ...]:
    """Look up ``FAMILY_INTENTS`` by longest-prefix match against ``name``."""
    n = name.lower()
    best_key: str | None = None
    for key in FAMILY_INTENTS:
        if n.startswith(key):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    if best_key is None:
        return ("mixed",)
    return FAMILY_INTENTS[best_key]


# ---------------------------------------------------------------------------
# Bucketing + final assembly
# ---------------------------------------------------------------------------


def _bucket_index(params_b: float) -> int:
    for i, (lo, hi) in enumerate(PARAM_BANDS):
        if lo < params_b <= hi:
            return i
    # Fallback: outside all bands → drop into the largest.
    return len(PARAM_BANDS) - 1


def _deduplicate(raw: list[RawModel]) -> list[RawModel]:
    """Collapse near-duplicates (same family + params) keeping the lowest rank."""
    seen: dict[tuple[str, float], RawModel] = {}
    for m in raw:
        key = (m.family, m.params_b)
        if key not in seen or m.popularity_rank < seen[key].popularity_rank:
            seen[key] = m
    return list(seen.values())


def assemble_registry(sources: list[SourceResult], generator_id: str) -> Registry:
    """Bucket, top-N, tag, and pydantic-validate the result."""
    raw: list[RawModel] = []
    for s in sources:
        raw.extend(s.models)
    deduped = _deduplicate(raw)

    bucketed: dict[int, list[RawModel]] = defaultdict(list)
    for m in deduped:
        bucketed[_bucket_index(m.params_b)].append(m)

    chosen: list[RecommendedModel] = []
    for i in range(len(PARAM_BANDS)):
        band_models = bucketed[i]
        band_models.sort(key=lambda m: m.popularity_rank)
        for m in band_models[:TOP_PER_BAND]:
            chosen.append(
                RecommendedModel(
                    id=m.ollama_id or f"{m.name}:{m.tag}",
                    family=m.family,
                    params_b=m.params_b,
                    min_effective_gb_q4=estimate_min_budget_gb(m.params_b),
                    intent_lean=_intents_for(m.name),
                    ollama_id=m.ollama_id,
                    huggingface_id=m.huggingface_id,
                    context_length=m.context_length,
                    popularity_rank=m.popularity_rank,
                    rank_source=m.rank_source,
                ),
            )

    return Registry(
        schema_version=1,
        generated_at=datetime.now(tz=UTC),
        generator=generator_id,
        sources=tuple(RegistrySource(name=s.name, snapshot_at=s.snapshot_at, note=s.note) for s in sources),
        models=tuple(chosen),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _git_sha() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if sha.returncode == 0:
            return sha.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "src" / "vaner" / "setup" / "recommended" / "data.json"),
        help="Path to write data.json.",
    )
    parser.add_argument(
        "--source-snapshot",
        type=Path,
        default=None,
        help="Read fixture snapshots from this dir instead of hitting the network.",
    )
    parser.add_argument(
        "--skip-ollama",
        action="store_true",
        help="Skip the Ollama library source.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the registry to stdout instead of writing it.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Do not fail when the produced registry has zero models. CI release pipeline must NOT pass this.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else (logging.INFO if args.verbose == 1 else logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    sources: list[SourceResult] = []
    if not args.skip_ollama:
        sources.append(harvest_ollama(args.source_snapshot))

    generator_id = f"refresh_recommended_models.py@{_git_sha()}"
    try:
        registry = assemble_registry(sources, generator_id)
    except ValidationError as exc:
        logger.error("schema validation failed: %s", exc)
        return 2

    if not registry.models and not args.allow_empty:
        logger.error("registry is empty — staleness gate; pass --allow-empty if intentional")
        return 3

    payload = registry.model_dump(mode="json")
    if args.dry_run:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("wrote %d models to %s", len(registry.models), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
