# Order Intake Service: Design Spec

GenHealth.ai take-home. Submit by 12:55 PM PT on 2026-11-06 (due 1:00 PM).

**Goal.** A deployed Python API that turns an uploaded DME order PDF into an Order, keeps the source of every value, and sends anything doubtful to a person instead of guessing. A wrong value means a denied claim or a delayed patient, so the design prefers a visible flag to a confident answer.

**Success.** A reviewer signs up at the public URL, uploads a PDF we have never seen, and gets back the five fields with their source pages and any flags. A second user cannot see that order.

**What the sample taught us.** GenHealth's sample (`DME Patient Demo Document CPAP.fax.pdf`) shaped this design:

| Observation | Design consequence |
| --- | --- |
| Image-only: 0 text characters on all 4 pages | The scan path is the main path, not an edge case. Every quote on this file is unverified, so it goes to review. |
| NPI `9182734556` fails the checksum (run, not estimated) | Keep the value exactly as printed and flag it. |
| Prescriber Arjun Raj (page 1) and referring physician Gregory House (page 2) | The prompt must separate them. Only the prescriber has an NPI. |
| DOB 12/05/1900 | Plausibility flag. |
| PDF has 4 pages, fax header says 3 of 3, last page is blank | Page numbers mean the PDF page. Blank pages must not fail. |
| Filename says CPAP, content is foot orthotics | Never use the filename. It is not stored or logged. |

---

## 1. Requirements checklist

### GenHealth's prompt (these win any conflict)
- [ ] G1. REST API written in Python
- [ ] G2. CRUD on an Order, saved to a database
- [ ] G3. Accepts an uploaded PDF
- [ ] G4. Extracts patient first name, last name and date of birth
- [ ] G5. Extracts ordering provider name and NPI
- [ ] G6. Works on PDFs we have never seen, including scanned faxes and messy multi-page documents
- [ ] G7. Checks extracted values before saving them
- [ ] G8. Doesn't guess when a field is missing
- [ ] G9. Requires authentication
- [ ] G10. Each user can only see their own orders
- [ ] G11. Deployed and reachable at a public endpoint, with a working upload endpoint (no public solution means not considered)
- [ ] G12. Any frontend uses JavaScript or TypeScript
- [ ] G13. Secrets stay out of the repository
- [ ] G14. Commit as we go, so the history can be followed
- [ ] G15. Attempt stays within 3 consecutive hours
- [ ] G16. Whatever else a production-ready MVP needs
- [ ] G17. Deliverable: email the public URL and a repo link (or zip with `.git`) to mahum@genhealth.ai and mike@genhealth.ai. Sent by the author.

### Engineering standards
- [ ] S1. Synthetic data only, in code, fixtures, logs and prompts
- [ ] S2. Every query on user data is scoped by `owner_id`; another user's record returns 404 (changed from org scoping during spec review)
- [ ] S3. Every read or change of a patient record is audited, ids only
- [ ] S4. Never log document text, model output or patient fields, including through tracebacks
- [ ] S5. All LLM output is validated against a typed schema
- [ ] S6. Retry only what the model can fix
- [ ] S7. A bad value on the source document becomes a flag, not a correction
- [ ] S8. Any trusted value keeps its source quote and page
- [ ] S9. Quotes from pages with no text layer are marked unverified
- [ ] S10. AI may approve when criteria are clearly met, or escalate. Only a person can deny.
- [ ] S11. Document content is untrusted; instructions inside it are never followed

### Environment
- [ ] E1. Repo at `C:\Users\pchri\dev\genhealth\takehome`, short paths only
- [ ] E2. uv, Python 3.13 (not 3.15), Node 24, Docker, gh
- [ ] E3. FastAPI, Pydantic v2, SQLite, pytest, React + TypeScript + Vite
- [ ] E4. Model `claude-opus-5-5`; key in a gitignored `.env`, plus `.env.example`
- [ ] E5. Runs locally as well as deployed, from a documented path that works on this Windows machine

