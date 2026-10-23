import { App as AntApp, Checkbox, Popconfirm } from 'antd'
import { ArrowLeft, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from './api'
import { JobCards } from './JobCards'
import { Link } from './Link'
import { paths } from './router'
import { T } from './texts'
import { isBusy, useJobs } from './useJobs'

/** All jobs, newest first; a tile opens the list of the codes of that job. */
export function JobsScreen() {
  const { message } = AntApp.useApp()
  const { jobs, percents, reload } = useJobs(100)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)

  // Jobs that are still running cannot be deleted, so "select all" skips them
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
    // One after another: the service answers each delete on its own, a failure skips only that job
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

  return (
    <>
      <section className="band">
        <div className="wrap">
          <nav className="crumbs" aria-label={T.jobs.title}>
            <ArrowLeft size={16} aria-hidden="true" />
            <Link to={paths.home}>{T.jobs.back}</Link>
          </nav>
          <h1 className="page-title">
            {T.jobs.title}
            {jobs !== null && jobs.length > 0 && (
              <span className="title-count num">{T.jobs.count(jobs.length)}</span>
            )}
          </h1>
        </div>
      </section>
      <section className="wrap section">
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
            <Popconfirm
              title={T.jobs.deleteAsk(chosen.length)}
              okText={T.jobs.deleteYes}
              cancelText={T.jobs.deleteNo}
              okButtonProps={{ danger: true }}
              disabled={chosen.length === 0}
              onConfirm={() => void remove()}
            >
              <button type="button" className="btn danger" disabled={chosen.length === 0 || deleting}>
                <Trash2 size={16} aria-hidden="true" /> {T.jobs.deleteSelected(chosen.length)}
              </button>
            </Popconfirm>
          </div>
        )}
        <JobCards jobs={jobs} percents={percents} selected={selected} onToggle={toggle} />
      </section>
    </>
  )
}
