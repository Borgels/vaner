# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class PrivacyConfig(BaseModel):
    allowed_paths: list[str] = Field(default_factory=lambda: ["."])
    excluded_patterns: list[str] = Field(default_factory=lambda: ["*.env", "*.key", "*.pem", "credentials*", "secrets*"])
    redact_patterns: list[str] = Field(default_factory=list)
    telemetry: str = "local"
    exclude_private: bool = False


class BackendConfig(BaseModel):
    """Configuration for the LLM backend that serves end-user requests.

    ``base_url`` and ``model`` are required -- Vaner will refuse to start the
    proxy if either is empty.  They are left blank by default so that ``vaner
    init`` generates a config that forces the user to fill in their own values
    rather than silently defaulting to a specific provider.

    Examples::

        # OpenAI
        base_url = "https://api.openai.com/v1"
        model = "gpt-4o"
        api_key_env = "OPENAI_API_KEY"

        # Anthropic via OpenAI-compatible proxy
        base_url = "https://api.anthropic.com/v1"
        model = "claude-opus-4-5"
        api_key_env = "ANTHROPIC_API_KEY"

        # Local Ollama
        base_url = "http://127.0.0.1:11434/v1"
        model = "qwen2.5-coder:32b"

        # Local vLLM / LM Studio / any OpenAI-compatible server
        base_url = "http://127.0.0.1:8000/v1"
        model = "Qwen/Qwen2.5-Coder-32B-Instruct"
    """

    name: str = "custom"
    base_url: str = ""
    model: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    prefer_local: bool = True
    fallback_enabled: bool = False
    fallback_base_url: str | None = None
    fallback_model: str | None = None
    fallback_api_key_env: str = "OPENAI_API_KEY"
    remote_budget_per_hour: int = 60
    request_timeout_seconds: float = 30.0
    # Phase 4 / Phase B: reasoning-LLM support.
    # reasoning_mode tells Vaner how to handle thinking-block preambles:
    #   - "off": disable thinking at the provider level when possible; reject
    #     responses that still emit a preamble.
    #   - "allowed": thinking permitted; adapter strips it; trace captured.
    #   - "required": provider must emit a thinking preamble; error otherwise.
    #   - "provider_default": trust the adapter/provider default (status quo).
    reasoning_mode: Literal["off", "allowed", "required", "provider_default"] = "provider_default"
    # Default cap on content tokens (applied when the engine doesn't supply a
    # per-prediction budget).
    max_response_tokens: int = 2048
    # Extra tokens allowed for thinking when reasoning_mode != "off".
    reasoning_token_budget: int = 8192
    # Try response_format={"type":"json_object"} before tolerant parsing.
    prefer_structured_output: bool = True


class GenerationConfig(BaseModel):
    use_llm: bool = False
    generation_model: str | None = None
    max_file_chars: int = 8000
    summary_max_tokens: int = 400
    llm_timeout_seconds: float = 30.0
    max_concurrent_generations: int = 4
    max_generations_per_cycle: int = 4000


class ProxyConfig(BaseModel):
    proxy_token: str | None = None
    max_requests_per_minute: int = 120
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None


class GatewayConfig(BaseModel):
    passthrough_enabled: bool = False
    routes: dict[str, str] = Field(default_factory=dict)
    annotate_response_trailer: bool = False
    annotate_system_note: Literal["off", "min", "full"] = "off"
    shadow_rate: float = 0.0


class MCPConfig(BaseModel):
    transport: Literal["stdio", "sse"] = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = 8472


