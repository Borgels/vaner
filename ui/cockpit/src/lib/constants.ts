import type { CockpitSettings, UIAccent, UIScenarioKind } from '../types'

export const KIND_COLOR: Record<UIScenarioKind, string> = {
  research: 'var(--kind-research)',
  explain: 'var(--kind-explain)',
  debug: 'var(--kind-debug)',
  change: 'var(--kind-change)',
  refactor: 'var(--kind-refactor)',
}

export const ACCENT_MAP: Record<UIAccent, string> = {
  violet: 'oklch(0.7 0.105 300)',
  amber: 'oklch(0.8 0.12 85)',
  teal: 'oklch(0.74 0.1 190)',
}

export const DEFAULT_COCKPIT_SETTINGS: CockpitSettings = {
  density: 'relaxed',
  accent: 'violet',
  reduceMotion: false,
  topK: 10,
  gatewayEnabled: true,
}
