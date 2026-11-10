# Approved Orders are final

**Date:** 2026-11-10
**Status:** Approved 2026-11-10
**Replaces:** decision A16 in [design-spec.md](../../design-spec.md) ("Edits to an approved Order: status returns to `needs_review`")

## Why

An approved Order means it is ready for downstream use (A6). Today any edit to an approved Order sends it back to `needs_review` and erases who approved it. That means the record downstream consumers acted on can change after the fact. An approved Order should be a historical record: what was approved, by whom and when, stays as it was.

## The rule

`approved` is a final status. Once an Order is approved:

- its fields can't be edited,
- its status can't change, including back to `needs_review`,
- it can't be deleted,
- it can still be read, listed, and its source PDF downloaded.

This applies to every approved Order, whether code approved it on upload or a person approved it through `/review`. There is no override. A wrong value on an approved Order is handled by creating a new manual Order.

The server enforces the rule. The UI only reflects it.

### Out of scope

- `denied` keeps today's rules: no edits (409), but it can be deleted.
- `needs_review` keeps today's rules: editable, reviewable, deletable.
- No new endpoint, status, column or migration.

## API contract

| Request on an `approved` Order | Before | After |
|---|---|---|
| `PATCH` with a changed value | 200, status reset to `needs_review`, `reviewed_by`/`reviewed_at` cleared | **409** `{"detail": "An approved Order is final and can't be edited."}` |
| `PATCH` with identical values, padded values, or `{}` | 200, no write | **409**, same body |
| `DELETE` | 204, soft delete, PDF bytes cleared | **409** `{"detail": "An approved Order is final and can't be deleted."}` |
| `POST /review` | 409 | 409 (unchanged) |
| `GET` Order, list, `GET /document` | 200 | 200 (unchanged) |

Every PATCH on an approved Order is refused, whatever the body, so the answer depends only on the status and never on what the client sent.

### Order of checks

1. **Body validation** (FastAPI, before the route runs). A malformed or invalid body is a 422 whatever the status. This includes `extra="forbid"` refusing `status`, `owner_id` and `source`. The Order is never loaded, so a 422 reveals nothing about it. The custom 422 handler in `app/main.py` never sends back the submitted input. Tests 7 and 8 assert both points on this path.
2. **Ownership** (`get_order`). Missing, deleted or someone else's Order is a 404 and audits `order.access_denied`, as today. Another user's approved Order is therefore a 404, never a 409, so its status is not revealed.
3. **Final status.** `approved` gives 409 for PATCH and DELETE. `denied` gives 409 for PATCH, with its existing message.
4. **The write**, as today.

### Audit

A refused PATCH or DELETE writes no audit row and changes nothing, the same as today's refused PATCH on a denied Order. Nothing is returned except the refusal.

### Concurrency

The existing `version` column (`version_id_col`) already covers the races:

- PATCH or DELETE loads a `needs_review` Order, an approval commits, then the write runs: the UPDATE matches no row, `StaleDataError` gives the existing 409 `"Order changed since it was loaded. Reload it and try again."`, and nothing is written.
- An edit commits while a review is in flight: `/review`'s atomic claim plus the version check give 409.

No new locking is needed.

## Who approved it

There is no schema change. The approver signal stays implicit and becomes a tested contract:

| Approved by | `reviewed_by` | `reviewed_at` | Only reachable via |
|---|---|---|---|
| Code | `null` | `null` | Upload whose every value is present, every quote `verified` against a text layer, and with no flags |
| A person | the user's id | set | `POST /v1/orders/{id}/review` with `approve` |

A refused PATCH or DELETE leaves both fields unchanged. A code approval is never stamped, and a person's approval is never cleared or overwritten. The UI's `reviewNote` already reads this signal.

## Code changes

**`backend/app/orders.py`**

- `update_order`: after `get_order`, refuse `approved` with 409 `APPROVED_NOT_EDITABLE`. The `denied` check stays, with its existing message. Remove the branch that resets an approved Order to `needs_review` and clears `reviewed_by`/`reviewed_at`.
- `delete_order`: after `get_order`, refuse `approved` with 409 `APPROVED_NOT_DELETABLE`, before any change to the Order or its Document.
- The messages are module constants, so tests import them rather than repeating the strings.

**`frontend/src/OrderDetail.tsx`**

- `reviewNote`: for an approved Order, add "This order is final and can't be changed." Edit controls are already hidden for anything but `needs_review` (`canEdit={needsReview}`), and the UI has no delete control. The existing 409 handling (`showConflict`) covers a stale page.

**Docs**

