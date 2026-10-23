import { ArrowLeft } from 'lucide-react'
import { JobCards } from './JobCards'
import { Link } from './Link'
import { paths } from './router'
import { useJobs } from './useJobs'
import { T } from './texts'

/** All jobs, newest first; a tile opens the list of the codes of that job. */
export function JobsScreen() {
  const { jobs, percents } = useJobs(100)
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
        <JobCards jobs={jobs} percents={percents} />
      </section>
    </>
  )
}
