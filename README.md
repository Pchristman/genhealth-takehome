# Order Intake Service

A Python API that turns an uploaded DME order PDF into an Order. It extracts the patient's first name, last name and date of birth, and the ordering provider's name and NPI. It keeps the source quote and page of each value, and sends anything doubtful to a person instead of guessing.

- Public URL: https://pchristman-genhealth.up.railway.app (Swagger at `/docs`)
- Repo: https://github.com/Pchristman/genhealth-takehome
- Design: [docs/design-spec.md](docs/design-spec.md)
- Plan: [docs/implementation-plan.md](docs/implementation-plan.md)

The running service needs a signup code to register. It is provided separately and is not in this repo.

Where this README and the spec differ, this README describes what was built. The spec was written first: it has a 120 s timeout, and the build has a 240 s model deadline.

**Scanned PDFs always go to a person.** A quote can only be verified against a text layer, and a scan has none. The model reads the page image, and nothing in code can confirm what it read, so every value on a scan is `unverified` and the Order is `needs_review`. A searchable scan (a page covered by an image, with an OCR layer on top) counts as a scan, because an OCR layer is also a guess about the image. The cost is a person's time on every fax, but a wrong value means a denied claim or a delayed patient.

## How to run it

You need [uv](https://docs.astral.sh/uv/), Node 24 and, for the Docker path, Docker Desktop. These commands work in PowerShell on Windows.

### Configuration

Copy the example file, then fill it in. Both paths read `.env` at the repo root.

```powershell
Copy-Item .env.example .env
```

| Variable | What it is |
| --- | --- |
| `JWT_SECRET` | Signs login tokens. Required, at least 32 characters. Generate one: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SIGNUP_CODE` | The code a new user must give to register. Choose any value for local use. While it is blank, signup is closed. |
| `QUOTA_USER_DAILY`, `QUOTA_GLOBAL_DAILY` | Uploads allowed per day, counted in UTC. Defaults are 20 and 100. The deployed service uses 50 and 200. |
| `ANTHROPIC_API_KEY` | Without it the app still starts. CRUD and auth work, and uploads save as `needs_review` with an `extraction_failed` flag. |

`.env` is gitignored. Never commit it.

### Dev

```powershell
cd backend
uv sync
uv run python -m scripts.seed
uv run uvicorn app.main:app --reload
```

The seed script creates two demo users and prints the password once, or uses `SEED_PASSWORD`. In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/auth` and `/v1` to the API on port 8000. The local database is `backend/app.db`, also gitignored.

### Docker

```powershell
docker compose up --build
```

Open http://localhost:8000. This is the production image, with the built frontend served by FastAPI and SQLite on a named volume.

### Tests

```powershell
cd backend
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use a stub Claude client, so they need no key and no network. CI runs these, plus the frontend's lint and build, on every push and pull request.

### Eval against a running service

This spends real model calls: eight of the generated PDFs reach the model, and the three bad files are refused first. `--pdf` adds GenHealth's sample, which stays out of git.

```powershell
cd backend
$env:SIGNUP_CODE = "<the signup code>"
uv run python -m scripts.eval --base-url http://localhost:8000 --pdf "..\samples\sample.pdf"
```

It registers a throwaway user with a generated password and prints one line per file: field names, pass or fail, status, flag codes and verification values, never patient values. It exits non-zero on any mismatch. Each run leaves that user and its synthetic orders in the database, and uses 9 of the service's daily uploads with `--pdf`.

### Messy document sets

`scripts/make_messy_faxes.py` generates 12 synthetic faxes with known answers, and `scripts/eval_messy.py` scores them, or any folder with the same `expected.json`, against a running service.

## Using the API

The upload flow with curl. On Windows, run this in Git Bash.

```bash
BASE=https://pchristman-genhealth.up.railway.app

curl -X POST "$BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "at-least-12-characters", "signup_code": "<provided separately>"}'

TOKEN=$(curl -s -X POST "$BASE/auth/token" \
  -d "username=you@example.com" \
  -d "password=at-least-12-characters" | sed -E 's/.*"access_token":"([^"]+)".*/\1/')

curl -X POST "$BASE/v1/orders/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@order.pdf;type=application/pdf"
```

The response is the Order, with `evidence` (value, quote, page and verification per field) and `flags`.

| Method and path | Purpose |
| --- | --- |
| `POST /auth/register` | Sign up with email, password and `signup_code` |
| `POST /auth/token` | Log in, OAuth2 form, returns a bearer token valid for 60 minutes |
| `GET /auth/me` | The current user |
| `GET /v1/orders` | List your orders, paged, with a status filter |
| `POST /v1/orders` | Create an order by hand |
| `POST /v1/orders/upload` | Upload a PDF in multipart field `file`, returns 201 with the Order |
| `GET /v1/orders/{id}` | Read an order with its evidence and flags |
| `PATCH /v1/orders/{id}` | Edit an order that needs review. Approved and denied orders return 409 |
| `DELETE /v1/orders/{id}` | Soft delete. Reads return 404 and the PDF bytes are cleared. Approved orders return 409 |
| `POST /v1/orders/{id}/review` | `{"decision": "approve"}` or `{"decision": "deny"}`, person only |
| `GET /v1/orders/{id}/document` | The original PDF |
| `GET /health` | Liveness |

Errors: 401 no token, 403 wrong signup code, 404 not yours or not found, 409 stale write or wrong status, 413 too big, 415 not a PDF, 422 unreadable or invalid, 429 over quota, 503 busy or the save failed. Up to 8 extractions run at once. Beyond that, upload returns 503 with `Retry-After`.

## What works

Covered by the test suite, with a stub in place of the model:

- Register, log in, and CRUD on Orders, stored in SQLite.
- PDF upload. Bad files are refused before any model call: not a PDF, encrypted, corrupt, over 20 MB or over 100 pages.
- Extraction of the five fields, each with a source quote and PDF page number.
- Checks before saving: NPI (10 digits, starting with 1 or 2, passing Luhn), date of birth (not in the future, at most 120 years old), missing values, placeholders such as repeated-character names, and quotes against the page's text layer.
- Code sets the status. `approved` needs all five fields present, verified and free of flags. Anything else is `needs_review`, with the value kept as printed and a flag. Only a person can deny.
- An approved order is final. It can't be edited, reopened or deleted, and who approved it never changes.
- Isolation by `owner_id`. Another user's order returns 404, the same as one that doesn't exist.
- An audit row, ids only, for every read or change of a patient record.
- Logs built from a field allowlist, with no document text, model output or patient fields.

The React frontend lists, uploads, edits and reviews orders, and opens the source PDF. It has no automated tests. CI runs its lint and build.

## What I checked on the live service

The final deployed commit is `9077cf9`. It matched `main` when deployed, and CI passed on it.

GenHealth's sample, by API and through the UI: it picked the prescriber over the referring physician, kept the NPI as printed and flagged it, flagged the DOB, marked every quote `unverified`, and sent the Order to `needs_review`. A second user got a 404. CRUD, the refusals (non-PDF, over 20 MB, bad token) and six uploads at once also worked there.

`scripts/eval.py` passed 11 of 12 cases on `c0252ee`. The miss was a wrong expected name for a fixture with a blank scan image. The app still marked it `unverified` and sent it to review, and that fixture no longer scores the name.

Locally in Docker, 12 generated messy faxes and 7 synthetic orders on real blank form layouts got 95 of 95 fields right. The forms first scored 33 of 35, which exposed two date formats the parser couldn't read, so I fixed those and reran them. These documents are cleaner than a rough real fax, so this doesn't prove the service on the worst scans.

A clean clone from GitHub installed, passed its 886 backend tests, built the frontend, and ran on the dev and Docker paths at `c0252ee`. Later commits changed the styling, the README and an eval expectation, not the app's backend code.

## Design decisions

The spec has the full list, with the alternatives I rejected. These matter most:

- A doubtful value is saved as `needs_review`, printed as it was. Rejecting it would lose the fax, and blanking it would hide what the reviewer needs to see.
- Code approves, not the model. The model's output has no status, confidence or free-text field, and it has no tools, so an injected instruction has nothing to change.
- The model transcribes and code checks. Code parses dates and checks NPIs, so a misprinted digit can't be quietly repaired.
- Retries are for what the model can fix: up to 3 attempts for a schema error or a quote that isn't on the page. A bad NPI or date never retries, because that would press the model to invent a passing value.
- Verification is per page, so a typed cover sheet can't make the scans behind it count as verified. A page under 25 non-space characters has no text layer.
- The PDF goes straight to Claude, which reads both page images and text. A separate OCR step would add a dependency and still be a guess about the image.
- Uploads are synchronous, up to a 240 s model deadline and 8 extractions at once. That's easy to test by hand, and it's the first thing I'd change for real volume.
- PDF parsing runs in a subprocess, so a crafted file can only exhaust a process that can be killed.
- `get_order` is the one way to load an Order. It filters on owner and deletion, so no route can forget that filter.

### Assumptions

- The ordering provider is whoever signed or issued the order. If the document doesn't make that clear, the value is null with an `ambiguous` flag.
- Dates are US month/day.
- Several patients in one PDF give one Order, with a `multiple_patients` flag.
- A PDF that isn't an order is still saved, with null fields and `missing` flags.
- If the model call fails, the Order is saved as `needs_review` with an `extraction_failed` flag, and the PDF is kept.
- A person entering or editing an order gets a 422 for a bad value, because they can fix it on the spot, unlike a fax.

## Trade-offs and what I cut

- Scans are the main path in this domain, so most real orders won't be approved automatically.
- A synchronous upload holds a connection for the whole model call.
- SQLite with PDFs as BLOBs means one file on one volume, so one instance.
- There's no NPPES lookup. The NPI check shows a number is well formed, not that it belongs to this provider.
- I left out extra order fields, such as items and diagnosis codes.
- There's no separation of duties, so an owner can review their own order.
- Signup started open and now needs a code, because open signup let anyone spend the model budget.
- Deploys have a manual step. Railway deploys from `main`, but push triggers didn't fire, so I run `railway service source connect` after each push and check the live commit.

## How AI was used

I built this with Claude Code. I started with a brainstorm that became the spec, then the plan, and I approved each before work moved on. Agents implemented the tasks in parallel git worktrees. An adversarial review agent then checked each task, and separate agents fixed what it found. I reviewed the findings, chose what to fix, and made the scope calls, such as scoping by user rather than by org.

The reviews caught real defects. In one, a value and quote truncated together (`2/17/1961` read from `12/17/1961`) passed the check and gave a false approval. In another, a tiny PDF could exhaust memory when parsed. The agents also made mistakes, which are listed below.

## Changes since submission

### Approved orders are final (2026-11-10)

**Before:** editing an approved order sent it back to `needs_review` and erased who approved it, and an approved order could be deleted.

**Now:** `PATCH` and `DELETE` on an approved order return 409. This holds whether code approved it on upload or a person approved it. Who approved it and when (`reviewed_by`/`reviewed_at`, both empty for a code approval) never changes. A wrong value on an approved order means creating a new order.

**Why:** an approval is what downstream use relies on, so it has to stay a fixed record.

**Verified:** `backend/tests/test_final_orders.py` and the rewritten tests pass in the full suite, and the detail page was checked by hand. This change is not deployed yet. "What I checked on the live service" describes `9077cf9`, which predates it.

## Corrections

| What went wrong | How it was caught | The fix |
| --- | --- | --- |
| The agent kept an org layer alongside user scoping, carrying `org_id` on every row even though GenHealth's requirement is per user. | I caught it reviewing the design spec: the standard should be user scoped. | Removed `Organization` and `org_id`. `owner_id` is now the only scope, and another user's record returns 404. |
| The agent said a push had triggered a Railway deploy when the service had no trigger. | Checking the deployed commit against origin/main, after `/v1/orders` still returned 404. | Connected the source, and made checking the live commit part of each deploy. |
| A merge was pushed with 3 failing tests, because the command chain had no gate. Railway never deployed it, but it is in history. | Reading the test output afterwards. | Merges and pushes now stop on a failed test run. |
| The agent's first frontend check covered only phone width. | I asked why it was only phone width. | Desktop and phone are both checked. |

## Security notes

- Synthetic data only. Real PHI would need a BAA with Anthropic and zero data retention.
- No encryption at rest beyond the host's.
- The container runs as root, because the Railway volume mounts as root.
- No login rate limit.
- Upload quotas and the Anthropic spend limit are the real cost cap, not the signup code.
- `fallbacks: "default"` is on, so a request refused by a safety classifier can run on another Claude model. Medical text can trip one by mistake.
- `version` catches writes that race each other. It doesn't catch a second tab acting on an old screen.
- A short real name such as `Na` or `Same` reads as a placeholder, and a person can't enter it either, so that Order can only be denied.
- A PDF with a malformed page tree, such as a bad `/Rotate`, is refused as unreadable.
- A very large compressed stream is contained by the worker's limits but may still reach the model and spend one upload.
- Document text is untrusted input. It sits in the user turn as data, the model has no tools, and instructions found in a document become a flag.
- Secrets come from the environment. The signup code, keys and JWT secret are not in the repo.

## Next steps

- NPPES lookup, to check that an NPI belongs to the named provider.
- OCR-based verification of scans, so a second reading can support the model's on a scan.
- An async job queue for uploads.
- Shared team queues.
- A document retention policy.
- Bring-your-own Claude key, depending on the business need. An internal tool is simpler with one managed key. A customer-facing product could use it for billing, but the service would then hold customers' keys, which needs its own security review.
- Migrations. There is no migration tool today, and `create_all` won't alter existing tables.
