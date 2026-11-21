# LOOM — TOOLS.md (Sanitized Access Map)

**No secret values live in this file.** This is a map of *what exists* and
*where the real credentials live* — not the credentials themselves.

## Source Documents (read-only, not modified)

| Source file | Path |
|---|---|
| Dreemzy Main Brain tools doc | `/Users/user/Desktop/dreemzy main brain/TOOLS.md` |
| Jasmine Phase 1 tools doc | `/Users/user/Desktop/Jasmine-Phase1/docs/TOOLS.md` |

Both files were read in read-only mode to build this map. Neither file was
modified. No secret values were copied into this document or exposed in chat.

## Tools Relevant to LOOM

| Tool | Used for | Credential exists? | Where it lives |
|---|---|---|---|
| **n8n** | Orchestration (Source/VOD → OpenShorts → Discord QC → GHL) | ✅ Yes (API key, host `fns2.app.n8n.cloud`) | **Canonical credential: Jasmine Phase 1 `docs/TOOLS.md` only.** Regenerated and verified September 8, 2026; full access; does not expire. This is the sole current n8n key for LOOM orchestration. Any n8n key present in Dreemzy Main Brain `TOOLS.md` is a superseded, older credential — do not use it. The credential value must never be reproduced in LOOM documentation, workflow JSON, logs, prompts, or reports. |
| **OpenRouter** | LLM routing for Qwen model calls | ✅ Yes (API key + REST example) | Jasmine Phase 1 `docs/TOOLS.md` only (not present in Dreemzy Main Brain copy) |
| **Qwen (via OpenRouter)** | Clip-selection / QC reasoning model | Accessed through OpenRouter key above, no separate credential | Same as OpenRouter |
| **GHL (GoHighLevel)** | Media Storage + Social Planner (canonical storage/distribution plane) | ✅ Yes — primary account PIT token + location ID, plus a separate Derm by Erica sub-account token | Both TOOLS.md files, "GoHighLevel" section |
| **Discord** | QC step in the pipeline | ❌ Not found in either source doc | No bot token / webhook currently documented anywhere found. **Needs to be created and documented before the Discord QC segment can start.** |
| **GitHub** | OpenShorts repo (`origin` = `github.com/mutonby/openshorts.git`) | Uses local git/gh CLI auth on this Mac; no explicit PAT found in either source doc | Local `git`/`gh` credential store (not a TOOLS.md entry) |
| **Docker** | Self-hosting the OpenShorts build | No credential needed (local Docker Desktop / Compose) | N/A — local only |
| **Retell AI** | Voice (not currently part of LOOM pipeline; noted for completeness) | ✅ Yes | Both TOOLS.md files |
| **Twilio** | SMS/WhatsApp (not currently part of LOOM pipeline) | ✅ Yes | Both TOOLS.md files |
| **OpenAI** | Backup LLM (not currently part of LOOM pipeline) | ✅ Yes | Both TOOLS.md files |
| **Supabase** | Conversation memory (unapproved for LOOM per `LOOM_SOURCE_OF_TRUTH.md`) | ✅ Yes | Both TOOLS.md files |
| **Vercel** | Dashboard hosting (not currently part of LOOM pipeline) | ✅ Yes | Both TOOLS.md files |
| **Cloudinary** | ❌ Rejected for LOOM MVP | ✅ Yes (present, but out of scope) | Jasmine Phase 1 `docs/TOOLS.md` only |

## Notes

- Where a credential "exists," this file confirms only that fact — never the
  value. Pull the real value from the source file listed, at the time it's
  actually needed, and never paste it into HANDOFF.md, LOOM_SOURCE_OF_TRUTH.md,
  README.md, or chat.
- **Resolved (Sept 8, 2026):** the n8n API key ambiguity is closed. Jasmine
  Phase 1 `docs/TOOLS.md` holds the current canonical key (regenerated and
  verified September 8, 2026, full access, does not expire) — confirmed live
  via a read-only authentication check against the n8n host. Dreemzy Main
  Brain `TOOLS.md`'s July 11, 2026 key is superseded; do not use it for LOOM
  orchestration or any new work.
- Discord has no documented credential anywhere found on the Desktop. This is
  a gap, not an oversight — it needs to be set up when the Discord QC segment
  starts.
- Do not add credentials for any tool not on the Locked architecture list
  without checking `LOOM_SOURCE_OF_TRUTH.md` first (e.g. Cloudinary and
  Supabase both have live keys elsewhere but are explicitly out of scope here).

## Agent Toolkit Additions (Nov 2026 — LOOM Factory build resumption)

Cross-agent skill/tool toolkit at `~/.local/share/agent-toolkit` (governed by that toolkit's own
`TOOLKIT.md`) is shared across every LOOM workstation. Reused as-is from the FridgeGraph build;
three additions made specifically when resuming LOOM Factory:

| Tool | What it is | Where it lives |
|---|---|---|
| **gstack** | Garry Tan's open-sourced Claude Code config: 23 specialist subagents + 8 power tools | `~/.gstack` (already active before this note was written) |
| **CLI-Anything** | Universal CLI-generator bridge — lets an agent control arbitrary software via generated wrapper CLIs | `~/.cli-anything` (Python 3.12 venv); wrappers exposed on PATH via `~/bin/cli-hub` etc. |
| **vercel-labs/agent-browser** | Native (Rust) browser-automation CLI for agents, prebuilt per-platform binaries via npm | Tracked in `agent-toolkit/receipts/sources.json`; skill symlinked at `~/.agents/skills/agent-browser`. **Still needs** `npm i -g agent-browser && agent-browser install` run once on this Mac, and does not yet reach `~/.claude/skills` (that symlink is outside this toolkit's reach from automation — do it by hand once: `ln -s ~/.local/share/agent-toolkit/sources/vercel-labs--agent-browser/skills/agent-browser ~/.claude/skills/agent-browser`). |

LoopForge was considered and explicitly rejected — Ralph Loop already covers that need.
Agent lineup (Bryan, Nov 2026): Codex, Claude (Claude Code), OpenClaw (Bentley); Muse dropped. See
`AGENTS.md` in each workstation for the per-agent entry point.
