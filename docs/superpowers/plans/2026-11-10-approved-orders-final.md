# Approved Orders Are Final Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an approved Order a final, read-only record. `PATCH` and `DELETE` on an approved Order return 409 and change nothing.

**Architecture:** Two route guards in `backend/app/orders.py`, placed after `get_order` so ownership is still checked first. They follow the existing denied-Order refusal. The `version` column already handles races. No schema change. The UI only gains a note.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy on SQLite, pytest, React + TypeScript (Vite).

**Spec:** [docs/superpowers/specs/2026-11-10-approved-orders-final-design.md](../specs/2026-11-10-approved-orders-final-design.md)

## Global Constraints

- Edit refusal body, exactly: `{"detail": "An approved Order is final and can't be edited."}`, status 409.
- Delete refusal body, exactly: `{"detail": "An approved Order is final and can't be deleted."}`, status 409.
- The denied-Order PATCH refusal keeps its message: `"A denied Order can't be edited."`
- Order of checks: body validation (422), then `get_order` ownership (404), then the final-status check (409), then the write.
- A refused PATCH or DELETE writes no audit row and no change. `version` and `updated_at` don't move.
- The approver signal: code approval means `reviewed_by` and `reviewed_at` are null; person approval means both are set. A refusal never changes either.
- `denied` and `needs_review` keep their current behaviour, apart from the existing tests the spec lists as rewritten.
- Synthetic data only in tests. Reuse `ORDER` from `tests/test_orders.py` and `tests/pdf_fixtures`.
- Write the failing test first. Commit messages say why and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Gate before every commit: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`

## Review Focus

1. **A stale browser tab with an edit open on an Order that another tab has since approved.** Saving should show the final message and reload into the read-only view, not a blank error. Checked by hand in Task 3 (the frontend has no test runner).
2. **The same refused PATCH sent twice.** Neither attempt should move `version` or `updated_at`. Task 1, test 1 sends the PATCH twice.
3. **A denied Order after the new guard is added.** It must still get its own message, not the approved one. This is already pinned by `test_patch_on_denied_returns_409_and_the_denial_stands`, which Task 1 runs unchanged.
4. **A refused DELETE on an approved Order that has a PDF.** The PDF must stay downloadable. Task 2, test 4 asserts `GET /document` is 200.
5. **An approved Order in the status-filtered list after a refused DELETE.** It must still be listed under `approved`. Task 2, test 4 asserts it.

---

### Task 1: PATCH refuses approved Orders

**Files:**
- Create: `backend/tests/test_final_orders.py`
- Modify: `backend/app/orders.py` (`update_order`, around lines 178-199; new module constants near the top)
- Modify: `backend/tests/test_orders.py` (lines 164-232)
- Modify: `backend/tests/test_stale_writes.py` (`test_a_stale_write_leaves_the_connection_clean`, around lines 110-118)

**Interfaces:**
- Produces: `app.orders.APPROVED_NOT_EDITABLE: str = "An approved Order is final and can't be edited."` and `app.orders.DENIED_NOT_EDITABLE: str = "A denied Order can't be edited."` (moves the existing inline string into a constant).
- Produces, in `tests/test_final_orders.py`:
  - `approved_order(client, db, headers, reviewed_by: str | None = "reviewer-id") -> dict` creates a manual Order, `set_status`es it to approved, and returns `GET /v1/orders/{id}` JSON.
  - `stored_values() -> list[str]` returns `ORDER`'s five values, for the no-leak assertions.

- [ ] **Step 1: Write the failing tests in `tests/test_final_orders.py`.** Import `ORDER`, `create_order`, `set_status` from `tests.test_orders`, and `add_flag`, `add_evidence`, `user_id` from `tests.test_review`.
  - `test_patch_on_approved_is_refused`, parametrized with ids:
    - `changed`: `{"provider_name": "Ione Vantongeren DO"}`
    - `same`: `{"provider_name": ORDER["provider_name"]}`
    - `padded`: `{"provider_name": f"  {ORDER['provider_name']} "}`
    - `all_fields`: `ORDER`
    - `empty`: `{}`

    Assert that each of **two** identical PATCHes gives `status_code == 409` and `json() == {"detail": APPROVED_NOT_EDITABLE}`, that the GET after equals the GET before, and that `db.get(Order, id).version` is unchanged (call `db.rollback()` first, so the read is fresh).
  - `test_refused_patch_leaves_flags_and_evidence_alone`: approved Order, `add_flag(db, id, "provider_npi", "invalid_npi")`, `add_evidence(db, id, "provider_npi", "9182734556")`, then PATCH `{"provider_npi": "1245319599"}`. Assert 409, the flag's `resolved_at is None`, and the evidence row's `corrected is False`.
  - `test_patch_cannot_set_status_on_approved`: PATCH `{"status": "needs_review"}` on an approved Order. Assert 422, the stored status is `approved`, `"approved" not in response.text`, and no value from `stored_values()` is in `response.text`.
  - `test_bad_body_on_approved_is_422_and_changes_nothing`, parametrized over `{"provider_npi": "1234567890"}` (bad check digit) and `{"patient_first_name": None}`. Assert 422, the GET after equals the GET before, no value from `stored_values()` and not the submitted value (when it is a string) is in `response.text`, and every error keeps a safe message: `all(e["type"] in SAFE_MESSAGE_TYPES or e["msg"] == "Invalid value." for e in response.json()["detail"])`, with `SAFE_MESSAGE_TYPES` imported from `app.main`.
  - `test_needs_review_order_can_still_be_edited`: a fresh manual Order, PATCH `{"provider_name": "Ione Vantongeren DO"}`. Assert 200, `status == "needs_review"`, and the value changed.

- [ ] **Step 2: Bring the existing approved-Order tests in line with the new rule.**

  I swept every test that sets `approved`, uploads a clean PDF, or calls PATCH or DELETE. Each one falls into exactly one of the groups below. If a test not listed here fails after Step 4, stop and report it. Don't adjust it to pass.

  **Rewrite: these encode "an edit reopens an approved Order".**

  | Test | Change | Why |
  |---|---|---|
  | `test_orders.py::test_patch_on_approved_returns_to_needs_review` | Rename it to `test_patch_on_approved_is_refused_and_the_approval_stands` and keep it in `test_orders.py`. Same setup: `set_status(approved, reviewed_by="reviewer-id")`, then PATCH `provider_name`. Assert 409 with `{"detail": APPROVED_NOT_EDITABLE}`, `status == "approved"`, `reviewed_by == "reviewer-id"`, `reviewed_at` unchanged from a GET taken first, and `provider_name == ORDER["provider_name"]`. | The old test asserted the opposite rule. Its replacement sits next to the denied-Order test so the two final statuses read side by side. `test_final_orders.py` test 1 covers the body variants. |
  | `test_orders.py::test_patch_with_no_real_change_leaves_the_order_alone` | Drop the `set_status(... approved ...)` line, so it runs on a `needs_review` Order. The comment becomes `# same status, same updated_at`. | The no-op path is still worth covering. On an approved Order it would now be a 409, which test 1's `same`, `padded`, `all_fields` and `empty` cases already cover. |
  | `test_orders.py::test_patch_changing_one_of_several_values_counts_as_a_change` | Drop the `set_status` line. Replace `status == "needs_review"` with `response.json()["provider_npi"] == "1245319599"`, and keep the `updated_at` assertion. | What it tests (one changed field among identical ones counts as a change) doesn't depend on status. It used a status reset as its evidence of a change; the new evidence is the stored value. |
  | `test_stale_writes.py::test_a_stale_write_leaves_the_connection_clean` | The retry asserts `409` and `json() == {"detail": APPROVED_NOT_EDITABLE}`, then `stored(engine, order_id).status == OrderStatus.approved`. | It used to show recovery by the retry succeeding. Now it shows recovery by the retry reading the committed approval: a poisoned session would repeat the stale-write message instead. |

  **Keep unchanged, but confirm each passes after Step 4. They involve approval, and the reason they still hold is not obvious.**

  | Test | Why it still holds |
  |---|---|
  | `test_stale_writes.py::test_stale_patch_cannot_write_over_an_approval` | The PATCH loaded the Order while it was `needs_review`, so the new guard (which checks the loaded row) passes, and the version check refuses the write with the stale message. That race is exactly why the guard alone isn't enough. |
  | `test_stale_writes.py::test_stale_delete_cannot_remove_an_approved_order` | The same race for DELETE. Task 2's guard also passes here, and the version check refuses the write. |
  | `test_review.py::test_review_requires_needs_review_status` | An approved Order can't be reviewed again. This is unchanged and is part of "can't go back to `needs_review`". |
  | `test_orders.py::test_list_is_paged_and_filters_by_status` | It only reads an approved Order. |
  | `test_upload.py` tests asserting `status == "approved"` (`test_upload_clean_text_pdf_is_approved`, `test_clean_text_pdf_stays_verified`, `test_upload_logs_contain_no_patient_values`, and the second-connection test near line 453) | These cover code approval on upload. None of them edits or deletes the Order afterwards. |
  | `test_orders.py::test_patch_on_denied_returns_409_and_the_denial_stands`, `test_audit.py::test_refused_patch_on_a_denied_order_writes_no_update_audit` | The denied rule is unchanged. They pin that the new approved check doesn't take over the denied message. |

  **Unaffected: these PATCH or DELETE only `needs_review` Orders.** `test_orders.py` (round trip, length, blanking, deleted-404, extra fields, no-echo), `test_review.py` (flag and evidence correction, `test_patch_writes_the_order_once`), `test_validators.py` (PATCH 422 and accept cases), `test_audit.py` (no-change read, no patient values), and `test_isolation.py` (foreign, deleted and missing ids are all 404).

