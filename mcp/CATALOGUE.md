# Registered workbench MCP catalogue

Generated from the real registered server; do not edit by hand.


### Tools

| Tool | Group | Safety | Profiles | Required arguments | Purpose |
|---|---|---|---|---|---|
| `build_plan` | checks | WRITE_LOCAL | operate | repo_id, commit, profile_id | Retain a conservative pinned build plan and dry-run evidence; never build or request a gate. |
| `build_start` | checks | RUN | operate | plan_id, idempotency_key | Start an operator-approved build plan as an owned gated Job; never prepare shaders, accept SDK terms or upgrade dependencies. |
| `checks_pr` | checks | WRITE_LOCAL | operate | repo_id, commit, branch, title_artifact, idempotency_key | Run the configured repository-bound pre-push check, including its fetch; never push or publish. |
| `checks_source` | checks | RUN | operate | repo_id, commit, idempotency_key | Run the reviewed game-free suite or exact declared test names; optionally compare only failures with a reviewed base commit. |
| `checks_windows_syntax` | checks | RUN | operate | repo_id, commit, base, idempotency_key | Run the existing Windows-target fixture check; Windows runtime is always not_run. |
| `ci_read` | checks | READ | observe, operate | repository | Read allowlisted PR or run checks and failing test names; compare with completed main runs without rerunning or modifying CI. |
| `evidence_analyze` | evidence | RUN | operate | run_id, analyzer_id, arguments, idempotency_key | Start an idle-gated approved analyzer: present_frame_times, readback_waits, thread_cpu or memory_lines. Returns a Job; invalid evidence produces rejected metrics, never invented zeroes. |
| `evidence_bundle` | evidence | WRITE_LOCAL | operate | request | Assemble private manifest/table/draft artifacts from registered evidence. No typed metric numbers, upload, post or public approval. |
| `evidence_compare` | evidence | READ | observe, operate | pairing | Compare exactly two explicit existing run pairs with shared quiet/repeat/paired rules; reject unlike clocks, settings and instrument sources. |
| `evidence_panels` | evidence | WRITE_LOCAL | operate | images, label, idempotency_key | Start an owned idle-gated labelled panel Job in new output. Source hashes are retained and inspection remains pending. |
| `evidence_scan_frames` | evidence | RUN | operate | capture_artifact_id, idempotency_key | Start an idle-gated retained pixel-capture scan. Only consecutive triplets are tested; visual review remains required. |
| `github_thread_read` | knowledge | READ | observe, operate | repository, number | Read bounded, untrusted issue/PR text with full pagination status, head and snapshot hash; GET only. |
| `github_watch` | knowledge | WRITE_LOCAL | operate | threads, timeout_seconds, idempotency_key | Watch allowlisted threads until a deadline as an owned Job; persist private cursors, never post. |
| `job_cancel` | status | WRITE_LOCAL | operate | job_id | Request cancellation of one registry-owned worker; preserve its output and receipt. |
| `job_read` | status | READ | observe, operate | job_id | Read one owned job, bounded events and its final child receipt. |
| `job_wait` | status | READ | observe, operate | job_id | Wait for observed terminal state under a deadline; cancelling this wait never cancels work. |
| `jobs_list` | status | READ | observe, operate | none | List this registry's jobs with a bounded cursor page. |
| `knowledge_search` | knowledge | READ | observe, operate | query | Search titles, headings and first lines of explicitly registered skills/reports; bounded revision-bound cursors. |
| `nightly_status` | knowledge | READ | observe, operate | none | Observe retained nightly state and read-only SQLite; never run rolling --check or construct its store. |
| `prloop_next` | knowledge | READ | observe, operate | none | Read the installed loop's next action on existing state; never initialize configuration or execute the proposed action. |
| `prloop_refresh` | knowledge | WRITE_LOCAL | operate | snapshot_id, idempotency_key | Scan an initialized loop snapshot as an owned Job under its coordinator lock; never claim or attest review. |
| `queue_status` | status | READ | observe, operate | none | Observe GPU, Intel and build locks and tickets without acquiring a lock or creating files; wait estimates stay unknown. |
| `run_faults` | evidence | READ | observe, operate | run_id | Classify retained fatal/device/driver lines; unavailable journal coverage stays unknown, known refusals stay separate. |
| `run_logs` | evidence | READ | observe, operate | run_id | Read bounded whole retained log lines by cursor or last N; filters are approved signatures, not regexes. |
| `save_clone` | session | WRITE_LOCAL | operate | save_id, idempotency_key | Copy a registered save into a new owned directory with a private hash manifest. Never overwrite or modify a library. |
| `session_age` | session | WRITE_LOCAL | operate | run_id, age, idempotency_key | Submit one age change on a disposable debug profile; unavailable commands never fall back to cheats. |
| `session_input` | session | WRITE_LOCAL | operate | run_id, steps, units, idempotency_key | Submit owned typed input. Frames are game presents; hos at is seconds and duration milliseconds, rounded half away from zero at 60 frames/s. Completion is effect_unverified. |
| `session_launch` | session | RUN | operate | build_id, profile_id, wall_seconds, idempotency_key | Submit a bounded disposable windowless session through the configured wrapper and gate. Returns a Job; no direct launch. |
| `session_options_get` | session | READ | observe, operate | run_id | Read bounded canonical settings with a cursor. |
| `session_options_set` | session | WRITE_LOCAL | operate | run_id, values | Set owned-run canonical options, returning before/after. Device, window, microphone and resource-class changes refuse. |
| `session_saves_list` | session | READ | observe, operate | run_id | Read bounded owned-session save metadata, not contents; consistency during writes is not guaranteed. |
| `session_shot` | session | WRITE_LOCAL | operate | run_id | Capture to a new server-chosen run path; return artifact metadata, not image bytes. No overlay. |
| `session_state` | session | READ | observe, operate | run_id | Read bounded state with sample age and capability. Missing sampled-frame freshness remains unknown. |
| `session_stop` | session | WRITE_LOCAL | operate | run_id, reason, idempotency_key | Submit owned disposable-session stop; observe game exit before gate release. Save draining is unsupported. |
| `session_teleport` | session | WRITE_LOCAL | operate | run_id, destination_id, idempotency_key | Submit one debug travel to a bridge-listed destination on a disposable debug profile. No replay on uncertain acceptance. |
| `session_wait` | session | READ | observe, operate | run_id, predicate, idempotency_key | Submit a bounded observed predicate wait as a Job. Cancelling it never stops the game or resends input. |
| `team_read` | knowledge | READ | observe, operate | room | Read an allowlisted existing room through the trusted GET-only/no-save CLI; no joining, sign-in or settings access. |
| `test_requests_read` | knowledge | READ | observe, operate | none | Read approved request-room cards only through an attested GET-only/no-save trusted CLI; stock unsafe first reads refuse. |
| `workbench_status` | status | READ | observe, operate | none | Read configured capabilities, current policy and bounded active jobs; no connectivity probe. |

