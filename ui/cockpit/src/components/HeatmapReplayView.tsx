import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { adaptScenario } from '../api/adapt'
import { getHeatmapReplay, streamHeatmapReplay } from '../api/client'
import type { PipelineEvent } from '../api/usePipelineEvents'
import {
  buildScenarioHeatmapData,
  filterEventsByType,
  normalizeLiveWorkHeatmapEvents,
  scenarioStateAtCursor,
  type HeatmapEvent,
  type HeatmapEventType,
  type HeatmapGroupBy,
  type HeatmapRow,
  type HeatmapSortBy,
} from '../lib/heatmap'
import type { UIScenario } from '../types'

type TimeRange = '5m' | '15m' | '1h' | 'session'
type ReplaySpeed = 0.5 | 1 | 2 | 4

const TIME_RANGES: Array<{ id: TimeRange; label: string; ms: number }> = [
  { id: '5m', label: '5m', ms: 5 * 60_000 },
  { id: '15m', label: '15m', ms: 15 * 60_000 },
  { id: '1h', label: '1h', ms: 60 * 60_000 },
  { id: 'session', label: 'session', ms: 4 * 60 * 60_000 },
]
const EVENT_TYPES: HeatmapEventType[] = ['work', 'model', 'tool', 'file', 'test', 'error', 'risk', 'context', 'user', 'suggestion']
const LEFT_WIDTH = 300
const TOP_HEIGHT = 44
const ROW_HEIGHT = 32
const BOTTOM_HEIGHT = 46

interface HeatmapReplayViewProps {
  scenarios: UIScenario[]
  events: PipelineEvent[]
  selectedScenarioId: string | null
  onSelectScenario: (id: string) => void
  onScenarioFeedback: (id: string, result: 'useful' | 'partial' | 'irrelevant') => void
  onPinScenario: (id: string) => void
  onOpenScenarioMap: () => void
  onOpenEvidence: () => void
}

type HoverState =
  | { kind: 'cell'; x: number; y: number; row: HeatmapRow; sampleIndex: number; timeMs: number; event?: HeatmapEvent }
  | { kind: 'event'; x: number; y: number; row: HeatmapRow; event: HeatmapEvent }
  | null