class ComputeConfig(BaseModel):
    device: str = "auto"
    cpu_fraction: float = 0.2
    gpu_memory_fraction: float = 0.5
    idle_only: bool = True
    idle_cpu_threshold: float = 0.6
    idle_gpu_threshold: float = 0.7
    embedding_device: str | None = None
    exploration_concurrency: int = 4
    max_parallel_precompute: int = 1
    max_cycle_seconds: int = 300
    """Hard wall-clock cap for a single precompute cycle.

    Prevents the ponder loop from running away if a backend stalls or the
    frontier grows pathologically. ``0`` disables the cap (unbounded); any
    positive value bounds the cycle to that many seconds. Vaner resumes on
    the next cycle, so bounding here is safe.
    """

    max_session_minutes: int | None = None
    """Optional cumulative cap for a continuous ``vaner daemon`` session.

    ``None`` (default) means unbounded — the daemon keeps running until
    stopped. When set, the daemon exits cleanly once the wall-clock since
    ``daemon start`` exceeds this many minutes. Users who want Vaner to
    never ponder for more than, say, 30 minutes can set ``30`` here.
    """

    adaptive_cycle_budget: bool = True
    """Let Vaner sub-cap each precompute cycle by the estimated time until
    the next user prompt.

    When enabled (default) the engine consults an EMA of inter-prompt gaps
    and shrinks the effective cycle deadline so one exploration phase
    finishes before the next prompt arrives. During long idle periods the
    budget expands back up to ``max_cycle_seconds``. The static cap still
    applies as a hard upper bound — the adaptive model only ever *shortens*
    the cycle during active sessions.
    """

    adaptive_cycle_min_seconds: float = 5.0
    """Floor for the adaptive cycle budget. A value of 5.0 guarantees at
    least one LLM round-trip and a cache write even when the user is
    prompting very rapidly.
    """

    adaptive_cycle_utilisation: float = 0.8
    """Fraction of the estimated next-prompt ETA that Vaner is willing to
    spend pondering. ``0.8`` leaves headroom to finish writing artefacts /
    cache entries before the user arrives. Raise toward ``1.0`` for more
    aggressive speculation; lower for safer margins.
    """


class IntentConfig(BaseModel):
    enabled: bool = True
    include_global_skills: bool = True
    skill_roots: list[str] = Field(default_factory=lambda: [".cursor/skills", ".claude/skills", "skills"])
    lookback_turns: int = 8
    skills_loop_enabled: bool = True
    max_feedback_events_per_cycle: int = 5
    domain: Literal["coding", "research", "writing", "ops"] = "coding"
    embedding_classifier_enabled: bool = True
    cross_workspace_profile: bool = False


class ExplorationEndpoint(BaseModel):
    """One endpoint in a multi-endpoint exploration pool.

    Each entry points at an OpenAI-compatible chat-completions server (vLLM,
    Ollama's ``/v1`` shim, LM Studio, OpenAI proper, …) with its own model and
    weight. The pool distributes LLM calls across entries via weighted
    round-robin, skipping endpoints that have failed repeatedly.
    """

    url: str
    """Base URL for the endpoint, e.g. ``http://gpu-host-01.example:8000/v1``."""

    model: str
    """Model name to request at this endpoint."""

    weight: float = 1.0
    """Relative share of traffic. Endpoints with weight 0 are disabled; a weight
    of 2.0 receives twice the load of a weight-1.0 endpoint in the same pool.
    """

    api_key_env: str = ""
    """Name of an environment variable holding the endpoint's API key. Empty =
    no auth header (e.g. local vLLM with ``--api-key disabled``).
    """

    backend: Literal["openai", "ollama"] = "openai"
    """Protocol the endpoint speaks. Default ``openai`` covers vLLM and any
    OpenAI-compatible server; ``ollama`` uses the native ``/api/generate``
    protocol.
    """

    latency_p50_ms: float = 800.0
    context_window: int = 8192
    reasoning_depth_hint: Literal["low", "medium", "high"] = "medium"
    structured_output_reliability: float = 0.7
    cost_per_1k_tokens: float = 0.0
    cost_ceiling_per_cycle_usd: float = 0.0


