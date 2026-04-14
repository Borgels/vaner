# OpenAI-Compatible Proxy

```bash
vaner proxy --host 127.0.0.1 --port 8471
```

Point your OpenAI-compatible client to `http://127.0.0.1:8471/v1`.

Example backend config:

```toml
[backend]
name = "openai"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"
```

The proxy prepends a system context message produced from Vaner's selected artefacts.
