# LOOM — Factory MVP

**30-second entry point. Read this first.**

## What This Is
LOOM is a multi-stage content factory: pull source video/VOD → auto-clip with AI →
QC in Discord → store/distribute through GoHighLevel (GHL) → publish to
TikTok/Instagram/YouTube. Long-term vision spans four stages (Factory, Editor,
Cultural Editor, Learning/Distribution Intelligence) — see `LOOM_SOURCE_OF_TRUTH.md`.

## Current Status
- **Stage:** 1 — Factory
- **Segment:** 1 — OpenShorts Baseline
- **Current step:** Self-hosted OpenShorts Docker build succeeded; frontend loads;
  functional Clip Generator workspace is reachable. The workspace currently
  blocks clip generation unless Gemini **and** Upload-Post keys are configured.
  LOOM does not use Upload-Post — GHL Media Storage + GHL Social Planner are the
  locked storage/distribution plane instead. (Fix proposed, not yet applied —
  see `HANDOFF.md`.)

## Repository Location
`/Users/user/Desktop/LOOM-Factory/openshorts`
(git remote: `https://github.com/mutonby/openshorts.git`, branch `main`)

## Local Application URL
http://localhost:5175/#app

## Project Documents
- [`LOOM_SOURCE_OF_TRUTH.md`](./LOOM_SOURCE_OF_TRUTH.md) — canonical architecture & locked decisions
- [`HANDOFF.md`](./HANDOFF.md) — live operational handoff / current execution state
- [`TOOLS.md`](./TOOLS.md) — sanitized access map (no secret values)

## Required Reading Order (any human or model entering this project)
1. `README.md` (this file)
2. `LOOM_SOURCE_OF_TRUTH.md`
3. `HANDOFF.md`
4. `TOOLS.md`
5. Relevant OpenShorts source files (as needed for the current task)

## ⚠️ Warnings
- **`LOOM_SOURCE_OF_TRUTH.md` controls architecture.** If chat instructions
  conflict with it, stop and ask Bryan before proceeding.
- **`HANDOFF.md` controls current execution state.** No task is complete until
  `HANDOFF.md` reflects the actual current state.
- **Never expose credentials.** No API keys, tokens, passwords, cookies, or
  complete auth headers belong in any of these four files or in chat.
- **Do not modify `cloud/`** inside the OpenShorts repo.
- **Do not introduce unapproved services or dependencies** (see "Rejected" and
  "Deferred" sections of `LOOM_SOURCE_OF_TRUTH.md`) without Bryan's explicit sign-off.