- [ ] **Step 3: Run them and confirm the failures.**
  Run: `cd backend && uv run pytest tests/test_final_orders.py tests/test_orders.py tests/test_stale_writes.py -q`
  Expected: the `test_patch_on_approved_is_refused` cases, `test_refused_patch_leaves_flags_and_evidence_alone`, `test_patch_on_approved_is_refused_and_the_approval_stands` and `test_a_stale_write_leaves_the_connection_clean` FAIL (200 instead of 409, or an ImportError on `APPROVED_NOT_EDITABLE`). The 422 tests and `test_needs_review_order_can_still_be_edited` pass, because they pin existing behaviour on the new path.

- [ ] **Step 4: Implement in `update_order`.** Right after `get_order`:
  - if `order.status == OrderStatus.approved`, raise `HTTPException(409, detail=APPROVED_NOT_EDITABLE)`,
  - keep the denied check, now using `DENIED_NOT_EDITABLE`,
  - delete the `if order.status == OrderStatus.approved:` reset block and its comment.

  Nothing else in the route changes.

- [ ] **Step 5: Run the full gate.**
  Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
  Expected: all pass, including the unchanged `test_patch_on_denied_returns_409_and_the_denial_stands`.

- [ ] **Step 6: Stop and show the diff and test output. Commit after approval.**
  ```bash
  git add backend/app/orders.py backend/tests/test_final_orders.py backend/tests/test_orders.py backend/tests/test_stale_writes.py
  git commit -m "Refuse edits to approved Orders so an approval can't be undone by a change" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 2: DELETE refuses approved Orders, and the approval-path matrix

**Files:**
- Modify: `backend/tests/test_final_orders.py`
- Modify: `backend/app/orders.py` (`delete_order`)

**Interfaces:**
- Consumes: `APPROVED_NOT_EDITABLE`, and `approved_order` / `stored_values` from Task 1.
- Produces: `app.orders.APPROVED_NOT_DELETABLE: str = "An approved Order is final and can't be deleted."`

- [ ] **Step 1: Write the failing tests.**
  - `test_delete_on_approved_is_refused`: approved Order with `attach_document`. Assert that DELETE gives 409 with `{"detail": APPROVED_NOT_DELETABLE}`; that GET is 200 and `deleted_at` is null in the DB; that `Document.content` is not None; that `GET /v1/orders/{id}/document` is 200; and that the id is in `GET /v1/orders?status=approved` items.
  - `test_refused_write_records_nothing`: approved Order. Snapshot the audit rows whose `resource_id == id`, send a refused PATCH and a refused DELETE, and assert the rows are unchanged.
  - `test_someone_elses_approved_order_is_404`: Alice's approved Order, with Bob's headers. Assert that PATCH `{"provider_name": "Ione Vantongeren DO"}` and DELETE each give 404, that Alice's GET is unchanged, and that `("order.access_denied", "order", id)` was audited with `actor_id == user_id(bob)`.
  - `test_denied_order_can_still_be_deleted`: `set_status(denied)`, then DELETE gives 204.
  - `test_needs_review_order_can_still_be_deleted`: DELETE gives 204.
  - `test_approved_order_is_final_whoever_approved_it`, parametrized over:
    - path:
      - `code`: `stub_llm(reply(clean_result()))`, then `upload(client, headers)`
      - `person_upload`: `stub_llm(reply(clean_result()))`, then `upload(client, headers, fx.scan_pdf())`, then `post_review(... "approve")`
      - `person_manual`: `create_order`, then `post_review(... "approve")`
    - attempt:
      - `patch_changed`: PATCH `{"provider_name": "Ione Vantongeren DO"}`, expecting `APPROVED_NOT_EDITABLE`
      - `patch_empty`: PATCH `{}`, expecting `APPROVED_NOT_EDITABLE`
      - `delete`: expecting `APPROVED_NOT_DELETABLE`

    Each case asserts:
    1. Before the attempt: `status == "approved"`, and `source == "upload"` or `"manual"` as the path implies. For `code`, `reviewed_by is None and reviewed_at is None`. For the person paths, `reviewed_by == user_id(client, headers)` and `reviewed_at` is set.
    2. The attempt gives 409 with the expected detail.
    3. A GET afterwards equals the GET before. That covers the approver signal, status, values and `updated_at`.

    Import `upload` from `tests.test_upload`, `reply`/`clean_result` from `tests.llm_stub`, `fx` from `tests.pdf_fixtures`, and `post_review` from `tests.test_review`.
  - `test_only_a_clean_upload_is_approved_without_a_person`. This is a contract test: it pins current behaviour, so it passes on first run, and that's expected.
    - `create_order` gives `needs_review`.
    - A clean upload that is approved with `reviewed_by is None` has every `evidence[*].verification == "verified"` and `flags == []`.
    - `post_review(... "approve")` on a manual Order sets both `reviewed_by` and `reviewed_at`.

- [ ] **Step 2: Run them and confirm the failures.**
  Run: `cd backend && uv run pytest tests/test_final_orders.py -q`
  Expected: the DELETE tests and the matrix's `delete` cases FAIL (204 instead of 409, or an ImportError on `APPROVED_NOT_DELETABLE`). The matrix's PATCH cases, the 404, denied, needs-review and contract tests pass.

- [ ] **Step 3: Implement in `delete_order`.** Right after `get_order`, if `order.status == OrderStatus.approved`, raise `HTTPException(409, detail=APPROVED_NOT_DELETABLE)`. That's before any field or Document is touched.

- [ ] **Step 4: Run the full gate.**
  Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
  Expected: all pass, including `test_stale_writes.py::test_stale_delete_cannot_remove_an_approved_order` (the race test) and `test_isolation.py`.

- [ ] **Step 5: Stop and show the diff and test output. Commit after approval.**
  ```bash
  git add backend/app/orders.py backend/tests/test_final_orders.py
  git commit -m "Refuse deleting approved Orders so the approved record and its PDF are kept" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 3: Show the rule in the UI and the docs

