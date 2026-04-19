# Vaner VS Code/Cursor Extension

Local cockpit extension for Vaner.

## Features

- Status bar indicator that tracks live decision stream from `GET /decisions/stream`.
- Command palette actions:
  - `Vaner: Open Cockpit`
  - `Vaner: Configure OPENAI_BASE_URL`
- Side panel webview showing:
  - latest decision payload
  - detected compute devices from `GET /compute/devices`

## Development

```bash
cd ide/vscode
npm install
npm run build
```

Then open this folder in VS Code and press `F5`.

## Publishing

- VS Code Marketplace (`vsce`) and Open VSX (`ovsx`) are both supported.
- Provide `VSCE_PAT` and `OVSX_PAT` to the publish workflow.
