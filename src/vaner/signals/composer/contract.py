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


class ComposerAdapterCapabilities(BaseModel):
    """What an adapter can truthfully emit.

    The daemon uses ``emits`` to validate each ``DraftIntentSnapshot``
    on ingest. An L0 adapter that declares ``emits=("submitted",)``
    cannot send a payload with ``lifecycle_state="actionable"``.
    """

    level: CapabilityLevel
    emits: tuple[LifecycleState, ...] = Field(
        description="Lifecycle states this adapter can truthfully produce.",
    )
    host_app: str = Field(
        description="Stable identifier for the host (e.g. 'claude-code', 'cursor').",
    )
    host_kind: HostKind


class DraftIntentSnapshot(BaseModel):
    """A single composer-lifecycle observation from an adapter."""

    session_id: str = Field(
        description="Stable id for the composer session. Used to correlate snapshots and to invalidate predictions on cancel/abandon.",
    )
    snapshot_id: str = Field(
        description="Unique id for this snapshot. Adapters MUST generate a fresh id per emission.",
    )
    timestamp: str = Field(description="ISO-8601 timestamp of the observation.")
    lifecycle_state: LifecycleState
    text_hash: str = Field(
        description="sha256 hex digest of the draft text. The raw text is never transmitted in 0.8.7.",
    )
    length_chars: int = Field(ge=0)
    capabilities: ComposerAdapterCapabilities

    workspace_id: str | None = None
    conversation_id: str | None = None
    field_role: FieldRole | None = None
    inferred_intent_label: str | None = None
    inferred_intent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ComposerEvent(BaseModel):
    """Envelope written into ``SignalEvent.payload`` for ``kind=composer_lifecycle``.

    Wrapping the snapshot in an envelope leaves room for adapter-level
    fields that should not live on the snapshot itself (e.g. transport
    metadata, batch markers) without breaking the snapshot contract.
    """

    snapshot: DraftIntentSnapshot
