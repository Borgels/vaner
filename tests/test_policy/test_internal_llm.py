# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.policy.internal_llm import (
    CORE_POLICY,
    DRAFT_POLICY,
    EVIDENCE_SUMMARY_POLICY,
    JSON_CONTRACT_POLICY,
    PREDICTION_POLICY,
    internal_llm_policy,
)


def test_core_policy_is_contract_preserving_not_persona() -> None:
    assert "Preserve the requested output contract over style" in CORE_POLICY
    assert "paths, symbols, constants" in CORE_POLICY
    assert "assistant persona" not in CORE_POLICY.lower()


def test_json_policy_bans_common_parser_breakers() -> None:
    policy = internal_llm_policy(JSON_CONTRACT_POLICY)
    assert "valid JSON only" in policy
    assert "no markdown fences" in policy
    assert "no markdown fences, preamble, commentary, or trailing prose" in policy
    assert "not chain-of-thought" in policy


def test_task_overlays_are_short_for_local_models() -> None:
    for policy in [
        internal_llm_policy(EVIDENCE_SUMMARY_POLICY),
        internal_llm_policy(JSON_CONTRACT_POLICY, PREDICTION_POLICY),
        internal_llm_policy(DRAFT_POLICY),
    ]:
        assert len(policy.split()) <= 140
