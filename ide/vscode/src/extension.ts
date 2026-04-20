import * as vscode from "vscode";

type DecisionEvent = {
  id: string;
  kind: string;
  score: number;
  freshness: string;
  entities: string[];
  summary?: {
    fresh: number;
    recent: number;
    stale: number;
    total: number;
  };
  top_scenarios?: Array<{
    id: string;
    kind: string;
    score: number;
    freshness: string;
  }>;
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
    state.panel.webview.onDidReceiveMessage(async (message) => {
      const base = proxyBaseUrl().replace(/\/$/, "");
      if (message?.type === "refresh") {
        await pushScenariosToPanel(state.panel);
        return;
      }
      if (message?.type === "expand" && typeof message.id === "string") {
        await fetch(`${base}/scenarios/${encodeURIComponent(message.id)}/expand`, { method: "POST" });
        await pushScenariosToPanel(state.panel);
        return;
      }
      if (message?.type === "outcome" && typeof message.id === "string" && typeof message.result === "string") {
        await fetch(`${base}/scenarios/${encodeURIComponent(message.id)}/outcome`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ result: message.result }),
        });
        await pushScenariosToPanel(state.panel);
      }
    });
    void pushScenariosToPanel(state.panel);
    state.panel.onDidDispose(() => {
      state.panel = null;
    });
  });

  const configureMcp = vscode.commands.registerCommand("vaner.configureMcp", async () => {
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!folder) {
      vscode.window.showErrorMessage("Open a workspace folder first.");
      return;
    }
    const mcpPath = vscode.Uri.file(`${folder}/.cursor/mcp.json`);
    const payload = {
      mcpServers: {
        vaner: {
          command: "vaner",
          args: ["mcp", "--path", "."],
        },
      },
    };
    await vscode.workspace.fs.createDirectory(vscode.Uri.file(`${folder}/.cursor`));
    await vscode.workspace.fs.writeFile(mcpPath, Buffer.from(`${JSON.stringify(payload, null, 2)}\n`, "utf8"));
    vscode.window.showInformationMessage("Configured .cursor/mcp.json for Vaner.");
  });

  const enableGatewayCapability = vscode.commands.registerCommand(
    "vaner.enableGatewayCapability",
    async () => {
      const value = await vscode.window.showInputBox({
        prompt: "OPENAI_BASE_URL",
        value: "http://127.0.0.1:8471/v1",
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

  context.subscriptions.push(openCockpit, configureMcp, enableGatewayCapability, state.status);

  void startDecisionStream(state);
}

export function deactivate(): void {}

function proxyBaseUrl(): string {
  const cfg = vscode.workspace.getConfiguration("vaner");
  return cfg.get<string>("cockpitUrl", "http://127.0.0.1:8473");
}

async function startDecisionStream(state: {
  panel: vscode.WebviewPanel | null;
  status: vscode.StatusBarItem;
  streamAbort: AbortController | null;
  lastDecision: DecisionEvent | null;
}): Promise<void> {
  state.streamAbort?.abort();
  state.streamAbort = new AbortController();
  const streamUrl = `${proxyBaseUrl().replace(/\/$/, "")}/scenarios/stream`;
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
        if (event.summary) {
          state.status.text = `Vaner • ${event.summary.fresh}/${event.summary.total} fresh`;
        } else {
          state.status.text = `Vaner • ${event.kind} • ${event.freshness}`;
        }
        state.panel?.webview.postMessage({ type: "decision", payload: event });
        await pushScenariosToPanel(state.panel);
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

async function pushScenariosToPanel(panel: vscode.WebviewPanel | null): Promise<void> {
  if (!panel) {
    return;
  }
  const base = proxyBaseUrl().replace(/\/$/, "");
  try {
    const response = await fetch(`${base}/scenarios?limit=10`);
    const payload = await response.json();
    panel.webview.postMessage({ type: "scenarios", payload });
  } catch {
    panel.webview.postMessage({ type: "scenarios", payload: { count: 0, scenarios: [] } });
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
  <div class="row muted">Cockpit: ${proxyUrl}</div>
  <div class="row">
    <button id="refreshDevices">Refresh compute devices</button>
    <button id="refreshScenarios">Refresh scenarios</button>
  </div>
  <div class="row"><strong>Top scenario</strong></div>
  <pre id="decision">{}</pre>
  <div class="row"><strong>Top scenarios</strong></div>
  <div id="scenarioList"></div>
  <div class="row"><strong>Compute devices</strong></div>
  <pre id="devices">loading…</pre>
  <script>
    const vscode = acquireVsCodeApi();
    const decisionEl = document.getElementById("decision");
    const devicesEl = document.getElementById("devices");
    const scenarioListEl = document.getElementById("scenarioList");
    window.addEventListener("message", (event) => {
      if (event.data?.type === "decision") {
        decisionEl.textContent = JSON.stringify(event.data.payload, null, 2);
      } else if (event.data?.type === "scenarios") {
        const scenarios = event.data.payload?.scenarios || [];
        scenarioListEl.innerHTML = scenarios.map((item) => {
          const safeId = String(item.id || "");
          return \`<div style="border:1px solid var(--vscode-editorWidget-border);border-radius:6px;padding:8px;margin:8px 0;">
            <div><strong>\${safeId}</strong> • \${item.kind} • score=\${Number(item.score || 0).toFixed(3)} • \${item.freshness}</div>
            <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">
              <button data-action="expand" data-id="\${safeId}">Expand</button>
              <button data-action="outcome" data-result="useful" data-id="\${safeId}">Useful</button>
              <button data-action="outcome" data-result="partial" data-id="\${safeId}">Partial</button>
              <button data-action="outcome" data-result="irrelevant" data-id="\${safeId}">Irrelevant</button>
            </div>
          </div>\`;
        }).join("");
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
    function refreshScenarios() {
      vscode.postMessage({ type: "refresh" });
    }
    scenarioListEl.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const action = target.dataset.action;
      const id = target.dataset.id;
      if (!action || !id) return;
      if (action === "expand") {
        vscode.postMessage({ type: "expand", id });
      } else if (action === "outcome") {
        vscode.postMessage({ type: "outcome", id, result: target.dataset.result });
      }
    });
    document.getElementById("refreshDevices").addEventListener("click", refreshDevices);
    document.getElementById("refreshScenarios").addEventListener("click", refreshScenarios);
    refreshDevices();
    refreshScenarios();
  </script>
</body>
</html>`;
}
