import { Modal } from 'antd'
import { ArrowLeft, ArrowRight, GraduationCap, Play } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { T } from './texts'

const SEEN_KEY = 'ktru-tour-seen'

/** One picture per step (public/tour): the app on the example file, the place to press marked. */
const PICTURES = ['upload', 'process', 'colors', 'review', 'accept', 'replace', 'export', 'catalog']
const PICTURE_WIDTH = 960
const PICTURE_HEIGHT = 540

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

/** The "Start the tour" button and the tour: short steps with a picture each. It opens on its
 *  own only at the first visit; the last step can put the example file into the list. */
export function Tutorial({ onTry }: { onTry: () => void }) {
  const [open, setOpen] = useState(firstVisit)
  const [step, setStep] = useState(0)
  const button = useRef<HTMLButtonElement>(null)
  const steps = T.tour.steps
  const last = step === steps.length - 1
  const [title, action, alt] = steps[step]

  const close = () => {
    setOpen(false)
    remember()
  }
  const start = () => {
    setStep(0)
    setOpen(true)
  }

  // Arrows walk the steps as the buttons do; Esc is the window's own
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') setStep((s) => Math.min(s + 1, steps.length - 1))
      else if (e.key === 'ArrowLeft') setStep((s) => Math.max(s - 1, 0))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, steps.length])

  return (
    <>
      <button ref={button} type="button" className="btn tour-start" onClick={start}>
        <GraduationCap size={18} aria-hidden="true" /> {T.tour.start}
      </button>
      <Modal
        open={open}
        title={title}
        onCancel={close}
        afterClose={() => button.current?.focus()}
        footer={null}
        centered
        width={760}
        className="tour"
        aria-label={T.tour.label}
      >
        <p className="tour-count num" aria-live="polite">
          {T.tour.count(step + 1, steps.length)}
        </p>
        <img
          className="tour-picture"
          src={`/tour/${PICTURES[step]}.webp`}
          alt={alt}
          width={PICTURE_WIDTH}
          height={PICTURE_HEIGHT}
        />
        <p className="tour-action">{action}</p>
        <div className="tour-actions">
          <button type="button" className="btn ghost" onClick={close}>
            {T.tour.skip}
          </button>
          <span className="grow" />
          <button
            type="button"
            className="btn"
            disabled={step === 0}
            onClick={() => setStep(step - 1)}
          >
            <ArrowLeft size={16} aria-hidden="true" /> {T.tour.back}
          </button>
          {last ? (
            <>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  close()
                  onTry()
                }}
              >
                <Play size={16} aria-hidden="true" /> {T.tour.tryExample}
              </button>
              <button type="button" className="btn primary" onClick={close}>
                {T.tour.done}
              </button>
            </>
          ) : (
            <button type="button" className="btn primary" onClick={() => setStep(step + 1)}>
              {T.tour.next} <ArrowRight size={16} aria-hidden="true" />
            </button>
          )}
        </div>
      </Modal>
    </>
  )
}
