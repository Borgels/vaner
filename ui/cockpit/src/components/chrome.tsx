import { useEffect, useMemo, useRef, useState } from 'react'

import type {
  BackendPreset,
  BackendSettings,
  CockpitSettings,
  ComputeDevice,
  ComputeSettings,
  ImpactSummary,
  LimitSettings,
  MCPSettings,
  UIMode,
  UIPackageState,
  UIPinnedFact,
  UISkill,
} from '../types'

export interface CommandItem {
  id: string
  kind: string
  kindColor?: string
  label: string
  keywords?: string
  hint?: string
  run: () => void
}

interface TopBarProps {
  running: boolean
  onToggleRun: () => void
  packageState: UIPackageState | null
  onOpenSettings: () => void
  onOpenPalette: () => void
  mode: UIMode
}

const MODE_LABEL: Record<UIMode, string> = {
  daemon: 'DAEMON',
  proxy: 'PROXY',
  mcp: 'MCP',
}

export function TopBar({ running, onToggleRun, packageState, onOpenSettings, onOpenPalette, mode }: TopBarProps) {
  return (
    <div
      style={{
        gridArea: 'top',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        padding: '0 20px',
        borderBottom: '1px solid var(--line-1)',
        background: 'var(--bg-1)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <svg width="22" height="22" viewBox="0 0 22 22">
          <circle cx="11" cy="11" r="9" fill="none" stroke="var(--accent)" strokeWidth="1.4" />
          <circle cx="11" cy="11" r="4" fill="var(--accent)" />
          <circle cx="18" cy="6" r="1.6" fill="var(--amber)" />
          <path d="M11 11 L18 6" stroke="var(--amber)" strokeWidth="0.9" />
        </svg>
        <span className="editorial" style={{ fontSize: 19, letterSpacing: -0.3 }}>
          Vaner
        </span>
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-4)', letterSpacing: 1, marginLeft: 2 }}>
          COCKPIT · {MODE_LABEL[mode]}
        </span>
      </div>

      <div style={{ flex: 1 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: running ? 'var(--ok)' : 'var(--fg-4)',
              boxShadow: running ? '0 0 10px var(--ok)' : 'none',
              animation: running ? 'dc-pulse 1.4s infinite' : 'none',
            }}
          />
          <span className="mono" style={{ fontSize: 10.5, letterSpacing: 0.8, color: 'var(--fg-2)' }}>
            {running ? 'LIVE' : 'RETRYING'}
          </span>
        </div>
        <button onClick={onToggleRun} style={headerBtn}>
          ↻ refresh
        </button>
        {packageState ? (
          <>
            <div style={{ width: 1, height: 20, background: 'var(--line-1)' }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className="mono" style={{ fontSize: 10, color: 'var(--fg-4)', letterSpacing: 0.6 }}>
                PKG
              </span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--amber)', fontWeight: 500 }}>
                {packageState.id}
              </span>
              <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>
                {packageState.tokens.toLocaleString()} / {packageState.budget.toLocaleString()} tok
              </span>
            </div>
          </>
        ) : null}
        <button onClick={onOpenPalette} style={headerBtn} title="Command palette (Ctrl/Cmd+K)">
          ⌘K
        </button>
        <button onClick={onOpenSettings} style={headerBtn} title="Settings">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.3">
            <circle cx="6" cy="6" r="1.5" />
            <path d="M6 1v1.5M6 9.5V11M11 6H9.5M2.5 6H1M9.5 2.5 8.5 3.5M3.5 8.5 2.5 9.5M9.5 9.5 8.5 8.5M3.5 3.5 2.5 2.5" />
          </svg>
        </button>
      </div>
    </div>
  )
}

const headerBtn: React.CSSProperties = {
  background: 'var(--bg-2)',
  border: '1px solid var(--line-1)',
  color: 'var(--fg-2)',
  padding: '6px 10px',
  borderRadius: 'var(--r-1)',
  fontFamily: 'var(--font-mono)',
  fontSize: 10.5,
  cursor: 'pointer',
  letterSpacing: 0.4,
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
}

interface LeftRailProps {
  mode: UIMode
  skills: UISkill[]
  pinned: UIPinnedFact[]
  packageState: UIPackageState | null
  onUnpin: (id: string) => void
  onSkillNudge: (name: string, delta: number) => void
  scenarioCount: number
  impact?: ImpactSummary
}

export function LeftRail({
  mode,
  skills,
  pinned,
  packageState,
  onUnpin,
  onSkillNudge,
  scenarioCount,
  impact,
}: LeftRailProps) {
  return (
    <div
      style={{
        gridArea: 'rail',
        borderRight: '1px solid var(--line-1)',
        background: 'var(--bg-1)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div style={{ padding: '16px 16px 10px' }}>
        <div className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)', marginBottom: 10 }}>
          {mode === 'proxy' ? 'PROXY' : 'FRONTIER'}
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontFamily: 'var(--font-display)', fontSize: 28, color: 'var(--fg-1)', fontVariantNumeric: 'tabular-nums' }}>
            {mode === 'proxy' ? (impact?.count ?? 0) : scenarioCount}
          </span>
          {packageState ? (
            <span className="mono" style={{ fontSize: 10.5, color: 'var(--amber)' }}>
              {packageState.chosen} chosen
            </span>
          ) : null}
        </div>
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-3)', marginTop: 2 }}>
          {mode === 'proxy' ? 'recent proxy decisions' : 'open scenarios'}
        </div>
      </div>

      <div style={{ padding: '4px 16px 14px', borderBottom: '1px solid var(--line-hair)' }}>
        <div className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)', marginBottom: 10 }}>
          {mode === 'proxy' ? 'IMPACT' : 'SKILLS'}
        </div>
        {mode !== 'proxy' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            {skills.map((skill) => (
              <div key={skill.name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontFamily: 'var(--font-display)', fontSize: 12, color: 'var(--fg-1)' }}>{skill.name}</div>
                  <div style={{ fontSize: 10.5, color: 'var(--fg-4)', marginTop: 2 }}>{skill.desc}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
                    <div style={{ flex: 1, height: 3, background: 'var(--bg-inset)', borderRadius: 2 }}>
                      <div
                        style={{
                          width: `${skill.weight * 100}%`,
                          height: '100%',
                          background: 'var(--accent)',
                          borderRadius: 2,
                          transition: 'width .4s ease',
                        }}
                      />
                    </div>
                    <span
                      className="mono"
                      style={{ fontSize: 10, color: 'var(--fg-3)', fontVariantNumeric: 'tabular-nums', minWidth: 30, textAlign: 'right' }}
                    >
                      {(skill.weight * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => onSkillNudge(skill.name, -0.04)}
                  style={skillNudgeBtn}
                  title={`Nudge ${skill.name} down`}
                  aria-label={`Nudge ${skill.name} down`}
                >
                  −
                </button>
                <button
                  onClick={() => onSkillNudge(skill.name, 0.04)}
                  style={skillNudgeBtn}
                  title={`Nudge ${skill.name} up`}
                  aria-label={`Nudge ${skill.name} up`}
                >
                  +
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
            <Row k="count" v={impact?.count ?? 0} />
            <Row k="latency gain" v={`${Number(impact?.mean_latency_gain_ms ?? 0).toFixed(2)} ms`} />
            <Row k="char delta" v={Number(impact?.mean_char_delta ?? 0).toFixed(2)} />
            <Row k="idle used" v={`${Number(impact?.idle_seconds_used ?? 0).toFixed(2)} s`} />
          </div>
        )}
      </div>

      <div style={{ padding: '14px 16px 10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)' }}>
            PINNED CONTEXT
          </span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--amber)' }}>
            {pinned.length}
          </span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {pinned.map((fact) => (
            <div
              key={fact.id}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 8,
                padding: '8px 10px',
                background: 'var(--bg-2)',
                border: '1px solid var(--line-hair)',
                borderRadius: 'var(--r-1)',
                fontSize: 11.5,
                color: 'var(--fg-2)',
                lineHeight: 1.45,
              }}
            >
              <span style={{ color: 'var(--amber)', flexShrink: 0, marginTop: 2 }}>◆</span>
              <span style={{ flex: 1 }}>{fact.text}</span>
              <button
                onClick={() => onUnpin(fact.id)}
                style={{ background: 'transparent', border: 'none', color: 'var(--fg-4)', cursor: 'pointer', padding: 0, fontSize: 13, lineHeight: 1 }}
              >
                ×
              </button>
            </div>
          ))}
          {!pinned.length ? (
            <div style={{ fontSize: 11.5, color: 'var(--fg-4)', padding: '6px 0', fontStyle: 'italic' }}>
              No pinned facts. Pin scenarios or add notes to always include.
            </div>
          ) : null}
        </div>
      </div>

      <div style={{ flex: 1 }} />

      {packageState ? (
        <div style={{ padding: '14px 16px', borderTop: '1px solid var(--line-hair)', background: 'var(--bg-0)' }}>
          <div className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)', marginBottom: 8 }}>
            CURRENT PACKAGE
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>
            <Row k="id" v={packageState.id} />
            <Row k="tokens" v={`${packageState.tokens.toLocaleString()} / ${packageState.budget.toLocaleString()}`} />
            <Row k="chosen" v={packageState.chosen} />
            <Row k="partial" v={packageState.partial} />
            <Row k="rejected" v={packageState.rejected} />
            <Row k="compression" v={`${(packageState.compression * 100).toFixed(0)}%`} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

const skillNudgeBtn: React.CSSProperties = {
  background: 'var(--bg-2)',
  border: '1px solid var(--line-hair)',
  color: 'var(--fg-3)',
  padding: '2px 6px',
  borderRadius: 'var(--r-1)',
  cursor: 'pointer',
  fontFamily: 'var(--font-mono)',
  fontSize: 11,
  lineHeight: 1,
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
      <span style={{ color: 'var(--fg-4)' }}>{k}</span>
      <span>{v}</span>
    </div>
  )
}

