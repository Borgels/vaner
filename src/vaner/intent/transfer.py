# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TransferConfig:
    enable_structural_transfer: bool = True
    enable_user_transfer: bool = True
    enable_community_transfer: bool = False
    transfer_weight_decay: float = 0.9


def bootstrap_transfer_priors(
    *,
    corpus_type: str,
    query_count: int,
    config: TransferConfig | None = None,
) -> dict[str, float]:
    config = config or TransferConfig()
    priors: dict[str, float] = {}
    if config.enable_structural_transfer:
        priors[f"structural:{corpus_type}"] = 1.0
    if config.enable_user_transfer:
        priors["user:history"] = 1.0
    if config.enable_community_transfer:
        priors["community:global"] = 1.0
    decay = config.transfer_weight_decay ** max(0, query_count // 25)
    return {key: value * decay for key, value in priors.items()}
