# Multi-Backend Proxy Routing

Use one Vaner proxy and select upstream model per request.

## 1) Configure named backends

```toml
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen2.5-coder:32b"
api_key_env = "OLLAMA_API_KEY"

[backends.openai]
base_url = "https://api.openai.com/v1"
model = "gpt-4o"
api_key_env = "OPENAI_API_KEY"

[backends.claude]
base_url = "https://api.anthropic.com/v1"
model = "claude-sonnet-4-20250514"
api_key_env = "ANTHROPIC_API_KEY"
```

## 2) Start proxy

```bash
vaner proxy --path . --host 127.0.0.1 --port 8471
```

## 3) Pick backend per request

```bash
curl -s http://127.0.0.1:8471/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Vaner-Backend: openai" \
  -d '{"messages":[{"role":"user","content":"summarize this module"}]}'
```

If `X-Vaner-Backend` is omitted, Vaner uses `[backend]`.