class ExplorationConfig(BaseModel):
    """Controls how aggressively Vaner explores the scenario space.

    The underlying engine is always a continuous best-first search; these
    fields are the dials that shape how deep and broad it goes.

    Aggressiveness presets are convenience constructors — use the class methods
    ``conservative()``, ``normal()``, ``aggressive()``, ``maximum()`` rather
    than setting fields by hand.

    LLM Exploration
    ---------------
    Set ``exploration_endpoint`` to point at a local Ollama instance or any
    OpenAI-compatible server (vLLM, LM Studio, remote API).  Leave empty to
    auto-detect on localhost.  ``exploration_model`` selects the model; if
    empty Vaner picks the first available model reported by the endpoint.
    ``exploration_backend`` can be ``"auto"`` (probe endpoint), ``"ollama"``,
    or ``"openai"`` (OpenAI-compatible / vLLM).

    Embeddings
    ----------
    ``embedding_model`` names a sentence-transformers model used to embed
    prompts and cache entries for semantic matching.  ``embedding_device``
    controls the torch device (``"cpu"``, ``"cuda"``).  Leave
    ``embedding_model`` empty to disable embedding-based cache matching.
    """

    max_exploration_depth: int = 3
    """Maximum LLM branch depth from the original graph seed."""

    frontier_max_size: int = 500
    """Maximum number of pending scenarios in the frontier at once."""

    min_priority: float = 0.10
    """Scenarios with effective priority below this threshold are rejected."""

    dedup_threshold: float = 0.70
    """Jaccard overlap above which two scenarios are considered duplicates."""

    saturation_coverage: float = 0.90
    """Fraction of known files that, once covered, signals saturation."""

    llm_gate: Literal["none", "non_trivial", "all"] = "non_trivial"
    """Which scenarios are sent to the LLM.
    - "none"        — graph-walk only, no LLM calls
    - "non_trivial" — LLM for depth > 0 or non-graph sources
    - "all"         — LLM for every scenario
    """

    # ------------------------------------------------------------------
    # Deep-drill on high-priority predictions
    # ------------------------------------------------------------------
    # When a scenario's *effective* priority exceeds ``deep_drill_priority_threshold``
    # Vaner treats it as a high-confidence next-prompt prediction and invests
    # extra compute: more LLM follow-on branches, a depth-cap bonus that lets
    # the lineage exceed ``max_exploration_depth``, and a softer per-hop decay
    # so the deep line stays competitive in the frontier. Children inherit a
    # decrementing bonus budget, so the drill-down is bounded.

    deep_drill_priority_threshold: float = 0.60
    """Effective-priority threshold (post source/layer multipliers) above
    which a scenario is treated as a high-priority prediction worth deeper
    exploration. ``1.01`` effectively disables deep-drill.
    """

    deep_drill_depth_bonus: int = 2
    """Extra depth a high-priority lineage may explore beyond
    ``max_exploration_depth``. The bonus decrements by 1 each LLM hop, so a
    value of 2 lets one extra generation branch at full budget and one at
    half budget before the frontier's regular depth cap reasserts.
    """

    deep_drill_max_followons: int = 5
    """Max follow-on branches the LLM is allowed to propose for a
    high-priority scenario (vs. the default 3 for normal scenarios). The LLM
    prompt is widened accordingly; the frontier's admission gate still
    dedups and filters.
    """

    deep_drill_branch_decay: float = 0.88
    """Priority decay applied when pushing a follow-on branch from a
    high-priority parent. The default (``0.88``) is gentler than the general
    ``branch_priority_decay`` (``0.70``), so deep-drill lineages stay near
    the top of the frontier instead of getting crowded out by fresh seeds.
    """

    # ------------------------------------------------------------------
    # Exploration LLM endpoint (separate from the user-facing backend)
    # ------------------------------------------------------------------

    exploration_model: str = ""
    """Model name for the exploration LLM.
    Ollama tag (e.g. ``"qwen2.5-coder:32b"``) or OpenAI-compatible model ID
    (e.g. ``"Qwen/Qwen3.5-35B-A3B-FP8"`` for vLLM).  Empty = pick first
    available model from the detected endpoint.
    """

    exploration_endpoint: str = ""
    """Base URL for the exploration LLM endpoint.

    Examples:

    - Ollama:        ``"http://127.0.0.1:11434"``
    - vLLM / local:  ``"http://127.0.0.1:8000/v1"``
    - Remote vLLM:   ``"http://your-server:8000/v1"``
    - OpenAI API:    ``"https://api.openai.com/v1"``

    Empty string = auto-probe localhost (Ollama on 11434, then vLLM on 8000).
    """

    exploration_backend: Literal["auto", "ollama", "openai"] = "auto"
    """Which client protocol to use.
    - ``"auto"``   — probe endpoint, infer from API shape
    - ``"ollama"`` — force Ollama ``/api/generate`` protocol
    - ``"openai"`` — force OpenAI ``/v1/chat/completions`` protocol (for vLLM)
    """

    exploration_api_key: str = ""
    """API key for the exploration endpoint.  Use ``"EMPTY"`` for local
    vLLM which requires a non-empty but unauthenticated token.  Leave empty
    to read from the ``VANER_EXPLORATION_API_KEY`` environment variable or
    fall back to ``"EMPTY"`` for local endpoints.
    """

    endpoints: list[ExplorationEndpoint] = Field(default_factory=list)
    """Optional pool of exploration endpoints for multi-endpoint routing.

    When non-empty, Vaner builds an ``ExplorationEndpointPool`` that dispatches
    each LLM call via weighted round-robin across the pool. Per-endpoint health
    tracking skips any endpoint that has failed 3+ times in a row until a
    60-second cooldown elapses.

    When empty, Vaner falls back to the single-endpoint path using
    ``exploration_endpoint`` / ``exploration_model`` / ``exploration_backend``
    above — existing behaviour, unchanged.

    Example:

    .. code-block:: toml

        [[exploration.endpoints]]
        url = "http://gpu-host-01.example:8000/v1"
        model = "Qwen/Qwen2.5-Coder-32B"
        weight = 1.0

        [[exploration.endpoints]]
        url = "http://gpu-host-02.example:8000/v1"
        model = "Qwen/Qwen2.5-Coder-32B"
        weight = 1.0

    API keys, when needed, are read from the environment variable named by
    ``api_key_env`` on each entry.
    """
    economics_first_routing: bool = True
    """When true, prefer the cheapest endpoint that satisfies minimum latency,
    context window, and reliability requirements for exploration tasks."""

    # ------------------------------------------------------------------
    # Embedding model for semantic cache matching
    # ------------------------------------------------------------------

    embedding_model: str = "all-MiniLM-L6-v2"
    """sentence-transformers model for semantic prompt embeddings used in
    cache matching.  Set to empty string to disable embedding-based matching
    and fall back to token-Jaccard only.
    """

    embedding_device: str = "cpu"
    """Torch device for the embedding model (``"cpu"`` or ``"cuda"``)."""

    # ------------------------------------------------------------------
    # Unused-cache decay (cleanup of unlikely predictions)
    # ------------------------------------------------------------------

    unused_cache_max_age_seconds: float = 1800.0
    """How long a precomputed cache entry is allowed to sit untouched before
    it is purged, independent of its TTL.

    Vaner precomputes a lot of speculative context. Some predictions get
    *unlikely* over time — the developer never issued anything close, or
    the cache entry competed with a sibling that matched first. Keeping
    unused entries around pollutes future cache matching with stale prompt
    hints and wastes store space. After this many seconds without a single
    cache hit the entry is dropped regardless of TTL.

    Set to ``0.0`` to disable the decay pass and rely solely on TTL
    expiration. The default (30 minutes) leaves enough headroom for a user
    who steps away briefly while still reclaiming slots aggressively in
    long-lived daemons.
    """

    predicted_response_enabled: bool = False
    """Experimental: when the top prompt-macro is validated (high
    ``use_count`` and confidence) invest a dedicated LLM call to generate a
    *draft response* for that macro and stash it in the cache enrichment as
    ``predicted_response``. Agents consuming Vaner's context package may
    surface the draft to the user the moment the expected prompt arrives.

    Off by default because it amplifies LLM spend; opt in only on operators
    who want Vaner to ponder *answers*, not just context.
    """

    predicted_response_min_macro_use_count: int = 3
    """Minimum ``use_count`` on a prompt macro before Vaner is willing to
    spend a predicted-response LLM call on it.
    """

    predicted_response_max_per_cycle: int = 1
    """How many predicted-response drafts Vaner may generate per cycle.
    Kept low by default — an opt-in budget rather than a free-for-all.
    """

    @property
    def endpoint(self) -> str:
        return self.exploration_endpoint

    @property
    def model(self) -> str:
        return self.exploration_model

    @property
    def backend(self) -> str:
        return self.exploration_backend

    @classmethod
    def conservative(cls) -> ExplorationConfig:
        """Graph-walk only, no LLM, shallow depth."""
        return cls(max_exploration_depth=1, llm_gate="none", min_priority=0.20)

    @classmethod
    def normal(cls) -> ExplorationConfig:
        """LLM for non-trivial scenarios, moderate depth."""
        return cls(max_exploration_depth=2, llm_gate="non_trivial")

    @classmethod
    def aggressive(cls) -> ExplorationConfig:
        """Deep exploration, LLM for all non-trivial, large frontier."""
        return cls(
            max_exploration_depth=4,
            frontier_max_size=2000,
            min_priority=0.05,
            llm_gate="non_trivial",
        )

    @classmethod
    def maximum(cls) -> ExplorationConfig:
        """Unrestricted exploration — use when compute is truly unlimited."""
        return cls(
            max_exploration_depth=10,
            frontier_max_size=10000,
            min_priority=0.01,
            saturation_coverage=0.99,
            llm_gate="all",
        )


