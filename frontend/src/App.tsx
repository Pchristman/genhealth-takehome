import { useCallback, useMemo, useState } from 'react'
import { createClient } from './api'
import type { OrderStatus } from './api'
import { Login } from './Login'
import { OrderDetail } from './OrderDetail'
import { Orders } from './Orders'

interface Session {
  token: string
  email: string
}

// The token lives only in this state, never in browser storage, so a reload logs the user out.
export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [notice, setNotice] = useState('')
  const [orderId, setOrderId] = useState<string | null>(null)
  const [filter, setFilter] = useState<OrderStatus | ''>('')
  const [offset, setOffset] = useState(0)

  const endSession = useCallback((message: string) => {
    setSession(null)
    setOrderId(null)
    setFilter('')
    setOffset(0)
    setNotice(message)
  }, [])

  const client = useMemo(
    () =>
      session &&
      createClient(session.token, () => endSession('Your session ended. Please log in again.')),
    [session, endSession],
  )

  return (
    <>
      <header className="topbar">
        <h1>Order Intake</h1>
        {session && (
          <div className="topbar-user">
            <span className="muted topbar-email">{session.email}</span>
            <button type="button" className="quiet" onClick={() => endSession('')}>
              Log out
            </button>
          </div>
        )}
      </header>

      <main className="page">
        {!session || !client ? (
          <Login
            notice={notice}
            onLoggedIn={(token, email) => {
              setNotice('')
              setSession({ token, email })
            }}
          />
        ) : orderId ? (
          // The key stops one order's data from ever sitting behind another order's id.
          <OrderDetail
            key={orderId}
            client={client}
            orderId={orderId}
            onBack={() => setOrderId(null)}
          />
        ) : (
          <Orders
            client={client}
            filter={filter}
            offset={offset}
            onFilter={(next) => {
              setFilter(next)
              setOffset(0)
            }}
            onOffset={setOffset}
            onOpen={setOrderId}
          />
        )}
      </main>

      <footer className="page page-foot muted small">
        You stay logged in only while this page is open. Reloading it logs you out.
      </footer>
    </>
  )
}