### Process and pre-submit gates
- [ ] P1. Approval after the spec and after the plan
- [ ] P2. 4 to 6 ordered tasks, each reviewable and tested
- [ ] P3. One task at a time; after tests are green, an adversarial review subagent reviews the task, findings go to the author, and a separate subagent fixes them; stop and show changes and test results after each
- [ ] P4. Failing test first; nothing is called working until it has been run
- [ ] P5. Commit spec and plan on approval, then after each approved task, with why in the message
- [ ] P6. No push until the author releases it
- [ ] P7. Report any slip against the schedule, and what to cut
- [ ] P8. Corrections section in the README: what went wrong, how it was caught, the fix
- [ ] P9. Separate security and correctness review pass; the author decides what gets fixed
- [ ] P10. README complete: run, what works, decisions, trade-offs and cuts, AI use, corrections, security notes, next steps, links to `docs/`
- [ ] P11. A clean clone installs, passes tests and runs

### Conflict
None remaining. The original standard scoped by org, and GenHealth's prompt scopes by user. User scope became the standard during spec review, so there is no org layer.

### Deployment and access
- **P6 and G11.** Railway deploys from GitHub, so every deploy is a push. P6 still holds: nothing is pushed until the author releases it, and the first push must happen by the 11:00 checkpoint.
- **G17.** The repo is public, so reviewers need no extra access. Nothing secret may ever be pushed.

---

## 2. Ambiguities

Nothing here blocks work. Every item has a working assumption.

### Questions for GenHealth
| # | Question | Why it matters | Assumption until answered |
| --- | --- | --- | --- |
| A1 | Do reviewers test by hand or with a script? If scripted, is there an expected path, field name or response shape? | The prompt names no contract. A script could miss our endpoint. | Hand or Swagger testing. `POST /v1/orders/upload`, multipart field `file`, documented at `/docs` and in the README. |
| A2 | When prescriber and referring physician differ, which is the "ordering provider"? | The sample has both. | The prescriber, meaning whoever signed or issued the order. If the document doesn't make that clear, value is null with an `ambiguous` flag. |
| A3 | Does "checks before saving" mean a failed value must not be saved at all, or saved and flagged? | Decides whether a bad NPI blocks the Order. | Checks run before commit. The Order is saved as `needs_review`, with the value as printed and a flag. Rejecting it would lose the fax, and S7 requires a flag. |

### Settled with a stated assumption
| # | Ambiguity | Assumption |
| --- | --- | --- |
| A4 | Is there a team or org level? | No. Each user owns their data. Signing up needs the code from `SIGNUP_CODE`. The seed script adds two users, to demonstrate isolation. Shared team queues are a next step. |
| A5 | Order fields beyond the five? | None. Items and diagnosis codes are out of scope. |
| A6 | What do approve and deny mean for an Order? | Approved means ready for downstream use. Denied means a person rejected it. |
| A7 | When may the AI approve? | Only when code confirms all five fields are present, verified against a text layer, and free of flags. Scans never qualify (S9), so GenHealth's sample always goes to review. |
| A8 | Date format | US month/day. `12/05/1900` means 1900-12-05. Two-digit years are flagged `ambiguous`. Other dates are read as US month/day without a flag, and the source quote stays visible. |
| A9 | Plausible DOB | Not in the future and at most 120 years old. Anything else is flagged, not removed. |
| A10 | NPI check | 10 digits starting with 1 or 2, passing Luhn with the 80840 prefix. Spaces and hyphens are stripped for the check only. No NPPES registry lookup. |
| A11 | Provider name format | As printed, including credentials (`Arjun Raj D.P.M`). |
| A12 | Several patients in one PDF | One Order, with a `multiple_patients` flag. The model picks nothing silently. |
| A13 | Non-order or unreadable PDF | Order saved with null fields and `missing` flags. It is never rejected for looking unusual. |
| A14 | Model call fails | Order saved as `needs_review` with an `extraction_failed` flag. The PDF is kept, so the fax isn't lost. |
| A15 | Manual create or edit with a bad value | 422. A person can correct it on the spot, unlike a fax. Person-entered orders start as `needs_review`. |
| A16 | Edits to an approved Order | ~~Status returns to `needs_review`.~~ Superseded 2026-11-10 by A16a. |
| A16a | Edits to, or deletion of, an approved Order (amended 2026-11-10) | 409. Approved is final, whether code or a person approved it. Who approved it and when never changes. See §7. |
| A17 | Delete | Soft delete. Reads return 404 and the PDF bytes are cleared. Audit rows stay. |
| A18 | Separation of duties | Not enforced. The owner can review their own order. Listed as a production gap. |
| A19 | Limits | 20 MB, 100 pages, 20 uploads per user per day, 100 per day overall. 20 MB keeps the base64 request under the API's 32 MB limit. |
| A20 | Update verb | `PATCH` only. |

