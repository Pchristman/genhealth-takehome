import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, FIELDS, FIELD_LABELS, STATUS_LABELS, messageOf } from './api'
import type { Client, Decision, Evidence, FieldName, Flag, Order } from './api'

interface Props {
  client: Client
  orderId: string
  onBack: () => void
}

const FLAG_TEXT: Record<string, string> = {
  missing: 'This value was not found in the document.',
  illegible: 'This value could not be read clearly.',
  ambiguous: 'The document is unclear about which value is right.',
  conflicting_values: 'The document gives different values for this field.',
  invalid_npi: 'This NPI fails the NPI check digit, so it may be misprinted.',
  invalid_date: 'This could not be understood as a date.',
  implausible_dob: 'This date of birth is in the future or more than 120 years ago.',
  unverified_quote: 'This came from a scanned page, so it could not be checked against PDF text.',
  quote_not_found: 'The quoted text was not found on the page given.',
  multiple_patients: 'The document appears to cover more than one patient.',
  embedded_instructions:
    'The document contains text that looks like instructions to the software. It was not followed.',
  extraction_failed:
    'The document could not be read automatically. The PDF was kept, so enter the values by hand.',
}

const VERIFICATION: Record<string, { label: string; meaning: string }> = {
  verified: { label: 'Verified', meaning: 'Quote matched the text on that page.' },
  unverified: { label: 'Unverified', meaning: 'Scanned page, so there is no text to check.' },
  failed: { label: 'Failed', meaning: 'Quote did not match the text on that page.' },
  not_applicable: { label: 'Not checked', meaning: 'There was no value to check.' },
}

const EDIT_LABELS: Record<FieldName, string> = {
  ...FIELD_LABELS,
  patient_dob: 'Date of birth (YYYY-MM-DD)',
  provider_npi: 'Provider NPI (10 digits)',
}

function flagText(flag: Flag): string {
  return FLAG_TEXT[flag.code] ?? 'This needs a person to check it.'
}

function Flags({ flags }: { flags: Flag[] }) {
  if (flags.length === 0) {
    return null
  }
  return (
    <ul className="flags">
      {flags.map((flag) => (
        <li key={flag.id} className={flag.resolved_at ? 'flag flag-resolved' : 'flag'}>
          <span>{flagText(flag)}</span> <code>{flag.code}</code>
          {flag.resolved_at && <span className="muted small"> Resolved by an edit.</span>}
        </li>
      ))}
    </ul>
  )
}

// Once a person changes a value, the check says nothing about it, only about what was printed.
function VerificationBadge({ evidence }: { evidence: Evidence }) {
  const known = VERIFICATION[evidence.verification]
  const label = known?.label ?? evidence.verification
  return (
    <div className="badge-row">
      <span
        className={
          evidence.corrected ? 'badge badge-printed' : `badge verification-${evidence.verification}`
        }
      >
        {evidence.corrected ? `As printed: ${label}` : label}
      </span>
      {known && <span className="muted small">{known.meaning}</span>}
    </div>
  )
}

interface FieldCardProps {
  order: Order
  field: FieldName
  canEdit: boolean
  editing: boolean
  busy: boolean
  locked: boolean
  error: string
  onEdit: () => void
  onCancel: () => void
  onSave: (value: string) => void
}

function FieldCard(props: FieldCardProps) {
  const { order, field, canEdit, editing, busy, locked, error, onEdit, onCancel, onSave } = props
  const [draft, setDraft] = useState(order[field] ?? '')
  const evidence = order.evidence.find((row) => row.field === field)
  const flags = order.flags.filter((flag) => flag.field === field)
  const value = order[field]
  const printed = evidence?.extracted_value
  const showPrinted = evidence?.corrected || (printed && printed !== value)
  const inputId = `edit-${field}`

  function submit(event: FormEvent) {
    event.preventDefault()
    onSave(draft)
  }

  return (
    <article className={`field-card field-${field}`}>
      <h3>{FIELD_LABELS[field]}</h3>
      {editing ? (
        <form onSubmit={submit} className="stack-tight">
          <div className="field-input">
            <label htmlFor={inputId}>{EDIT_LABELS[field]}</label>
            <input
              id={inputId}
              type="text"
              autoFocus
              autoComplete="off"
              required
              inputMode={field === 'provider_npi' ? 'numeric' : undefined}
              placeholder={field === 'patient_dob' ? 'YYYY-MM-DD' : undefined}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
          </div>
          {error && (
            <p className="alert alert-error" role="alert">
              {error}
            </p>
          )}
          <div className="button-row">
            <button type="submit" disabled={busy}>
              Save
            </button>
            <button type="button" className="quiet" disabled={busy} onClick={onCancel}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <p className={value === null ? 'value value-empty' : 'value'}>{value ?? 'No value'}</p>
      )}

      {evidence ? (
        <>
          {evidence.corrected && (
            <div className="badge-row">
              <span className="badge badge-edited">Edited by a person</span>
            </div>
          )}
          {showPrinted && (
            <p className="small as-printed">
              <span className="muted">As printed:</span> {printed ?? 'No value'}
            </p>
          )}
          <VerificationBadge evidence={evidence} />
          {evidence.source_quote && (
            <blockquote>
              <span className="muted small">Source quote</span>
              {evidence.source_quote}
            </blockquote>
          )}
          <p className="muted small">
            {evidence.page === null ? 'No PDF page recorded' : `PDF page ${evidence.page}`}
          </p>
        </>
      ) : (
        <p className="muted small">
          {order.source === 'manual' ? 'Entered by a person.' : 'Nothing was extracted.'}
        </p>
      )}

      <Flags flags={flags} />

      {canEdit && !editing && (
        <button type="button" className="quiet" disabled={busy || locked} onClick={onEdit}>
          Edit
        </button>
      )}
    </article>
  )
}

