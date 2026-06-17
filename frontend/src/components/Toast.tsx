import { useEffect, useState } from 'react'

let toastTimeout: ReturnType<typeof setTimeout> | null = null
let setToastGlobal: ((msg: string) => void) | null = null

export function showToast(msg: string) {
  setToastGlobal?.(msg)
}

export default function Toast() {
  const [message, setMessage] = useState('')
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    setToastGlobal = (msg: string) => {
      setMessage(msg)
      setVisible(true)
      if (toastTimeout) clearTimeout(toastTimeout)
      toastTimeout = setTimeout(() => setVisible(false), 2500)
    }
    return () => { setToastGlobal = null }
  }, [])

  if (!visible) return null
  return <div className="toast">{message}</div>
}
