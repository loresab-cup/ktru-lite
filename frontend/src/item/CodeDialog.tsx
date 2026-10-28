import { Input, Modal } from 'antd'
import {
  Check
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import {
  api,
  ktruUrl
} from '../api'
import { T } from '../texts'


type Known = { name: string; status: string } | 'unknown' | 'checking' | 'error' | null


/** Manual input of a code in a window: the code is checked against the catalog as it is typed. */
export function CodeDialog({ open, busy, onClose, onSave }: {
  open: boolean
  busy: boolean
  onClose: () => void
  onSave: (code: string) => Promise<boolean>
}) {
  const [value, setValue] = useState('')
  const [known, setKnown] = useState<Known>(null)
  const [completed, setCompleted] = useState('')
  const code = value.replace(/\s+/g, '') // "10.12.10.000- 00000005" from a spreadsheet
  const valid = ktruUrl(code) !== null

  useEffect(() => {
    if(!valid) return
    let alive = true
    const timer = window.setTimeout(() => {
      setKnown('checking')
      api
        .catalog(code)
        .then((list) => {
          const hit = list.find((c) => c.code === code)
          if(alive) {
            setKnown(hit ? { name: hit.name, status: hit.status } : 'unknown')
            setCompleted(code)
          }
        })
        .catch(() => {
          if(alive) {
            setKnown('error')
            setCompleted(code)
          }
        })
    }, 300)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [code, valid])

  const state = valid ? (completed === code ? known : 'checking') : null
  const found = typeof state === 'object' && state !== null
  const submit = async () => {
    if(!found || busy) return
    if(await onSave(code)) {
      setValue('')
      onClose()
    }
  }

  let helper: ReactNode = <span className="muted">{T.drawer.codeFormat}</span>
  if(value && !valid) helper = <span className="field-error">{T.drawer.codeFormat}</span>
  else if(state === 'checking') helper = <span className="muted">{T.drawer.codeChecking}</span>
  else if(state === 'unknown') helper = <span className="field-error">{T.drawer.codeUnknown}</span>
  else if(state === 'error') helper = <span className="field-error">{T.catalogSearch.failed}</span>
  else if(found) {
    helper = (
      <span className="field-ok">
        {T.drawer.codeFound(state.name, T.codeStatus[state.status] ?? state.status)}
      </span>
    )
  }

  return (
    <Modal
      open={open}
      centered
      width={480}
      title={T.drawer.enterCodeTitle}
      okText={T.drawer.saveCode}
      cancelText={T.drawer.cancel}
      okButtonProps={{ disabled: !found || busy, icon: <Check size={16} /> }}
      onOk={() => void submit()}
      onCancel={onClose}
      destroyOnHidden
    >
      <p className="muted">{T.drawer.enterCodeHint}</p>
      <label className="modal-field">
        {T.drawer.codeLabel}
        <Input
          autoFocus
          value={value}
          maxLength={40}
          placeholder={T.drawer.codePlaceholder}
          status={value && (!valid || state === 'unknown') ? 'error' : undefined}
          onChange={(e) => setValue(e.target.value)}
          onPressEnter={() => void submit()}
        />
        <span aria-live="polite">{helper}</span>
      </label>
    </Modal>
  )
}
