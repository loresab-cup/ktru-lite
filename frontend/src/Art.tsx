// Drawings of the app in the wireframe style: a picture placeholder and a small line chart.
// Every color comes from theme.css, so the pictures follow the theme.

/** Picture placeholder: a grey box crossed by two diagonals (the name stayed from the old waves). */
export function Waves({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <g className="stub">
        <rect className="stub-box" x="1" y="1" width="98" height="98" vectorEffect="non-scaling-stroke" />
        <path className="stub-cross" d="M1 1L99 99M99 1L1 99" vectorEffect="non-scaling-stroke" />
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
