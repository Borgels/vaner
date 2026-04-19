# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.governor import PredictionGovernor


def test_governor_background_yields_during_user_request() -> None:
    governor = PredictionGovernor(mode=PredictionGovernor.Mode.BACKGROUND)
    assert governor.should_continue() is True
    governor.notify_user_request_start()
    assert governor.should_continue() is False
    governor.notify_user_request_end()
    assert governor.should_continue() is True


def test_governor_dedicated_runs_until_stopped() -> None:
    governor = PredictionGovernor(mode=PredictionGovernor.Mode.DEDICATED)
    assert governor.should_continue() is True
    governor.notify_user_request_start()
    assert governor.should_continue() is True
    governor.stop()
    assert governor.should_continue() is False


def test_governor_budget_decrements() -> None:
    governor = PredictionGovernor(mode=PredictionGovernor.Mode.BUDGET, budget_units=2)
    assert governor.remaining == 2
    assert governor.should_continue() is True
    assert governor.iteration_done(1) is True
    assert governor.remaining == 1
    assert governor.iteration_done(1) is True
    assert governor.remaining == 0
    assert governor.should_continue() is False
