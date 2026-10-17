import { useId, useMemo } from 'react'

// Misty mountains: sky, sun, three ranges with fog between them and a row of fir trees.
// Every color comes from theme.css, so the picture follows the theme.

function seeded(seed: number) {
  let value = seed
  return () => {
    value = (value * 9301 + 49297) % 233280
    return value / 233280
  }
}

function treesPath(count: number, seed: number) {
  const random = seeded(seed)
  let path = ''
  for (let i = 0; i < count; i++) {
    const x = (i / count) * 1240 - 20 + random() * 18
    const base = 512 + 14 * Math.sin(x / 140) + random() * 10
    const h = 34 + random() * 46
    const w = h * 0.42
    // Two tiers make the silhouette of a fir tree
    path += `M${x} ${base - h}L${x + w / 2} ${base - h * 0.45}L${x + w * 0.3} ${base - h * 0.45}`
    path += `L${x + w / 1.6} ${base}L${x - w / 1.6} ${base}L${x - w * 0.3} ${base - h * 0.45}`
    path += `L${x - w / 2} ${base - h * 0.45}Z`
  }
  return path
}

interface Props {
  className?: string
  trees?: number
  /** false: transparent sky, the ranges lie over what is behind the picture */
  sky?: boolean
  sun?: boolean
}

export function Mountains({ className, trees = 46, sky = true, sun = true }: Props) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '')
  const firs = useMemo(() => treesPath(trees, 7), [trees])
  const stop = (name: string, opacity = 1) => ({ stopColor: `var(--${name})`, stopOpacity: opacity })

  return (
    <svg
      className={className}
      viewBox="0 0 1200 600"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={`${id}sky`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={stop('sky-top')} />
          <stop offset="1" style={stop('sky-bottom')} />
        </linearGradient>
        <radialGradient id={`${id}glow`}>
          <stop offset="0" style={stop('sun-glow')} />
          <stop offset="1" style={stop('sun-glow', 0)} />
        </radialGradient>
        <linearGradient id={`${id}fog`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={stop('fog', 0)} />
          <stop offset="0.5" style={stop('fog')} />
          <stop offset="1" style={stop('fog', 0)} />
        </linearGradient>
        <linearGradient id={`${id}floor`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={stop('fog', 0)} />
          <stop offset="1" style={stop('fog', 0.9)} />
        </linearGradient>
      </defs>

      {sky && <rect width="1200" height="600" fill={`url(#${id}sky)`} />}
      {sun && (
        <g>
          <circle cx="830" cy="190" r="170" fill={`url(#${id}glow)`} />
          <circle cx="830" cy="190" r="62" style={{ fill: 'var(--sun)' }} opacity="0.9" />
        </g>
      )}

      <path
        d="M0 380L120 300L210 340L330 215L430 300L520 255L640 150L760 270L860 225L980 300L1080 238L1200 290V600H0Z"
        style={{ fill: 'var(--mtn-far)' }}
      />
      <path
        d="M640 150L606 192L622 188L634 204L648 186L664 196L676 194ZM330 215L304 248L318 244L330 256L342 242L356 250ZM860 225L840 248L852 246L862 256L874 244Z"
        style={{ fill: 'var(--line)' }}
        opacity="0.75"
      />
      <rect y="270" width="1200" height="150" fill={`url(#${id}fog)`} />

      <path
        d="M0 430Q90 360 170 400T340 370Q420 320 500 382T690 360Q780 300 870 372T1050 352Q1130 332 1200 370V600H0Z"
        style={{ fill: 'var(--mtn-mid)' }}
      />
      <path
        d="M70 330C300 470 520 470 640 400S980 250 1140 300"
        fill="none"
        style={{ stroke: 'var(--line)' }}
        strokeWidth="1.6"
        opacity="0.8"
      />
      <path
        d="M90 352C320 492 540 492 660 420S1000 272 1160 322"
        fill="none"
        style={{ stroke: 'var(--line)' }}
        strokeWidth="1"
        opacity="0.5"
      />
      <rect y="380" width="1200" height="130" fill={`url(#${id}fog)`} />

      <path
        d="M0 505Q200 450 420 495T820 488Q1000 458 1200 492V600H0Z"
        style={{ fill: 'var(--mtn-near)' }}
      />
      <path d={firs} style={{ fill: 'var(--tree)' }} />
      <rect y="470" width="1200" height="130" fill={`url(#${id}floor)`} />
    </svg>
  )
}