interface StreamPanelProps {
  title: string
  subtitle: string
  events: Array<{ id: string; t: string; tag: string; color: string; msg: string; scn: string | null }>
  onSelect: (id: string) => void
  live: boolean
}

export function StreamPanel({ title, subtitle, events, onSelect, live }: StreamPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0
    }
  }, [events.length])

  return (
    <div
      style={{
        gridArea: 'stream',
        borderLeft: '1px solid var(--line-1)',
        background: 'var(--bg-1)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: '14px 16px 10px',
          borderBottom: '1px solid var(--line-hair)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div>
          <div className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)' }}>
            {title}
          </div>
          <div style={{ fontSize: 13, color: 'var(--fg-1)', marginTop: 2, fontFamily: 'var(--font-display)' }}>
            {subtitle}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: live ? 'var(--ok)' : 'var(--fg-4)', boxShadow: live ? '0 0 8px var(--ok)' : 'none' }} />
          <span className="mono" style={{ fontSize: 10, color: 'var(--fg-3)' }}>
            {live ? 'tailing' : 'waiting'}
          </span>
        </div>
      </div>
      <div ref={scrollRef} className="scroll" style={{ flex: 1, overflow: 'auto', fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.5 }}>
        {events.map((event, index) => (
          <div
            key={event.id}
            style={{
              padding: '6px 16px',
              borderBottom: '1px solid var(--line-hair)',
              display: 'flex',
              gap: 8,
              cursor: event.scn ? 'pointer' : 'default',
              opacity: index > 3 ? 0.9 - index * 0.02 : 1,
              transition: 'opacity .2s',
              background: index === 0 ? 'color-mix(in oklch, var(--accent) 7%, transparent)' : 'transparent',
            }}
            onClick={() => event.scn && onSelect(event.scn)}
          >
            <span style={{ color: 'var(--fg-4)', minWidth: 52 }}>{event.t}</span>
            <span style={{ color: event.color, minWidth: 62 }}>{event.tag}</span>
            <span style={{ color: 'var(--fg-2)', flex: 1 }}>{event.msg}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  commands: CommandItem[]
}

export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    setQuery('')
    setIndex(0)
    window.setTimeout(() => inputRef.current?.focus(), 20)
  }, [open])

  const filtered = useMemo(() => {
    const normalized = query.toLowerCase().trim()
    return commands.filter(
      (command) =>
        !normalized ||
        command.label.toLowerCase().includes(normalized) ||
        (command.keywords ?? '').toLowerCase().includes(normalized),
    )
  }, [commands, query])

  useEffect(() => {
    if (!open) {
      return
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      } else if (event.key === 'ArrowDown') {
        setIndex((current) => Math.min(filtered.length - 1, current + 1))
        event.preventDefault()
      } else if (event.key === 'ArrowUp') {
        setIndex((current) => Math.max(0, current - 1))
        event.preventDefault()
      } else if (event.key === 'Enter') {
        const command = filtered[index]
        if (command) {
          command.run()
          onClose()
        }
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [filtered, index, onClose, open])

  if (!open) {
    return null
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,.55)',
        zIndex: 100,
        display: 'flex',
        justifyContent: 'center',
        paddingTop: '12vh',
        backdropFilter: 'blur(4px)',
      }}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        style={{
          width: 560,
          maxHeight: 420,
          background: 'var(--bg-1)',
          border: '1px solid var(--line-1)',
          borderRadius: 'var(--r-3)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 24px 80px rgba(0,0,0,.6)',
        }}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setIndex(0)
          }}
          placeholder="type a command or scenario…"
          style={{
            padding: '16px 20px',
            background: 'transparent',
            border: 'none',
            outline: 'none',
            color: 'var(--fg-1)',
            fontFamily: 'var(--font-display)',
            fontSize: 15,
            borderBottom: '1px solid var(--line-hair)',
          }}
        />
        <div className="scroll" style={{ overflow: 'auto', flex: 1 }}>
          {filtered.map((command, itemIndex) => (
            <div
              key={command.id}
              onMouseEnter={() => setIndex(itemIndex)}
              onClick={() => {
                command.run()
                onClose()
              }}
              style={{
                padding: '10px 20px',
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                background: itemIndex === index ? 'color-mix(in oklch, var(--accent) 14%, var(--bg-1))' : 'transparent',
                cursor: 'pointer',
                borderLeft: itemIndex === index ? '2px solid var(--accent)' : '2px solid transparent',
              }}
            >
              <span
                className="mono"
                style={{ fontSize: 9.5, letterSpacing: 0.6, color: command.kindColor ?? 'var(--fg-4)', minWidth: 56, textTransform: 'uppercase' }}
              >
                {command.kind}
              </span>
              <span style={{ fontSize: 13, color: 'var(--fg-1)', flex: 1 }}>{command.label}</span>
              {command.hint ? (
                <span className="mono" style={{ fontSize: 10, color: 'var(--fg-4)' }}>
                  {command.hint}
                </span>
              ) : null}
            </div>
          ))}
          {!filtered.length ? <div style={{ padding: 20, color: 'var(--fg-4)', fontSize: 12 }}>no matches.</div> : null}
        </div>
      </div>
    </div>
  )
}