**Files:**
- Modify: `frontend/src/OrderDetail.tsx` (`reviewNote`, around lines 198-207)
- Modify: `docs/design-spec.md` (line 111, A16 and the new A16a; line 189, the API row; line 197, the errors list; a new §7 at the end)
- Modify: `docs/implementation-plan.md` (line 110, the test list; a new section after "Decision added since the spec", line 40)
- Modify: `README.md` (the `PATCH` and `DELETE` rows, lines 122-123; "What works", around line 138; a new "Changes since submission" section before "Corrections", line 197)

- [ ] **Step 1: Change `reviewNote(order: Order): string`.** When `order.status === 'approved'`, append `' This order is final and can\'t be changed.'` to both the automatic and the by-a-person sentence. Leave `needs_review` and `denied` unchanged.

- [ ] **Step 2: Amend the docs. Record the change; don't rewrite history.** The original spec and plan were approved as delivered, so they keep their original text, marked as superseded, with the new rule added beside it. This follows the existing "Decision added since the spec" pattern in `docs/implementation-plan.md`. The README describes the service as it is now, so its reference rows show the new behaviour, and a dated section says what changed.

  **`docs/design-spec.md`**
  - Keep A16's row, and change its last cell to: `~~Status returns to needs_review.~~ Superseded 2026-11-10 by A16a.`
  - Add a row right after it: `| A16a | Edits to, or deletion of, an approved Order (amended 2026-11-10) | 409. Approved is final, whether code or a person approved it. Who approved it and when never changes. See §7. |`
  - API table, the `GET`, `PATCH`, `DELETE /v1/orders/{id}` row: append `. PATCH and DELETE refuse an approved Order (A16a)` to the description.
  - Errors line: `409 stale write or denied Order` becomes `409 stale write, denied Order, or approved Order (A16a)`.
  - New final section, `## 7. Amendments after delivery`, with one table row:

    | Date | Area | Before | After | Why | Detail |
    |---|---|---|---|---|---|
    | 2026-11-10 | Approved Orders | An edit set the Order back to `needs_review` and cleared `reviewed_by`/`reviewed_at`. An approved Order could be deleted. | `approved` is final. PATCH and DELETE return 409, and nothing changes, including who approved it. | Downstream consumers act on an approval, so the approved record must not change after the fact. | [spec](superpowers/specs/2026-11-10-approved-orders-final-design.md), [plan](superpowers/plans/2026-11-10-approved-orders-final.md) |

  **`docs/implementation-plan.md`**
  - In Task 2's test list, keep the `test_patch_on_approved_returns_to_needs_review` bullet, struck through, and append: `superseded 2026-11-10 by test_patch_on_approved_is_refused_and_the_approval_stands (A16a)`.
  - After the "Decision added since the spec" section, add `## Amendment after delivery (2026-11-10)` with one paragraph: approved Orders are final; PATCH and DELETE return 409; four tests were rewritten. Link the new plan for the details.

  **`README.md`**
  - API table: the `PATCH` row becomes `Edit an order that needs review. Approved and denied orders return 409`. The `DELETE` row becomes `Soft delete. Reads return 404 and the PDF bytes are cleared. Approved orders return 409`. The errors line is unchanged, since "wrong status" already covers it.
  - "What works": after the status bullet, add `- An approved order is final. It can't be edited, reopened or deleted, and who approved it never changes.`
  - New section, `## Changes since submission`, placed before `## Corrections`:
    - `### Approved orders are final (2026-11-10)`
    - **Before:** editing an approved order sent it back to `needs_review` and erased who approved it, and an approved order could be deleted.
    - **Now:** `PATCH` and `DELETE` on an approved order return 409. This holds whether code approved it on upload or a person approved it. Who approved it and when (`reviewed_by`/`reviewed_at`, both empty for a code approval) never changes. A wrong value on an approved order means creating a new order.
    - **Why:** an approval is what downstream use relies on, so it has to stay a fixed record.
    - **Verified:** the backend tests in `tests/test_final_orders.py`, plus the hand check in Task 3 Step 4. State whether it has been deployed. "What I checked on the live service" describes `9077cf9`, which predates this change, so don't claim live verification until it is deployed and checked.

- [ ] **Step 3: Run the frontend gate.**
  Run: `cd frontend && npm ci && npm run lint && npm run build`
  Expected: no lint errors, and the build succeeds.

- [ ] **Step 4: Check by hand in the running app** (`docker compose up --build`, or uvicorn plus `npm run dev`).
  - A manual Order approved through the UI shows the final note and no Edit buttons.
  - With the same `needs_review` Order open in two tabs, open an edit in tab A, approve in tab B, then save in tab A. Tab A shows "An approved Order is final and can't be edited." and reloads to the approved view (Review Focus 1).
  - If an Anthropic key is set, a clean text-PDF upload shows "Approved automatically… This order is final and can't be changed."

- [ ] **Step 5: Stop and show. Commit after approval.**
  ```bash
  git add frontend/src/OrderDetail.tsx docs/design-spec.md docs/implementation-plan.md README.md
  git commit -m "Tell reviewers an approved Order is final, and record the rule in the docs" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```