function OpenFlags({ flags }: { flags: Flag[] }) {
  return (
    <ul className="flags" aria-label="Open flags">
      {flags.map((flag) => (
        <li key={flag.id} className="flag">
          <strong>{FIELD_LABELS[flag.field as FieldName] ?? 'Whole document'}:</strong>{' '}
          {flagText(flag)}
        </li>
      ))}
    </ul>
  )
}

function reviewNote(order: Order): string {
  if (order.status === 'needs_review') {
    return ''
  }
  if (!order.reviewed_at) {
    return 'Approved automatically: every value was verified against the PDF text.'
  }
  const when = new Date(order.reviewed_at).toLocaleString()
  return `${STATUS_LABELS[order.status]} by a person on ${when}.`
}

export function OrderDetail({ client, orderId, onBack }: Props) {
  const [order, setOrder] = useState<Order | null>(null)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [fieldError, setFieldError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<FieldName | null>(null)
  const [confirming, setConfirming] = useState<Decision | null>(null)
  const [pdfBusy, setPdfBusy] = useState(false)
  const [blockedPdfUrl, setBlockedPdfUrl] = useState('')
  const pdfUrl = useRef('')
  const mounted = useRef(false)
  const alertRef = useRef<HTMLParagraphElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)

  const load = useCallback(() => client.getOrder(orderId), [client, orderId])

  useEffect(() => {
    let current = true
    load().then(
      (loaded) => {
        if (current) {
          setOrder(loaded)
          setLoadError('')
        }
      },
      (failure: unknown) => {
        if (current) {
          setOrder(null)
          setLoadError(messageOf(failure))
        }
      },
    )
    return () => {
      current = false
    }
  }, [load])

  const loaded = order !== null
  useEffect(() => {
    if (loaded) {
      headingRef.current?.focus()
    }
  }, [loaded])

  useEffect(() => {
    if (actionError) {
      alertRef.current?.focus()
    }
  }, [actionError])

  // Logging out unmounts this too, so a patient's PDF never outlives the session that opened it.
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (pdfUrl.current) {
        URL.revokeObjectURL(pdfUrl.current)
        pdfUrl.current = ''
      }
    }
  }, [])

  // Someone else changed the Order, so what's on screen is out of date: say so and show the truth.
  async function showConflict(failure: unknown) {
    setActionError(messageOf(failure))
    setEditing(null)
    setConfirming(null)
    setOrder(await load())
  }

  async function act(work: () => Promise<Order>) {
    setBusy(true)
    setActionError('')
    setFieldError('')
    try {
      setOrder(await work())
      setEditing(null)
      setConfirming(null)
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) {
        await showConflict(failure).catch(() => undefined)
      } else if (editing) {
        setFieldError(messageOf(failure))
      } else {
        setActionError(messageOf(failure))
      }
    } finally {
      setBusy(false)
    }
  }

  function decide(decision: Decision) {
    void act(() => client.review(orderId, decision))
  }

  async function viewPdf() {
    // Opened before the wait: a browser blocks a new tab that doesn't follow a click directly.
    const opened = window.open('', '_blank')
    setPdfBusy(true)
    setActionError('')
    try {
      const blob = await client.pdf(orderId)
      if (!mounted.current) {
        opened?.close()
        return
      }
      if (pdfUrl.current) {
        URL.revokeObjectURL(pdfUrl.current)
      }
      const url = URL.createObjectURL(blob)
      pdfUrl.current = url
      if (opened) {
        opened.location.href = url
      }
      // Always set: the link from an earlier click points at the URL revoked just above.
      setBlockedPdfUrl(opened ? '' : url)
    } catch (failure) {
      opened?.close()
      setActionError(messageOf(failure))
    } finally {
      setPdfBusy(false)
    }
  }

  if (!order) {
    return (
      <div className="stack">
        <button type="button" className="link" onClick={onBack}>
          ← Back to orders
        </button>
        {loadError ? (
          <p className="alert alert-error" role="alert">
            {loadError}
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </div>
    )
  }

  const needsReview = order.status === 'needs_review'
  const scanned = order.evidence.some((row) => row.verification === 'unverified')
  const missing = FIELDS.filter((field) => order[field] === null)
  const openFlags = order.flags.filter((flag) => !flag.resolved_at)
  const documentFlags = order.flags.filter((flag) => flag.field === null)
  const name = [order.patient_first_name, order.patient_last_name].filter(Boolean).join(' ')
  const editOpen = editing !== null
  const flagCount = `${openFlags.length} open ${openFlags.length === 1 ? 'flag' : 'flags'}`

  return (
    <div className="stack">
      <button type="button" className="link" onClick={onBack}>
        ← Back to orders
      </button>

      <section className="card stack-tight order-head">
        <div className="section-head">
          <h2 ref={headingRef} tabIndex={-1}>
            {name || 'Order'}
          </h2>
          <span className={`badge status-${order.status}`}>{STATUS_LABELS[order.status]}</span>
        </div>
        {reviewNote(order) && <p className="muted small">{reviewNote(order)}</p>}
        {order.document_id && (
          <div className="button-row">
            <button type="button" className="quiet" disabled={pdfBusy} onClick={viewPdf}>
              {pdfBusy ? 'Opening…' : 'View PDF'}
            </button>
            {blockedPdfUrl && (
              <a href={blockedPdfUrl} target="_blank" rel="noreferrer">
                Open the PDF
              </a>
            )}
          </div>
        )}
      </section>

      {actionError && (
        <p ref={alertRef} tabIndex={-1} className="alert alert-error" role="alert">
          {actionError}
        </p>
      )}

      {scanned && (
        <div className="alert alert-warning" role="note">
          <strong>A person must check these values.</strong> Some came from a scanned page, so they
          could not be confirmed against text in the PDF. Compare each one with the PDF before
          approving.
        </div>
      )}

      {!needsReview && <Flags flags={documentFlags} />}

      <div className={needsReview ? 'detail with-review' : 'detail'}>
        {needsReview && (
          <section className="card stack-tight review-panel">
            <h2>Review</h2>
            {missing.length > 0 && (
              <p className="muted small">
                Every field needs a value before approval. Missing:{' '}
                {missing.map((field) => FIELD_LABELS[field]).join(', ')}.
              </p>
            )}
            {openFlags.length > 0 && <OpenFlags flags={openFlags} />}
            {openFlags.length > 0 && missing.length === 0 && (
              <p className="muted small">
                You can approve with open flags. They stay on the record.
              </p>
            )}
            {confirming ? (
              <div className="stack-tight">
                <p>
                  {confirming === 'deny'
                    ? "Deny this order? This can't be undone."
                    : `Approve with ${flagCount}? This can't be undone.`}
                </p>
                <div className="button-row">
                  <button
                    type="button"
                    className={confirming === 'deny' ? 'danger' : undefined}
                    disabled={busy}
                    onClick={() => decide(confirming)}
                  >
                    {confirming === 'deny' ? 'Yes, deny' : 'Yes, approve'}
                  </button>
                  <button
                    type="button"
                    className="quiet"
                    autoFocus
                    disabled={busy}
                    onClick={() => setConfirming(null)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="button-row">
                <button
                  type="button"
                  disabled={busy || editOpen || missing.length > 0}
                  onClick={() =>
                    openFlags.length > 0 ? setConfirming('approve') : decide('approve')
                  }
                >
                  Approve
                </button>
                <button
                  type="button"
                  className="danger-quiet"
                  disabled={busy || editOpen}
                  onClick={() => setConfirming('deny')}
                >
                  Deny
                </button>
              </div>
            )}
            {editOpen && <p className="muted small">Save or cancel your edit first.</p>}
          </section>
        )}

        <section className="fields" aria-label="Order fields">
          {FIELDS.map((field) => (
            <FieldCard
              key={`${field}:${order[field]}:${editing === field}`}
              order={order}
              field={field}
              canEdit={needsReview}
              editing={editing === field}
              busy={busy}
              locked={editOpen}
              error={editing === field ? fieldError : ''}
              onEdit={() => {
                setEditing(field)
                setFieldError('')
                setConfirming(null)
              }}
              onCancel={() => setEditing(null)}
              onSave={(value) => void act(() => client.editField(orderId, field, value))}
            />
          ))}
        </section>
      </div>
    </div>
  )
}
