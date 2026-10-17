// Drawings of the app: layered waves (like an area chart) and thin outline shapes on the page
// background. Every color comes from theme.css, so the pictures follow the theme.

// The left part stays low: the heading of the hero card lies there
const WAVES = [
  'M0 230V178C140 128 240 112 350 150S600 190 700 110 900 50 1000 110V230Z',
  'M0 230V202C120 184 220 124 330 152S520 205 640 150 860 85 1000 150V230Z',
  'M0 230V212C180 204 260 150 360 178S560 214 700 190 900 168 1000 196V230Z',
]
const LINE = 'M0 170C140 204 230 140 360 150S560 204 690 140 880 40 1000 70'

export function Waves({ className, line = true }: { className?: string; line?: boolean }) {
  return (
    <svg
      className={className}
      viewBox="0 0 1000 230"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      {WAVES.map((d, i) => (
        <path key={d} d={d} style={{ fill: `var(--wave-${i + 1})` }} />
      ))}
      {line && (
        <path
          d={LINE}
          fill="none"
          style={{ stroke: 'var(--wave-line)' }}
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  )
}

/** Outline circles, a triangle, a pentagon and zigzags scattered over the page. */
export function Deco() {
  const stroke = { stroke: 'var(--deco)' }
  return (
    <svg className="deco" aria-hidden="true" focusable="false">
      <g fill="none" strokeWidth="2" strokeLinejoin="round" style={stroke}>
        <circle cx="72%" cy="9%" r="5" />
        <circle cx="97%" cy="44%" r="6" />
        <circle cx="3%" cy="58%" r="4" />
        <circle cx="45%" cy="96%" r="4" />
        <svg x="88%" y="5%" width="22" height="22" overflow="visible">
          <path d="M2 11L19 2 16 20Z" />
        </svg>
        <svg x="64%" y="14%" width="26" height="26" overflow="visible">
          <path d="M13 1L25 10 20 24H6L1 10Z" />
        </svg>
        <svg x="1.5%" y="40%" width="34" height="20" overflow="visible">
          <path d="M0 6l6-5 6 5 6-5 6 5 6-5M0 16l6-5 6 5 6-5 6 5 6-5" />
        </svg>
        <svg x="84%" y="93%" width="34" height="12" overflow="visible">
          <path d="M0 10l6-7 6 7 6-7 6 7 6-7" />
        </svg>
      </g>
    </svg>
  )
}

/** A small line chart of numbers, e.g. positions of the recent jobs. */
export function Sparkline({ values }: { values: number[] }) {
  const points = values.length > 1 ? values : [0, ...values, 0]
  const max = Math.max(1, ...points)
  const d = points
    .map((v, i) => `${i ? 'L' : 'M'}${(i / (points.length - 1)) * 60 + 2} ${22 - (v / max) * 18}`)
    .join('')
  return (
    <svg className="spark" viewBox="0 0 64 24" aria-hidden="true" focusable="false">
      <path d={d} fill="none" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
