// Drawings of the app: thin waves and a small line chart. Every color comes from theme.css,
// so the pictures follow the theme.

const WAVES = 7

/** Thin parallel waves, the decoration of the hero and of empty states. */
export function Waves({ className }: { className?: string }) {
  const lines = Array.from({ length: WAVES }, (_, i) => {
    const y = 60 + i * 14
    const lift = 26 + i * 3
    return `M-20 ${y} C 120 ${y - lift}, 230 ${y + lift}, 360 ${y - 6} S 560 ${y - lift}, 720 ${y + 4}`
  })
  return (
    <svg
      className={`waves ${className ?? ''}`}
      viewBox="0 0 700 200"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      {lines.map((d, i) => (
        <path key={d} d={d} className={i === 3 ? 'wave strong' : 'wave'} fill="none" />
      ))}
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
      <path d={d} fill="none" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
