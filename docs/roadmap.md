# Vaner.ai Roadmap

## Focus

Build a local-first predictive context middleware that proves one thing first:

**Vaner selects materially better context than manual prompting.**

## Near-term Phases

1. **Foundation:** licensing, repo hygiene, core package layout, clean architecture
2. **Preparation Engine:** collect signals and generate context artefacts in background
3. **Broker:** select and compress context at prompt time under strict token budget
4. **Thin Proxy:** OpenAI-compatible enrichment and forwarding
5. **Public Release:** docs, examples, packaging

## v1 Success Criteria

- Works on one developer machine and one repository
- Produces inspectable context decisions
- Improves relevance on repo-specific questions
- Maintains local-first and no-content-logging guarantees
