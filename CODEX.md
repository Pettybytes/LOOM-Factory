# CODEX.md — read me first

Codex: **`CODEX.md` is not auto-discovered.** Codex loads `AGENTS.md` from the working directory
automatically, so start by reading that file in full. If you're resuming a session and can't
confirm you have it, open `AGENTS.md` explicitly.

- `AGENTS.md` in the working directory loads automatically; a nested `AGENTS.md` does not —
  reference it by path if needed.
- Skills are discovered from `~/.agents/skills/` and `~/.codex/skills/`. Every skill installed to
  `~/.agents/skills` (including the newly added `agent-browser`) should appear in the Codex
  prompt's skills list.
- Codex models are back in active use for this build cycle (usage reset, Nov 2026) — this is the
  primary workstation for Codex-driven work on LOOM.

Rules that bind you: `AGENTS.md` §2. Never merge, never tag, never read credential files, never
invent a test result.