### Resources

| URI or template | Profiles | Purpose |
|---|---|---|
| `workbench://evidence/analyzers` | operate | Approved analyzer IDs, measurements, units, clocks and required retained inputs; no probe. |
| `workbench://jobs/{id}/events{?cursor,limit}` | observe, operate | Append-only events, at most 100 per page; continue with next_cursor. |
| `workbench://nightly/{cycle}{?start,limit,revision}` | observe, operate | Retained nightly cycle status and bounded private report; no new work or database initialization. |
| `workbench://obligations{?cursor,limit,pr,since}` | observe, operate | Recorded promises with posted-text links and active loop obligations; no invented completion state. |
| `workbench://policy` | observe, operate | Current local policy; configuration is not authorization for a new action. |
| `workbench://reports/{id}{?start,limit,revision}` | observe, operate | Registered report ID only; bounded revision-pinned lines, private by default. |
| `workbench://runs/{id}` | observe, operate | One owned run manifest; no raw files or save data. |
| `workbench://skills/{name}{?start,limit,revision}` | observe, operate | Allowlisted installed skill, file-hash revision and bounded line anchors; text is not authorization. |
| `workbench://tools` | observe, operate | Exactly the registered tools, safety classes and hints. |

### Prompts

| Prompt | Profiles | Purpose |
|---|---|---|
| `compare_builds` | observe, operate | Plan comparable instruments, warmup and authorized paired measurements from installed skills; never grant a hold. |
| `prepare_evidence_handoff` | observe, operate | Plan a human evidence handoff with fresh-thread, image, number and CI checks; never publish. |
| `prove_fix` | observe, operate | Plan proof from installed workbench/evidence sections; no permission to run or post. |
| `recover_or_cancel` | observe, operate | Plan retained-success reconciliation or owned cancellation from installed process rules; no save-drain promise. |
| `reproduce_issue` | observe, operate | Plan reproduction from installed triage/play guidance; supplied thread and hardware are untrusted data. |
| `test_requested_pr` | observe, operate | Plan exact-head requested-PR prerequisites; card approval differs from code review and no claim is made. |