- `docs/design-spec.md`: A16 becomes "Edits to, or deletion of, an approved Order: 409. Approved is final." Update the 409 line in the errors list.
- `README.md`: update the `PATCH` row ("It returns to `needs_review`"), add that `DELETE` refuses approved Orders, and update the 409 line.

## Testing

New tests go in `backend/tests/test_final_orders.py`. Each is written first and fails against the current code.

### The rule

1. `test_patch_on_approved_is_refused`, parametrized over a changed value, the identical value, the identical value padded with spaces, all five fields, and `{}`. Asserts 409 with the edit message. A GET before and after are equal, and `version` is unchanged.
2. `test_refused_patch_leaves_flags_and_evidence_alone`: approved Order with an open flag and evidence on `provider_npi`, PATCH that field. Asserts the flag is still unresolved and `FieldEvidence.corrected` is still false.
3. `test_refused_write_records_nothing`: a refused PATCH and a refused DELETE add no audit rows for the Order.
4. `test_delete_on_approved_is_refused`: approved Order with a PDF. Asserts 409 with the delete message, `deleted_at` is null, GET is 200, the PDF bytes are kept, `GET /document` is 200, and the Order is still in the list.

### Approval-path matrix

5. `test_approved_order_is_final_whoever_approved_it`, parametrized over:
   - approval path:
     - `code`: clean text-layer upload with the stub LLM
     - `person_upload`: scan upload, then `/review approve`
     - `person_manual`: `POST /v1/orders`, then `/review approve`
   - attempt: PATCH with a changed value, PATCH `{}`, DELETE

   For each of the 9 cases it asserts:
   1. the setup produced the expected approver signal (table above) and `source`,
   2. the attempt gets 409 with the matching message,
   3. the approver signal, status, values and `updated_at` are unchanged afterwards.
6. `test_only_a_clean_upload_is_approved_without_a_person`:
   - a manual create is never approved without `/review`,
   - an upload that ends approved with `reviewed_by` null has every evidence row `verified` and no flags,
   - `/review approve` always sets both `reviewed_by` and `reviewed_at`.

### Edge cases

7. `test_patch_cannot_set_status_on_approved`: `{"status": "needs_review"}` gives 422, and the status is still approved. The response text contains none of the Order's stored values (names, DOB, provider name, NPI) and not the word `approved`, so the refusal reveals neither patient data nor the status.
8. `test_bad_body_on_approved_is_422_and_changes_nothing`: an invalid NPI, and a null name, each give 422 with the Order unchanged. The response text contains none of the Order's stored values and not the submitted bad value. Each error's `msg` is either an allowlisted Pydantic message or `"Invalid value."`.
9. `test_someone_elses_approved_order_is_404`: another user's PATCH and DELETE each give 404, the owner's Order is unchanged, and `order.access_denied` is audited against the caller.
10. `test_denied_order_can_still_be_deleted`: DELETE gives 204.
11. `test_needs_review_order_can_still_be_edited_and_deleted`: PATCH gives 200 and the Order stays `needs_review`. DELETE gives 204.

### Existing tests kept as race and regression coverage

- `test_stale_writes.py::test_stale_patch_cannot_write_over_an_approval`
- `test_stale_writes.py::test_stale_delete_cannot_remove_an_approved_order`
- `test_review.py::test_review_rejects_an_edit_that_landed_mid_review`
- `test_review.py::test_review_requires_needs_review_status`

### Existing tests changed because they encode the old rule

- `test_orders.py::test_patch_on_approved_returns_to_needs_review`: removed, replaced by test 1.
- `test_orders.py::test_patch_with_no_real_change_leaves_the_order_alone`: same cases, on a `needs_review` Order.
- `test_orders.py::test_patch_changing_one_of_several_values_counts_as_a_change`: on a `needs_review` Order. Asserts the value changed and `updated_at` moved.
- `test_stale_writes.py::test_a_stale_write_leaves_the_connection_clean`: the retry now gets 409 with the edit message. That still shows the session recovered, because the retry read the committed approval.

### Frontend

There are no automated frontend tests (CI runs lint and build). Check by hand: an approved Order shows the final note and no Edit control, for both a code approval and a person's approval.

## Success criteria

- No API call can change, reopen or delete an approved Order. The tests above prove it for every approval path.
- Who approved an Order, and when, never changes after approval.
- `needs_review` and `denied` behave exactly as before, apart from the rewritten tests that encoded the old rule.
- `uv run pytest -q`, `ruff check`, `ruff format --check`, `npm run lint` and `npm run build` all pass.
