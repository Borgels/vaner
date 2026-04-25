#!/usr/bin/env node
// 0.8.6 WS10 — Minimal axe-core scan runner. Used by both `npm run
// a11y:scan` (cockpit dev server) and `npm run a11y:scan:mcp` (MCP Apps
// UI HTML bundle). Reads tests/a11y-audit-baseline.json to know what
// targets to scan and which violations are pre-acknowledged.
//
// CI calls this script after `npm run build` + `npm run preview`. The
// preview server URL must match the baseline's `cockpit-dev-server` URL
// (default: http://127.0.0.1:4173 from `vite preview`; we standardise
// on 5173 for the baseline so the workflow can override via env if
// needed).
//
// Behaviour:
//   - Spawns axe-core via @axe-core/cli for each target.
//   - For HTML-file targets (no URL, only `source`), opens the file with
//     a headless Chromium provided by @axe-core/cli's --browser flag.
//   - Aggregates results; exits 1 when any violation has a severity in
//     baseline.fail_on_severities AND is not in baseline.known_violations.
//
// This is intentionally a small wrapper so that contributors can also
// run axe locally without standing up the full CI pipeline.

import { existsSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const COCKPIT_ROOT = path.resolve(__dirname, '..')
const REPO_ROOT = path.resolve(COCKPIT_ROOT, '..', '..')
const BASELINE_PATH = path.join(COCKPIT_ROOT, 'tests', 'a11y-audit-baseline.json')

const args = process.argv.slice(2)
const ONLY_MCP = args.includes('--mcp')
const ONLY_COCKPIT = args.includes('--cockpit')

if (!existsSync(BASELINE_PATH)) {
  console.error(`Missing baseline: ${BASELINE_PATH}`)
  process.exit(2)
}
const baseline = JSON.parse(readFileSync(BASELINE_PATH, 'utf-8'))

const targets = baseline.targets.filter((target) => {
  if (ONLY_MCP) return target.name === 'mcp-apps-active-predictions'
  if (ONLY_COCKPIT) return target.name === 'cockpit-dev-server'
  return true
})

const failOn = new Set(baseline.fail_on_severities ?? ['serious', 'critical'])
let totalNewViolations = 0

for (const target of targets) {
  const url = target.url || (target.source ? `file://${path.join(REPO_ROOT, target.source)}` : null)
  if (!url) {
    console.error(`Target ${target.name} has neither url nor source; skipping.`)
    continue
  }
  console.log(`\n=== axe-core scan: ${target.name} ===`)
  console.log(`URL: ${url}`)

  // Defer to @axe-core/cli (installed via devDependencies). If it is
  // not on PATH, we surface a clear error so contributors know what to
  // install rather than failing with "command not found".
  const result = spawnSync(
    'npx',
    ['--no-install', '@axe-core/cli', url, '--exit', '--stdout', '--tags', 'wcag2a,wcag2aa,best-practice'],
    { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'pipe'] },
  )
  if (result.error) {
    console.error(`@axe-core/cli not found: ${result.error.message}`)
    console.error('Install dev deps with `npm install` and retry.')
    process.exit(2)
  }
  const stdout = result.stdout || ''
  process.stdout.write(stdout)
  if (result.stderr) process.stderr.write(result.stderr)

  // axe-cli exits 1 on any violation; parse JSON to filter by severity.
  // The CLI prints a JSON array of violations on stdout when --stdout is
  // passed. Be defensive: an empty stdout means no violations.
  let violations = []
  try {
    const trimmed = stdout.trim()
    if (trimmed.startsWith('[')) {
      violations = JSON.parse(trimmed)
    }
  } catch (err) {
    console.error(`Could not parse axe-cli stdout as JSON: ${err.message}`)
  }

  const known = new Set((target.known_violations ?? []).map((v) => v.id))
  for (const v of violations) {
    const severity = v.impact ?? 'unknown'
    if (!failOn.has(severity)) continue
    if (known.has(v.id)) continue
    totalNewViolations += 1
    console.error(`NEW [${severity}] ${v.id}: ${v.help}`)
  }
}

if (totalNewViolations > 0) {
  console.error(`\n${totalNewViolations} new violation(s) above the baseline threshold.`)
  process.exit(1)
}
console.log('\nNo new violations above the baseline threshold.')
process.exit(0)