---

## 3. Core end-to-end path

If everything else gets cut, this must work at the public URL:

1. `POST /auth/register`, then `POST /auth/token`.
2. `POST /v1/orders/upload` with a PDF.
3. Reject bad input before spending anything: not a PDF, encrypted, corrupt, too large or too many pages.
4. Read each page's text layer and record which pages have one.
5. Send the PDF to Claude and receive the typed result: value, quote and page for each field.
6. Code checks quotes on text-layer pages, then checks NPI, DOB and missing fields. It adds flags and never rewrites a value.
7. Code sets the status: `approved` only if the gate passes, otherwise `needs_review`.
8. In one transaction, save the Order, its evidence, flags and audit row.
9. `GET /v1/orders/{id}` returns the Order to its owner. Any other user gets 404.

---

## 4. Data model

Timestamps are UTC. Ids are UUIDs.

| Table | Key fields | Scoped by `owner_id` | Audited |
| --- | --- | --- | --- |
| `User` | `id`, `email` (unique), `password_hash` | is the owner | no (holds no patient data) |
| `Order` | `id`, `status`, `source` (`upload` or `manual`), `patient_first_name`, `patient_last_name`, `patient_dob` (date), `provider_name`, `provider_npi`, `document_id`, `reviewed_by`, `reviewed_at`, `deleted_at`, `created_at`, `updated_at` | yes | every read, list row, change, review, delete |
| `Document` | `id`, `sha256`, `byte_size`, `page_count`, `text_pages` (JSON list), `content` (BLOB), `created_at` | yes | upload, download |
| `FieldEvidence` | `id`, `order_id`, `field`, `extracted_value`, `source_quote`, `page`, `verification` (`verified`, `unverified`, `failed`, `not_applicable`), `corrected` | yes | returned with the Order, so its read is covered |
| `ReviewFlag` | `id`, `order_id`, `field` (nullable), `code`, `resolved_by`, `resolved_at` | yes | returned with the Order, so its read is covered |
| `AuditEvent` | `id`, `actor_id`, `action`, `resource_type`, `resource_id`, `request_id`, `created_at` | by `actor_id` | append-only, no update or delete route |

- **Patient data lives in** `Order`, `FieldEvidence` and `Document`. `AuditEvent` holds ids and action names only, including field names but never values.
- **`patient_dob` when unparseable:** null on the Order. The printed text stays in `FieldEvidence.extracted_value`, with a flag.
- **`FieldEvidence` is never edited** apart from `corrected`, so a person's edit can't erase what the fax said.
- **One loader:** `get_order(db, principal, order_id)` filters on `owner_id` and `deleted_at`. It is the only way to load an Order. A miss writes `order.access_denied` against the caller and returns 404. Not-found and someone-else's look identical.
- **Status:** `needs_review`, `approved`, `denied`. The pipeline can only set the first two. `denied` is reachable only through the review route, which requires a logged-in user.
- **Flag codes:** `missing`, `illegible`, `ambiguous`, `conflicting_values`, `invalid_npi`, `invalid_date`, `implausible_dob`, `unverified_quote`, `quote_not_found`, `multiple_patients`, `embedded_instructions`, `extraction_failed`.

---

## 5. Design decisions and rejected alternatives