export function HeatmapReplayView({
  scenarios,
  events,
  selectedScenarioId,
  onSelectScenario,
  onScenarioFeedback,
  onPinScenario,
  onOpenScenarioMap,
  onOpenEvidence,
}: HeatmapReplayViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 1000, h: 620 })
  const [range, setRange] = useState<TimeRange>('15m')
  const [groupBy, setGroupBy] = useState<HeatmapGroupBy>('cluster')
  const [sortBy, setSortBy] = useState<HeatmapSortBy>('active')
  const [showStale, setShowStale] = useState(false)
  const [showLowConfidence, setShowLowConfidence] = useState(true)
  const [onlyPinned, setOnlyPinned] = useState(false)
  const [onlyWithEvents, setOnlyWithEvents] = useState(false)
  const [enabledEvents, setEnabledEvents] = useState<Set<HeatmapEventType>>(() => new Set(EVENT_TYPES))
  const [mode, setMode] = useState<'live' | 'replay'>('live')
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<ReplaySpeed>(1)
  const [now, setNow] = useState(() => Date.now())
  const [cursor, setCursor] = useState(() => Date.now())
  const [hover, setHover] = useState<HoverState>(null)
  const [selectedEvent, setSelectedEvent] = useState<HeatmapEvent | null>(null)
  const [payload, setPayload] = useState<Awaited<ReturnType<typeof getHeatmapReplay>> | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const element = wrapRef.current
    if (!element) return
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setSize({ w: Math.max(680, entry.contentRect.width), h: Math.max(420, entry.contentRect.height) })
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => {
      const next = Date.now()
      setNow(next)
      if (mode === 'live') {
        setCursor(next)
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [mode])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const toTs = Date.now()
        const fromTs = toTs - rangeMsFor(range)
        const next = await getHeatmapReplay({ fromTs, toTs, limit: 140 })
        if (cancelled) return
        setPayload(next)
        setError(null)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      }
    }
    if (mode !== 'replay') return
    void load()
    return () => {
      cancelled = true
    }
  }, [mode, range])

  useEffect(() => {
    if (mode !== 'live') return
    const source = streamHeatmapReplay({ rangeMs: rangeMsFor(range), limit: 140 })
    const handleMessage = (event: MessageEvent<string>) => {
      try {
        setPayload(JSON.parse(event.data))
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    }
    source.addEventListener('replay_snapshot', handleMessage as EventListener)
    source.onmessage = handleMessage
    source.onerror = () => {
      setError('Heatmap stream disconnected; retrying...')
    }
    return () => {
      source.removeEventListener('replay_snapshot', handleMessage as EventListener)
      source.close()
    }
  }, [mode, range])

  useEffect(() => {
    if (!playing || mode !== 'replay') return
    const timer = window.setInterval(() => {
      setCursor((current) => {
        const rangeMs = rangeMsFor(range)
        const end = now
        const next = current + 1000 * speed
        if (next >= end) {
          setPlaying(false)
          setMode('live')
          return end
        }
        return Math.max(end - rangeMs, next)
      })
    }, 1000)
    return () => window.clearInterval(timer)
  }, [mode, now, playing, range, speed])

  const replayScenarios = useMemo(() => (payload?.scenarios ?? []).map(adaptScenario), [payload?.scenarios])
  const replayEvents = useMemo(() => normalizeLiveWorkHeatmapEvents(payload?.events ?? []), [payload?.events])

  const rows = useMemo(
    () =>
      buildScenarioHeatmapData(replayScenarios, events, {
        now,
        rangeMs: rangeMsFor(range),
        activeScenarioId: selectedScenarioId,
        showStale,
        showLowConfidence,
        onlyPinned,
        onlyWithEvents,
        eventTypes: enabledEvents,
        groupBy,
        sortBy,
        buckets: Math.min(180, Math.max(72, Math.floor((size.w - LEFT_WIDTH) / 8))),
        samples: payload?.samples ?? [],
        events: replayEvents,
      }),
    [enabledEvents, events, groupBy, now, onlyPinned, onlyWithEvents, payload?.samples, range, replayEvents, replayScenarios, selectedScenarioId, showLowConfidence, showStale, size.w, sortBy],
  )

  const visibleRows = useMemo(() => rows.slice(0, Math.max(1, Math.floor((size.h - TOP_HEIGHT - BOTTOM_HEIGHT) / ROW_HEIGHT))), [rows, size.h])
  const selectedRow = selectedScenarioId ? rows.find((row) => row.scenario.id === selectedScenarioId) ?? null : null
  const selectedSample = selectedRow ? scenarioStateAtCursor(selectedRow, cursor) : null
  const filteredEvents = useMemo(() => filterEventsByType(rows.flatMap((row) => row.events), enabledEvents), [enabledEvents, rows])

  const hitTest = useCallback(
    (clientX: number, clientY: number): HoverState => {
      const rect = canvasRef.current?.getBoundingClientRect()
      if (!rect) return null
      const x = clientX - rect.left
      const y = clientY - rect.top
      const plot = plotRect(size)
      if (x < 0 || y < 0 || x > size.w || y > size.h) return null
      const rowIndex = Math.floor((y - TOP_HEIGHT) / ROW_HEIGHT)
      const row = visibleRows[rowIndex]
      if (!row) return null
      const rangeMs = rangeMsFor(range)
      const start = now - rangeMs
      for (const event of row.events) {
        if (!enabledEvents.has(event.type)) continue
        const ex = LEFT_WIDTH + ((event.timestamp - start) / rangeMs) * plot.w
        const ey = TOP_HEIGHT + rowIndex * ROW_HEIGHT + ROW_HEIGHT / 2
        const radius = 4 + event.magnitude * 7
        if (Math.hypot(x - ex, y - ey) <= radius + 3) {
          return { kind: 'event', x, y, row, event }
        }
      }
      if (x >= LEFT_WIDTH && x <= LEFT_WIDTH + plot.w) {
        const timeMs = start + ((x - LEFT_WIDTH) / plot.w) * rangeMs
        return { kind: 'cell', x, y, row, sampleIndex: nearestSampleIndex(row, timeMs), timeMs }
      }
      return { kind: 'cell', x, y, row, sampleIndex: Math.max(0, row.samples.length - 1), timeMs: now }
    },
    [enabledEvents, now, range, size, visibleRows],
  )

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.floor(size.w * dpr)
    canvas.height = Math.floor(size.h * dpr)
    canvas.style.width = `${size.w}px`
    canvas.style.height = `${size.h}px`
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    drawHeatmap(ctx, {
      width: size.w,
      height: size.h,
      rows: visibleRows,
      now,
      rangeMs: rangeMsFor(range),
      cursor,
      mode,
      selectedScenarioId,
      eventTypes: enabledEvents,
      colors: canvasColors(canvas),
    })
  }, [cursor, enabledEvents, mode, now, range, selectedScenarioId, size, visibleRows])

  const jumpLive = () => {
    setMode('live')
    setPlaying(false)
    setCursor(Date.now())
  }

  return (
    <div style={viewStyle}>
      <div style={toolbarStyle}>
        <div>
          <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10, letterSpacing: 1.2 }}>HEATMAP REPLAY</div>
          <div style={{ color: 'var(--fg-1)', fontSize: 17, fontFamily: 'var(--font-display)', marginTop: 2 }}>
            Scenario intensity over time
          </div>
        </div>
        <ReplayControls
          mode={mode}
          playing={playing}
          speed={speed}
          range={range}
          onPlay={() => {
            setMode('replay')
            setPlaying((value) => !value)
          }}
          onJumpLive={jumpLive}
          onSpeed={setSpeed}
          onRange={(next) => {
            setRange(next)
            setCursor(Date.now() - rangeMsFor(next) * 0.25)
            setMode('replay')
          }}
        />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Select label="Group" value={groupBy} onChange={(value) => setGroupBy(value as HeatmapGroupBy)} options={['cluster', 'status', 'file-area', 'type']} />
          <Select label="Sort" value={sortBy} onChange={(value) => setSortBy(value as HeatmapSortBy)} options={['active', 'readiness', 'relevance', 'confidence', 'recent']} />
          <Toggle checked={showStale} onChange={setShowStale} label="stale" />
          <Toggle checked={!showLowConfidence} onChange={(value) => setShowLowConfidence(!value)} label="hide low conf" />
          <Toggle checked={onlyPinned} onChange={setOnlyPinned} label="pinned" />
          <Toggle checked={onlyWithEvents} onChange={setOnlyWithEvents} label="with events" />
        </div>
      </div>

      <div style={eventToggleStyle}>
        {EVENT_TYPES.map((type) => (
          <button
            key={type}
            type="button"
            onClick={() => {
              setEnabledEvents((current) => {
                const next = new Set(current)
                if (next.has(type)) next.delete(type)
                else next.add(type)
                return next
              })
            }}
            style={eventTypeButtonStyle(enabledEvents.has(type), type)}
          >
            {type}
          </button>
        ))}
      </div>

      <div style={mainStyle}>
        <div ref={wrapRef} style={canvasWrapStyle}>
          {error ? (
            <div role="alert" style={emptyStyle}>
              <div style={{ fontFamily: 'var(--font-display)', color: 'var(--fg-1)', fontSize: 16 }}>Could not load heatmap replay</div>
              <div style={{ marginTop: 8 }}>{error}</div>
            </div>
          ) : !rows.length || !payload?.samples?.length ? (
            <div style={emptyStyle}>
              <div style={{ fontFamily: 'var(--font-display)', color: 'var(--fg-1)', fontSize: 16 }}>No recorded heatmap samples yet</div>
              <div style={{ marginTop: 8 }}>Heatmap data will appear as real scenario score samples are recorded.</div>
            </div>
          ) : null}
          <canvas
            ref={canvasRef}
            role="img"
            aria-label="Scenario replay heatmap"
            onPointerMove={(event) => setHover(hitTest(event.clientX, event.clientY))}
            onPointerLeave={() => setHover(null)}
            onClick={(event) => {
              const hit = hitTest(event.clientX, event.clientY)
              if (!hit) return
              onSelectScenario(hit.row.scenario.id)
              if (hit.kind === 'event') {
                setSelectedEvent(hit.event)
              } else {
                setSelectedEvent(null)
              }
            }}
            onDoubleClick={jumpLive}
            onPointerDown={(event) => {
              const rect = canvasRef.current?.getBoundingClientRect()
              if (!rect) return
              const x = event.clientX - rect.left
              if (x < LEFT_WIDTH) return
              const plot = plotRect(size)
              const nextCursor = now - rangeMsFor(range) + ((x - LEFT_WIDTH) / plot.w) * rangeMsFor(range)
              setCursor(Math.max(now - rangeMsFor(range), Math.min(now, nextCursor)))
              setMode('replay')
              setPlaying(false)
            }}
            style={{ display: 'block', cursor: 'crosshair' }}
          />
          {hover ? <HeatmapTooltip hover={hover} rangeMs={rangeMsFor(range)} now={now} /> : null}
        </div>
        <HeatmapInspector
          row={selectedRow}
          sample={selectedSample}
          event={selectedEvent}
          events={filteredEvents}
          cursor={cursor}
          onOpenScenarioMap={onOpenScenarioMap}
          onOpenEvidence={onOpenEvidence}
          onScenarioFeedback={onScenarioFeedback}
          onPinScenario={onPinScenario}
        />
      </div>
    </div>
  )
}

