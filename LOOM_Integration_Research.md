# LOOM integration research and execution brief

## Findings and scope

LOOM's recent failures are concentrated at application boundaries: response shape, candidate identity, missing transcript states, and downstream completion tracking. The reported evidence supports retaining OpenShorts as the clipping chassis and repairing the adapter layer. It does not support rebuilding the renderer, switching providers again, or regenerating completed clips.

This brief compares public upstream materials available September 12, 2026 with the execution reports supplied in the conversation. The customized Mac checkout and live workflows were not directly inspected for this report. Public main-branch code is not evidence that a fix is installed locally. Some source pages were only partially accessible; pull request 77's title was available, but its implementation was not. Recommendations below are deliberately distinguished from observed bugs.

The last reported state is: callback routing works; two candidates are emitted; WF02 loses candidate context during transcript retrieval; WF01 rejects the resulting incomplete records. Credential masking was previously identified as the authentication problem. Permanent credential handling, no-speech handling, and controlled resume remain pending implementation evidence. There is no basis to promise that any of them is already fixed permanently.

## Upstream evidence and LOOM implications

### 1. UI/API caption parity is an active upstream concern

The upstream pull-request listing shows open PR 77, titled “feat(subtitles): named caption presets shared by the modal, the API, MCP and the auto pass,” opened September 7. This supports the relevance of explicit cross-interface presets; it does not prove that the change is merged, complete, or available in LOOM. [1]

LOOM action: persist an explicit processing recipe with each job: output format, layout, hook setting/style, caption settings, and duration targets already supported by the installed backend. Tenant defaults should resolve into that recipe before submission. A replay of downstream scoring must reuse the recipe and final assets rather than silently apply newer defaults. Do not import this upstream PR during 3E merely because its title is relevant.

### 2. Other users report duplicated work across modules

Issue 68 asks OpenShorts to share a video's transcript between tools rather than regenerate it for each function. This is a user-reported integration limitation, not proof that every path currently duplicates inference. [2]

LOOM action: keep one source record and reuse stored transcript, clip metadata, assets, and completed scores. Builder context and media-processing context should stay separate: DeepSeek needs relevant code and compact execution evidence, not full videos or every historical transcript.

### 3. Job visibility after restart has been a real problem

Issue 46 describes a generated project becoming unavailable after laptop hibernation. Upstream app.py contains a recovery function whose comment explains that jobs formerly lived in memory and finished files could remain on disk while endpoints returned job-not-found errors. [3][4]

LOOM action: never equate a missing API job record with a need to regenerate media. Preserve the source/job/candidate manifest in existing durable persistence, and transfer final files to GHL in Segment 5. Any local cleanup must be conditional on confirmed durable storage. An upstream recovery function does not replace LOOM's scoring, review, and publishing records.

### 4. Final asset selection and timestamp domains matter

Upstream app.py documents visible doubled captions when an already-captioned video was used for another derivation. It also handles recut clips with remapped, clip-relative transcripts instead of assuming one contiguous source window. [4]

LOOM action: explicitly identify the final rendered asset used for review and publishing. Keep clean/intermediate assets separate. For today's contiguous clips, end minus start is a reasonable duration fallback when boundaries are valid; future stitched clips must use their actual rendered duration and remapped timeline. Do not expand today's task into recut implementation.

### 5. Prompt instructions alone are not enforcement

Issue 80 reports that a Knowledge selection feature has tested helper functions but its production pipeline does not consume structured gate verdicts; enforcement remains prompt-level. This concerns that upstream feature, not necessarily LOOM's installed version. [5]

LOOM action: validate Qwen's structured result in code, including required fields, numeric types and permitted ranges. An HTTP success or persuasive model explanation is not a valid scoring result. A valid score also does not prove editorial calibration or visual quality.

### 6. Review state needs persistence beyond the displayed UI

Issue 81 identifies an outstanding running-backend verification for review-state save/restore, including preserving existing edit state. It concerns upstream triage behavior and is not evidence of a LOOM Discord defect. [6]

