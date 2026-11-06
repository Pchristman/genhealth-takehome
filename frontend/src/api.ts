export type OrderStatus = 'needs_review' | 'approved' | 'denied'
export type Decision = 'approve' | 'deny'

export const FIELDS = [
  'patient_first_name',
  'patient_last_name',
  'patient_dob',
  'provider_name',
  'provider_npi',
] as const

export type FieldName = (typeof FIELDS)[number]

export const FIELD_LABELS: Record<FieldName, string> = {
  patient_first_name: 'Patient first name',
  patient_last_name: 'Patient last name',
  patient_dob: 'Date of birth',
  provider_name: 'Ordering provider',
  provider_npi: 'Provider NPI',
}

export const STATUS_LABELS: Record<OrderStatus, string> = {
  needs_review: 'Needs review',
  approved: 'Approved',
  denied: 'Denied',
}

export interface Evidence {
  field: string
  extracted_value: string | null
  source_quote: string | null
  page: number | null
  verification: string
  corrected: boolean
}

export interface Flag {
  id: string
  field: string | null
  code: string
  resolved_by: string | null
  resolved_at: string | null
}

export interface Order extends Record<FieldName, string | null> {
  id: string
  status: OrderStatus
  source: 'upload' | 'manual'
  document_id: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string
  updated_at: string
  evidence: Evidence[]
  flags: Flag[]
}

export interface OrderPage {
  items: Order[]
  total: number
  limit: number
  offset: number
}

export const PAGE_SIZE = 25

const INPUT_LABELS: Record<string, string> = {
  ...FIELD_LABELS,
  email: 'Email',
  password: 'Password',
  signup_code: 'Signup code',
}

// The API says only "Invalid value." for these, so the screen adds what it wants instead.
const INPUT_HINTS: Record<string, string> = {
  patient_dob: 'Use YYYY-MM-DD, for a date in the last 120 years.',
  provider_npi: 'Use the 10 digit NPI.',
}

const STATUS_FALLBACKS: Record<number, string> = {
  413: 'That file is too large.',
  415: 'That file is not a PDF.',
  429: 'The daily upload limit has been reached.',
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

interface ValidationIssue {
  loc?: unknown[]
  msg?: string
}

function describeIssue(issue: ValidationIssue): string {
  const name = String(issue.loc?.at(-1) ?? '')
  const parts = [INPUT_LABELS[name], issue.msg, INPUT_HINTS[name]]
  return parts.filter(Boolean).join(': ').replace(/\.: /, '. ')
}

async function toError(response: Response): Promise<ApiError> {
  const fallback =
    STATUS_FALLBACKS[response.status] ?? `The request failed (status ${response.status}).`
  const body: unknown = await response.json().catch(() => null)
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') {
    return new ApiError(response.status, detail)
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return new ApiError(response.status, (detail as ValidationIssue[]).map(describeIssue).join(' '))
  }
  return new ApiError(response.status, fallback)
}

async function send(path: string, init: RequestInit): Promise<Response> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError(0, 'Could not reach the server. Check your connection and try again.')
  }
  if (!response.ok) {
    throw await toError(response)
  }
  return response
}

export function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Something went wrong. Try again.'
}

export async function register(email: string, password: string, signupCode: string): Promise<void> {
  await send('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, signup_code: signupCode }),
  })
}

export async function logIn(email: string, password: string): Promise<string> {
  const response = await send('/auth/token', {
    method: 'POST',
    body: new URLSearchParams({ username: email, password }),
  })
  const body = (await response.json()) as { access_token: string }
  return body.access_token
}

export type Client = ReturnType<typeof createClient>

export function createClient(token: string, onExpired: () => void) {
  async function authed(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${token}`)
    try {
      return await send(path, { ...init, headers })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onExpired()
      }
      throw error
    }
  }

  async function json<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await authed(path, init)
    return (await response.json()) as T
  }

  function jsonBody(method: string, body: unknown): RequestInit {
    return {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }
  }

  return {
    listOrders(status: OrderStatus | '', offset: number): Promise<OrderPage> {
      const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
      if (status) {
        query.set('status', status)
      }
      return json<OrderPage>(`/v1/orders?${query}`)
    },

    getOrder(id: string): Promise<Order> {
      return json<Order>(`/v1/orders/${id}`)
    },

    upload(file: File): Promise<Order> {
      const form = new FormData()
      form.append('file', file)
      return json<Order>('/v1/orders/upload', { method: 'POST', body: form })
    },

    editField(id: string, field: FieldName, value: string): Promise<Order> {
      return json<Order>(`/v1/orders/${id}`, jsonBody('PATCH', { [field]: value }))
    },

    review(id: string, decision: Decision): Promise<Order> {
      return json<Order>(`/v1/orders/${id}/review`, jsonBody('POST', { decision }))
    },

    async pdf(id: string): Promise<Blob> {
      const response = await authed(`/v1/orders/${id}/document`)
      return response.blob()
    },
  }
}
