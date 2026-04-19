# Docker Quickstart

Run daemon + proxy + context-only endpoints with one compose file.

```bash
REPO_ROOT=. docker compose up -d
```

Services:

- `vaner-daemon`: keeps cache warm in background
- `vaner-proxy`: `http://127.0.0.1:8471/v1/chat/completions`
- `vaner-context`: `http://127.0.0.1:8472/v1/context`

Stop:

```bash
docker compose down
```
