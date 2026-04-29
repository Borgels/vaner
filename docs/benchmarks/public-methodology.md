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

Private training data, learned priors, raw prompts, judge transcripts, policy
weights, and internal failure traces remain outside the public repository.
