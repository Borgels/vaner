# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class PrivacyConfig(BaseModel):
    allowed_paths: list[str] = Field(default_factory=lambda: ["."], description="Repository-relative paths Vaner may index.")
    excluded_patterns: list[str] = Field(
        default_factory=lambda: ["*.env", "*.key", "*.pem", "credentials*", "secrets*"],
        description="Glob patterns excluded from indexing.",
    )
    redact_patterns: list[str] = Field(default_factory=list, description="Patterns redacted from stored snippets.")
    telemetry: str = Field(default="local", description="Telemetry mode. Local keeps telemetry on disk only.")
    exclude_private: bool = Field(default=False, description="Whether to skip private files detected by policy heuristics.")


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

    name: str = Field(default="custom", description="Human-readable backend profile name.")
    base_url: str = Field(default="", description="OpenAI-compatible base URL for user-facing completions.")
    model: str = Field(default="", description="User-facing chat model name.")
    api_key_env: str = Field(default="OPENAI_API_KEY", description="Environment variable that stores backend API key.")
    prefer_local: bool = Field(default=True, description="Prefer local backend routes when both local and remote exist.")
    fallback_enabled: bool = Field(default=False, description="Enable fallback backend when primary fails.")
    fallback_base_url: str | None = Field(default=None, description="Fallback OpenAI-compatible base URL.")
    fallback_model: str | None = Field(default=None, description="Fallback model name.")
    fallback_api_key_env: str = Field(default="OPENAI_API_KEY", description="Environment variable for fallback API key.")
    remote_budget_per_hour: int = Field(default=60, description="Soft hourly budget guard for remote inference.")


class GenerationConfig(BaseModel):
    use_llm: bool = Field(default=False, description="Enable LLM-generated file summaries during precompute.")
    generation_model: str | None = Field(default=None, description="Override model for generation summaries.")
    max_file_chars: int = Field(default=8000, description="Maximum source characters sent for one summary.")
    summary_max_tokens: int = Field(default=400, description="Token cap for generated summary text.")
    max_concurrent_generations: int = Field(default=4, description="Max parallel summary generations.")
    max_generations_per_cycle: int = Field(default=4000, description="Max summary generations per precompute cycle.")


class ProxyConfig(BaseModel):
    proxy_token: str | None = Field(default=None, description="Optional auth token for non-loopback proxy exposure.")
    max_requests_per_minute: int = Field(default=120, description="Rate limit for proxy ingress.")
    ssl_certfile: str | None = Field(default=None, description="TLS certificate file path for proxy server.")
    ssl_keyfile: str | None = Field(default=None, description="TLS key file path for proxy server.")


class GatewayConfig(BaseModel):
    passthrough_enabled: bool = Field(default=False, description="Enable gateway passthrough mode.")
    routes: dict[str, str] = Field(default_factory=dict, description="Model-prefix to provider base URL route map.")
    annotate_response_trailer: bool = Field(default=False, description="Append gateway annotation trailer to responses.")
    annotate_system_note: Literal["off", "min", "full"] = Field(
        default="off",
        description="Inject system note annotations into forwarded requests.",
    )
    shadow_rate: float = Field(default=0.0, description="Sampling rate for shadow traffic comparisons.")


class MCPConfig(BaseModel):
    transport: Literal["stdio", "sse"] = Field(default="stdio", description="MCP server transport mode.")
    http_host: str = Field(default="127.0.0.1", description="Host for SSE MCP mode.")
    http_port: int = Field(default=8472, description="Port for SSE MCP mode.")


class IntentConfig(BaseModel):
    enabled: bool = Field(default=True, description="Enable intent scoring and scenario prioritization.")
    include_global_skills: bool = Field(default=False, description="Allow scanning skills outside repository roots.")
    skill_roots: list[str] = Field(
        default_factory=lambda: [".cursor/skills", ".claude/skills", "skills"],
        description="Repository-relative skill directories to scan for SKILL.md files.",
    )
    skills_loop_enabled: bool = Field(default=True, description="Enable closed-loop skill attribution feedback.")
    max_feedback_events_per_cycle: int = Field(default=200, description="Max feedback events consumed each cycle.")


class ComputeConfig(BaseModel):
    device: str = Field(default="auto", description="Primary compute device for precompute (`auto`, `cpu`, `cuda`, `mps`).")
    cpu_fraction: float = Field(default=0.2, description="CPU share reserved for background computation.")
    gpu_memory_fraction: float = Field(default=0.5, description="GPU memory share reserved for background computation.")
    idle_only: bool = Field(default=True, description="Run precompute only when host utilization is below thresholds.")
    idle_cpu_threshold: float = Field(default=0.6, description="CPU utilization cap for `idle_only` gating.")
    idle_gpu_threshold: float = Field(default=0.7, description="GPU utilization cap for `idle_only` gating.")
    embedding_device: str | None = Field(default=None, description="Device for embedding model (`cpu`, `cuda`, `mps`, `auto`).")
    exploration_concurrency: int = Field(default=4, description="Max concurrent exploration tasks.")
    max_parallel_precompute: int = Field(default=1, description="Max parallel precompute workers.")
    max_cycle_seconds: int = Field(default=300, description="Hard wall-clock cap for one precompute cycle.")
    """Hard wall-clock cap for a single precompute cycle.

    Prevents the ponder loop from running away if a backend stalls or the
    frontier grows pathologically. ``0`` disables the cap (unbounded); any
    positive value bounds the cycle to that many seconds. Vaner resumes on
    the next cycle, so bounding here is safe.
    """

    max_session_minutes: int | None = Field(default=None, description="Optional total runtime cap for one daemon session.")
    """Optional cumulative cap for a continuous ``vaner daemon`` session.

    ``None`` (default) means unbounded — the daemon keeps running until
    stopped. When set, the daemon exits cleanly once the wall-clock since
    ``daemon start`` exceeds this many minutes. Users who want Vaner to
    never ponder for more than, say, 30 minutes can set ``30`` here.
    """


