# SPDX-License-Identifier: Apache-2.0
"""ComposerAdapter contract — 0.8.7 WS2 (Live Intent Capture, Phase 1).

Vaner consumes explicit composer/prompt lifecycle events from supported
clients via a ``ComposerAdapter``. This module defines the wire contract
for those events. Pydantic models here are the source of truth; the
JSON Schema artifact at ``docs/specs/composer-adapter.schema.json`` is
generated from them by ``vaner.signals.composer.schema:main``.

Privacy contract:

- The draft text itself NEVER crosses this boundary. Adapters send a
  ``text_hash`` (sha256 of the draft) and a length, never the raw text
  or a preview. ``text_preview`` / ``local_text_ref`` from the broader
  product spec are deferred until a redaction module exists.
- Local-only by default. Cloud processing is not enabled in 0.8.7.
- Per-client opt-in inherits the host's plugin/extension enable/disable
  surface in 0.8.7; first-class per-client opt-in scaffolding lands in
  v0.8.8.

Truthful state:

- An adapter MUST only emit ``lifecycle_state`` values it can actually
  observe. A Level-0 (submit-time) adapter emits ``"submitted"`` and
  nothing else. A Level-1 adapter that genuinely sees pre-submit draft
  changes may emit the volatile states (``observing`` … ``actionable``).
  Fabricating higher states corrupts the engine's scoring downstream.
- ``ComposerAdapterCapabilities.emits`` declares what an adapter can
  truthfully produce. The daemon validates each event against this
  capability declaration on ingest.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LifecycleState = Literal[
    "observing",
    "tentative",
    "stabilizing",
    "actionable",
    "submitted",
    "cleared",
    "abandoned",
]

CapabilityLevel = Literal["L0", "L1", "L2", "L3"]

HostKind = Literal[
    "ai_chat_client",
    "ide",
    "editor",
    "support_console",
    "writing_tool",
    "terminal_ai_client",
    "enterprise_app",
]

FieldRole = Literal[
    "chat_composer",
    "agent_prompt",
    "email_body",
    "doc_editor",
    "support_reply",
    "task_note",
]


# sha256 hex digest pattern. The text_hash field MUST match this so
# the daemon's error envelope (which echoes invalid input) can never
# reflect raw draft text mistakenly placed in the text_hash slot.
_SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"

# ISO-8601 datetime pattern (date + time + Z or +HH:MM offset). Pinned
# so adapters can't send free-form strings that downstream telemetry
# range queries would silently misbehave on.
_ISO8601_TS_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
)


class ComposerAdapterCapabilities(BaseModel):
    """What an adapter can truthfully emit.

    The daemon uses ``emits`` to validate each ``DraftIntentSnapshot``
    on ingest. An L0 adapter that declares ``emits=("submitted",)``
    cannot send a payload with ``lifecycle_state="actionable"``.
    """

    level: CapabilityLevel
    emits: tuple[LifecycleState, ...] = Field(
        min_length=1,
        description="Lifecycle states this adapter can truthfully produce. MUST be non-empty.",
    )
    host_app: str = Field(
        min_length=1,
        max_length=128,
        description="Stable identifier for the host (e.g. 'claude-code', 'cursor').",
    )
    host_kind: HostKind


class DraftIntentSnapshot(BaseModel):
    """A single composer-lifecycle observation from an adapter."""

    session_id: str = Field(
        min_length=1,
        max_length=256,
        description="Stable id for the composer session. Used to correlate snapshots and to invalidate predictions on cancel/abandon.",
    )
    snapshot_id: str = Field(
        min_length=1,
        max_length=128,
        description="Unique id for this snapshot. Adapters MUST generate a fresh id per emission.",
    )
    timestamp: str = Field(
        pattern=_ISO8601_TS_PATTERN,
        description="ISO-8601 timestamp of the observation (e.g. '2026-04-25T20:14:45Z').",
    )
    lifecycle_state: LifecycleState
    text_hash: str = Field(
        pattern=_SHA256_HEX_PATTERN,
        description="sha256 hex digest of the draft text (lowercase, 64 chars). The raw text is never transmitted in 0.8.7.",
    )
    length_chars: int = Field(ge=0)
    capabilities: ComposerAdapterCapabilities

    workspace_id: str | None = Field(default=None, max_length=2048)
    conversation_id: str | None = Field(default=None, max_length=256)
    field_role: FieldRole | None = None
    inferred_intent_label: str | None = Field(default=None, max_length=256)
    inferred_intent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
