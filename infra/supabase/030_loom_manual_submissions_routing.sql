-- LOOM — Manual Source Intake V1.1: per-tenant dedup + qualification flag.
-- Idempotent: safe to re-run. No secret values.
--
-- Dedup changes from "one source, once globally" to "one source per tenant"
-- (the same canonical source MAY exist as separate candidates for multiple
-- tenants). tenant_id='' means Unassigned (a routing state, not a tenant).

alter table public.loom_manual_submissions
  drop constraint if exists loom_manual_submissions_identity;

alter table public.loom_manual_submissions
  add constraint loom_manual_submissions_identity
  unique (duplicate_fingerprint, tenant_id);

alter table public.loom_manual_submissions
  add column if not exists qualification_required boolean not null default true;

comment on column public.loom_manual_submissions.qualification_required is
  'True when tenant-specific qualification has NOT been run for the current tenant. Set true on assign/reassign (never fabricate a score). tenant_id='''' = Unassigned.';