TierPolicy = Literal["auto", "opt_in", "off"]


class IntentArtefactTiersConfig(BaseModel):
    """Source-tier policies for intent-bearing artefacts (spec §12).

    ``auto`` — the tier's connectors run without extra opt-in.
    ``opt_in`` — connectors only run when their per-connector ``enabled``
    flag is set. ``off`` — disables the tier entirely regardless of
    per-connector flags.

    Defaults: T1 ``auto`` (repo-local plans), T2/T3/T4 ``opt_in``.
    """

    T1: TierPolicy = "auto"
    T2: TierPolicy = "opt_in"
    T3: TierPolicy = "opt_in"
    T4: TierPolicy = "opt_in"


class LocalPlanSourceConfig(BaseModel):
    """``[sources.intent_artefacts.local_plan]`` (T1).

    Empty ``allowlist`` / ``excludelist`` mean the connector falls back
    to its module-level defaults (see
    :mod:`vaner.intent.connectors.local_plan`).
    """

    allowlist: list[str] = Field(default_factory=list)
    excludelist: list[str] = Field(default_factory=list)


class MarkdownOutlineSourceConfig(BaseModel):
    """``[sources.intent_artefacts.markdown_outline]`` (T2, opt-in)."""

    enabled: bool = False
    excludelist: list[str] = Field(default_factory=list)
    max_candidates: int = 500


