import {
  AlertTriangle,
  CheckCheck,
  CircleAlert,
  CircleCheck,
  CircleX,
  ExternalLink,
  Eye,
  Loader,
  PenLine,
  Sparkles,
} from 'lucide-react'
import { useState, type CSSProperties, type ReactNode } from 'react'
import { ktruUrl, type ItemStatus, type Zone } from './api'
import { Waves } from './Art'
import { T } from './texts'

const ZONE_ICONS = { high: CircleCheck, medium: CircleAlert, low: CircleX }

export function ZoneIcon({ zone, size = 15 }: { zone: Zone; size?: number }) {
  const Icon = ZONE_ICONS[zone] ?? CircleX
  return <Icon className={`zone-icon zone-${zone}`} size={size} aria-hidden="true" />
}

/** Percent + thin bar of the zone color; the zone is also said by the icon and its label. */
export function Confidence({ value, zone }: { value: number | null; zone: Zone }) {
  const percent = value ?? 0
  return (
    <div className="conf" title={`${T.drawer.similarity}: ${T.zone[zone]}`}>
      <div className="conf-top num">
        <ZoneIcon zone={zone} />
        {percent.toFixed(0)} %<span className="sr-only">, {T.zone[zone]}</span>
      </div>
      <div className="bar" aria-hidden="true">
        <span className={`zone-${zone}`} style={{ width: `${percent}%` }} />
      </div>
    </div>
  )
}

const STATUS_ICONS: Record<ItemStatus, typeof Eye> = {
  auto: Sparkles,
  needs_review: Eye,
  corrected: PenLine,
  accepted: CheckCheck,
  failed: AlertTriangle,
}

const STATUS_TONE: Record<ItemStatus, string> = {
  auto: 'high',
  needs_review: 'medium',
  corrected: '',
  accepted: 'high',
  failed: 'low',
}

export function StatusChip({ status }: { status: ItemStatus }) {
  const Icon = STATUS_ICONS[status]
  return (
    <span className={`chip ${STATUS_TONE[status]}`}>
      <Icon size={13} aria-hidden="true" />
      {T.itemStatus[status]}
    </span>
  )
}

const MARK_ICONS: Record<ItemStatus, typeof CircleCheck> = {
  auto: CircleCheck,
  accepted: CircleCheck,
  corrected: CircleCheck,
  needs_review: CircleAlert,
  failed: CircleX,
}

const MARK_TONE: Record<ItemStatus, string> = {
  auto: 'high',
  accepted: 'high',
  corrected: 'high',
  needs_review: 'medium',
  failed: 'low',
}

/** The round mark next to a code, like the green tick of a catalog position. */
export function StatusMark({ status, size = 24 }: { status: ItemStatus; size?: number }) {
  const Icon = MARK_ICONS[status]
  return (
    <span className={`mark ${MARK_TONE[status]}`} title={T.itemStatus[status]}>
      <Icon size={size} aria-hidden="true" />
      <span className="sr-only">{T.itemStatus[status]}</span>
    </span>
  )
}

export function JobStatusChip({ status }: { status: string }) {
  const tone = status === 'done' ? 'high' : status === 'failed' ? 'low' : ''
  return (
    <span className={`chip ${tone}`}>
      {status === 'processing' || status === 'queued' ? (
        <Loader size={12} aria-hidden="true" />
      ) : (
        <span className={`dot ${tone}`} aria-hidden="true" />
      )}
      {T.jobStatus[status] ?? status}
    </span>
  )
}

export function Skeleton({ height = 16, width = '100%', style }: {
  height?: number
  width?: number | string
  style?: CSSProperties
}) {
  return <div className="skeleton" style={{ height, width, ...style }} aria-hidden="true" />
}

export function EmptyState({ title, hint, children }: {
  title: string
  hint?: string
  children?: ReactNode
}) {
  return (
    <div className="empty">
      <Waves />
      <b>{title}</b>
      {hint && <span>{hint}</span>}
      {children}
    </div>
  )
}

/** The code as a link to the KTRU registry of the EIS; opens in a new tab. */
export function KtruCode({ code }: { code: string }) {
  const url = ktruUrl(code)
  if (!url) return <span className="code">{code}</span>
  return (
    <a
      className="code link"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title={T.results.openEis}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      {code}
      <ExternalLink size={12} aria-hidden="true" />
      <span className="sr-only">{T.results.openEis}</span>
    </a>
  )
}

const LONG_TEXT = 220

/** The source line of a position: a long one is cut to three lines and opens on demand. */
export function RawText({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > LONG_TEXT
  return (
    <div className="res-meta">
      <div className={long && !open ? 'raw-clamp' : undefined}>
        <span className="res-meta-label">{T.results.sourceText}:</span> {text}
      </div>
      {long && (
        <button type="button" className="text-link" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? T.results.hide : T.results.showFull}
        </button>
      )}
    </div>
  )
}

const RADIUS = 80
const CIRCLE = 2 * Math.PI * RADIUS

const MINI_RADIUS = 9
const MINI_CIRCLE = 2 * Math.PI * MINI_RADIUS

/** Small ring for lists; the percent itself is said in the text next to it. */
export function MiniRing({ percent, size = 20 }: { percent: number; size?: number }) {
  return (
    <svg className="mini-ring" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <circle className="ring-track" cx="12" cy="12" r={MINI_RADIUS} fill="none" strokeWidth="3" />
      <circle
        className="ring-value"
        cx="12"
        cy="12"
        r={MINI_RADIUS}
        fill="none"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray={MINI_CIRCLE}
        strokeDashoffset={MINI_CIRCLE * (1 - percent / 100)}
        transform="rotate(-90 12 12)"
      />
    </svg>
  )
}

export function Ring({ percent, label, size = 200, spinning = false }: {
  percent: number
  label: string
  size?: number
  spinning?: boolean // a running job: a short arc keeps going round, so the wait is visible
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 200 200" role="img" aria-label={`${percent} % ${label}`}>
      <circle className="ring-track" cx="100" cy="100" r={RADIUS} fill="none" strokeWidth="10" />
      <circle
        className="ring-value"
        cx="100"
        cy="100"
        r={RADIUS}
        fill="none"
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={CIRCLE}
        strokeDashoffset={CIRCLE * (1 - percent / 100)}
        transform="rotate(-90 100 100)"
      />
      {spinning && (
        <circle
          className="ring-spin"
          cx="100"
          cy="100"
          r={RADIUS}
          fill="none"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${CIRCLE / 8} ${CIRCLE}`}
        />
      )}
      <text className="ring-text num" x="100" y="106" textAnchor="middle">
        {percent} %
      </text>
      <text className="ring-sub" x="100" y="132" textAnchor="middle">
        {label}
      </text>
    </svg>
  )
}
