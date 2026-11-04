# Data governance

- **Aggregation only.** SIVEP-Gripe row-level files are aggregated to municipality-week counts inside the import job.
  Personal columns never reach Silver or any API, and Bronze files are read-only. `sivep.raw` has no role.
- **No individual worker data.** Workforce and facility views are aggregated. There are no individual worker
  movements, rankings or scorecards.
- **No plaintext secrets.** Sources store a `auth_secret_ref` name only. Plaintext credentials in a source config are
  refused (HTTP 400), and audit metadata is redacted (`core/security.redact`).
- **Allowlisted HTTPS only.** Connectors may only reach approved HTTPS hosts. Redirects are re-checked, and IP
  literals and URL credentials are refused.
- **Audited state changes.** Every state-changing request writes an `audit_event`: actor, role, action, target,
  old/new version, reason and request id.
- **Explicit statuses.** Sources that need a licence or a manual file stay `needs_authorization` or
  `manual_import_required`. Their data is never substituted.
- **Licences are not verified for you.** Each source records its licence. Confirm the terms with the provider before
  any redistribution.
