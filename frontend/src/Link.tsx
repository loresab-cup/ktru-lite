import type { AnchorHTMLAttributes, MouseEvent } from 'react'
import { navigate } from './router'

/** A real link (opens in a new tab with a modifier key) that does not reload the page. */
export function Link({ to, onClick, ...rest }: { to: string } & AnchorHTMLAttributes<HTMLAnchorElement>) {
  const go = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (event.defaultPrevented || event.button !== 0) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(to)
  }
  return <a href={to} onClick={go} {...rest} />
}
