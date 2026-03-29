# Vaner Editor Integration

Vaner injects pre-computed repository context into your LLM prompts automatically via a local proxy.

## How it works

```
Cursor / VS Code
      ↓  (API request)
localhost:11435  ←  VanerProxy
      ↓  (injects top-5 artifact summaries as system message)
localhost:11434  ←  Ollama (or any OpenAI-compatible endpoint)
```

When you send a prompt from your editor, Vaner intercepts it, prepends the most relevant cached artifact summaries, and forwards the enriched request to your model. Your model sees what files you've been working on before it answers.

## Setup

### 1. Start the daemon

```bash
cd ~/repos/YourProject
python vaner.py init       # first time only
python vaner.py daemon start
python vaner.py status     # verify proxy is running on :11435
```

### 2. Configure your editor

**Cursor**
1. Settings → Features → OpenAI API Key section
2. Set **Override OpenAI Base URL** to `http://localhost:11435`

**VS Code — Continue extension**
Edit `~/.continue/config.json`:
```json
{
  "models": [{
    "title": "Ollama (via Vaner)",
    "provider": "ollama",
    "model": "qwen2.5-coder:32b",
    "apiBase": "http://localhost:11435"
  }]
}
```

**Any OpenAI-compatible client**
Set the base URL to `http://localhost:11435`.

### 3. Verify

```bash
# Check proxy is up
curl -s http://localhost:11435/health

# Check context is being injected (look for "Vaner context" in system message)
curl -s -X POST http://localhost:11435/api/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hello"}]}' | head -c 200
```

## Troubleshooting

| Problem | Fix |
|---|---|
| Proxy not running | `python vaner.py daemon start` |
| No context injected | `python vaner.py inspect` — check artifacts exist; run `python vaner.py analyze` to populate cache |
| Port conflict | Change `proxy_port` in `.vaner/config.json` |
| Upstream unreachable | Verify Ollama is running: `ollama list` |

## Configuration

Edit `.vaner/config.json`:
```json
{
  "proxy_enabled": true,
  "proxy_port": 11435,
  "proxy_upstream": "http://localhost:11434"
}
```
