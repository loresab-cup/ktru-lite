import {
  AlertTriangle,
  CheckCheck,
  CircleAlert,
  CircleCheck,
  CircleX,
  Eye,
  Loader,
  PenLine,
  Sparkles,
} from 'lucide-react'
import type { CSSProperties, ReactNode } from 'react'
import type { ItemStatus, Zone } from './api'
import { Mountains } from './Mountains'
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
      <Mountains trees={18} />
      <b>{title}</b>
      {hint && <span>{hint}</span>}
      {children}
    </div>
  )
}