interface SettingsDrawerProps {
  open: boolean
  onClose: () => void
  mode: UIMode
  backend: BackendSettings | null
  compute: ComputeSettings | null
  mcp: MCPSettings | null
  limits: LimitSettings | null
  cockpit: CockpitSettings
  presets: BackendPreset[]
  devices: ComputeDevice[]
  devicesWarning: string | null
  gatewayEnabled: boolean
  onSaveBackend: (patch: Partial<BackendSettings>) => Promise<void>
  onSaveCompute: (patch: Partial<ComputeSettings>) => Promise<void>
  onSaveMcp: (patch: Partial<MCPSettings>) => Promise<void>
  onSaveContext: (maxContextTokens: number) => Promise<void>
  onPatchCockpit: (patch: Partial<CockpitSettings>) => void
  onToggleGateway?: (enabled: boolean) => Promise<void>
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--line-hair)' }}>
      <div className="mono" style={{ fontSize: 9.5, letterSpacing: 1.2, color: 'var(--fg-4)', marginBottom: 12 }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0', gap: 16 }}>
      <span style={{ fontSize: 12.5, color: 'var(--fg-2)' }}>{label}</span>
      {children}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  background: 'var(--bg-inset)',
  border: '1px solid var(--line-1)',
  borderRadius: 'var(--r-1)',
  color: 'var(--fg-1)',
  fontFamily: 'var(--font-mono)',
  fontSize: 11,
  padding: '5px 8px',
  minWidth: 160,
}

