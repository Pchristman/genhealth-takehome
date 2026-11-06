import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { PAGE_SIZE, STATUS_LABELS, messageOf } from './api'
import type { Client, Order, OrderPage, OrderStatus } from './api'

interface Props {
  client: Client
  filter: OrderStatus | ''
  offset: number
  onFilter: (filter: OrderStatus | '') => void
  onOffset: (offset: number) => void
  onOpen: (orderId: string) => void
}

function patientName(order: Order): string {
  const names = [order.patient_last_name, order.patient_first_name].filter(Boolean)
  return names.length > 0 ? names.join(', ') : 'Name not found'
}

function Upload({ client, onOpen }: Pick<Props, 'client' | 'onOpen'>) {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!file) {
      return
    }
    setUploading(true)
    setError('')
    try {
      const order = await client.upload(file)
      // An upload takes a minute. If the person has moved on or logged out, it must not pull
      // them onto another order: it waits in the list.
      if (mounted.current) {
        onOpen(order.id)
      }
    } catch (failure) {
      setError(messageOf(failure))
      setUploading(false)
    }
  }

  return (
    <section className="card upload">
      <h2>Upload an order</h2>
      <form onSubmit={submit} className="stack">
        <div className="field-input">
          <label htmlFor="pdf">PDF file</label>
          <input
            id="pdf"
            type="file"
            accept="application/pdf,.pdf"
            disabled={uploading}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              setError('')
            }}
          />
        </div>
        <button type="submit" disabled={uploading || !file}>
          {uploading ? 'Reading the PDF…' : 'Upload'}
        </button>
        {uploading && (
          <div role="status" className="stack-tight">
            <progress aria-label="Reading the PDF" />
            <p className="muted small">
              Reading the document. This can take a minute or more, so keep this page open.
            </p>
          </div>
        )}
        {error && (
          <p className="alert alert-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </section>
  )
}

export function Orders({ client, filter, offset, onFilter, onOffset, onOpen }: Props) {
  const [page, setPage] = useState<OrderPage | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let current = true
    client.listOrders(filter, offset).then(
      (loaded) => {
        if (!current) {
          return
        }
        // This page emptied, say after its last order was reviewed. The queue itself may not be.
        if (loaded.items.length === 0 && offset > 0) {
          onOffset(Math.max(0, offset - PAGE_SIZE))
          return
        }
        setPage(loaded)
        setError('')
      },
      (failure: unknown) => {
        if (current) {
          setError(messageOf(failure))
        }
      },
    )
    return () => {
      current = false
    }
  }, [client, filter, offset, onOffset])

  const lastShown = page ? page.offset + page.items.length : 0

  return (
    <div className="orders-layout">
      <Upload client={client} onOpen={onOpen} />

      <section className="card">
        <div className="section-head">
          <h2>Orders</h2>
          <div className="field-input inline">
            <label htmlFor="status-filter">Status</label>
            <select
              id="status-filter"
              value={filter}
              onChange={(event) => onFilter(event.target.value as OrderStatus | '')}
            >
              <option value="">All</option>
              <option value="needs_review">{STATUS_LABELS.needs_review}</option>
              <option value="approved">{STATUS_LABELS.approved}</option>
              <option value="denied">{STATUS_LABELS.denied}</option>
            </select>
          </div>
        </div>

        {error && (
          <p className="alert alert-error" role="alert">
            {error}
          </p>
        )}
        {!page && !error && <p className="muted">Loading…</p>}
        {page && page.items.length === 0 && (
          <p className="muted">{filter ? 'No orders with this status.' : 'No orders yet.'}</p>
        )}

        {page && page.items.length > 0 && (
          <ul className="order-list">
            {page.items.map((order) => {
              const openFlags = order.flags.filter((flag) => !flag.resolved_at).length
              return (
                <li key={order.id}>
                  <button type="button" className="order-row" onClick={() => onOpen(order.id)}>
                    <span className="order-main">
                      <strong>{patientName(order)}</strong>
                      <span className="muted small">
                        <span className="mono">{order.patient_dob ?? 'No date of birth'}</span> ·{' '}
                        {order.provider_name ?? 'No provider'}
                      </span>
                      <span className="muted small mono">
                        Added {new Date(order.created_at).toLocaleString()}
                      </span>
                    </span>
                    <span className="order-side">
                      <span className={`badge status-${order.status}`}>
                        {STATUS_LABELS[order.status]}
                      </span>
                      {openFlags > 0 && (
                        <span className="flag-count">
                          {openFlags} open {openFlags === 1 ? 'flag' : 'flags'}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}

        {page && (offset > 0 || page.total > PAGE_SIZE) && (
          <div className="pager">
            <button
              type="button"
              className="quiet"
              disabled={offset === 0}
              onClick={() => onOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </button>
            <span className="muted small">
              {page.offset + 1}–{lastShown} of {page.total}
            </span>
            <button
              type="button"
              className="quiet"
              disabled={lastShown >= page.total}
              onClick={() => onOffset(offset + PAGE_SIZE)}
            >
              Next
            </button>
          </div>
        )}
      </section>
    </div>
  )
}
