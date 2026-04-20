from __future__ import annotations

from typing import Literal


def build_cockpit_html(mode: Literal["daemon", "proxy"]) -> str:
    if mode not in {"daemon", "proxy"}:
        raise ValueError(f"Unsupported cockpit mode: {mode}")

    return _COCKPIT_HTML.replace("__VANER_MODE__", mode)


_COCKPIT_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Vaner Cockpit</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      :root {
        color-scheme: light dark;
        --bg: #f6f7f9;
        --surface: #ffffff;
        --muted: #6a7280;
        --border: #d7dde4;
        --text: #111827;
        --accent: #2563eb;
        --fresh: #0f766e;
        --recent: #0369a1;
        --stale: #b45309;
        --warn: #92400e;
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --bg: #111827;
          --surface: #1f2937;
          --muted: #94a3b8;
          --border: #334155;
          --text: #f9fafb;
          --accent: #60a5fa;
          --fresh: #14b8a6;
          --recent: #38bdf8;
          --stale: #fbbf24;
          --warn: #f59e0b;
        }
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
        line-height: 1.45;
        background: var(--bg);
        color: var(--text);
      }
      .wrap {
        max-width: 1100px;
        margin: 0 auto;
        padding: 20px;
      }
      .panel {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 14px;
        margin-bottom: 14px;
      }
      h1, h2 { margin: 0; }
      h1 { font-size: 1.45rem; }
      h2 { font-size: 1.05rem; margin-bottom: 10px; }
      .header {
        display: flex;
        gap: 10px;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
      }
      .subtle { color: var(--muted); font-size: 0.9rem; }
      .pills, .chips {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      .pill, .chip {
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 0.85rem;
      }
      .chip.fresh { border-color: color-mix(in srgb, var(--fresh) 60%, var(--border)); }
      .chip.recent { border-color: color-mix(in srgb, var(--recent) 60%, var(--border)); }
      .chip.stale { border-color: color-mix(in srgb, var(--stale) 60%, var(--border)); }
      .status-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: #9ca3af;
      }
      .status-dot.live { background: #22c55e; }
      .status-dot.error { background: #ef4444; }
      .grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 14px;
      }
      @media (max-width: 900px) {
        .grid { grid-template-columns: 1fr; }
      }
      .scenario-list {
        display: grid;
        gap: 10px;
      }
      .scenario-card {
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 12px;
      }
      .scenario-card.flash {
        outline: 2px solid color-mix(in srgb, var(--accent) 65%, transparent);
      }
      .row {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
      }
      .mono {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 0.85rem;
      }
      .tags {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .tag {
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 2px 6px;
        font-size: 0.8rem;
      }
      .warning {
        margin-top: 8px;
        border-left: 3px solid var(--warn);
        padding-left: 8px;
        color: var(--warn);
        font-size: 0.86rem;
      }
      .actions {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin-top: 10px;
      }
      button {
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface);
        color: var(--text);
        padding: 6px 10px;
        cursor: pointer;
      }
      button.primary {
        border-color: color-mix(in srgb, var(--accent) 60%, var(--border));
      }
      button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      select, input {
        min-width: 220px;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface);
        color: var(--text);
        padding: 6px;
      }
      details { margin-top: 8px; }
      pre {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px;
        overflow-x: auto;
        white-space: pre-wrap;
      }
      .toast {
        position: fixed;
        right: 14px;
        bottom: 14px;
        max-width: 380px;
        padding: 10px 12px;
        border: 1px solid var(--border);
        border-radius: 10px;
        background: var(--surface);
        display: none;
      }
      .toast.show { display: block; }
      .hidden { display: none; }
    </style>
  </head>
  <body data-mode="__VANER_MODE__">
    <div class="wrap">
      <section class="panel header">
        <div>
          <h1>Vaner Cockpit</h1>
          <div class="subtle" id="subtitle">Live status and controls</div>
        </div>
        <div class="row">
          <div id="streamDot" class="status-dot"></div>
          <span id="streamState" class="subtle">connecting...</span>
        </div>
      </section>

      <section class="panel">
        <h2>Status</h2>
        <div id="statusPills" class="pills"></div>
      </section>

      <section class="panel" id="summaryPanel">
        <h2 id="summaryTitle">Summary</h2>
        <div id="summaryChips" class="chips"></div>
      </section>

      <div class="grid">
        <section class="panel" id="leftPanel">
          <h2 id="leftTitle">Top Scenarios</h2>
          <div id="scenarioList" class="scenario-list"></div>
          <pre id="proxyDecision" class="hidden"></pre>
        </section>
        <section class="panel" id="rightPanel">
          <h2 id="rightTitle">Compute</h2>
          <div class="subtle" id="computeState">loading...</div>
          <div id="daemonComputeControls" class="actions">
            <select id="deviceSelect"></select>
            <button id="applyDevice" class="primary">Apply Device</button>
          </div>
          <div id="proxyComputeControls" class="actions hidden">
            <input id="cpuFraction" type="number" min="0.01" max="1" step="0.01" />
            <button id="applyCpuFraction" class="primary">Apply CPU Fraction</button>
          </div>
          <div id="proxyGatewayControls" class="actions hidden">
            <button id="gatewayDisable">Disable enrichment</button>
            <button id="gatewayEnable">Enable enrichment</button>
          </div>
          <div id="computeWarning" class="warning hidden"></div>
        </section>
      </div>
    </div>
    <div id="toast" class="toast" role="status" aria-live="polite"></div>

    <script>
      window.__VANER_MODE = "__VANER_MODE__";
      const mode = window.__VANER_MODE;
      const isDaemon = mode === "daemon";
      const isProxy = mode === "proxy";

      const subtitle = document.getElementById("subtitle");
      const summaryTitle = document.getElementById("summaryTitle");
      const summaryChips = document.getElementById("summaryChips");
      const leftTitle = document.getElementById("leftTitle");
      const rightTitle = document.getElementById("rightTitle");
      const scenarioList = document.getElementById("scenarioList");
      const proxyDecision = document.getElementById("proxyDecision");
      const statusPills = document.getElementById("statusPills");
      const computeState = document.getElementById("computeState");
      const computeWarning = document.getElementById("computeWarning");
      const daemonComputeControls = document.getElementById("daemonComputeControls");
      const proxyComputeControls = document.getElementById("proxyComputeControls");
      const proxyGatewayControls = document.getElementById("proxyGatewayControls");
      const deviceSelect = document.getElementById("deviceSelect");
      const applyDevice = document.getElementById("applyDevice");
      const cpuFraction = document.getElementById("cpuFraction");
      const applyCpuFraction = document.getElementById("applyCpuFraction");
      const gatewayDisable = document.getElementById("gatewayDisable");
      const gatewayEnable = document.getElementById("gatewayEnable");
      const streamDot = document.getElementById("streamDot");
      const streamState = document.getElementById("streamState");
      const toast = document.getElementById("toast");

      let scenarioMap = new Map();

      function escapeHtml(value) {
        return String(value ?? "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }

      function showToast(message) {
        toast.textContent = String(message);
        toast.classList.add("show");
        setTimeout(() => toast.classList.remove("show"), 2200);
      }

      function setStreamState(state) {
        streamDot.classList.remove("live", "error");
        if (state === "live") {
          streamDot.classList.add("live");
          streamState.textContent = "live";
          return;
        }
        if (state === "error") {
          streamDot.classList.add("error");
          streamState.textContent = "reconnecting...";
          return;
        }
        streamState.textContent = "connecting...";
      }

      function renderStatus(status) {
        const backend = status.backend || {};
        const compute = status.compute || {};
        const mcp = status.mcp || {};
        const pills = [
          `<span class="pill"><strong>Health:</strong>${escapeHtml(status.health || "unknown")}</span>`,
          `<span class="pill"><strong>Backend:</strong>${
            escapeHtml(backend.base_url || "(unset)")
          } [${escapeHtml(backend.model || "(unset)")} ]</span>`,
          `<span class="pill"><strong>Device:</strong>${escapeHtml(compute.device || "auto")}</span>`,
        ];
        if (isDaemon) {
          pills.push(
            `<span class="pill"><strong>MCP:</strong>${
              escapeHtml(mcp.transport || "unknown")
            } ${escapeHtml(mcp.http_host || "")}:${escapeHtml(mcp.http_port || "")}</span>`
          );
        }
        if (isProxy) {
          pills.push(`<span class="pill"><strong>Gateway:</strong>${status.gateway_enabled ? "enabled" : "disabled"}</span>`);
        }
        statusPills.innerHTML = pills.join("");
        computeState.textContent =
          `Selected device: ${compute.device || "auto"} | ` +
          `cpu_fraction=${compute.cpu_fraction} | ` +
          `gpu_fraction=${compute.gpu_memory_fraction}`;
        if (isProxy) {
          cpuFraction.value = String(compute.cpu_fraction ?? 0.2);
        }
      }

      function renderSummary(stats) {
        if (isDaemon) {
          summaryTitle.textContent = "Scenario Freshness";
          const summary = stats || { fresh: 0, recent: 0, stale: 0, total: 0 };
          summaryChips.innerHTML = `
            <span class="chip fresh">fresh: ${summary.fresh ?? 0}</span>
            <span class="chip recent">recent: ${summary.recent ?? 0}</span>
            <span class="chip stale">stale: ${summary.stale ?? 0}</span>
            <span class="chip">total: ${summary.total ?? 0}</span>
          `;
          return;
        }
        summaryTitle.textContent = "Impact Summary";
        const impact = stats || {};
        summaryChips.innerHTML = `
          <span class="chip">count: ${impact.count ?? 0}</span>
          <span class="chip">latency_gain_ms: ${Number(impact.mean_latency_gain_ms || 0).toFixed(2)}</span>
          <span class="chip">char_delta: ${Number(impact.mean_char_delta || 0).toFixed(2)}</span>
          <span class="chip">idle_seconds_used: ${Number(impact.idle_seconds_used || 0).toFixed(2)}</span>
        `;
      }

      async function postJson(url, payload) {
        const response = await fetch(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          throw new Error((await response.text()) || `Request failed: ${response.status}`);
        }
        return await response.json();
      }

      function daemonCardHtml(row) {
        const entities = (row.entities || []).map((entity) => `<span class="tag mono">${escapeHtml(entity)}</span>`).join("");
        const gaps = (row.coverage_gaps || []).map((gap) => `<div class="warning">${escapeHtml(gap)}</div>`).join("");
        const evidence = (row.evidence || [])
          .slice(0, 4)
          .map((ev) => `<li><span class="mono">${escapeHtml(ev.source_path || ev.key || "")}</span>: ${escapeHtml(ev.excerpt || "")}</li>`)
          .join("");
        return `
          <div class="row">
            <div class="pills">
              <span class="pill">${escapeHtml(row.kind)}</span>
              <span class="pill">score ${Number(row.score || 0).toFixed(3)}</span>
              <span class="pill">${escapeHtml(row.freshness || "unknown")}</span>
            </div>
            <span class="mono">${escapeHtml(row.id || "")}</span>
          </div>
          <div class="tags">${entities || '<span class="subtle">No entities</span>'}</div>
          ${gaps}
          <div class="actions">
            <button data-action="expand" data-id="${escapeHtml(row.id)}">Expand</button>
            <button data-action="outcome" data-id="${escapeHtml(row.id)}" data-result="useful">Useful</button>
            <button data-action="outcome" data-id="${escapeHtml(row.id)}" data-result="partial">Partial</button>
            <button data-action="outcome" data-id="${escapeHtml(row.id)}" data-result="irrelevant">Irrelevant</button>
          </div>
          <details>
            <summary>Details</summary>
            <div class="subtle">Evidence</div>
            <ul>${evidence || "<li>No evidence</li>"}</ul>
            <div class="subtle">Prepared context</div>
            <pre>${escapeHtml(row.prepared_context || "")}</pre>
          </details>
        `;
      }

      function renderDaemonScenarios(rows, highlightIds = new Set()) {
        if (!rows.length) {
          scenarioList.innerHTML = '<div class="subtle">No scenarios yet.</div>';
          return;
        }
        scenarioList.innerHTML = rows.map((row) => {
          const flash = highlightIds.has(row.id) ? "flash" : "";
          return `<article class="scenario-card ${flash}" data-scenario-id="${escapeHtml(row.id)}">${daemonCardHtml(row)}</article>`;
        }).join("");
      }

      function applyModeLayout() {
        if (isDaemon) {
          subtitle.textContent = "Live scenario frontier and compute controls";
          leftTitle.textContent = "Top Scenarios";
          rightTitle.textContent = "Compute";
          proxyDecision.classList.add("hidden");
          proxyComputeControls.classList.add("hidden");
          proxyGatewayControls.classList.add("hidden");
          daemonComputeControls.classList.remove("hidden");
          return;
        }
        subtitle.textContent = "Live proxy decisions, impact, and controls";
        leftTitle.textContent = "Recent Proxy Decisions";
        rightTitle.textContent = "Proxy Controls";
        scenarioList.classList.add("hidden");
        proxyDecision.classList.remove("hidden");
        daemonComputeControls.classList.add("hidden");
        proxyComputeControls.classList.remove("hidden");
        proxyGatewayControls.classList.remove("hidden");
      }

      async function refreshStatus() {
        const response = await fetch("/status");
        renderStatus(await response.json());
      }

      async function refreshDaemonScenarios() {
        const response = await fetch("/scenarios?limit=10");
        const payload = await response.json();
        const rows = payload.scenarios || [];
        scenarioMap = new Map(rows.map((row) => [row.id, row]));
        renderDaemonScenarios(rows);
      }

      async function refreshDevices() {
        const response = await fetch("/compute/devices");
        const payload = await response.json();
        const devices = payload.devices || [];
        deviceSelect.innerHTML = devices.map((device) => (
          `<option value="${escapeHtml(device.id)}">${escapeHtml(device.label)} (${escapeHtml(device.kind)})</option>`
        )).join("");
        if (payload.selected) {
          deviceSelect.value = payload.selected;
        }
        if (payload.warning) {
          computeWarning.classList.remove("hidden");
          computeWarning.textContent = payload.warning;
        } else {
          computeWarning.classList.add("hidden");
          computeWarning.textContent = "";
        }
      }

      async function refreshProxySummary() {
        const response = await fetch("/impact/summary");
        renderSummary(await response.json());
      }

      async function refreshProxyDecisions() {
        const response = await fetch("/decisions?limit=1");
        const payload = await response.json();
        const item = (payload.items || [])[0];
        proxyDecision.textContent = item ? JSON.stringify(item, null, 2) : "No decisions yet.";
      }

      scenarioList.addEventListener("click", async (event) => {
        if (!isDaemon) return;
        const target = event.target;
        if (!(target instanceof HTMLButtonElement)) return;
        const id = target.dataset.id;
        if (!id) return;
        target.disabled = true;
        try {
          if (target.dataset.action === "expand") {
            await postJson(`/scenarios/${id}/expand`, {});
            showToast(`Expanded ${id}`);
          } else if (target.dataset.action === "outcome") {
            await postJson(`/scenarios/${id}/outcome`, { result: target.dataset.result });
            showToast(`Recorded ${target.dataset.result} for ${id}`);
          }
          await refreshDaemonScenarios();
          await refreshStatus();
        } catch (error) {
          showToast(`Action failed: ${error}`);
        } finally {
          target.disabled = false;
        }
      });

      applyDevice.addEventListener("click", async () => {
        if (!isDaemon) return;
        applyDevice.disabled = true;
        try {
          await postJson("/compute", { device: deviceSelect.value });
          showToast(`Set compute device to ${deviceSelect.value}`);
          await refreshStatus();
          await refreshDevices();
        } catch (error) {
          showToast(`Failed to update device: ${error}`);
        } finally {
          applyDevice.disabled = false;
        }
      });

      applyCpuFraction.addEventListener("click", async () => {
        if (!isProxy) return;
        applyCpuFraction.disabled = true;
        try {
          const value = Number(cpuFraction.value || "0.2");
          await postJson("/compute", { cpu_fraction: value });
          showToast(`Set CPU fraction to ${value}`);
          await refreshStatus();
        } catch (error) {
          showToast(`Failed to update CPU fraction: ${error}`);
        } finally {
          applyCpuFraction.disabled = false;
        }
      });

      gatewayDisable.addEventListener("click", async () => {
        if (!isProxy) return;
        await postJson("/gateway/toggle", { enabled: false });
        showToast("Gateway enrichment disabled");
        await refreshStatus();
      });

      gatewayEnable.addEventListener("click", async () => {
        if (!isProxy) return;
        await postJson("/gateway/toggle", { enabled: true });
        showToast("Gateway enrichment enabled");
        await refreshStatus();
      });

      function wireDaemonStream() {
        const stream = new EventSource("/scenarios/stream");
        stream.onopen = () => setStreamState("live");
        stream.onerror = () => setStreamState("error");
        stream.onmessage = (event) => {
          setStreamState("live");
          const data = JSON.parse(event.data);
          renderSummary(data.summary);
          const rows = [];
          const highlightIds = new Set();
          const top = data.top_scenarios || [];
          for (const entry of top) {
            const previous = scenarioMap.get(entry.id);
            const merged = { ...(previous || {}), ...entry };
            scenarioMap.set(entry.id, merged);
            rows.push(merged);
            highlightIds.add(entry.id);
          }
          if (rows.length) {
            renderDaemonScenarios(rows, highlightIds);
          }
        };
      }

      function wireProxyStream() {
        const stream = new EventSource("/decisions/stream");
        stream.onopen = () => setStreamState("live");
        stream.onerror = () => setStreamState("error");
        stream.onmessage = (event) => {
          setStreamState("live");
          proxyDecision.textContent = JSON.stringify(JSON.parse(event.data), null, 2);
        };
      }

      applyModeLayout();
      if (isDaemon) {
        Promise.all([refreshStatus(), refreshDaemonScenarios(), refreshDevices()]).then(() => {
          fetch("/status").then((r) => r.json()).then((payload) => renderSummary(payload.scenario_counts));
        }).catch((error) => showToast(`Initial load failed: ${error}`));
        wireDaemonStream();
      } else {
        Promise.all([refreshStatus(), refreshProxySummary(), refreshProxyDecisions()]).catch(
          (error) => showToast(`Initial load failed: ${error}`)
        );
        wireProxyStream();
      }

      setInterval(() => {
        refreshStatus().catch(() => {});
        if (isProxy) {
          refreshProxySummary().catch(() => {});
        }
      }, 15000);
    </script>
  </body>
</html>
"""
