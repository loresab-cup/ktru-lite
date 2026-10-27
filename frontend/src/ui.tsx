import {
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  ExternalLink,
  Info,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import { useState, type CSSProperties, type ReactNode } from 'react'
import { ktruUrl, type ItemStatus, type Zone } from './api'
import { T } from './texts'

/** The percent in the color of its zone; the zone is also said in words for screen readers. */
export function Confidence({ value, zone }: { value: number | null; zone: Zone }) {
  const percent = value ?? 0
  return (
    <span className={`conf num zone-${zone}`} title={T.zone[zone]}>
      {percent.toFixed(0)} %<span className="sr-only">, {T.zone[zone]}</span>
    </span>
  )
}

/** A colored dot and the word: the status never rests on color alone. */
export function StatusChip({ status }: { status: ItemStatus }) {
  return (
    <span className={`status status-${status}`}>
      <span className="status-dot" aria-hidden="true" />
      {T.itemStatus[status]}
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

/** The numbers to show: the first, the last and a window around the current page; a gap of one
 *  page shows that page, a longer one shows dots (null). */
function pageNumbers(current: number, pages: number): (number | null)[] {
  const shown = new Set([1, pages])
  for (let p = current - 3; p <= current + 3; p++) if (p >= 1 && p <= pages) shown.add(p)
  const sorted = [...shown].sort((a, b) => a - b)
  const result: (number | null)[] = []
  sorted.forEach((page, i) => {
    const gap = i > 0 ? page - sorted[i - 1] : 1
    if (gap === 2) result.push(page - 1)
    else if (gap > 2) result.push(null)
    result.push(page)
  })
  return result
}

/** Page buttons with a wide window of numbers; on a phone only the near ones stay. */
export function Pager({ current, total, pageSize, onChange }: {
  current: number
  total: number
  pageSize: number
  onChange: (page: number) => void
}) {
  const pages = Math.ceil(total / pageSize)
  return (
    <nav className="pager" aria-label={T.results.pager.label}>
      <button
        type="button"
        className="pg arrow"
        disabled={current <= 1}
        aria-label={T.results.pager.prev}
        onClick={() => onChange(current - 1)}
      >
        <ChevronLeft size={18} aria-hidden="true" />
      </button>
      {pageNumbers(current, pages).map((page, i) =>
        page === null ? (
          <span key={`gap-${i}`} className="pg-gap" aria-hidden="true">
            …
          </span>
        ) : (
          <button
            key={page}
            type="button"
            className={`pg ${page === current ? 'on' : ''} ${Math.abs(page - current) > 1 && page !== 1 && page !== pages ? 'far' : ''}`}
            aria-label={T.results.pager.page(page)}
            aria-current={page === current ? 'page' : undefined}
            onClick={() => onChange(page)}
          >
            {page}
          </button>
        ),
      )}
      <button
        type="button"
        className="pg arrow"
        disabled={current >= pages}
        aria-label={T.results.pager.next}
        onClick={() => onChange(current + 1)}
      >
        <ChevronRight size={18} aria-hidden="true" />
      </button>
    </nav>
  )
}

/** A round icon, a title, a hint and buttons: lists with nothing in them, failures, lost pages. */
export function EmptyState({ icon: Icon, tone = 'blue', title, hint, children, role }: {
  icon: LucideIcon
  tone?: 'blue' | 'red'
  title: string
  hint?: string
  children?: ReactNode
  role?: 'alert' | 'status'
}) {
  return (
    <section className="empty" role={role}>
      <span className={`empty-icon ${tone}`} aria-hidden="true">
        <Icon size={40} strokeWidth={1.5} />
      </span>
      <h2 className="empty-title">{title}</h2>
      {hint && <p className="empty-hint">{hint}</p>}
      {children && <div className="empty-actions">{children}</div>}
    </section>
  )
}

const NOTICE_ICONS: Record<'info' | 'warn' | 'error' | 'ok', LucideIcon> = {
  info: Info,
  warn: TriangleAlert,
  error: TriangleAlert,
  ok: CircleCheck,
}

/** A colored strip on the page: what the person should know before going on. */
export function Notice({ kind, title, children }: {
  kind: keyof typeof NOTICE_ICONS
  title: string
  children?: ReactNode
}) {
  const Icon = NOTICE_ICONS[kind]
  return (
    <div className={`notice ${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      <Icon size={22} aria-hidden="true" />
      <div>
        <b>{title}</b>
        {children && <p>{children}</p>}
      </div>
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
      <ExternalLink size={14} aria-hidden="true" />
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
    <div className="raw-text">
      <div className="cap">{T.results.sourceText}</div>
      <div className={long && !open ? 'raw-clamp' : undefined}>{text}</div>
      {long && (
        <button type="button" className="text-link" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? T.results.hide : T.results.showFull}
        </button>
      )}
    </div>
  )
}

const MINI = { radius: 19, circle: 2 * Math.PI * 19 }

/** Small ring with the percent inside, for the tiles of the specifications. */
export function PercentRing({ percent, size = 48 }: { percent: number; size?: number }) {
  return (
    <svg className="mini-ring" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      <circle className="ring-track" cx="24" cy="24" r={MINI.radius} fill="none" strokeWidth="6" />
      <circle
        className="ring-value"
        cx="24"
        cy="24"
        r={MINI.radius}
        fill="none"
        strokeWidth="6"
        strokeLinecap="round"
        strokeDasharray={MINI.circle}
        strokeDashoffset={MINI.circle * (1 - percent / 100)}
        transform="rotate(-90 24 24)"
      />
      <text className={`ring-mini-text num ${percent >= 100 ? 'tight' : ''}`} x="24" y="28.5" textAnchor="middle">
        {percent}%
      </text>
    </svg>
  )
}

const RADIUS = 80
const CIRCLE = 2 * Math.PI * RADIUS

export function Ring({ percent, label, size = 132, spinning = false }: {
  percent: number
  label: string
  size?: number
  spinning?: boolean // a running job: a short arc keeps going round, so the wait is visible
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 200 200" role="img" aria-label={`${percent} % ${label}`}>
      <circle className="ring-track" cx="100" cy="100" r={RADIUS} fill="none" strokeWidth="18" />
      <circle
        className="ring-value"
        cx="100"
        cy="100"
        r={RADIUS}
        fill="none"
        strokeWidth="18"
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
          strokeWidth="18"
          strokeLinecap="round"
          strokeDasharray={`${CIRCLE / 8} ${CIRCLE}`}
        />
      )}
      <text className="ring-text num" x="100" y="115" textAnchor="middle">
        {percent} %
      </text>
    </svg>
  )
}
