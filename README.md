# Order Intake Service

The full README is written at the end of the build. See [docs/design-spec.md](docs/design-spec.md).

## Corrections

| What went wrong | How it was caught | The fix |
| --- | --- | --- |
| The agent kept an org layer alongside user scoping, carrying `org_id` on every row even though GenHealth's requirement is per user. | Caught in human review of the design spec: the standard should be user scoped. | Removed `Organization` and `org_id`. `owner_id` is now the only scope, and another user's record returns 404. |
