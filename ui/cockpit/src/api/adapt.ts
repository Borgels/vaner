import type { ScenarioApiPayload, UIEvidence, UIScenario } from '../types'

const RAW_SCENARIO_ID = /^scn_[a-f0-9]{8,}$/i
const DIFF_PREFIX = /^Snippet:\s*diff --git\b/i

function cleanText(value: string | null | undefined): string {
  return (value ?? '').replace(/\s+/g, ' ').trim()
}

function trimLabel(value: string, max = 72): string {
  if (value.length <= max) {
    return value
  }
  return `${value.slice(0, max - 1).trimEnd()}...`
}

function pathLabel(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.slice(-2).join('/') || path
}

function fileName(path: string): string {
  return path.split('/').filter(Boolean).pop() || path
}

function commonDirectory(paths: string[]): string | null {
  const directories = paths
    .map((path) => path.split('/').filter(Boolean).slice(0, -1))
    .filter((parts) => parts.length > 0)
  if (!directories.length) {
    return null
  }
  const [first, ...rest] = directories
  const common: string[] = []
  for (const [index, part] of first.entries()) {
    if (rest.every((candidate) => candidate[index] === part)) {
      common.push(part)
    } else {
      break
    }
  }
  if (!common.length) {
    return null
  }
  return common.join('/')
}

function humanPathList(paths: string[]): string {
  const labels = paths.map(pathLabel)
  if (labels.length <= 2) {
    return labels.join(' and ')
  }
  return `${labels.slice(0, 2).join(', ')} and ${labels.length - 2} more`
}

function actionVerb(kind: ScenarioApiPayload['kind']): string {
  switch (kind) {
    case 'debug':
      return 'Debug'
    case 'change':
      return 'Update'
    case 'explain':
      return 'Review'
    case 'refactor':
      return 'Refactor'
    default:
      return 'Inspect'
  }
}

function labelFromDiff(path: string, text: string): string | null {
  const version = text.match(/[-+](?:VERSION|version)\s*=\s*["']?([^"'\s]+)["']?.*[-+](?:VERSION|version)\s*=\s*["']?([^"'\s]+)["']?/i)
  if (version?.[1] && version?.[2] && version[1] !== version[2]) {
    return `Update version ${version[1]} -> ${version[2]} in ${fileName(path)}`
  }
  if (/CHANGELOG\.md/i.test(path)) {
    const release = text.match(/\+##\s*\[?([0-9][^\]\s]*)\]?/)
    return release?.[1] ? `Review changelog for ${release[1]}` : 'Review changelog update'
  }
  if (/plugin\.json$/i.test(path)) {
    return `Review plugin manifest change in ${pathLabel(path)}`
  }
  return null
}

function labelFromPath(payload: ScenarioApiPayload): string | null {
  const entities = (payload.entities ?? []).filter(Boolean)
  if (entities.length > 1) {
    const directory = commonDirectory(entities)
    if (directory) {
      return `${actionVerb(payload.kind)} ${entities.length} files in ${directory}`
    }
    return `${actionVerb(payload.kind)} related changes in ${humanPathList(entities)}`
  }
  const path = cleanText(payload.path ?? payload.evidence?.find((item) => cleanText(item.source_path))?.source_path ?? entities[0])
  if (!path) {
    return null
  }
  if (path.endsWith('.md') || path.endsWith('.mdx')) {
    return `Review ${pathLabel(path)}`
  }
  return `${actionVerb(payload.kind)} ${pathLabel(path)}`
}

function reasonFromPayload(payload: ScenarioApiPayload, title: string): string {
  const explicit = cleanText(payload.reason)
  if (explicit) {
    return explicit
  }
  const paths = (payload.entities ?? []).filter(Boolean)
  const evidencePaths = (payload.evidence ?? [])
    .map((item) => cleanText(item.source_path))
    .filter(Boolean)
  const uniquePaths = Array.from(new Set(paths.length ? paths : evidencePaths))
  if (uniquePaths.length) {
    const changeCount = (payload.evidence ?? []).filter((item) => DIFF_PREFIX.test(cleanText(item.excerpt))).length
    const basis =
      changeCount > 0
        ? `${changeCount} change ${changeCount === 1 ? 'summary' : 'summaries'}`
        : `${payload.evidence?.length ?? 0} evidence items`
    return `${title}. Based on ${basis} touching ${humanPathList(uniquePaths)}.`
  }
  return `${title}.`
}

export function scenarioDisplayTitle(payload: ScenarioApiPayload): string {
  const explicit = cleanText(payload.title)
  if (explicit && !RAW_SCENARIO_ID.test(explicit)) {
    return trimLabel(explicit)
  }

  const reason = cleanText(payload.reason)
  if (reason) {
    return trimLabel(reason)
  }

  if ((payload.entities ?? []).filter(Boolean).length > 1) {
    const grouped = labelFromPath(payload)
    if (grouped) {
      return trimLabel(grouped)
    }
  }

  for (const item of payload.evidence ?? []) {
    const excerpt = cleanText(item.excerpt)
    const path = cleanText(item.source_path)
    if (!excerpt) {
      continue
    }
    const diffLabel = labelFromDiff(path, excerpt)
    if (diffLabel) {
      return trimLabel(diffLabel)
    }
  }

  const excerpt = cleanText(payload.evidence?.find((item) => {
    const text = cleanText(item.excerpt)
    return text && !DIFF_PREFIX.test(text)
  })?.excerpt)
  if (excerpt && !excerpt.startsWith('Snippet:')) {
    return trimLabel(excerpt)
  }

  const pathLabelText = labelFromPath(payload)
  if (pathLabelText) {
    return trimLabel(pathLabelText)
  }

  const path = cleanText(payload.path ?? payload.evidence?.find((item) => cleanText(item.source_path))?.source_path)
  if (path) {
    return `${payload.kind ?? 'scenario'}: ${pathLabel(path)}`
  }

  return payload.id.replace(/^scn_/, 'scenario ')
}

export function adaptScenario(payload: ScenarioApiPayload): UIScenario {
  const title = scenarioDisplayTitle(payload)
  return {
    id: payload.id,
    kind: payload.kind ?? 'research',
    title,
    score: Number(payload.score ?? 0),
    freshness: payload.freshness ?? 'recent',
    depth: Number(payload.depth ?? 0),
    parent: payload.parent ?? null,
    path: payload.path ?? payload.evidence?.[0]?.source_path ?? '',
    skill: payload.skill ?? null,
    decisionState: payload.decision_state ?? 'active',
    reason: reasonFromPayload(payload, title),
    entities: payload.entities ?? [],
    pinned: Boolean(payload.pinned),
  }
}

export function adaptEvidence(payload: ScenarioApiPayload): UIEvidence[] {
  return (payload.evidence ?? []).map((item) => ({
    file: item.source_path ?? item.key ?? '',
    lines: item.start_line || item.end_line ? `${item.start_line ?? '?'}-${item.end_line ?? '?'}` : null,
    note: item.excerpt ?? '',
    startLine: item.start_line ?? null,
    endLine: item.end_line ?? null,
  }))
}