function TextInput({
  value,
  onCommit,
  placeholder,
  width,
}: {
  value: string
  onCommit: (value: string) => void
  placeholder?: string
  width?: number
}) {
  const [local, setLocal] = useState(value)
  useEffect(() => setLocal(value), [value])
  return (
    <input
      value={local}
      onChange={(event) => setLocal(event.target.value)}
      onBlur={() => local !== value && onCommit(local)}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          ;(event.target as HTMLInputElement).blur()
        }
      }}
      placeholder={placeholder}
      style={{ ...inputStyle, width: width ?? 180 }}
    />
  )
}

function NumberInput({
  value,
  onCommit,
  min,
  max,
  step,
  width,
}: {
  value: number | null
  onCommit: (value: number | null) => void
  min?: number
  max?: number
  step?: number
  width?: number
}) {
  const [local, setLocal] = useState<string>(value == null ? '' : String(value))
  useEffect(() => setLocal(value == null ? '' : String(value)), [value])
  return (
    <input
      type="number"
      value={local}
      min={min}
      max={max}
      step={step}
      onChange={(event) => setLocal(event.target.value)}
      onBlur={() => {
        const parsed = local === '' ? null : Number(local)
        if (parsed !== value && (parsed == null || Number.isFinite(parsed))) {
          onCommit(parsed)
        }
      }}
      style={{ ...inputStyle, width: width ?? 120 }}
    />
  )
}