LOOM action: Segment 4 needs a durable review record keyed to the exact candidate/asset version. Discord is the interaction surface; the stored decision is authoritative. Approve, reject, hold, and any explicitly requested retry must have distinct meanings. A retry must not automatically regenerate paid work.

### 7. Sub-workflow aggregation has a version-specific edge case

n8n documents both per-item and all-items sub-workflow modes, plus an option to wait for completion. Its release notes also describe a change in returned items when a child workflow's final node executes more than once: older trigger versions retain last-run-only behavior, while the new behavior can concatenate results. [7][8]

LOOM action: as part of the current aggregation correction, inspect only the relevant child return behavior and call configuration. Two input candidates must yield two distinct terminal records. Do not assume an upgrade is required; the installed graph may avoid the affected pattern entirely. Confirm aggregation by candidate identity, not merely array position or a green parent execution.

## Immediate contract to finish 3E

The correct repair unit is the entire affected handoff, not one missing field at a time. Use saved data to validate the following contract before invoking Qwen.

| Boundary | Required invariant | Relevant outlier |
|---|---|---|
| Completed job to candidate items | One item per clip; stable job, run, tenant and clip identifiers | Zero clips must produce an explicit outcome, not silent success |
| Candidate to transcript retrieval | HTTP output is attached separately; original context survives | Responses arriving in a different order cannot swap clips |
| Transcript to scorer | Distinguish present speech, verified no speech, unavailable data, and retrieval failure | Empty captions alone do not establish silence |
| Scorer to stored result | Valid schema; result tied to candidate and scoring version | Unknown paid-call outcome cannot trigger an automatic repeat |
| Child result to aggregation | Every expected candidate has one terminal outcome | One failed candidate cannot disappear from totals |
| Aggregation to review | Final asset and its identity survive | A raw clip must not replace its hooked/captioned final version |

For the current job, two successful scoring records remain the stated acceptance target. General support for skipped or failed candidates must not be used to relabel this test as a pass without an explicit acceptance decision.

No-speech handling deserves one important qualification: Gemini's hook and predicted score are model outputs, not independent evidence of what a silent clip contains. If Qwen receives no transcript and no grounded visual evidence, it cannot reliably judge the clip's visual action. Record missing evidence honestly. Permit the established schema's appropriate low-confidence or eligibility behavior; do not fabricate a transcript or visual description merely to satisfy required fields. If the current rubric demands speech-dependent scores with no valid representation of non-applicability, that is a real product decision, not a reason to investigate Gemini authentication again.

## Permanent resume semantics

A callback receipt, completed rendering, completed scoring, review approval, and successful publication are different states. Persist them separately. Resume should target the first unfinished downstream stage while preserving completed work.

Use existing durable storage and an atomic claim for the job/stage. A read-then-write boolean without concurrency protection is insufficient. Store attempt identity and completion outcomes; release only the claim owned by that attempt. An expired processing lease permits recovery investigation, not blind repetition of a potentially completed paid call.

Store a completed score before advancing to the next stage. If an inference may have completed but its response was lost, record outcome-unknown and stop that candidate's paid activity for reconciliation. Absolute exactly-once external billing cannot be promised without provider support. LOOM can prevent known duplicate work and refuse automatic retries when the outcome is ambiguous.

Credential handling belongs in one reusable loader: read the full configured secret from the known environment location, use it directly, and expose only success/failure in logs. Never reconstruct secrets from masked output or transcripts. A 401 is evidence of a rejected request, not proof of revocation. This removes the known masking failure mode; it cannot guarantee that a real expiration or future revocation will never occur.

## Roadmap through Segment 5 and beyond