| Decision | Chosen | Rejected, and why |
| --- | --- | --- |
| **Reading scans** | Send the PDF to Claude, which sees page images and text | Tesseract then LLM: another system dependency and more failure modes, and its output would still be a guess about the image. |
| **Output shape** | Structured output via `client.messages.parse`, with our own quote and page per field | API citations: they return a 400 alongside structured output. Plain JSON in text: nothing enforces the schema. Forced `tool_choice`: returns a 400 on this model. |
| **Who approves** | Code. The model has no status field and no tools. | Model-reported confidence or approval: a document could talk it into approving. With no such field, an injection has nothing to change. |
| **Transcription** | The model copies values as printed. Code parses dates and checks NPIs. | Asking the model to normalize: it could quietly repair a misprinted digit, breaking S7. |
| **Retries** | Up to 3 attempts, only for schema errors, a missing or out-of-range quote, or a quote not found on a text-layer page | Retrying on a bad NPI or date: that pressures the model to invent a passing value. After attempts run out, the value stays with a `quote_not_found` flag. |
| **Verification** | Per page. Under 25 non-space characters counts as no text layer. | Per document: a typed cover sheet over scanned pages would mark scans as verified. |
| **Upload flow** | Synchronous, 120 s client timeout | Queue and polling: more moving parts, and harder for a reviewer to test by hand. Sync is the first thing to change for real volume. |
| **Rejecting vs saving doubtful values** | Save as `needs_review` with flags | Rejecting with 422: the fax disappears from the workflow. Blanking the bad value: the reviewer loses what was printed. |
| **Auth** | Email and password, stdlib `scrypt`, HS256 JWT (60 min), OAuth2 form so Swagger's Authorize button works | API keys: reviewers can't make two users. SSO: out of scope. Argon2: extra compiled dependency for no gain here. |
| **Signup** | Signup code from `SIGNUP_CODE`, with upload quotas | Open signup: anyone could spend the Anthropic credit. Site-wide Basic auth: collides with the Bearer header. |
| **Hosting** | Railway trial with a volume, deployed from GitHub on push to `main`. Fallback: Railway Hobby or Fly.io, no code change. | Railway CLI deploys: no link between the git history and what's running. Render free: files are lost on spin-down, so accounts vanish. Hugging Face: Docker Spaces need a paid plan. Postgres: outside the stack, and a volume makes it unnecessary. |
| **Frontend** | React app built in Docker and served by FastAPI | Separate frontend host: a second deploy plus CORS. |
| **PDF storage** | BLOB in SQLite | Discarding after extraction: reviewers can't check the source, and A14 loses the fax. Object storage: another account. |
| **Isolation** | `owner_id` on every user-owned row, one loader | Org layer: no requirement needs it, and it adds a table nothing uses. Row filter per route: one forgotten filter leaks patient data. |
| **Logging** | JSON logs from a field allowlist. Tracebacks reduced to file, line and function. 422 responses have `input` removed. | Stock logging: `uvicorn.error` tracebacks and pydantic errors can include values. |
| **Refusal fallback** | `fallbacks: "default"`, because medical text can trip a safety classifier | Plain refusal: a false positive would send the fax to `extraction_failed`. Noted because a refused request may run on a model other than `claude-opus-5-5`. |

**Prompt injection.** Document content sits inside the user turn. The system prompt states it is data. The model has no tools and returns only the schema. If it sees instructions in the document, it sets `embedded_instructions_found`, which adds a flag. A synthetic injected fixture is part of the tests.

**Model settings.** `claude-opus-5-5`, effort `high`, `max_tokens` 16000. `thinking` is omitted, since it can't be disabled on this model and `budget_tokens` returns a 400. Exact SDK call is confirmed by a live smoke test in the task that adds it.

**Model schema.** Each of the five fields is `{value, source_quote, page, issue}`, with `issue` one of `none`, `illegible`, `ambiguous`, `conflicting`. Top-level booleans are `multiple_patients` and `embedded_instructions_found`. There is no free-text field.

### API
| Method and path | Purpose |
| --- | --- |
| `POST /auth/register`, `POST /auth/token` | Sign up, log in |
| `GET /v1/orders` (paged, status filter), `POST /v1/orders` | List own orders, manual create |
| `GET`, `PATCH`, `DELETE /v1/orders/{id}` | Read with evidence and flags, edit, soft delete. PATCH and DELETE refuse an approved Order (A16a) |
| `POST /v1/orders/upload` | Upload a PDF, returns 201 with the Order |
| `POST /v1/orders/{id}/review` | `{"decision": "approve"}` or `"deny"`, person only, audited as `order.approve` or `order.deny` |
| `GET /v1/orders/{id}/document` | Original PDF |
| `GET /health` | Liveness |

