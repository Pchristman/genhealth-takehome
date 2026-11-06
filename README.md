# Order Intake Service

A Python API that turns an uploaded DME order PDF into an Order. It extracts the patient's first name, last name and date of birth, and the ordering provider's name and NPI. It keeps the source quote and page of each value, and sends anything doubtful to a person instead of guessing.

- Public URL: https://pchristman-genhealth.up.railway.app (Swagger at `/docs`)
- Repo: https://github.com/Pchristman/genhealth-takehome
- Design: [docs/design-spec.md](docs/design-spec.md)
- Plan: [docs/implementation-plan.md](docs/implementation-plan.md)

The running service needs a signup code to register. It is provided separately and is not in this repo.

Where this README and the spec differ, this README describes what was built. The spec was written first: it has open signup and a 120 s timeout, and the build has a signup code and a 240 s model deadline.

**Scanned PDFs always go to a person.** A quote can only be verified against a text layer, and a scan has none. The model reads the page image, and nothing in code can confirm what it read, so every value on a scan is `unverified` and the Order is `needs_review`. A searchable scan (a page covered by an image, with an OCR layer on top) counts as a scan, because an OCR layer is also a guess about the image. The cost is a person's time on every fax. The alternative is approving a value no code has checked, where a wrong one means a denied claim or a delayed patient.

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

This spends real model calls: seven of the generated PDFs reach the model, and the three bad files are refused first. `--pdf` adds GenHealth's sample, which stays out of git.

```powershell
cd backend
$env:SIGNUP_CODE = "<the signup code>"
uv run python -m scripts.eval --base-url http://localhost:8000 --pdf "..\samples\DME Patient Demo Document CPAP.fax.pdf"
```

It registers a throwaway user with a generated password and prints one line per file: field names, pass or fail, status, flag codes and verification values, never patient values. It exits non-zero on any mismatch. Each run leaves that user and its synthetic orders in the database, and uses 8 of the 100 uploads the service allows per day.

## Using the API

The upload flow with curl. On Windows, run this in Git Bash.

