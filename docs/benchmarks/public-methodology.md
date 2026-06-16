# Public Benchmark Methodology

Vaner benchmark reports compare three ways of answering the same public-corpus
task:

- Naked: the answer model receives only the user task.
- Naive RAG: the answer model receives top-k lexical or embedding retrieval
  context from the same public corpus.
- Vaner: the answer model receives Vaner's prepared context.

The public report focuses on product-level outcomes: answer quality, relevant
path coverage, latency, estimated or provider-reported cost, and archetype-level
strengths and weaknesses. Public reports may name public corpora, public repos,
models, and aggregate metrics.

Benchmark-driven product work must follow the context-preparation guardrails in
[`docs/context-preparation.md`](../context-preparation.md): benchmarks are
diagnostic lenses, and fixes should name the general failure mode, improve a
reusable Vaner capability, report product-shaped metrics, avoid dataset-only
product logic, and run context-specific behavior through explicit policies or
tools.

Private training data, learned priors, raw prompts, judge transcripts, policy
weights, and internal failure traces remain outside the public repository.