function SelectInput<T extends string>({
  value,
  options,
  onChange,
  width,
}: {
  value: T
  options: Array<{ value: T; label: string }>
  onChange: (value: T) => void
  width?: number
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value as T)}
      style={{ ...inputStyle, width: width ?? 180 }}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

function Seg({
  value,
  options,
  onChange,
}: {
  value: string
  options: string[]
  onChange: (value: string) => void
}) {
  return (
    <div style={{ display: 'flex', background: 'var(--bg-inset)', borderRadius: 'var(--r-1)', padding: 2 }}>
      {options.map((option) => (
        <button
          key={option}
          onClick={() => onChange(option)}
          style={{
            padding: '4px 10px',
            background: value === option ? 'var(--accent)' : 'transparent',
            color: value === option ? '#fff' : 'var(--fg-2)',
            border: 'none',
            borderRadius: 'calc(var(--r-1) - 2px)',
            cursor: 'pointer',
            fontFamily: 'var(--font-mono)',
            fontSize: 10.5,
            letterSpacing: 0.3,
          }}
        >
          {option}
        </button>
      ))}
    </div>
  )
}

export function SettingsDrawer(props: SettingsDrawerProps) {
  const {
    open,
    onClose,
    mode,
    backend,
    compute,
    mcp,
    limits,
    cockpit,
    presets,
    devices,
    devicesWarning,
    gatewayEnabled,
    onSaveBackend,
    onSaveCompute,
    onSaveMcp,
    onSaveContext,
    onPatchCockpit,
    onToggleGateway,
  } = props

  if (!open) {
    return null
  }

  const presetMatch = presets.find(
    (preset) => backend && preset.base_url === backend.base_url && (preset.default_model === backend.model || preset.default_model === ''),
  )
  const activePresetName = presetMatch?.name ?? backend?.name ?? 'custom'

  return (
    <div
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', zIndex: 90, display: 'flex', justifyContent: 'flex-end' }}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        style={{ width: 420, height: '100%', background: 'var(--bg-1)', borderLeft: '1px solid var(--line-1)', display: 'flex', flexDirection: 'column' }}
      >
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--line-hair)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div className="editorial" style={{ fontSize: 20, color: 'var(--fg-1)' }}>
              Settings
            </div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--fg-4)', marginTop: 2 }}>
              {mode} · saves to .vaner/config.toml
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: 'var(--fg-3)', fontSize: 20, cursor: 'pointer' }}>
            ×
          </button>
        </div>
        <div className="scroll" style={{ flex: 1, overflow: 'auto' }}>
          <Section title="BACKEND">
            <Field label="Preset">
              <SelectInput
                value={activePresetName}
                options={[
                  { value: 'custom', label: 'custom' },
                  ...presets.map((preset) => ({ value: preset.name, label: preset.name })),
                ]}
                onChange={(name) => {
                  if (name === 'custom') return
                  const preset = presets.find((item) => item.name === name)
                  if (!preset) return
                  void onSaveBackend({
                    name: preset.name,
                    base_url: preset.base_url,
                    model: preset.default_model,
                    api_key_env: preset.api_key_env,
                  })
                }}
              />
            </Field>
            <Field label="Base URL">
              <TextInput
                value={backend?.base_url ?? ''}
                onCommit={(value) => void onSaveBackend({ base_url: value, name: 'custom' })}
                placeholder="http://127.0.0.1:11434/v1"
              />
            </Field>
            <Field label="Model">
              <TextInput
                value={backend?.model ?? ''}
                onCommit={(value) => void onSaveBackend({ model: value })}
                placeholder="qwen2.5-coder:7b"
              />
            </Field>
            <Field label="API key env var">
              <TextInput
                value={backend?.api_key_env ?? ''}
                onCommit={(value) => void onSaveBackend({ api_key_env: value })}
                placeholder="OPENAI_API_KEY"
              />
            </Field>
            <Field label="Prefer local backend">
              <Seg
                value={backend?.prefer_local ? 'on' : 'off'}
                options={['off', 'on']}
                onChange={(value) => void onSaveBackend({ prefer_local: value === 'on' })}
              />
            </Field>
            <Field label="Fallback enabled">
              <Seg
                value={backend?.fallback_enabled ? 'on' : 'off'}
                options={['off', 'on']}
                onChange={(value) => void onSaveBackend({ fallback_enabled: value === 'on' })}
              />
            </Field>
            {backend?.fallback_enabled ? (
              <>
                <Field label="Fallback base URL">
                  <TextInput
                    value={backend?.fallback_base_url ?? ''}
                    onCommit={(value) => void onSaveBackend({ fallback_base_url: value })}
                    placeholder="https://api.openai.com/v1"
                  />
                </Field>
                <Field label="Fallback model">
                  <TextInput
                    value={backend?.fallback_model ?? ''}
                    onCommit={(value) => void onSaveBackend({ fallback_model: value })}
                  />
                </Field>
                <Field label="Remote budget / hour">
                  <NumberInput
                    value={backend?.remote_budget_per_hour ?? 60}
                    onCommit={(value) => value != null && void onSaveBackend({ remote_budget_per_hour: value })}
                    min={0}
                  />
                </Field>
              </>
            ) : null}
          </Section>

          <Section title="COMPUTE">
            <Field label="Device">
              <SelectInput
                value={compute?.device ?? 'cpu'}
                options={[
                  { value: 'auto', label: 'auto' },
                  ...devices.map((device) => ({ value: device.id, label: device.label })),
                ]}
                onChange={(device) => void onSaveCompute({ device })}
              />
            </Field>
            {devicesWarning ? (
              <div style={{ fontSize: 10.5, color: 'var(--amber)', padding: '0 0 4px', fontFamily: 'var(--font-mono)' }}>
                {devicesWarning}
              </div>
            ) : null}
            <Field label={`CPU fraction · ${((compute?.cpu_fraction ?? 0) * 100).toFixed(0)}%`}>
              <input
                type="range"
                min="0.05"
                max="1"
                step="0.05"
                value={compute?.cpu_fraction ?? 0.2}
                onChange={(event) => void onSaveCompute({ cpu_fraction: Number(event.target.value) })}
                style={{ width: 180 }}
              />
            </Field>
            <Field label={`GPU mem fraction · ${((compute?.gpu_memory_fraction ?? 0) * 100).toFixed(0)}%`}>
              <input
                type="range"
                min="0.05"
                max="1"
                step="0.05"
                value={compute?.gpu_memory_fraction ?? 0.5}
                onChange={(event) => void onSaveCompute({ gpu_memory_fraction: Number(event.target.value) })}
                style={{ width: 180 }}
              />
            </Field>
            <Field label="Idle only">
              <Seg
                value={compute?.idle_only ? 'on' : 'off'}
                options={['off', 'on']}
                onChange={(value) => void onSaveCompute({ idle_only: value === 'on' })}
              />
            </Field>
            <Field label="Exploration concurrency">
              <NumberInput
                value={compute?.exploration_concurrency ?? 4}
                onCommit={(value) => value != null && void onSaveCompute({ exploration_concurrency: value })}
                min={1}
                max={32}
              />
            </Field>
            <Field label="Max precompute parallel">
              <NumberInput
                value={compute?.max_parallel_precompute ?? 1}
                onCommit={(value) => value != null && void onSaveCompute({ max_parallel_precompute: value })}
                min={1}
                max={16}
              />
            </Field>
            <Field label="Max cycle seconds">
              <NumberInput
                value={compute?.max_cycle_seconds ?? 300}
                onCommit={(value) => value != null && void onSaveCompute({ max_cycle_seconds: value })}
                min={0}
              />
            </Field>
            <Field label="Max session minutes">
              <NumberInput
                value={compute?.max_session_minutes ?? null}
                onCommit={(value) => void onSaveCompute({ max_session_minutes: value })}
                min={0}
              />
            </Field>
          </Section>

          <Section title="CONTEXT">
            <Field label="Max context tokens">
              <NumberInput
                value={limits?.max_context_tokens ?? 4096}
                onCommit={(value) => value != null && void onSaveContext(value)}
                min={256}
                max={1_000_000}
                step={256}
              />
            </Field>
            <Field label="Top-k streamed (cockpit only)">
              <NumberInput
                value={cockpit.topK}
                onCommit={(value) => value != null && onPatchCockpit({ topK: value })}
                min={3}
                max={40}
              />
            </Field>
          </Section>

          <Section title="MCP">
            <Field label="Transport">
              <Seg
                value={mcp?.transport ?? 'stdio'}
                options={['stdio', 'sse']}
                onChange={(value) => void onSaveMcp({ transport: value as MCPSettings['transport'] })}
              />
            </Field>
            <Field label="HTTP host">
              <TextInput
                value={mcp?.http_host ?? '127.0.0.1'}
                onCommit={(value) => void onSaveMcp({ http_host: value })}
              />
            </Field>
            <Field label="HTTP port">
              <NumberInput
                value={mcp?.http_port ?? 8472}
                onCommit={(value) => value != null && void onSaveMcp({ http_port: value })}
                min={1024}
                max={65535}
              />
            </Field>
          </Section>

          <Section title="APPEARANCE">
            <Field label="Dense mode">
              <Seg
                value={cockpit.density}
                options={['relaxed', 'dense']}
                onChange={(value) => onPatchCockpit({ density: value as CockpitSettings['density'] })}
              />
            </Field>
            <Field label="Accent">
              <Seg
                value={cockpit.accent}
                options={['violet', 'amber', 'teal']}
                onChange={(value) => onPatchCockpit({ accent: value as CockpitSettings['accent'] })}
              />
            </Field>
            <Field label="Reduce motion">
              <Seg
                value={cockpit.reduceMotion ? 'on' : 'off'}
                options={['off', 'on']}
                onChange={(value) => onPatchCockpit({ reduceMotion: value === 'on' })}
              />
            </Field>
            {mode === 'proxy' && onToggleGateway ? (
              <Field label="Gateway">
                <Seg
                  value={gatewayEnabled ? 'enabled' : 'disabled'}
                  options={['enabled', 'disabled']}
                  onChange={(value) => void onToggleGateway(value === 'enabled')}
                />
              </Field>
            ) : null}
          </Section>
        </div>
      </div>
    </div>
  )
}

interface MismatchBannerProps {
  onReload: () => void
}

export function MismatchBanner({ onReload }: MismatchBannerProps) {
  return (
    <div
      style={{
        position: 'fixed',
        bottom: 20,
        left: '50%',
        transform: 'translateX(-50%)',
        background: 'var(--bg-1)',
        border: '1px solid var(--amber)',
        color: 'var(--amber)',
        borderRadius: 'var(--r-2)',
        padding: '10px 16px',
        zIndex: 120,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        boxShadow: '0 10px 40px rgba(0,0,0,.4)',
      }}
      role="alert"
    >
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
        Cockpit bundle out of date — the running server serves an older build.
      </span>
      <button
        onClick={onReload}
        style={{
          background: 'transparent',
          border: '1px solid var(--amber)',
          color: 'var(--amber)',
          padding: '4px 10px',
          borderRadius: 'var(--r-1)',
          fontFamily: 'var(--font-mono)',
          fontSize: 10.5,
          cursor: 'pointer',
        }}
      >
        reload
      </button>
    </div>
  )
}
