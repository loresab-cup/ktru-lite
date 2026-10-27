import { Tour, type TourProps } from 'antd'
import { GraduationCap } from 'lucide-react'
import { useState } from 'react'
import { T } from './texts'

const SEEN_KEY = 'ktru-tour-seen'

/** The steps that point at a part of the page: a hidden or missing part makes a centred card. */
const TARGETS: (string | null)[] = ['.drop', null, null, null, null, '.seg a[href="/catalog"]']

function firstVisit(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === null
  } catch {
    return false // no storage: never push the tour on the person at every visit
  }
}

function remember() {
  try {
    localStorage.setItem(SEEN_KEY, '1')
  } catch {
    // the tour will show again next time, nothing else is lost
  }
}

function target(selector: string | null) {
  if (selector === null) return null
  const element = document.querySelector<HTMLElement>(selector)
  return element && element.offsetParent !== null ? element : null
}

/** The "Start the tour" button and the tour itself; it opens on its own at the first visit. */
export function Tutorial() {
  const [open, setOpen] = useState(firstVisit)
  const close = () => {
    setOpen(false)
    remember()
  }
  const steps: TourProps['steps'] = T.tour.steps.map(([title, description], i) => ({
    title,
    description,
    // antd types the target as an element, but a missing one (null) gives a centred card
    target: () => target(TARGETS[i]) as HTMLElement,
  }))
  return (
    <>
      <button type="button" className="btn tour-start" onClick={() => setOpen(true)}>
        <GraduationCap size={18} aria-hidden="true" /> {T.tour.start}
      </button>
      <Tour open={open} steps={steps} onClose={close} onFinish={close} />
    </>
  )
}
