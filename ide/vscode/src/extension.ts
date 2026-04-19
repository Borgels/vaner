import * as vscode from "vscode";

type DecisionEvent = {
  id: string;
  cache_tier: string;
  token_used: number;
  selection_count: number;
};

export function activate(context: vscode.ExtensionContext): void {
  const state: {
    panel: vscode.WebviewPanel | null;
    status: vscode.StatusBarItem;
    streamAbort: AbortController | null;
    lastDecision: DecisionEvent | null;
  } = {
    panel: null,
    status: vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100),
    streamAbort: null,
    lastDecision: null,
  };

  state.status.text = "Vaner • starting";
  state.status.command = "vaner.openCockpit";
  state.status.tooltip = "Open Vaner cockpit";
  state.status.show();

  const openCockpit = vscode.commands.registerCommand("vaner.openCockpit", () => {
    if (state.panel) {
      state.panel.reveal(vscode.ViewColumn.Beside);
      return;
    }
    state.panel = vscode.window.createWebviewPanel(
      "vanerCockpit",
      "Vaner Cockpit",
      vscode.ViewColumn.Beside,
      { enableScripts: true }
    );
    state.panel.webview.html = renderCockpitHtml(proxyBaseUrl());
    state.panel.onDidDispose(() => {
      state.panel = null;
    });
  });

  const configureOpenAIBaseUrl = vscode.commands.registerCommand(
    "vaner.configureOpenAIBaseUrl",
    async () => {
      const value = await vscode.window.showInputBox({
        prompt: "OPENAI_BASE_URL",
        value: `${proxyBaseUrl().replace(/\/$/, "")}/v1`,
      });
      if (!value) {
        return;
      }
      const terminal = vscode.window.createTerminal("Vaner Configure");
      terminal.show();
      terminal.sendText(`export OPENAI_BASE_URL="${value}"`);
      vscode.window.showInformationMessage("OPENAI_BASE_URL exported in terminal session.");
    }
  );

  context.subscriptions.push(openCockpit, configureOpenAIBaseUrl, state.status);

  void startDecisionStream(state);
}

export function deactivate(): void {}

function proxyBaseUrl(): string {
  const cfg = vscode.workspace.getConfiguration("vaner");
  return cfg.get<string>("proxyUrl", "http://127.0.0.1:8471");
}

async function startDecisionStream(state: {
  panel: vscode.WebviewPanel | null;
  status: vscode.StatusBarItem;
  streamAbort: AbortController | null;
  lastDecision: DecisionEvent | null;
}): Promise<void> {
  state.streamAbort?.abort();
  state.streamAbort = new AbortController();
  const streamUrl = `${proxyBaseUrl().replace(/\/$/, "")}/decisions/stream`;
  try {
    const response = await fetch(streamUrl, {
      headers: { Accept: "text/event-stream" },
      signal: state.streamAbort.signal,
    });
    if (!response.ok || !response.body) {
      state.status.text = `Vaner • offline (${response.status})`;
      return;
    }
    state.status.text = "Vaner • connected";
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";
      for (const chunk of chunks) {
        const line = chunk.trim();
        if (!line.startsWith("data: ")) {
          continue;
        }
        const payload = line.slice(6);
        const event = JSON.parse(payload) as DecisionEvent;
        state.lastDecision = event;
        state.status.text = `Vaner • ${event.cache_tier} • ${event.token_used} tok`;
        state.panel?.webview.postMessage({ type: "decision", payload: event });
      }
    }
  } catch {
    state.status.text = "Vaner • offline";
  } finally {
    setTimeout(() => {
      void startDecisionStream(state);
    }, 3000);
  }
}

function renderCockpitHtml(proxyUrl: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Vaner Cockpit</title>
  <style>
    body { font-family: var(--vscode-font-family); padding: 12px; color: var(--vscode-foreground); }
    .muted { opacity: 0.75; }
    .row { margin: 8px 0; }
    pre { background: var(--vscode-editor-background); padding: 8px; border-radius: 4px; overflow-x: auto; }
    button { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: 0; padding: 6px 10px; border-radius: 4px; cursor: pointer; }
  </style>
</head>
<body>
  <h2>Vaner Cockpit</h2>
  <div class="row muted">Proxy: ${proxyUrl}</div>
  <div class="row">
    <button id="refreshDevices">Refresh compute devices</button>
  </div>
  <div class="row"><strong>Last decision</strong></div>
  <pre id="decision">{}</pre>
  <div class="row"><strong>Compute devices</strong></div>
  <pre id="devices">loading…</pre>
  <script>
    const vscode = acquireVsCodeApi();
    const decisionEl = document.getElementById("decision");
    const devicesEl = document.getElementById("devices");
    window.addEventListener("message", (event) => {
      if (event.data?.type === "decision") {
        decisionEl.textContent = JSON.stringify(event.data.payload, null, 2);
      }
    });
    async function refreshDevices() {
      try {
        const res = await fetch("${proxyUrl.replace(/\/$/, "")}/compute/devices");
        const data = await res.json();
        devicesEl.textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        devicesEl.textContent = "failed to fetch /compute/devices";
      }
    }
    document.getElementById("refreshDevices").addEventListener("click", refreshDevices);
    refreshDevices();
  </script>
</body>
</html>`;
}