`POST /auth/register` requires a signup code.

Errors: 401 no token, 403 wrong signup code, 404 not the caller's, 409 stale write, denied Order, or approved Order (A16a), 413 too big, 415 not a PDF, 422 unreadable or invalid, 429 over quota.

### Running locally
The app behaves the same locally and deployed. Only environment variables differ, and every setting has a local default except the Anthropic key and the JWT secret, which come from `.env`.

| Path | Commands | Use |
| --- | --- | --- |
| **Dev** | `cd backend`, `uv sync`, `uv run python -m scripts.seed`, `uv run uvicorn app.main:app --reload`. In a second terminal: `cd frontend`, `npm install`, `npm run dev`. | Day-to-day work. Vite on 5173 proxies `/auth` and `/v1` to the API on 8000. |
| **Docker** | `docker compose up --build`, then open http://localhost:8000 | Same image as production, with the built frontend served by FastAPI and a named volume for SQLite. |

- `.env` lives at the repo root and both paths read it. `cp .env.example .env`, then add the key.
- Local SQLite is `backend/app.db`, gitignored.
- Without an Anthropic key the app still starts. CRUD and auth work, and uploads save as `needs_review` with an `extraction_failed` flag, so a missing key can't be mistaken for a real extraction.
- Tests need no key and no network: `cd backend`, `uv run pytest -q`.
- The clean-clone check (P11) runs both paths.

### Tests
Unit and API tests use a stub Claude client, so they are fast, free and need no key. Generated synthetic PDFs cover clean text, image-only, a bad NPI, two providers, a missing field and injected instructions. A separate `scripts/eval.py` runs those plus the sample against the real API on demand, and needs a key. The sample stays out of git (`samples/` is ignored).

### Known limits to state in the README
- Anthropic data handling: real PHI would need a BAA and zero data retention. This build is synthetic only.
- No encryption at rest beyond the host's, no separation of duties, no login rate limit, no document retention policy.
- SQLite means a single instance. Acceptable here, not for production.

---

## 6. What we'd cut first

In order.

1. Frontend polish: styling, filters, empty states
2. `GET /v1/orders/{id}/document`
3. GitHub Actions CI
4. `order.access_denied` audit event (the 404 stays)
5. The frontend's edit form (keep list, upload, detail and approve/deny)
6. `scripts/eval.py` as a script (run the same checks by hand)
7. The whole frontend. Swagger at `/docs` becomes the demo. GenHealth lists a frontend as optional, though the role is full stack, so this is a last resort.

**Never cut:** public deploy, local run path, upload, validators and flags, evidence, the deny rule, user isolation, audit, log safety, tests for those, the review pass, README, clean-clone check.

### Time checkpoints
| Time | Must be true | If not |
| --- | --- | --- |
| 10:40 | Spec and plan approved | Shorten the plan |
| 11:00 | Skeleton live on Railway, volume survives a restart | Switch host now. This is the one disqualifying risk. |
| 11:45 | Upload works at the public URL | Cut items 1 to 5 |
| 12:10 | Feature freeze | Cut whatever remains unfinished |
| 12:30 | Review pass done, chosen fixes are in | Fix only security and correctness blockers |
| 12:45 | README done, clean clone verified | Submit what passes |
| 12:55 | Email sent | |

## 7. Amendments after delivery

| Date | Area | Before | After | Why | Detail |
| --- | --- | --- | --- | --- | --- |
| 2026-11-10 | Approved Orders | An edit set the Order back to `needs_review` and cleared `reviewed_by`/`reviewed_at`. An approved Order could be deleted. | `approved` is final. PATCH and DELETE return 409, and nothing changes, including who approved it. | Downstream consumers act on an approval, so the approved record must not change after the fact. | [spec](superpowers/specs/2026-11-10-approved-orders-final-design.md), [plan](superpowers/plans/2026-11-10-approved-orders-final.md) |
