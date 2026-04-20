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


class GenerationConfig(BaseModel):
    use_llm: bool = False
    generation_model: str | None = None
    max_file_chars: int = 8000
    summary_max_tokens: int = 400
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
    max_age_seconds: int = 3600
    max_context_tokens: int = 4096
    backend: BackendConfig = Field(default_factory=BackendConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    compute: ComputeConfig = Field(default_factory=ComputeConfig)
    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)
