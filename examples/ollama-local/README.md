# Ollama Local Backend

Set `.vaner/config.toml` backend values to your local OpenAI-compatible endpoint.

```toml
[backend]
name = "ollama"
base_url = "http://127.0.0.1:11434/v1"
model = "llama3.1"
api_key_env = "OLLAMA_API_KEY"
```

Run queries through Vaner after starting the daemon:

```bash
vaner daemon start
vaner query "summarize auth flow"
```
