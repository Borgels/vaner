# Cloud Agent / Context-Only HTTP

Use this when your cloud agent manages its own LLM provider but needs Vaner context.

## 1) Start Vaner in context-only mode

```bash
vaner init --profile minimal --path .
vaner daemon start --no-once --path .
vaner proxy --context-only --path . --host 127.0.0.1 --port 8472
```

## 2) Fetch context

```bash
curl -s http://127.0.0.1:8472/v1/context \
  -H "Content-Type: application/json" \
  -d '{"prompt":"review auth middleware", "top_n": 8}'
```

Use the returned `context` field in your agent prompt, then send the enriched prompt to your own backend.
