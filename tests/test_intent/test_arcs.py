from __future__ import annotations

from vaner.intent.arcs import ArcPredictionDescription, ConversationArcModel, derive_prompt_macro


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


def test_describe_next_returns_labelled_predictions():
    model = ConversationArcModel()
    for q in [
        "implement a parser",
        "add tests for parser",
        "test edge cases",
        "fix exception in parser",
    ]:
        model.observe(q)
    descriptions = model.describe_next("testing", top_k=3, recent_queries=["add tests for parser"])
    assert descriptions
    for d in descriptions:
        assert isinstance(d, ArcPredictionDescription)
        assert d.label
        assert d.description
        assert d.hypothesis_type in {"likely_next", "possible_branch", "long_tail"}
        assert d.specificity in {"concrete", "category", "anchor"}
        assert 0.0 <= d.confidence


def test_describe_next_hypothesis_type_tiers():
    # Cold-start with empty model → rank_next returns []; describe_next also returns []
    model = ConversationArcModel()
    assert model.describe_next("testing") == []


# ---------------------------------------------------------------------------
# WS1.b — label synthesis + specificity-via-anchor
# ---------------------------------------------------------------------------


def test_describe_next_label_echoes_raw_prompt_for_concrete():
    """When the prediction is concrete and we have a recent prompt, the label
    should echo the prompt's noun phrase rather than the bag-of-tokens macro."""
    model = ConversationArcModel()
    for q in [
        "implement a parser",
        "add tests for the parser module",
        "test edge cases",
        "fix exception in parser",
    ]:
        model.observe(q)
    descriptions = model.describe_next(
        "testing",
        top_k=3,
        recent_queries=["add tests for the parser module"],
    )
    assert descriptions
    # At least one concrete description should contain "parser" — the noun from
    # the raw query — and NOT the legacy bag-of-tokens phrase.
    concrete = [d for d in descriptions if d.specificity == "concrete"]
    assert concrete, "expected at least one concrete description"
    assert any("parser" in d.label.lower() for d in concrete)
    # Must not regress to the bag-of-tokens garbage label
    for d in concrete:
        assert "tests parser module" not in d.label.lower()


def test_describe_next_label_drops_leading_verbs():
    """Noun-phrase extraction should drop imperative-leading verbs so the label
    reads as a phrase about the predicted next category, not a rehash of what
    the user already did."""
    model = ConversationArcModel()
    for q in ["implement parser", "add tests parser", "review parser"]:
        model.observe(q)
    descriptions = model.describe_next("review", top_k=1, recent_queries=["add tests parser"])
    assert descriptions
    label = descriptions[0].label.lower()
    # The label should contain "parser" but NOT start with "add" (which was
    # the user's leading verb on the last prompt).
    assert "parser" in label
    assert not label.startswith("add")


def test_specificity_detects_file_like_anchors_as_concrete():
    """An anchor that looks like a file path/extension must produce
    specificity=concrete."""
    from vaner.intent.arcs import _specificity_for

    assert _specificity_for("src/engine.py", None) == "concrete"
    assert _specificity_for("cockpit/components/chrome.tsx", None) == "concrete"
    assert _specificity_for("Module::submodule", None) == "concrete"


def test_specificity_phase_anchors_are_not_concrete():
    """Pure phase labels (validation, planning, etc.) don't count as concrete."""
    from vaner.intent.arcs import _specificity_for

    assert _specificity_for("validation", None) == "category"
    assert _specificity_for("exploring", None) == "category"
    # Unknown non-path anchors fall into "anchor" tier
    assert _specificity_for("my-workflow", None) == "anchor"


def test_noun_phrase_shortens_long_prompts():
    from vaner.intent.arcs import _noun_phrase_from_query

    long_q = "Please can you implement a streaming JSON parser that handles deeply nested structures without blowing the call stack"
    snippet = _noun_phrase_from_query(long_q, max_len=40)
    assert len(snippet) <= 41  # max_len + ellipsis char
    # Politeness and leading verb stripped
    assert not snippet.lower().startswith("please")
    assert not snippet.lower().startswith("implement")


def test_noun_phrase_handles_empty_input():
    from vaner.intent.arcs import _noun_phrase_from_query

    assert _noun_phrase_from_query("") == ""
    assert _noun_phrase_from_query("   ") == ""