class GitHubIssuesSourceConfig(BaseModel):
    """``[sources.intent_artefacts.github_issues]`` (T3, opt-in).

    ``repos`` is the explicit per-repo allowlist; no wildcard access.
    Empty ``repos`` disables the connector even when ``enabled`` is
    True.
    """

    enabled: bool = False
    repos: list[str] = Field(default_factory=list)
    include_closed: bool = False
    max_issues: int = 200


class IntentArtefactsConfig(BaseModel):
    """``[sources.intent_artefacts]`` — 0.8.2 intent-bearing artefact
    ingestion (spec §12).

    ``enabled`` is the master switch. When ``False`` the pipeline and
    all connectors are inert — no discovery, no fetches, no persistence,
    no signals.
    """

    enabled: bool = True
    tiers: IntentArtefactTiersConfig = Field(default_factory=IntentArtefactTiersConfig)
    local_plan: LocalPlanSourceConfig = Field(default_factory=LocalPlanSourceConfig)
    markdown_outline: MarkdownOutlineSourceConfig = Field(default_factory=MarkdownOutlineSourceConfig)
    github_issues: GitHubIssuesSourceConfig = Field(default_factory=GitHubIssuesSourceConfig)


class SourcesConfig(BaseModel):
    """``[sources]`` — source-discipline controls.

    Today this carries only the 0.8.2 intent-artefact ingestion config.
    WS4 (0.8.3+) extends this with notes-tool and cloud-doc connectors.
    """

    intent_artefacts: IntentArtefactsConfig = Field(default_factory=IntentArtefactsConfig)


# 0.8.4 WS3 — background refinement feature flag.
#
# Deep-Run maturation (0.8.3 WS3) ships the machinery but is gated to
# declared Deep-Run windows. This config flag lets the engine run the
# same ``mature_one()`` loop on top-K READY predictions during ordinary
# background cycles, post-frontier, on spare compute.
#
# Ships default-OFF in 0.8.4. 0.8.5 flips the default after the κ
# anti-self-judging bench gate (WS1+WS2) passes. The log writes from
# WS4 (``prediction_adoption_outcomes``) happen regardless of this
# flag — data accumulates from day one.
class RefinementConfig(BaseModel):
    enabled: bool = Field(default=False)
    max_candidates_per_cycle: int = Field(default=3, ge=1)
    min_remaining_deadline_seconds: float = Field(default=2.0, ge=0.0)
    # WS4 — adoption-outcome sweep thresholds (these are always-active;
    # gating them behind ``enabled`` would delay log population).
    # Named ``_seconds`` to match the implementation in
    # ``engine._sweep_pending_adoption_outcomes`` which is wall-clock-
    # based, not cycle-count-based. The earlier ``_cycles`` name
    # (0.8.4-pre-hardening) promised cycle semantics the code did not
    # deliver; renamed during the 0.8.4 hardening pass.
    adoption_pending_confirm_seconds: float = Field(default=600.0, ge=0.0)


class VanerConfig(BaseModel):
    repo_root: Path
    store_path: Path
    telemetry_path: Path
    max_age_seconds: int = 3600
    max_context_tokens: int = 4096
    backend: BackendConfig = Field(default_factory=BackendConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    compute: ComputeConfig = Field(default_factory=ComputeConfig)
    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    refinement: RefinementConfig = Field(default_factory=RefinementConfig)