function ReplayControls({
  mode,
  playing,
  speed,
  range,
  onPlay,
  onJumpLive,
  onSpeed,
  onRange,
}: {
  mode: 'live' | 'replay'
  playing: boolean
  speed: ReplaySpeed
  range: TimeRange
  onPlay: () => void
  onJumpLive: () => void
  onSpeed: (speed: ReplaySpeed) => void
  onRange: (range: TimeRange) => void
}) {
  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      <button type="button" onClick={onPlay} style={controlButtonStyle(true)}>{playing ? 'pause' : 'play'}</button>
      <button type="button" onClick={onJumpLive} style={controlButtonStyle(mode === 'live')}>live</button>
      {([0.5, 1, 2, 4] as ReplaySpeed[]).map((item) => (
        <button key={item} type="button" onClick={() => onSpeed(item)} style={controlButtonStyle(speed === item)}>{item}x</button>
      ))}
      <div style={{ width: 1, height: 20, background: 'var(--line-1)' }} />
      {TIME_RANGES.map((item) => (
        <button key={item.id} type="button" onClick={() => onRange(item.id)} style={controlButtonStyle(range === item.id)}>{item.label}</button>
      ))}
    </div>
  )
}

function HeatmapTooltip({ hover, now, rangeMs }: { hover: NonNullable<HoverState>; now: number; rangeMs: number }) {
  const sample = hover.kind === 'cell'
    ? hover.row.samples[hover.sampleIndex] ?? scenarioStateAtCursor(hover.row, hover.timeMs)
    : scenarioStateAtCursor(hover.row, hover.event.timestamp)
  const event = hover.kind === 'event' ? hover.event : undefined
  const nearby = hover.row.events.filter((item) => Math.abs(item.timestamp - sample.timestamp) < rangeMs / 40).slice(0, 3)
  return (
    <div style={{ ...tooltipStyle, left: Math.min(hover.x + 14, 720), top: Math.max(12, hover.y - 12) }}>
      <div className="mono" style={{ color: 'var(--accent)', fontSize: 10 }}>{formatClock(event?.timestamp ?? sample.timestamp, now)}</div>
      <div style={{ color: 'var(--fg-1)', fontSize: 12.5, marginTop: 5 }}>{hover.row.scenario.title}</div>
      <div className="mono" style={{ color: 'var(--fg-3)', fontSize: 10, marginTop: 6 }}>
        intensity {Math.round(sample.intensity * 100)} · rel {Math.round(sample.relevance * 100)} · ready {Math.round(sample.readiness * 100)} · conf {Math.round(sample.confidence * 100)}
      </div>
      {event ? <div style={{ color: 'var(--fg-2)', fontSize: 12, marginTop: 8 }}>{event.title}</div> : null}
      {!event && nearby.length ? <div style={{ color: 'var(--fg-4)', fontSize: 11, marginTop: 7 }}>{nearby.map((item) => item.title).join(' · ')}</div> : null}
    </div>
  )
}

