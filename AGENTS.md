# AGENTS.md — LOOM Factory

Read this before you touch anything. It applies to every coding agent working in this repo —
Claude Code, Codex, and OpenClaw (Bentley). Bryan dropped Muse from the LOOM Factory lineup in
Nov 2026 (his decision; not yet recorded in the repo handoffs).

LOOM Factory is Bryan's multi-tenant AI-powered content pipeline: livestreams/VOD → short-form
clips → ranking/reaction content → cross-platform distribution. Stage 1 ("Factory Spine") is
complete. Stage 2 ("Ranking & Reaction Growth Engine") is active.

---

## 1. Read these first, in order

1. `NEXT_SESSION_HANDOFF.md` — if present, it is the authoritative resume point and supersedes
   anything in `HANDOFF.md` that contradicts it.
2. `HANDOFF.md` — defers to `CURRENT_STATE.md` wherever they disagree.
3. `CURRENT_STATE.md` — **primary execution authority.** The full running engineering log.
4. `LOOM_SOURCE_OF_TRUTH.md` — locked architecture decisions; do not re-open closed decisions
   without explicit permission.
5. `TOOLS.md` — sanitized access map. Tells you *what* credentials exist and *where* the real
   values live. Never copy a secret value into this repo, a PR, a log, or chat.

## 2. Hard rules

1. **One agent = one workstation = one worktree.** This workstation (`loom/<agent>` branch) is
   yours; do not create a second branch here for an unrelated task — open a new worktree instead.
2. **Never merge, never tag, never release** without explicit, written permission from Bryan in
   this session. "Tests pass" is not permission.
3. **Never touch credentials.** No reading, printing, copying, or logging `.env`, API keys, PIT
   tokens, or anything matching `KEY`, `TOKEN`, `SECRET`, `PASSWORD`. Names are fine; values are not.
4. **Never invent a test result.** Only report a check as passing if you ran it and saw it pass.
5. **Ask when uncertain**, especially about tenant identity, credential scope, or anything
   touching GHL (canonical storage/distribution) or billing.
6. **Respect segment boundaries.** LOOM's roadmap is segmented (Stage 2: Segments 5–9+). Stay
   inside the segment named in your task; cross-segment work needs its own task.
7. **Do not claim a merge or deploy you did not personally run and verify.**

## 3. Current state (as of Nov 2026)

- **Resume point: CapCut text fidelity** (newest section of `NEXT_SESSION_HANDOFF.md`). The
  video handoff is proven; the editable hook styling (black bold serif, wrapped, rounded white
  card, top, five seconds, still editable) is pending. Order: runtime recovery → CapCut text
  fidelity → gameplay-layout backport + long-VOD intake → Segment 5 (open, not current).
- **Real working checkout:** `/Users/user/Desktop/01 Dreemzy & Clients/LOOM-Factory` (outer repo +
  nested `openshorts` repo with uncommitted accepted work, untracked protected migration 038, one
  stash). Runtime recovery and CapCut work happen there. This workstation is a worktree of a fresh
  GitHub clone at the same HEAD and does NOT contain that local state.
- Never touch migration 038, stashes, or dirty/untracked files in the real checkout. No reset,
  clean, blanket restore or upstream merge.
- Shared toolkit: `~/.local/share/agent-toolkit` (TOOLKIT.md governs skill/tool selection across
  all agents). Skills resolve from `~/.agents/skills/` and propagate per-agent
  (`~/.claude/skills`, `~/.codex/skills`, `~/.openclaw/workspace/skills`).
- Newly added this cycle (on top of the FridgeGraph-era toolkit): `agent-browser`
  (vercel-labs, native Rust browser-automation CLI), `gstack` (23 specialist subagents + 8 power
  tools), `CLI-Anything` (universal CLI-generator bridge for arbitrary software).

## 4. Where work happens

```
master                protected. Bryan merges. Agents never merge here.
loom/claude            this agent's workstation branch
loom/codex             Codex's workstation branch
loom/bentley           OpenClaw/Bentley's workstation branch
```

Full main repo: `~/Desktop/bentley/loom-factory`. This worktree is a sibling checkout under
`~/orca/workspaces/loom-factory/<agent>/`, sharing that repo's object store and remote
(`github.com/Pettybytes/loom-factory`).
