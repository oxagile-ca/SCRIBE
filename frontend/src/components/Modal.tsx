import { useEffect, ReactNode } from 'react'

interface Props {
  title: string
  children: ReactNode
  onClose: () => void
  actions?: ReactNode
}

export default function Modal({ title, children, onClose, actions }: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 className="modal__title" style={{ margin: 0 }}>{title}</h3>
          <button className="btn btn--ghost btn--small" onClick={onClose}>X</button>
        </div>
        {children}
        {actions && <div className="modal__actions">{actions}</div>}
      </div>
    </div>
  )
}
