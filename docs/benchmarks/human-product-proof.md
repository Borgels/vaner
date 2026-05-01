# Prepared Work Human Product-Proof Test

Use this scripted check before making stronger public claims that Prepared Work
is understandable, trusted, and useful in the app. It is not a broad usability
study, and it does not replace automated E2E tests or benchmark runs.

For a real Codex CLI MCP client smoke, run:

```bash
uv run python scripts/e2e/codex_prepared_work_smoke.py
```

Add `--run-agent` only in an environment with Codex model credentials.

## Scope

Run 3 to 5 guided sessions using public fixture repos or public tasks only.
Include at least one developer-like user. If the tested flow is not code-only,
include at least one non-core user. A power user familiar with AI tools is
optional.

## Script

1. Start from a clean public fixture repo or public task.
2. Show Vaner with 2 to 5 Prepared Work cards.
3. Ask what each card appears to offer.
4. Ask which card the participant would inspect first and why.
5. Ask them to inspect one card.
6. Ask what evidence supports the card.
7. Ask whether they would export or adopt it.
8. Ask what they expect export or adopt to do before clicking.
9. Let them click export or adopt.
10. Verify whether their expectation matched the result.
11. Ask them to dismiss or give feedback on one bad or irrelevant card.
12. Ask what felt unsafe, confusing, stale, or too technical.

Do not explain internal terms unless the participant gets stuck. The point is to
test the product surface, not the facilitator's ability to teach it.

## Measurements

Record task completion, comprehension, trust, safety expectation, actionability,
confusion points, false-positive pain, and time to first useful action.

## Gates

Before stronger Prepared Work product claims:

- at least 80% of participants can explain what a Prepared Work card is
- at least 80% can inspect evidence without help
- at least 80% understand that export/adopt does not silently mutate repo files
- no critical trust failures occur
- no participant treats diagnostic or internal fields as product-facing concepts
- serious confusion points are fixed or documented as known limitations

## Report Template

```markdown
# Prepared Work Human Product-Proof Report

## Participants

## Fixture/Task

## Cards Shown

## Actions Attempted

## Completion Results

## Confusion Points

## Trust/Safety Issues

## Wording Fixes

## Verdict
```
