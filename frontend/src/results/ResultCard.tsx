import { Checkbox } from 'antd'
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronDown
} from 'lucide-react'
import { type ItemRow, type Sort } from '../api'
import { Link } from '../Link'
import {
  paths
} from '../router'
import { formatDate, T } from '../texts'
import { Confidence, StatusChip } from '../ui'




/** The caption of a column that sorts the list: click goes ascending, descending, off. */
export function SortCaption({ column, sort, order, onSort }: {
  column: { sort: Sort; label: string; className: string }
  sort: Sort | ''
  order: 'asc' | 'desc'
  onSort: (sort: Sort) => void
}) {
  const on = sort === column.sort
  const Icon = !on ? ArrowUpDown : order === 'asc' ? ArrowUp : ArrowDown
  return (
    <button
      type="button"
      className={`res-sort ${column.className} ${on ? 'on' : ''}`}
      aria-label={T.results.sort.by(column.label, on ? T.results.sort[order] : '')}
      onClick={() => onSort(column.sort)}
    >
      {column.label}
      <Icon size={14} aria-hidden="true" />
    </button>
  )
}


/** One position: its row number, the code with its name, the confidence and the status.
 *  When positions are picked, a returnable one shows a check box in place of the number. */
export function ResultCard({ item, jobId, open, onToggle, pick }: {
  item: ItemRow
  jobId: string
  open: boolean
  onToggle: () => void
  pick?: { checked: boolean; onChange: () => void }
}) {
  const to = paths.item(jobId, item.id)
  const characteristics = item.attributes.characteristics ?? []
  return (
    <article className="res-card">
      <div className="res-top">
        {pick ? (
          <span className="row-badge pick">
            <Checkbox
              checked={pick.checked}
              onChange={pick.onChange}
              aria-label={T.results.pick.row(item.row_number)}
            />
          </span>
        ) : (
          <span className="row-badge num" title={T.results.rowNumber(item.row_number)}>
            <span className="sr-only">{T.results.row} </span>
            {item.row_number}
          </span>
        )}
        <div className="res-title">
          <Link className={`res-code ${item.ktru_code ? '' : 'none'}`} to={to} data-item={item.id}>
            {item.ktru_code ?? T.results.noCode}
          </Link>
          {item.ktru_name && (
            <Link className="res-name sentence" to={to} tabIndex={-1}>
              {item.ktru_name}
            </Link>
          )}
          {item.status === 'needs_review' && item.attributes.review && (
            <p className="review-reason">{item.attributes.review}</p>
          )}
          {item.attributes.remembered && (
            <p className="remembered-note">
              {T.results.remembered(formatDate(item.attributes.remembered))}
            </p>
          )}
        </div>
        <div className="res-conf">
          <span className="cap-small">{T.results.confidence}</span>
          <Confidence value={item.confidence} zone={item.zone} />
        </div>
        <div className="res-status">
          <span className="cap-small">{T.results.status}</span>
          <StatusChip status={item.status} />
        </div>
      </div>
      <button type="button" className="res-more" aria-expanded={open} onClick={onToggle}>
        {T.results.more}
        <ChevronDown size={18} aria-hidden="true" className={open ? 'flip' : ''} />
      </button>
      {open && (
        <div className="res-more-body">
          {characteristics.length > 0 && (
            <div className="chips">
              {characteristics.map((c, i) => (
                <span key={i} className="chip">
                  {[c.name, [c.value, c.unit].filter(Boolean).join(' ')].filter(Boolean).join(': ')}
                </span>
              ))}
            </div>
          )}
          <div className="res-source">
            <div className="cap">{T.results.sourceText}</div>
            {item.raw_text}
          </div>
        </div>
      )}
    </article>
  )
}