| Stage | Smallest meaningful completion | What to anticipate during implementation |
|---|---|---|
| 3E | Existing job resumes, two candidates score, aggregation completes | Identity loss, ambiguous paid outcome, incomplete child returns |
| 4A | Observe the existing 0–100 scoring correction in this same run | Runtime schema validity is not a broad editorial calibration study |
| 4B–4D | Existing card reused; missing actions and durable decisions completed | Repeated clicks, stale asset versions, unauthorized or wrong-tenant decisions |
| 5 | One final asset stored in GHL; its returned media reference persisted and retrievable | Container paths, localhost URLs, authentication, file transport, MIME type, storage scope |
| 6 | Approved media mapped to the correct social account and publication record | Upload acceptance differs from publishing success; ambiguous post creation can duplicate posts |
| Tenant setup | Tenant recipe, location/account bindings, quotas and retention resolved explicitly | Defaults or callback payloads must not allow cross-tenant routing |
| Hardening and scale | Resource/concurrency limits and per-stage cost records | Local disk exhaustion, laptop sleep, renderer contention, retention and backpressure |
| Learning and editorial expansion | Performance joined back to candidate, recipe and publication version | Avoid comparing different platforms' metrics without consistent definitions |

Segment 5's key early decision is transport. A local container file path is not a remote media URL. If GHL imports a URL, it must be able to retrieve it with the supported authentication arrangement. Otherwise use a supported binary upload route. Do not broadly remove relay authentication to make a URL import work. Validate the actual selected endpoint's current limits and permissions when implementing; documentation search results alone are insufficient to lock those details.

Use one existing final clip for the Segment 5 storage proof. Record the returned media identifier/URL and confirm retrieval before allowing local cleanup. This test should not require OpenShorts or model inference. GHL remains the storage and publishing plane; upstream direct publishing is not a reason to add another distributor.

The route to entering Segment 5 today is conditional but concrete: finish the current bounded 3E test, record 4A runtime evidence, complete only missing review/card/decision functionality, then implement the first durable asset transfer. No broad re-audit is required. No reliable same-day guarantee can be made before the current repair result is known.

## Bentley operating agreement

Within the authorized segment, Bentley should diagnose and fix routine deterministic code, mapping, validation, and connection defects without requesting approval for each node. It should inspect the affected producer, consumer, and return path together; use saved data for non-paid validation; and report changes at the end of the work unit.

Stop only when the next action exceeds the approved paid-call budget, may repeat paid work with an unknown outcome, changes the architecture/product policy, requires new credentials or permissions, risks destructive data changes, or affects unrelated resources. Cheap inspection and deterministic correction do not independently authorize another paid attempt.

Keep a compact progress record containing current segment, confirmed fixes, exact remaining blocker, relevant file/node locations, and remaining paid-call allowance. Record installed versions when relevant to a defect. Reopen a passed check only when the affected dependency changes or new evidence contradicts it. Do not reread broad handoffs every turn, create new monitoring processes, or pull upstream main as a troubleshooting shortcut.

Builder spend and media inference spend should be reported separately. A two-call Qwen allowance does not bound DeepSeek's build/debug token usage. Keep model context to affected code and concise data samples, and stop exploratory source browsing when it no longer changes the implementation decision.

## Sources

1. OpenShorts, open pull requests, PR 77 listing, opened September 7, 2026. https://github.com/mutonby/openshorts/pulls
2. OpenShorts issue 68, “Offer all functions for the same video,” August 18, 2026. https://github.com/mutonby/openshorts/issues/68
3. OpenShorts issue 46, “Save past projects,” June 20, 2026, currently closed. https://github.com/mutonby/openshorts/issues/46
4. OpenShorts app.py, main branch, recovery and caption-reapplication excerpts. https://github.com/mutonby/openshorts/blob/main/app.py
5. OpenShorts issue 80, structured Knowledge gates, September 11, 2026. https://github.com/mutonby/openshorts/issues/80
6. OpenShorts issue 81, triage save/restore verification, September 11, 2026. https://github.com/mutonby/openshorts/issues/81
7. n8n, Execute Sub-workflow documentation. https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.executeworkflow
8. n8n, release notes, entry linking PR 30716 on returned child-workflow items. https://docs.n8n.io/changelog/release-notes

Local execution facts are attributed to the supplied Bentley reports for executions 10067–10072. They were not independently fetched in this research pass. Public issue reports identify relevant failure patterns; they do not establish that those defects exist in the customized LOOM checkout.
