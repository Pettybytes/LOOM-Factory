# CLAUDE.md

Claude Code: **read and follow `AGENTS.md` before any work in this repo.** It is the single
source of truth for rules, boundaries, credentials, and the Definition of Done. Where anything
here disagrees with `AGENTS.md`, `AGENTS.md` wins.

Claude-specific notes:

- You auto-discover `CLAUDE.md` and `AGENTS.md`; resolve skills from `~/.claude/skills/` via
  `/skill-name`.
- Skills added this cycle that should be available here: `agent-browser`, and whatever gstack
  subagents/power-tools fit the task at hand (check `~/.claude/skills/` for the current list —
  gstack and CLI-Anything's generated wrapper CLIs live under `~/.cli-anything`/`~/bin`).
- `superpowers` is installed. Its generic worktree/subagent-dispatch skills can conflict with the
  one-agent/one-workstation convention above — this file's convention wins.
- Money-adjacent code (billing, GHL spend, Apify/provider cost ceilings) is senior-only — check
  `CURRENT_STATE.md` for the current cost-ledger rules before touching anything that spends money.

Start here: `AGENTS.md` → `NEXT_SESSION_HANDOFF.md` (or `HANDOFF.md`) → `CURRENT_STATE.md`.
