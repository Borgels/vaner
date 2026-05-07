# Context Preparation Architecture

Vaner is a context prediction and preparation engine. Retrieval is one
mechanism inside that engine, not the product abstraction.

The intended preparation spine is:

```text
ContextNeed
-> PreparationPolicy
-> ContextTools
-> ContextToolTrace
-> ContextPackage
```

The core should infer what kind of context the next step needs, choose a
bounded preparation policy, execute general context tools, preserve provenance,
record coverage and gaps, and assemble the smallest sufficient context package.

## Benchmark-Driven Development Guardrails

EnterpriseRAG and similar benchmarks should be used as diagnostic lenses, not
as product targets. A benchmark miss is useful when it reveals a general Vaner
context-preparation failure. Fixes should improve Vaner's reusable preparation
engine, not encode dataset-specific shortcuts.

For any benchmark-driven change, the PR should answer the questions below.

### 1. What Is The General Failure Mode?

Name the underlying product failure, not only the benchmark case.

Good examples:

- multi-source synthesis exceeded the raw context budget;
- scheduling evidence was discovered but not selected;
- source-class expansion missed canonical documents;
- aggregation lacked row-level provenance;
- conflict or absence evidence was not represented in the final package.

Bad examples:

- `qst_0437` needs these documents;
- `qst_0184` expects this Gmail path;
- EnterpriseRAG asks this exact question shape.

### 2. Why Is The Capability Non-Benchmark-Specific?

The change should apply to normal Vaner use cases outside the benchmark.

Examples:

- `aggregate_sources` should work for postmortems, customer feedback, research
  notes, incidents, meeting notes, and similar broad synthesis tasks.
- `prepare_scheduling_evidence` should work for invites, calendars, email
  threads, meeting notes, time windows, and timezone evidence generally.
- source-class hints should be a general candidate expansion mechanism, not a
  hidden shortcut to benchmark documents.

### 3. Which Product-Shaped Metrics Improved?

Do not report only raw benchmark recall if the product path is richer than raw
top-k selection.

Prefer metrics such as:

- raw selected document recall;
- aggregation input recall;
- aggregation provenance recall;
- scheduling evidence recall;
- final package support coverage;
- coverage and gap detection quality;
- tool trace correctness;
- provenance completeness.

For multi-source synthesis, raw selected documents may not be the main success
path. Aggregation input and provenance coverage may be the more relevant
measures.

### 4. Is Product Code Free Of Dataset-Only Language?

Product code should not contain:

- benchmark question IDs;
- expected benchmark document IDs;
- dataset-specific source names;
- hardcoded answer shapes for one benchmark;
- routing keyed to EnterpriseRAG-specific phrasing.

Dataset-specific logic belongs in tests, fixtures, harness code, or
diagnostics, not in Vaner's product engine.

### 5. Is Context-Specific Behavior Behind Explicit Policies Or Tools?

Context-specific behavior should be invoked by the selected preparation policy,
not hidden as always-on selector behavior.

Examples:

- scheduling ranking belongs behind the scheduling policy or tool path;
- aggregation belongs behind `multi_source_synthesis`;
- source-class and canonical ranking belong behind source-class or
  canonical-rank tools;
- conflict logic belongs behind `conflict_resolution`;
- absence logic belongs behind `absence_check`.

The selector should become less magical over time. The preparation trace should
explain why a behavior ran.

## Review Standard

A benchmark-driven PR is acceptable if it can clearly show:

```text
benchmark miss
-> general failure mode
-> reusable Vaner capability
-> product-shaped metric improvement
-> no dataset-specific product logic
-> explicit policy/tool path
```

A benchmark-driven PR is not acceptable if it mainly teaches Vaner a
benchmark-specific route, source, or answer pattern.
