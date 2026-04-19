# Multi-Agent Cursor (MCP)

Use this when Cursor runs multiple agents in parallel and each agent should fetch Vaner context independently.

## 1) Start Vaner MCP

```bash
vaner init --profile advanced --path .
vaner daemon start --no-once --path .
vaner mcp --path .
```

## 2) Add MCP server to Cursor

```json
{
  "mcpServers": {
    "vaner": {
      "command": "vaner",
      "args": ["mcp", "--path", "."]
    }
  }
}
```

## 3) Agent usage

Each agent calls `get_context(prompt)` before it queries its own model backend.
No backend routing is required in Vaner for this mode.