function HeatmapInspector({
  row,
  sample,
  event,
  events,
  cursor,
  onOpenScenarioMap,
  onOpenEvidence,
  onScenarioFeedback,
  onPinScenario,
}: {
  row: HeatmapRow | null
  sample: ReturnType<typeof scenarioStateAtCursor> | null
  event: HeatmapEvent | null
  events: HeatmapEvent[]
  cursor: number
  onOpenScenarioMap: () => void
  onOpenEvidence: () => void
  onScenarioFeedback: (id: string, result: 'useful' | 'partial' | 'irrelevant') => void
  onPinScenario: (id: string) => void
}) {
  if (!row) {
    return (
      <aside style={inspectorStyle}>
        <div className="mono" style={eyebrow}>INSPECTOR</div>
        <div style={{ color: 'var(--fg-4)', fontSize: 12, marginTop: 10 }}>Click a row or bubble to inspect replay state.</div>
      </aside>
    )
  }
  const latest = row.events.filter((item) => item.timestamp <= cursor).slice(-6).reverse()
  return (
    <aside style={inspectorStyle}>
      <div className="mono" style={eyebrow}>{event ? 'EVENT' : 'SCENARIO'}</div>
      <div style={{ fontFamily: 'var(--font-display)', color: 'var(--fg-1)', fontSize: 17, lineHeight: 1.25, marginTop: 6 }}>
        {event?.title ?? row.scenario.title}
      </div>
      <div className="mono" style={{ color: row.active ? 'var(--ok)' : 'var(--fg-4)', fontSize: 10.5, marginTop: 7 }}>
        {row.status} · intensity {Math.round((sample?.intensity ?? row.currentIntensity) * 100)} · {formatClock(event?.timestamp ?? cursor, Date.now())}
      </div>
      {event ? (
        <Panel title="Event Detail">
          <div>{event.description || event.title}</div>
          {event.paths.length ? <MetaLine label="paths" value={event.paths.slice(0, 4).join(' · ')} /> : null}
          <MetaLine label="type" value={`${event.type} · ${event.severity}`} />
        </Panel>
      ) : (
        <>
          <Panel title="Score">
            <ScoreGrid
              values={[
                ['readiness', sample?.readiness ?? 0],
                ['relevance', row.scenario.relevance],
                ['confidence', row.scenario.confidence],
                ['freshness', sample?.freshness ?? 0],
              ]}
            />
          </Panel>
          <Panel title="Why Hot Now">
            {row.active ? 'This row is selected or marked active. Recent linked events and readiness are keeping it near the top.' : row.events.length ? 'Recent linked events increased the row intensity.' : row.scenario.visibilityReason || row.scenario.reason || 'Current scenario score is derived from relevance, readiness, confidence, and freshness.'}
          </Panel>
        </>
      )}
      <Panel title="Latest Events">
        {latest.length ? latest.map((item) => (
          <div key={item.id} style={eventLineStyle}>
            <span className="mono" style={{ color: eventColor(item.type), fontSize: 10 }}>{item.type}</span>
            <span style={{ color: 'var(--fg-2)' }}>{item.title}</span>
          </div>
        )) : <span style={{ color: 'var(--fg-4)' }}>No linked events before this cursor.</span>}
      </Panel>
      <Panel title="Files">
        {row.scenario.entities.length ? row.scenario.entities.slice(0, 6).map((item) => <MetaLine key={item} label="" value={item} />) : row.scenario.path || 'No file path recorded.'}
      </Panel>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
        <button type="button" onClick={() => onPinScenario(row.scenario.id)} style={controlButtonStyle(false)}>pin</button>
        <button type="button" onClick={() => onScenarioFeedback(row.scenario.id, 'useful')} style={controlButtonStyle(false)}>useful</button>
        <button type="button" onClick={() => onScenarioFeedback(row.scenario.id, 'irrelevant')} style={controlButtonStyle(false)}>suppress</button>
        <button type="button" onClick={onOpenScenarioMap} style={controlButtonStyle(true)}>scenario map</button>
        <button type="button" onClick={onOpenEvidence} style={controlButtonStyle(false)}>evidence</button>
      </div>
      {events.length > 200 ? <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10, marginTop: 12 }}>{events.length} visible events, dense stream aggregated in canvas.</div> : null}
    </aside>
  )
}

