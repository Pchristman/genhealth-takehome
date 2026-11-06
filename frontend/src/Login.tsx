import { useState } from 'react'
import type { FormEvent } from 'react'
import { logIn, messageOf, register } from './api'

interface Props {
  notice: string
  onLoggedIn: (token: string, email: string) => void
}

export function Login({ notice, onLoggedIn }: Props) {
  const [signingUp, setSigningUp] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [signupCode, setSignupCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      if (signingUp) {
        await register(email, password, signupCode)
      }
      onLoggedIn(await logIn(email, password), email)
    } catch (failure) {
      setError(messageOf(failure))
      setBusy(false)
    }
  }

  function switchMode() {
    setSigningUp(!signingUp)
    setError('')
  }

  return (
    <section className="card narrow">
      <h2>{signingUp ? 'Create an account' : 'Log in'}</h2>
      {notice && (
        <p className="alert" role="status">
          {notice}
        </p>
      )}
      <form onSubmit={submit} className="stack">
        <div className="field-input">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div className="field-input">
          <label htmlFor="password">
            Password{signingUp && <span className="muted"> (at least 12 characters)</span>}
          </label>
          <input
            id="password"
            type="password"
            autoComplete={signingUp ? 'new-password' : 'current-password'}
            required
            minLength={signingUp ? 12 : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {signingUp && (
          <div className="field-input">
            <label htmlFor="signup-code">Signup code</label>
            <input
              id="signup-code"
              type="text"
              autoComplete="off"
              required
              value={signupCode}
              onChange={(event) => setSignupCode(event.target.value)}
            />
          </div>
        )}
        {error && (
          <p className="alert alert-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? 'Please wait…' : signingUp ? 'Sign up' : 'Log in'}
        </button>
      </form>
      <button type="button" className="link" onClick={switchMode}>
        {signingUp ? 'Have an account? Log in' : 'Need an account? Sign up'}
      </button>
    </section>
  )
}
