import { App as AntApp, Checkbox } from 'antd'
import { FolderOpen, Trash2, Upload } from 'lucide-react'
import { useState } from 'react'
import { api } from './api'
import { JobCards } from './JobCards'
import { Link } from './Link'
import { paths } from './router'
import { T } from './texts'
import { confirmLook } from './confirm'
import { EmptyState } from './ui'
import { isBusy, useJobs } from './useJobs'

const MAX = 1000
const PAGE = 100 // specifications per "show more"; the service gives up to 1000 at once

/** All specifications, newest first; a tile opens the list of their codes. */
export function JobsScreen() {
  const { message, modal } = AntApp.useApp()
  const [limit, setLimit] = useState(PAGE)
  const { jobs, total, percents, reload } = useJobs(limit)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)

  // Specifications that are still running cannot be deleted, so "select all" skips them
  const removable = (jobs ?? []).filter((job) => !isBusy(job)).map((job) => job.id)
  const chosen = removable.filter((id) => selected.has(id))
  const allChosen = removable.length > 0 && chosen.length === removable.length

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const remove = async () => {
    setDeleting(true)
    // One after another: the service answers each delete on its own, a failure skips only that one
    const results: boolean[] = []
    for (const id of chosen) {
      results.push(await api.deleteJob(id).then(() => true, () => false))
    }
    const done = results.filter(Boolean).length
    const failed = results.length - done
    if (done) message.success(T.jobs.deleted(done))
    if (failed) message.error(T.jobs.deleteFailed(failed))
    setSelected(new Set())
    setDeleting(false)
    reload()
  }

  const ask = () =>
    modal.confirm({
      title: T.jobs.deleteTitle(chosen.length),
      content: T.jobs.deleteBody,
      ...confirmLook(Trash2),
      okText: T.jobs.deleteYes,
      cancelText: T.jobs.deleteNo,
      okButtonProps: { danger: true },
      onOk: remove,
    })

  const empty = jobs !== null && jobs.length === 0
  return (
    <>
      <section className="page-head">
        <div className="wrap">
          <div className="title-row">
            <div>
              <h1 className="page-title">
                {T.jobs.title}
                {jobs !== null && jobs.length > 0 && <span className="title-count num">{total}</span>}
              </h1>
              <p className="muted">{T.jobs.lead}</p>
            </div>
            <Link className="btn primary" to={paths.home}>
              <Upload size={16} aria-hidden="true" /> {T.jobs.upload}
            </Link>
          </div>
        </div>
      </section>
      <section className="wrap section">
        {empty && (
          <EmptyState icon={FolderOpen} title={T.jobs.empty} hint={T.jobs.emptyHint}>
            <Link className="btn primary" to={paths.home}>
              <Upload size={16} aria-hidden="true" /> {T.jobs.upload}
            </Link>
          </EmptyState>
        )}
        {jobs !== null && jobs.length > 0 && (
          <div className="bulk-bar">
            <Checkbox
              checked={allChosen}
              indeterminate={chosen.length > 0 && !allChosen}
              disabled={removable.length === 0 || deleting}
              onChange={(e) => setSelected(e.target.checked ? new Set(removable) : new Set())}
            >
              {T.jobs.selectAll}
            </Checkbox>
            <span className="muted num" aria-live="polite">
              {T.jobs.selected(chosen.length)}
            </span>
            <span className="grow" />
            <button
              type="button"
              className="btn danger small"
              disabled={chosen.length === 0 || deleting}
              onClick={ask}
            >
              <Trash2 size={16} aria-hidden="true" /> {T.jobs.deleteSelected(chosen.length)}
            </button>
          </div>
        )}
        {!empty && <JobCards jobs={jobs} percents={percents} selected={selected} onToggle={toggle} />}
        {jobs !== null && jobs.length < total && limit < MAX && (
          <div className="more-row">
            <button type="button" className="btn" onClick={() => setLimit(limit + PAGE)}>
              {T.jobs.showMore(Math.min(PAGE, total - jobs.length))}
            </button>
          </div>
        )}
      </section>
    </>
  )
}
