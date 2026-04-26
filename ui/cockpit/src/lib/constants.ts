import type { CockpitSettings, UIAccent, UIScenarioKind } from '../types'

export const ACCENT_MAP: Record<UIAccent, string> = {
  violet: 'oklch(68% 0.18 294)',
  amber: 'oklch(72% 0.16 72)',
  teal: 'oklch(70% 0.14 184)',
}

export const DEFAULT_COCKPIT_SETTINGS: CockpitSettings = {
  density: 'dense',
  accent: 'teal',
  reduceMotion: false,
  topK: 20,
  gatewayEnabled: false,
}

export const KIND_COLOR: Record<UIScenarioKind, string> = {
  research: 'var(--kind-research)',
  explain: 'var(--kind-explain)',
  change: 'var(--kind-change)',
  debug: 'var(--kind-debug)',
  refactor: 'var(--kind-refactor)',
}