class ExplorationConfig(BaseModel):
    """Controls how aggressively Vaner explores the scenario space.

    The underlying engine is always a continuous best-first search; these
    fields are the dials that shape how deep and broad it goes.

    Aggressiveness presets are convenience constructors — use the class methods
    ``conservative()``, ``normal()``, ``aggressive()``, ``maximum()`` rather
    than setting fields by hand.

    LLM Exploration
    ---------------
    Set ``endpoint`` to point at a local Ollama instance or any
    OpenAI-compatible server (vLLM, LM Studio, remote API).  Leave empty to
    auto-detect on localhost.  ``model`` selects the model; if
    empty Vaner picks the first available model reported by the endpoint.
    ``backend`` can be ``"auto"`` (probe endpoint), ``"ollama"``,
    or ``"openai"`` (OpenAI-compatible / vLLM).

    Embeddings
    ----------
    ``embedding_model`` names a sentence-transformers model used to embed
    prompts and cache entries for semantic matching. Leave ``embedding_model``
    empty to disable embedding-based cache matching.
    """

    max_exploration_depth: int = Field(default=3, description="Maximum LLM branch depth from initial seed.")
    """Maximum LLM branch depth from the original graph seed."""

    frontier_max_size: int = Field(default=500, description="Maximum queued scenarios in the frontier.")
    """Maximum number of pending scenarios in the frontier at once."""

    min_priority: float = Field(default=0.10, description="Minimum effective priority required to keep a scenario.")
    """Scenarios with effective priority below this threshold are rejected."""

    dedup_threshold: float = Field(default=0.70, description="Jaccard overlap threshold used for scenario deduplication.")
    """Jaccard overlap above which two scenarios are considered duplicates."""

    saturation_coverage: float = Field(default=0.90, description="Coverage ratio considered saturated for exploration.")
    """Fraction of known files that, once covered, signals saturation."""

    llm_gate: Literal["none", "non_trivial", "all"] = Field(default="non_trivial", description="Policy controlling LLM calls.")
    """Which scenarios are sent to the LLM.
    - "none"        — graph-walk only, no LLM calls
    - "non_trivial" — LLM for depth > 0 or non-graph sources
    - "all"         — LLM for every scenario
    """

    # ------------------------------------------------------------------
    # Exploration LLM endpoint (separate from the user-facing backend)
    # ------------------------------------------------------------------

    model: str = Field(default="", description="Model for exploration LLM. Empty picks first discovered model.")
    """Model name for the exploration LLM.
    Ollama tag (e.g. ``"qwen2.5-coder:32b"``) or OpenAI-compatible model ID
    (e.g. ``"Qwen/Qwen3.5-35B-A3B-FP8"`` for vLLM).  Empty = pick first
    available model from the detected endpoint.
    """

    endpoint: str = Field(default="", description="Base URL for exploration endpoint. Empty auto-probes localhost.")
    """Base URL for the exploration LLM endpoint.

    Examples:

    - Ollama:        ``"http://127.0.0.1:11434"``
    - vLLM / local:  ``"http://127.0.0.1:8000/v1"``
    - Remote vLLM:   ``"http://your-server:8000/v1"``
    - OpenAI API:    ``"https://api.openai.com/v1"``

    Empty string = auto-probe localhost (Ollama on 11434, then vLLM on 8000).
    """

    backend: Literal["auto", "ollama", "openai"] = Field(default="auto", description="Client protocol for exploration endpoint.")
    """Which client protocol to use.
    - ``"auto"``   — probe endpoint, infer from API shape
    - ``"ollama"`` — force Ollama ``/api/generate`` protocol
    - ``"openai"`` — force OpenAI ``/v1/chat/completions`` protocol (for vLLM)
    """

    api_key: str = Field(default="", description="API key for exploration endpoint (optional for local runtimes).")
    """API key for the exploration endpoint.  Use ``"EMPTY"`` for local
    vLLM which requires a non-empty but unauthenticated token.  Leave empty
    to read from the ``VANER_EXPLORATION_API_KEY`` environment variable or
    fall back to ``"EMPTY"`` for local endpoints.
    """

    # ------------------------------------------------------------------
    # Embedding model for semantic cache matching
    # ------------------------------------------------------------------

    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="sentence-transformers model used for semantic cache matching.",
    )
    """sentence-transformers model for semantic prompt embeddings used in
    cache matching.  Set to empty string to disable embedding-based matching
    and fall back to token-Jaccard only.
    """

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


class VanerConfig(BaseModel):
    repo_root: Path
    store_path: Path
    telemetry_path: Path
    max_age_seconds: int = Field(default=3600, description="Max age (seconds) for reusable context artefacts.")
    max_context_tokens: int = Field(default=4096, description="Maximum token budget for injected context.")
    backend: BackendConfig = Field(default_factory=BackendConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    compute: ComputeConfig = Field(default_factory=ComputeConfig)
    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)