```bash
BASE=https://pchristman-genhealth.up.railway.app

curl -X POST "$BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "at-least-12-characters", "signup_code": "<provided separately>"}'

TOKEN=$(curl -s -X POST "$BASE/auth/token" \
  -d "username=you@example.com" \
  -d "password=at-least-12-characters" | python -c "import json, sys; print(json.load(sys.stdin)['access_token'])")

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
| `PATCH /v1/orders/{id}` | Edit an order. It returns to `needs_review` |
| `DELETE /v1/orders/{id}` | Soft delete. Reads return 404 and the PDF bytes are cleared |
| `POST /v1/orders/{id}/review` | `{"decision": "approve"}` or `{"decision": "deny"}`, person only |
| `GET /v1/orders/{id}/document` | The original PDF |
| `GET /health` | Liveness |

Errors: 401 no token, 403 wrong signup code, 404 not yours or not found, 413 too big, 415 not a PDF, 422 unreadable or invalid, 429 over quota.

## What works

Covered by the test suite, with a stub in place of the model:

- Register, log in, and CRUD on Orders, stored in SQLite.
- PDF upload. Bad files are refused before any model call: not a PDF, encrypted, corrupt, over 20 MB or over 100 pages.
- Extraction of the five fields, each with a source quote and PDF page number.
- Checks before saving: NPI (10 digits, starting with 1 or 2, passing Luhn), date of birth (not in the future, at most 120 years old), missing values, placeholders such as repeated-character names, and quotes against the page's text layer.
- Code sets the status. `approved` needs all five fields present, verified and free of flags. Anything else is `needs_review`, with the value kept as printed and a flag. Only a person can deny.
- Isolation by `owner_id`. Another user's order returns 404, the same as one that doesn't exist.
- An audit row, ids only, for every read or change of a patient record.
- Logs built from a field allowlist, with no document text, model output or patient fields.

The React frontend lists, uploads, edits and reviews orders, and opens the source PDF. It has no automated tests. CI runs its lint and build.

Live status:

- TODO(verify): public URL serves the merged main commit, checked against origin/main.
- TODO(verify): eval results at the public URL, by fixture, including GenHealth's sample.
- TODO(verify): clean clone installs, passes tests, and runs on the dev and Docker paths.
- TODO(verify): CI passes on GitHub.
- TODO(verify): final test count on main.

## Design decisions

The spec has the full list, with rejected alternatives. The ones that matter most:

- **Save and flag, don't reject.** A doubtful value is saved as `needs_review`, printed as it was. Rejecting it would lose the fax, and blanking it would hide what the reviewer needs to see.
- **Code approves, not the model.** The model's output has no status, confidence or free-text field, and it has no tools. A document that talks the model round has nothing to change.
- **The model transcribes, code checks.** Values are copied as printed. Code parses dates and checks NPIs, so a misprinted digit can't be quietly repaired.
- **Retry only what the model can fix.** Up to 3 attempts for a schema error or a quote that isn't on the page. Never for a bad NPI or date, which would press the model to invent a passing value.
- **Verification is per page.** A typed cover sheet over scanned pages must not make the scans count as verified. A page under 25 non-space characters has no text layer.
- **The PDF goes straight to Claude.** It reads both page images and text. A separate OCR step would add a dependency and still produce a guess about the image.
- **Uploads are synchronous.** The request waits for the result, up to a 240 s model deadline, with at most 8 extractions at once. It is easy to test by hand, and it is the first thing to change for real volume.
- **PDF parsing runs in a subprocess.** A crafted file can then only exhaust a process that can be killed, not the service.
- **One way to load an Order.** `get_order` filters on owner and deletion, so one forgotten filter can't leak a record.

### Assumptions

- The ordering provider is whoever signed or issued the order. If the document doesn't make that clear, the value is null with an `ambiguous` flag.
- Dates are US month/day.
- Several patients in one PDF give one Order, with a `multiple_patients` flag.
- A PDF that isn't an order is still saved, with null fields and `missing` flags.
- If the model call fails, the Order is saved as `needs_review` with an `extraction_failed` flag, and the PDF is kept.
- A person entering or editing an order gets a 422 for a bad value, because they can fix it on the spot, unlike a fax.

## Trade-offs and what we cut

- **Every fax goes to a person.** Scans are the main path in this domain, so most real orders won't be approved automatically. That is the intended trade: review time for safety.
- **Synchronous upload.** Simple, but it holds a connection for the whole model call, and 8 concurrent extractions is the ceiling.
- **SQLite with PDFs as BLOBs.** One file on one volume, which means one instance. Fine for this, not for production.
- **No NPPES lookup.** The NPI check proves a number is well formed, not that it belongs to this provider.
- **Extra order fields cut.** Items and diagnosis codes are out of scope.
- **Separation of duties cut.** An owner can review their own order.
- **Signup changed from open to a code**, after review showed open signup let anyone spend the model budget.
- **Deploys have a manual step.** Railway deploys from `main`, but push triggers didn't fire, so `railway service source connect` is run after each push and the live commit is then checked.

## How AI was used

Built with Claude Code in this repo.

- **Spec and plan first.** Brainstorming produced the spec, then the plan, and each needed human approval before work moved on.
- **Agents implemented the tasks** in parallel git worktrees.
- **An adversarial review agent reviewed each task**, and separate agents fixed the findings.
- **The human owned the decisions.** The owner reviewed findings, chose what to fix, and made the scope calls, such as user scoping over an org layer.

The reviews caught real defects. Two examples: a value and quote truncated together (`2/17/1961` read from `12/17/1961`) passed the check and gave a false approval, and a tiny PDF could exhaust memory when parsed. The agents also made mistakes, listed below.

## Corrections

| What went wrong | How it was caught | The fix |
| --- | --- | --- |
| The agent kept an org layer alongside user scoping, carrying `org_id` on every row even though GenHealth's requirement is per user. | Caught in human review of the design spec: the standard should be user scoped. | Removed `Organization` and `org_id`. `owner_id` is now the only scope, and another user's record returns 404. |
| The agent said a push had triggered a Railway deploy when the service had no trigger. | Caught by checking the deployed commit against origin/main, after `/v1/orders` still returned 404. | Connected the source, and made checking the live commit part of each deploy. |
| A merge was pushed with 3 failing tests, because the command chain had no gate. Railway never deployed it, but it is in history. | Caught by reading the test output afterwards. | Merges and pushes now stop on a failed test run. |
| The agent's first frontend check covered only phone width. | Caught by the owner in review. | Desktop and phone are both checked. |

<!-- Append further rows directly under the last table row. -->

## Security notes

- Synthetic data only. Real PHI would need a BAA with Anthropic and zero data retention.
- No encryption at rest beyond the host's.
- The container runs as root, because the Railway volume mounts as root.
- No login rate limit.
- The signup code is short and guessable, so it is not the real cost control. Upload quotas (20 per user and 100 overall per day) and the Anthropic spend limit are the real cost cap.
- No separation of duties.
- Single-instance SQLite.
- Document text is untrusted input. It sits in the user turn as data, the model has no tools, and instructions found in a document become a flag.
- Secrets come from the environment. The signup code, keys and JWT secret are not in the repo.

## Next steps

- NPPES lookup, to check that an NPI belongs to the named provider.
- OCR-based verification of scans, so a second reading can support the model's on a scan.
- An async job queue for uploads.
- Shared team queues.
- A document retention policy.
- Migrations. There is no migration tool today, and `create_all` won't alter existing tables.