function drawHeatmap(
  ctx: CanvasRenderingContext2D,
  args: {
    width: number
    height: number
    rows: HeatmapRow[]
    now: number
    rangeMs: number
    cursor: number
    mode: 'live' | 'replay'
    selectedScenarioId: string | null
    eventTypes: Set<HeatmapEventType>
    colors: Record<string, string>
  },
) {
  ctx.clearRect(0, 0, args.width, args.height)
  ctx.fillStyle = args.colors.bg0
  ctx.fillRect(0, 0, args.width, args.height)
  const plot = plotRect({ w: args.width, h: args.height })
  const start = args.now - args.rangeMs

  ctx.strokeStyle = args.colors.grid
  ctx.lineWidth = 1
  for (let i = 0; i <= 6; i += 1) {
    const x = LEFT_WIDTH + (plot.w / 6) * i
    ctx.beginPath()
    ctx.moveTo(x, TOP_HEIGHT)
    ctx.lineTo(x, TOP_HEIGHT + args.rows.length * ROW_HEIGHT)
    ctx.stroke()
    ctx.fillStyle = args.colors.fg4
    ctx.font = '10px JetBrains Mono, monospace'
    ctx.fillText(formatClock(start + (args.rangeMs / 6) * i, args.now), x + 4, 24)
  }

  args.rows.forEach((row, rowIndex) => {
    const y = TOP_HEIGHT + rowIndex * ROW_HEIGHT
    const selected = row.scenario.id === args.selectedScenarioId
    ctx.fillStyle = selected ? args.colors.selectedRow : rowIndex % 2 ? args.colors.rowOdd : args.colors.rowEven
    ctx.fillRect(0, y, args.width, ROW_HEIGHT)
    if (row.active) {
      ctx.fillStyle = args.colors.ok
      ctx.fillRect(0, y + 4, 3, ROW_HEIGHT - 8)
    }
    if (row.scenario.pinned) {
      ctx.fillStyle = args.colors.amber
      ctx.beginPath()
      ctx.arc(14, y + ROW_HEIGHT / 2, 3, 0, Math.PI * 2)
      ctx.fill()
    }
    ctx.fillStyle = args.colors.fg2
    ctx.font = '11px Space Grotesk, sans-serif'
    ctx.fillText(truncate(row.scenario.title, 34), 26, y + 15)
    ctx.fillStyle = args.colors.fg4
    ctx.font = '10px JetBrains Mono, monospace'
    ctx.fillText(`${row.status} · ${Math.round(row.currentIntensity * 100)}`, 26, y + 27)

    const heatW = Math.max(4, Math.min(18, plot.w / 120))
    row.samples.forEach((sample) => {
      const x = LEFT_WIDTH + ((sample.timestamp - start) / args.rangeMs) * plot.w
      if (x < LEFT_WIDTH - heatW || x > LEFT_WIDTH + plot.w + heatW) return
      ctx.fillStyle = heatColor(sample.intensity, args.colors[`kind-${row.scenario.kind}`] || args.colors.accent, args.colors)
      ctx.fillRect(Math.max(LEFT_WIDTH, x - heatW / 2), y + 4, heatW, ROW_HEIGHT - 8)
    })
    ctx.strokeStyle = args.colors.grid
    ctx.beginPath()
    ctx.moveTo(0, y + ROW_HEIGHT)
    ctx.lineTo(args.width, y + ROW_HEIGHT)
    ctx.stroke()
  })

  args.rows.forEach((row, rowIndex) => {
    const y = TOP_HEIGHT + rowIndex * ROW_HEIGHT + ROW_HEIGHT / 2
    row.events.forEach((event) => {
      if (!args.eventTypes.has(event.type)) return
      const x = LEFT_WIDTH + ((event.timestamp - start) / args.rangeMs) * plot.w
      if (x < LEFT_WIDTH || x > LEFT_WIDTH + plot.w) return
      const future = args.mode === 'replay' && event.timestamp > args.cursor
      const radius = 4 + event.magnitude * 7
      ctx.globalAlpha = future ? 0.18 : 0.9
      ctx.fillStyle = eventColor(event.type)
      ctx.strokeStyle = event.severity === 'error' ? args.colors.err : args.colors.bg0
      ctx.lineWidth = event.severity === 'error' ? 2 : 1
      ctx.beginPath()
      ctx.arc(x, y, radius, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.globalAlpha = 1
    })
  })

  const cursorX = LEFT_WIDTH + ((args.cursor - start) / args.rangeMs) * plot.w
  ctx.strokeStyle = args.mode === 'live' ? args.colors.ok : args.colors.accent
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(cursorX, TOP_HEIGHT - 8)
  ctx.lineTo(cursorX, TOP_HEIGHT + args.rows.length * ROW_HEIGHT + 8)
  ctx.stroke()
  ctx.fillStyle = args.mode === 'live' ? args.colors.ok : args.colors.accent
  ctx.font = '10px JetBrains Mono, monospace'
  ctx.fillText(args.mode === 'live' ? 'LIVE' : 'REPLAY', Math.min(cursorX + 6, args.width - 64), TOP_HEIGHT - 13)
}

function rangeMsFor(range: TimeRange): number {
  return TIME_RANGES.find((item) => item.id === range)?.ms ?? 15 * 60_000
}

function plotRect(size: { w: number; h: number }) {
  return { x: LEFT_WIDTH, y: TOP_HEIGHT, w: Math.max(100, size.w - LEFT_WIDTH - 24), h: Math.max(100, size.h - TOP_HEIGHT - BOTTOM_HEIGHT) }
}

function nearestSampleIndex(row: HeatmapRow, timeMs: number): number {
  if (!row.samples.length) return 0
  let bestIndex = 0
  let bestDistance = Math.abs(row.samples[0].timestamp - timeMs)
  for (let index = 1; index < row.samples.length; index += 1) {
    const distance = Math.abs(row.samples[index].timestamp - timeMs)
    if (distance < bestDistance) {
      bestDistance = distance
      bestIndex = index
    }
  }
  return bestIndex
}

function canvasColors(canvas: HTMLCanvasElement): Record<string, string> {
  const styles = getComputedStyle(canvas)
  return {
    bg0: styles.getPropertyValue('--bg-0').trim() || '#111',
    bg1: styles.getPropertyValue('--bg-1').trim() || '#18181d',
    fg2: styles.getPropertyValue('--fg-2').trim() || '#ccc',
    fg4: styles.getPropertyValue('--fg-4').trim() || '#777',
    grid: styles.getPropertyValue('--line-hair').trim() || '#333',
    ok: styles.getPropertyValue('--ok').trim() || '#6cc76c',
    amber: styles.getPropertyValue('--amber').trim() || '#e6b656',
    err: styles.getPropertyValue('--err').trim() || '#d66',
    accent: styles.getPropertyValue('--accent').trim() || '#8cc',
    'kind-research': styles.getPropertyValue('--kind-research').trim() || '#ba86f0',
    'kind-explain': styles.getPropertyValue('--kind-explain').trim() || '#e6b656',
    'kind-change': styles.getPropertyValue('--kind-change').trim() || '#6ab7ff',
    'kind-debug': styles.getPropertyValue('--kind-debug').trim() || '#d76b5a',
    'kind-refactor': styles.getPropertyValue('--kind-refactor').trim() || '#74c6a2',
    selectedRow: 'rgba(120, 190, 180, 0.10)',
    rowOdd: 'rgba(255,255,255,0.018)',
    rowEven: 'rgba(255,255,255,0.006)',
  }
}

function heatColor(intensity: number, color: string, colors: Record<string, string>): string {
  const alpha = 0.08 + intensity * 0.74
  if (intensity > 0.82) return `color-mix(in oklch, ${colors.amber} ${Math.round(intensity * 72)}%, transparent)`
  return `color-mix(in oklch, ${color} ${Math.round(alpha * 100)}%, transparent)`
}

function eventColor(type: HeatmapEventType): string {
  return {
    work: '#6ab7ff',
    model: '#e6b656',
    tool: '#a36cc7',
    file: '#74c6a2',
    test: '#6cc76c',
    error: '#d76b5a',
    risk: '#e58b58',
    context: '#8db5ff',
    suggestion: '#ba86f0',
    user: '#d6d6d6',
  }[type]
}

function formatClock(timestamp: number, now: number): string {
  const delta = Math.round((timestamp - now) / 1000)
  if (Math.abs(delta) < 60) return `${delta}s`
  if (Math.abs(delta) < 3600) return `${Math.round(delta / 60)}m`
  return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function truncate(value: string, limit: number): string {
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <label className="mono" style={{ color: 'var(--fg-4)', fontSize: 10, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      {label}
      <select value={value} onChange={(event) => onChange(event.currentTarget.value)} style={selectStyle}>
        {options.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
    </label>
  )
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (checked: boolean) => void; label: string }) {
  return (
    <label className="mono" style={{ color: 'var(--fg-3)', fontSize: 10, display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.currentTarget.checked)} style={{ accentColor: 'var(--accent)' }} />
      {label}
    </label>
  )
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginTop: 16 }}>
      <div className="mono" style={eyebrow}>{title.toUpperCase()}</div>
      <div style={{ color: 'var(--fg-2)', fontSize: 12.5, lineHeight: 1.5 }}>{children}</div>
    </section>
  )
}

