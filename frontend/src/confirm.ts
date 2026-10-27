import type { LucideIcon } from 'lucide-react'
import { createElement } from 'react'

/** The look of the confirm dialogs: a round icon over centred text and two equal buttons. */
export function confirmLook(Icon: LucideIcon, tone: 'blue' | 'red' = 'red') {
  return {
    icon: createElement(
      'span',
      { className: `dialog-icon ${tone}`, 'aria-hidden': true },
      createElement(Icon, { size: 28, strokeWidth: 1.75 }),
    ),
    className: 'confirm-dialog',
    centered: true,
    width: 440,
  }
}
