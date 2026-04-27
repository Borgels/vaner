# SPDX-License-Identifier: Apache-2.0
"""Recommended-models registry (0.8.8 WS10.2).

The registry maps the local hardware's :class:`MemoryBudget` and the
user's selected work styles to a concrete model id (Ollama tag,
Hugging Face repo). It is **deterministically generated** by
``scripts/refresh_recommended_models.py`` against live model catalogs
(Ollama library, Hugging Face Hub) at release time — no hand-curated
seed list, no model names baked into source code.

Public API:

* :func:`load_registry` — read the JSON registry from disk; returns an
  empty registry with a warning when the file is absent (e.g. dev
  build that has not yet run the refresh script).
* :func:`pick_for` — given a memory budget and work styles, return the
  best-fitting model from the registry, or ``None`` when the registry
  is empty / no model fits the budget.

The data file (``data.json``) is gitignored. Releases carry the
generated registry as a build artefact; development checkouts that
have not run the refresh script return empty (the desktop wizard
falls back gracefully — see WS11.3 in the plan).
"""

from vaner.setup.recommended.loader import load_registry
from vaner.setup.recommended.payload import models_recommended_payload
from vaner.setup.recommended.resolver import alternatives_for, pick_for
from vaner.setup.recommended.schema import (
    RecommendedModel,
    Registry,
    RegistrySource,
)

__all__ = [
    "RecommendedModel",
    "Registry",
    "RegistrySource",
    "alternatives_for",
    "load_registry",
    "models_recommended_payload",
    "pick_for",
]
