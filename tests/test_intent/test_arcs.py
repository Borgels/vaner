from __future__ import annotations

from vaner.intent.arcs import ConversationArcModel, derive_prompt_macro


def test_arc_model_observe_and_predict_next():
    model = ConversationArcModel()
    model.observe("implement a parser")
    model.observe("add tests for parser")
    model.observe("test edge cases")
    model.observe("fix exception in parser")

    predicted = dict(model.predict_next("testing", top_k=3, recent_queries=["add tests", "test edge cases"]))

    assert predicted
    assert "debugging" in predicted


def test_arc_model_rebuild_from_history():
    model = ConversationArcModel()
    model.rebuild_from_history(
        [
            "implement feature X",
            "add tests for feature X",
            "fix exception in feature X",
        ]
    )

    predicted = dict(model.predict_next("testing", top_k=3, recent_queries=["add tests for feature X"]))
    assert "debugging" in predicted


def test_prompt_macro_mining_and_phase_summary():
    model = ConversationArcModel()
    queries = [
        "run a code review on this patch",
        "fix the review comments in engine",
        "run a code review on this patch",
        "run tests for the engine changes",
    ]
    model.rebuild_from_history(queries)

    macros = model.mine_prompt_macros(min_support=2)
    assert macros
    assert macros[0]["macro_key"].startswith("run code review")

    summary = model.summarize_workflow_phase(queries[-3:])
    assert summary.phase in {"validation", "stabilizing", "building"}
    assert summary.recent_macro


def test_derive_prompt_macro_filters_noise():
    macro = derive_prompt_macro("Please run a code review on this patch for me")
    assert macro == "run code review patch"