function ScoreGrid({ values }: { values: Array<[string, number]> }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
      {values.map(([label, value]) => (
        <div key={label} style={scoreCardStyle}>
          <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10 }}>{label}</div>
          <div style={{ color: 'var(--fg-1)', fontSize: 15, marginTop: 4 }}>{Math.round(value * 100)}</div>
        </div>
      ))}
    </div>
  )
}

function MetaLine({ label, value }: { label: string; value: string }) {
  return <div className="mono" style={{ color: 'var(--fg-4)', fontSize: 10.5, overflowWrap: 'anywhere', marginTop: 5 }}>{label ? `${label}: ` : ''}{value}</div>
}

const viewStyle: React.CSSProperties = {
  height: '100%',
  minHeight: 0,
  display: 'grid',
  gridTemplateRows: 'auto auto minmax(0, 1fr)',
  background: 'var(--bg-0)',
}
const toolbarStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'minmax(220px, 1fr) auto minmax(280px, 1fr)',
  gap: 14,
  alignItems: 'center',
  padding: '14px 18px 10px',
  borderBottom: '1px solid var(--line-1)',
  background: 'linear-gradient(180deg, var(--bg-1), var(--bg-0))',
}
const eventToggleStyle: React.CSSProperties = {
  display: 'flex',
  gap: 6,
  padding: '8px 18px',
  borderBottom: '1px solid var(--line-hair)',
  background: 'var(--bg-0)',
  overflowX: 'auto',
}
const mainStyle: React.CSSProperties = { minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 310px' }
const canvasWrapStyle: React.CSSProperties = { position: 'relative', minHeight: 0, overflow: 'hidden' }
const inspectorStyle: React.CSSProperties = {
  borderLeft: '1px solid var(--line-1)',
  background: 'var(--bg-1)',
  padding: 16,
  overflow: 'auto',
}
const emptyStyle: React.CSSProperties = {
  position: 'absolute',
  inset: 0,
  display: 'grid',
  placeContent: 'center',
  textAlign: 'center',
  color: 'var(--fg-4)',
  fontSize: 12,
  zIndex: 2,
  pointerEvents: 'none',
}
const tooltipStyle: React.CSSProperties = {
  position: 'absolute',
  zIndex: 4,
  width: 280,
  padding: 10,
  border: '1px solid var(--line-1)',
  borderRadius: 'var(--r-2)',
  background: 'color-mix(in oklch, var(--bg-1) 96%, transparent)',
  boxShadow: 'var(--shadow-md)',
  pointerEvents: 'none',
}
const selectStyle: React.CSSProperties = {
  background: 'var(--bg-2)',
  border: '1px solid var(--line-1)',
  borderRadius: 'var(--r-1)',
  color: 'var(--fg-2)',
  padding: '4px 6px',
  fontSize: 10,
}
const eyebrow: React.CSSProperties = { color: 'var(--fg-4)', fontSize: 10, letterSpacing: 1.1, marginBottom: 7 }
const scoreCardStyle: React.CSSProperties = {
  border: '1px solid var(--line-hair)',
  background: 'var(--bg-inset)',
  borderRadius: 'var(--r-2)',
  padding: 9,
}
const eventLineStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '54px minmax(0, 1fr)',
  gap: 8,
  padding: '6px 0',
  borderBottom: '1px solid var(--line-hair)',
  fontSize: 11.5,
}

function controlButtonStyle(active: boolean): React.CSSProperties {
  return {
    border: '1px solid var(--line-1)',
    background: active ? 'var(--accent-bg)' : 'var(--bg-2)',
    color: active ? 'var(--fg-1)' : 'var(--fg-3)',
    borderRadius: 'var(--r-1)',
    padding: '5px 8px',
    fontFamily: 'var(--font-mono)',
    fontSize: 10,
    cursor: 'pointer',
  }
}

function eventTypeButtonStyle(active: boolean, type: HeatmapEventType): React.CSSProperties {
  return {
    ...controlButtonStyle(active),
    borderColor: active ? eventColor(type) : 'var(--line-1)',
    color: active ? eventColor(type) : 'var(--fg-4)',
  }
}
