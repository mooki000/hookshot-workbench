# Hookshot workbench MCP

A local stdio server for owned jobs and retained evidence in the Hookshot workbench. It is an optional client of the
workbench, not a replacement scheduler or a game control protocol. It starts with observation tools only. Nothing here
includes game files, saves, routes, generated world data or a local machine's configuration.

The runtime is Python 3.12 or later on Linux, FastMCP 4.1.3 and MCP SDK 2.3.0, resolved in `uv.lock`. Linux procfs,
process groups and pidfds are required for owned worker supervision. The package does not install or update a game,
start a gate, contact a provider, or discover credentials on startup.

## How this relates to Hookshot's own client and MCP server

[Hookshot #2904](https://github.com/NotRexed/Hookshot/pull/2904) adds the generic session layer in
`scripts/hookshot_ctl.py`; an optional thin MCP server and scenario runner follow, taking over
[#1025](https://github.com/NotRexed/Hookshot/pull/1025). That layer has no machine policy.

This is the workbench's layer for one machine doing many tests: it starts games only through the workbench gates,
keeps runs/jobs/artifacts, and adds evidence tools, pre-push and Windows-target checks, and bounded readers. It speaks
the same documented game control bridge protocol itself; it does not import Hookshot's client. Its single-reader
dispatcher preserves interleaved events and matches concurrent replies, rather than importing mutable checkout code
or losing subscription events while awaiting a command.

The automated suite uses fake backends. One real windowless smoke exercised launch, status, capture, logs and ordered
stop, but input was attempted during boot and the image was black. The readiness repair has fake coverage; a second
real input/visible-image smoke is pending. No performance or save-drain guarantee is implied.

## Install and start

From this directory, with [uv](https://docs.astral.sh/uv/) installed:

```sh
export UV_CACHE_DIR="$PWD/../.uv-cache"
export UV_PYTHON_INSTALL_DIR="$UV_CACHE_DIR/python"
uv sync --locked --python /absolute/path/to/python3
uv run --locked python -m src.main
# Equivalent console entry point:
uv run --locked hookshot-workbench
```

The banner is disabled. Stdout is reserved for MCP; diagnostics go to stderr. The package disables FastMCP's default
cwd `.env` lookup before importing the framework. It never loads dotenv or searches the current directory for secrets.

A generic host entry (replace the paths yourself; no server-side shell expansion):

```json
{
  "mcpServers": {
    "hookshot-workbench": {
      "command": "uv",
      "args": ["run", "--locked", "--directory", "/absolute/path/to/workbench/mcp", "python", "-m", "src.main"],
      "env": {
        "UV_CACHE_DIR": "/absolute/path/to/workbench/.uv-cache",
        "UV_PYTHON_INSTALL_DIR": "/absolute/path/to/workbench/.uv-cache/python",
        "HOOKSHOT_WB_PROFILE": "observe"
      }
    }
  }
}
```

Stdio inherits the trust of the account and host launching it. This entry opens no network listener. Do not expose it
as an unauthenticated remote service. Missing optional backends are reported as `unavailable`, not silently emulated.
`configured` means a path exists, not that connectivity, a binary, a save, or gameplay has been validated.

## Shell entry and owned sessions

No configured MCP client is needed for the CLI. From the repository root:

```sh
uv run --project mcp hookshot-wb list --json
uv run --project mcp hookshot-wb list --profile operate
uv run --project mcp hookshot-wb call workbench_status --args '{}'
uv run --project mcp hookshot-wb read workbench://policy
uv run --project mcp hookshot-wb prompt recover_or_cancel --args '{"job_or_run_id":"an-owned-id"}'
uv run --project mcp hookshot-wb broker status
uv run --project mcp hookshot-wb broker stop --timeout 90
```

Tool calls go through the real server in memory, with the same profiles, schema validation and error envelopes.
Structured results are JSON on stdout; coded errors are JSON on stderr with a nonzero exit. On the first broker-owned
call, stderr also says `hookshot-wb: broker started` once. A returned Job is returned immediately, not awaited to
completion: in particular, a launch Job describes the session's lifetime. `job_read` nests rows under `events.events`;
each event name is its `kind`, for example `bridge_ready`. That event proves a bridge reply, **not input or image readiness**.
`job_wait` timing out is a normal result with `timed_out: true`;
an outer CLI `--timeout` is a separate bounded transport wait. Never replay an uncertain mutation.

Before a press or picture, use `session_wait` with `predicate: {"kind":"state","state":"menu"}` and `stable_frames:30`,
under a declared timeout. This works on a basic bridge without sampled game state; it is an observed UI-state heuristic,
not a freshness/controllability guarantee. A `controllable` predicate correctly remains unsupported without fresh samples.
Input refuses boot/intro/loading/unknown or unpresented/stalled state before sending; release remains available for cleanup.
Input Job events retain sanitized `bridge_request`/`bridge_reply` fields. The game gets the requested input timeout,
with one additional second only for delivery of its deadline reply. A confirmed “no controller read” reply is distinguished
from an uncertain partially consumed or disconnected mutation; no input is automatically replayed.

For bounded shell observation, `job_wait` can wait up to 30 seconds per call; repeat only until your overall deadline.
Read the existing readiness Job's result from `events.events`, not from the lifetime launch Job:

```sh
uv run --project mcp hookshot-wb call job_read --args "{\"job_id\":\"$JOB\",\"limit\":100}" |
  jq '.events.events[] | select(.kind == "bridge_ready")'
uv run --project mcp hookshot-wb call session_wait --args "{\"run_id\":\"$RUN\",\"predicate\":{\"kind\":\"state\",\"state\":\"menu\"},\"stable_frames\":30,\"timeout_seconds\":90,\"idempotency_key\":\"ui-ready\"}"
# Set WAIT_JOB from that response; overall deadline remains the operator's bound.
uv run --project mcp hookshot-wb call job_wait --args "{\"job_id\":\"$WAIT_JOB\",\"timeout_seconds\":30}"
uv run --project mcp hookshot-wb call job_read --args "{\"job_id\":\"$WAIT_JOB\",\"limit\":100}" |
  jq '.events.events[] | select(.kind == "result") | .data'
```

Require `matched:true` and a presented menu before input. After input, wait again for an appropriate drawing state before
the shot; a present counter alone can still be black boot/loading output. If readiness does not arrive inside the owned
run's remaining budget, stop and report it—do not press blindly or start another hold. Inspect the image afterward.

The generated [CATALOGUE.md](CATALOGUE.md) names each shell replacement and whether the CLI needs the broker.
Simple readers, catalogue, queues and retained comparisons are one-shot; they start no daemon. Live-session reads and
all local mutations/owned jobs share one persistent broker per state root. The broker's real server owns sessions;
each CLI request still enforces the selected observe/operate profile, including explicit extra-tool opt-ins.

The broker uses only a Unix socket inside `STATE_ROOT/cli` (0700); socket, owner record and lock are 0600.
Every connection checks Linux peer credentials. The held lock and pid/start-time/boot identity prevent duplicate
brokers and PID-reuse confusion. There is no TCP listener or raw-command endpoint. An orphaned session is listed by
`broker status`; a later broker never adopts or kills stored PIDs. Parent-death guardians still clean their owned work.
Unsent startup failures can be reconciled, but a sent request with a lost reply is never automatically retried.

Idle shutdown defaults to 60 seconds and does not stop live owned sessions/jobs. Explicit stop, signal or a configured
foreground lifetime closes sessions in game-before-gate order and records `broker_stop`, `broker_signal` or
`broker_deadline` as the Run reason. Start foreground service management with
`uv run --project mcp hookshot-wb serve`; optional `--idle-seconds` and `--lifetime-seconds` bound that service.
Changing backend configuration requires stopping the existing broker after its jobs are reconciled, then starting a
new one. IPC requests cap at 256 KiB and responses at 8 MiB.

For MCP clients, [the project example](../.mcp.json.example) provides both stdio profiles. Choose the appropriate entry;
`operate` already includes reads. Separate stdio servers do not adopt each other's live session connections.
No installer writes this example into a home or configuration directory.

## Configuration

All package variables use `HOOKSHOT_WB_`. Paths must be absolute (a leading `~` expands to the account's home).
No path to an actual game or checkout is built into this package. The host, not a model's tool arguments, configures
trusted tools and registered roots. Optional values default to unset.

| Variable | Default / meaning |
|---|---|
| `HOOKSHOT_WB_STATE_ROOT` | `$XDG_STATE_HOME/hookshot-workbench`, or `~/.local/state/hookshot-workbench`. Durable private registry and worker files. Must be outside every repository and not writable by others. |
| `HOOKSHOT_WB_HARNESS_DIR` | Unset. Trusted installed harness directory. |
| `HOOKSHOT_WB_GPU_GATE` | Unset. Existing card gate script. Queue observation reads its literal default state directory; it never runs the script. |
| `HOOKSHOT_WB_INTEL_GATE` | Unset. Existing Intel gate script. |
| `HOOKSHOT_WB_BUILD_GATE` | Unset. Existing build gate script. |
| `HOOKSHOT_WB_IDLE_GATE` | Unset. Existing CPU analysis/test gate script. |
| `HOOKSHOT_WB_CHECKOUT` | Unset. Reviewed Hookshot checkout containing trusted tooling. Not a caller-selected PR checkout. |
| `HOOKSHOT_WB_DEPENDENCY_PYTHON` | The server's interpreter. Explicit interpreter for trusted dependency tools. |
| `HOOKSHOT_WB_READ_ROOTS` | `[]`. JSON array of explicitly registered readable roots. |
| `HOOKSHOT_WB_WRITE_ROOTS` | `[]`. JSON array of explicitly registered writable roots (also readable). Register only owned disposable work/evidence roots. |
| `HOOKSHOT_WB_PROTECTED_ROOTS` | `[]`, added to always-protected `~/.config`, `~/.ssh`, `~/Downloads`. Add every player game/save folder; protection wins over registered roots. |
| `HOOKSHOT_WB_PROFILE` | `observe`. `operate` opts into RUN and WRITE_LOCAL tools as well. Unknown values fail startup. |
| `HOOKSHOT_WB_EXTRA_TOOLS` | Empty. Comma-separated exact tool names to add to the selected profile. Unknown or forbidden names fail startup. |
| `HOOKSHOT_WB_BROKER_IDLE_SECONDS` | `60`; between 0.05 and 3,600 seconds. Idle CLI broker shutdown only when it owns no live work. |
| `HOOKSHOT_WB_SESSION_CATALOGUE` | Unset. Operator-owned local session/build/save/profile catalogue. |
| `HOOKSHOT_WB_CHECKS_CATALOGUE` | Unset. Operator-owned reviewed checkouts, check/build profiles and CI repository allowlist. |
| `HOOKSHOT_WB_KNOWLEDGE_CONFIG` | Unset. Operator-owned knowledge/read-adapter configuration. |
| `HOOKSHOT_WB_PANELS_SCRIPT` | Unset. Reviewed image-panel helper. |
| `HOOKSHOT_WB_JOURNAL_READER` | Unset. Reviewed journal reader for bounded retained fault evidence; unavailable access remains unknown. |

The package also uses `HOME` and `XDG_STATE_HOME` only for the documented defaults. `UV_CACHE_DIR` and
`UV_PYTHON_INSTALL_DIR` configure uv, not the server. The package forces `FASTMCP_ENV_FILE` to the null device; framework
logging settings can otherwise be configured through FastMCP's documented environment variables.

The local catalogue schemas are `src/models/session.py`, `src/models/checks.py` and `src/models/knowledge.py`.
They are operator registrations, not account files. Register their directories and all selected inputs/backends in
READ_ROOTS; register STATE_ROOT explicitly in WRITE_ROOTS before using job/artifact producers. Missing components
refuse without executing a substitute. Invalid optional knowledge configuration is unavailable, not a server crash.
The stock team/request CLIs remain unavailable until a reviewed GET-only/no-save CLI contract exists.

Build evidence consumes the checks group's registered `build_start` manifests/results: full commit, binary hashes,
and `completed_receipt` with a successful `builder_done` or `linked_outputs` receipt, linked to a completed build Run.
Full-suite evidence consumes `checks_source` results with the exact `commit`, `coverage: suite`, `table_complete: true`,
zero failures and a successful owning Job receipt. An input/wait/analysis Job's Run references do not own its lifecycle.


## Current operator policy

The operator may create `policy.json` in the state root. There is no MCP tool to write or relax it. The file is bounded
to 64 KiB and read again at plan time and actual admission. Missing, malformed, oversized or symlinked policy files
allow observation but refuse execution. An absent rule never means permission.

A conservative example, deliberately allowing no gate lanes:

```json
{
  "windowless": true,
  "day_start_hour": 7,
  "night_start_hour": 22,
  "day": {"lanes": [], "classes": [], "max_hold_seconds": 480, "max_cpu_seconds": 480},
  "night": {"lanes": [], "classes": [], "max_hold_seconds": 480, "max_cpu_seconds": 480},
  "authorizations": []
}
```

Day/night use the host's local clock. Lanes are `gpu`, `intel`, `build`, `idle`; ordinary permitted classes are
`ordinary` and `whole`. GPU/Intel holds remain at most 480 seconds. CPU build/idle budgets may be explicitly raised
to 86,400 seconds. A profile selects the surface, not gate permission.

`exclusive` and `priority` always need an operator-authored named authorization bound to the exact `plan_hash`, `lane`,
`resource_class`, timezone-aware `expires_at`, and `max_hold_seconds` (or `max_cpu_seconds` for CPU work). IDs are
1–80 letters, digits, underscores or hyphens. A plan hash is a 64-digit SHA-256. Admission rechecks current policy;
old switch files, historical documentation and tool arguments cannot authorize windows or a new hold. An authorization
claim is durable and bound to one job; using a new idempotency key does not reuse a consumed hold.

## Optional proposed telemetry file

`system_telemetry(run_id, frame_window=None, time_window=None)` is a separate READ surface for a registered `perf.csv`.
The writer and file format are still a proposal in [Hookshot issue #2915](https://github.com/NotRexed/Hookshot/issues/2915).
The tool reports `available:false` and **“no performance file in this run”** when that run has no registered existing file;
it does not pretend the game already writes one or treat absence as an error.

When present, the configured trusted `perf/hs_perf.py` reader supplies bounded observations (64 MiB input, 128 KiB result).
Frame windows use exact renderer frame IDs; time windows use OS monotonic seconds. Optional same-run `frames.csv` must
have the matching renderer clock. Partial/failed captures retain their completeness warnings; missing metrics stay null.
This read makes no new job or artifact and cannot confer timing validity, a speed verdict, or causal significance.
The four existing `evidence_analyze` IDs, their schemas/dispatch, and all comparison rules are unchanged. Telemetry output
has its own producer and result type and is not accepted as a paired timing-analysis artifact.

## Registered surface

These tables come from real registration, not a hand-maintained list. Regenerate them after changing a group:

```sh
uv run python -m src.generate_readme --write
uv run python -m src.generate_readme --check
```

<!-- BEGIN GENERATED CATALOGUE -->

### Tools

| Tool | Group | Safety | Profiles | CLI broker | Required arguments | Replaces |
|---|---|---|---|---|---|---|
| `build_plan` | checks | WRITE_LOCAL | operate | yes | repo_id, commit, profile_id | Pinned build/donor planning and compiler dry-run inspection |
| `build_start` | checks | RUN | operate | yes | plan_id, idempotency_key | Gated full/reused build wrappers and completion bookkeeping |
| `checks_pr` | checks | WRITE_LOCAL | operate | yes | repo_id, commit, branch, title_artifact, idempotency_key | Repository-bound pre-push check; whole imported-commit attestations only |
| `checks_source` | checks | RUN | operate | yes | repo_id, commit, idempotency_key | Idle-gated source test runner and failing-name comparison |
| `checks_windows_syntax` | checks | RUN | operate | yes | repo_id, commit, base, idempotency_key | Windows-target fixture check wrapper (not Windows runtime) |
| `ci_read` | checks | READ | observe, operate | no | repository | CI reader plus failed-job/log and matching-main inspection |
| `evidence_analyze` | evidence | RUN | operate | yes | run_id, analyzer_id, arguments, idempotency_key | Idle-gated retained frame/readback/thread/memory analysis |
| `evidence_bundle` | evidence | WRITE_LOCAL | operate | yes | request | Hand-assembled evidence tables and private handoff drafts |
| `evidence_compare` | evidence | READ | observe, operate | no | pairing | Existing nightly/regression paired comparison rules |
| `evidence_panels` | evidence | WRITE_LOCAL | operate | yes | images, label, idempotency_key | Source-preserving panels helper and image provenance bookkeeping |
| `evidence_scan_frames` | evidence | RUN | operate | yes | capture_artifact_id, idempotency_key | Retained framelog inspection and existing detector invocation |
| `github_thread_read` | knowledge | READ | observe, operate | no | repository, number | Paginated gh issue/PR/comment/review GET reads |
| `github_watch` | knowledge | WRITE_LOCAL | operate | yes | threads, timeout_seconds, idempotency_key | Bounded thread watcher with retained snapshot cursors |
| `job_cancel` | status | WRITE_LOCAL | operate | yes | job_id | Hand-managed scoped cancellation and child receipts |
| `job_read` | status | READ | observe, operate | no | job_id | Manual progress logs and completion-receipt lookup |
| `job_wait` | status | READ | observe, operate | no | job_id | Repeated bounded completion checks without cancelling work |
| `jobs_list` | status | READ | observe, operate | no | none | Ad-hoc background process and topic job lists |
| `knowledge_search` | knowledge | READ | observe, operate | no | query | Selected skill/report heading search and revision lookup |
| `nightly_status` | knowledge | READ | observe, operate | no | none | Retained nightly/rolling files and read-only results database |
| `prloop_next` | knowledge | READ | observe, operate | no | none | Pure PR-loop next-action inspection without CLI initialization |
| `prloop_refresh` | knowledge | WRITE_LOCAL | operate | yes | snapshot_id, idempotency_key | One initialized PR-loop scan under its coordinator lock |
| `queue_status` | status | READ | observe, operate | no | none | Manual queue/holder files and procfs lock inspection |
| `run_faults` | evidence | READ | observe, operate | no | run_id | Manual game/kernel fault classification with coverage accounting |
| `run_logs` | evidence | READ | observe, operate | no | run_id | Bounded retained-log tail and approved signature filtering |
| `save_clone` | session | WRITE_LOCAL | operate | yes | save_id, idempotency_key | Fresh disposable copy of a registered seed with hashes |
| `session_age` | session | WRITE_LOCAL | operate | yes | run_id, age, idempotency_key | Optional typed bridge age command, with no cheat fallback |
| `session_input` | session | WRITE_LOCAL | operate | yes | run_id, steps, units, idempotency_key | Typed control-client sequence or scoped release commands |
| `session_launch` | session | RUN | operate | yes | build_id, profile_id, wall_seconds, idempotency_key | Windowless wrapper setup, gate admission and bridge readiness journal |
| `session_options_get` | session | READ | observe, operate | yes | run_id | Bounded canonical control option_get listing |
| `session_options_set` | session | WRITE_LOCAL | operate | yes | run_id, values | Scoped control option_set and conditional restoration |
| `session_saves_list` | session | READ | observe, operate | yes | run_id | Control saves metadata listing, not save-file export |
| `session_shot` | session | WRITE_LOCAL | operate | yes | run_id | Control screenshot into a fresh registered output |
| `session_state` | session | READ | observe, operate | yes | run_id | Control status plus sampled-state age and freshness checks |
| `session_stop` | session | WRITE_LOCAL | operate | yes | run_id, reason, idempotency_key | Owned input/game shutdown before gate release and final receipt |
| `session_teleport` | session | WRITE_LOCAL | operate | yes | run_id, destination_id, idempotency_key | One listed debug teleport plus observed arrival checks |
| `session_wait` | session | READ | observe, operate | yes | run_id, predicate, idempotency_key | Observed frame/state/map predicates, not guessed boot sleeps |
| `system_telemetry` | telemetry | READ | observe, operate | no | run_id | Optional proposed perf.csv reader (#2915); no file means unavailable observation, not an error |
| `team_read` | knowledge | READ | observe, operate | no | room | Attested GET-only existing-room read; unsafe stock CLI stays unavailable |
| `test_requests_read` | knowledge | READ | observe, operate | no | none | Attested GET-only request list/show; unsafe stock CLI stays unavailable |
| `workbench_status` | status | READ | observe, operate | no | none | Manual policy, prerequisite, revision and active-job inspection |

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

<!-- END GENERATED CATALOGUE -->

## Bounds and evidence

- A driver allocation exception requires the complete named allocator/status/API triple in the same observed second.
  Missing timestamps, partial triples and generic `NV_ERR_NO_MEMORY` at another location remain unknown and require stopping.
- Live owned logs have incomplete artifact metadata with unknown size/digest. After exit, a new complete artifact
  version becomes the Run's current log; prior metadata stays in the registry. A cursor from the old version is stale.
- Job pages: default 25, maximum 100. Event pages: default 50, maximum 100; `next_cursor` is the last observed
  sequence, not an offset. Events are append-only, at most 16 KiB each. Progress text is at most 2,048 characters.
- Queue pages: default 50, maximum 100 waiters per lane; queue/yielded entries share a page cursor. At most 10,000
  tickets/markers are scanned. Truncation and inaccessible procfs are explicit; an incomplete order has no claimed place.
- All queue figures carry a snapshot timestamp. Lock files are never created, opened for writing, acquired or unlinked.
  Actual `/proc/locks` occupancy wins over stale holder files. Process birth and command identity qualify queue tickets;
  zombies/dead processes do not count. Priority's artificial one-day offset is removed from age. Yielded priority
  tickets remain visible. The position is conditional alternating-priority order, not a reservation or admission promise.
- Build/Intel gates have no global FIFO ticket contract. Only visible blocked-lock entries are listed as waiters;
  nonblocking-loop waiters may be invisible. Their position/age stays null. Quiet locks and live quiet-wanted markers
  explain observed blockers. Memory/pressure/settle admission is not measured, and every ETA remains null with its basis.
- Snapshots are not atomic: a gate can change between file and procfs reads. An empty confirmed lock snapshot differs
  from unavailable inspection. Tool revision digests describe script bytes read, not executed behavior.
- `job_wait` is bounded to 30 seconds and checks observed state. Cancelling its await never cancels the job. `job_cancel`
  is separate and acts only on exact registry ownership; it does not kill by name or remove ticket/lock files.
- Jobs return immutable attempt IDs. The same idempotency key/request returns the original attempt, including failures.
  Changed input under the same key fails. A deliberate retry uses a new attempt after reconciliation, never overwrites.
- SQLite WAL transactions serialize independent server processes. State/receipt/event updates are durable; terminal
  records cannot be rewritten. Queue time and held time are separate. A guardian's receipt, not a launcher's return,
  records child exit and descendant cleanup. Server death triggers owned-worker cleanup; startup reconciles receipts.
  A separate bounded state-root initialization lock protects SQLite's first WAL transition; it is not a gate lock.
- Run and artifact metadata carry provenance and real instrument clocks. Unknown is null, never a fabricated zero.
  Raw files stay local; large results use artifact references. Producing a picture is not evidence somebody inspected it.

## Errors, permissions and cancellation

Every tool failure uses a framework ToolError with JSON fields
`code`, `operation`, `retryable`, `retry_after_seconds`, `operator_action`. No raw exception text or local path is put in
that envelope. Validation failures use the same boundary. Codes:

| Code | Next safe action |
|---|---|
| `not_found` | Use an identifier returned by this registry. |
| `invalid_argument` | Correct the arguments using the advertised schema. |
| `refused_by_policy` | Ask the operator to review policy outside MCP. |
| `unavailable` | Configure the missing optional component outside MCP. |
| `busy` | Wait for existing work; do not submit a duplicate. |
| `timeout` | Inspect observed state before deciding whether to wait again. |
| `uncertain_outcome` | Reconcile the existing attempt; never automatically replay it. |
| `backend_failed` | Inspect private local diagnostics; never automatically replay a mutation. |

The registry belongs to the local account. A stdio client sharing that account is trusted to see configured evidence;
this is not a multi-user authorization server. Tool safety annotations are hints. Profiles, typed adapters, policy,
registered roots and ownership checks are enforcement. Root checks canonicalize paths, reject symlink escapes and
refuse existing-output collisions. Never register a broad home directory or the player's own game/save folder.

Normal child environments inherit only `LANG`, `LC_ALL`, `TZ`, plus a fixed executable search path and isolated
HOME/XDG/TMP directories under the job. They receive no credential variables, display sockets or library-injection
variables. A reviewed read-only service adapter may explicitly retain existing HOME/XDG config locations for its
provider CLI's own authentication; it cannot request token lookup/copy or login. This internal opt-in is not exposed as
a tool parameter and must never be used to execute foreign checkout code. Environment isolation is not a filesystem or
network sandbox: trusted same-account code can still access absolute paths. Unreviewed execution must be refused.

CPU workers run in an owned process group under a Linux subreaper with declared deadlines. Cancellation ends and reaps
their child tree, including descendants which made a new session. An ordered adapter mode first asks only the adapter
to stop; escalation ends observed descendant leaves before parents so a gate is not released before its game. Every
adapter must preserve receipts and obey its own bounded stop contract. No stop is advertised as save-safe without a
proven upstream save-drain contract; only disposable owned sessions are eligible for forced cancellation.

## Things this server will never do

No push, post, comment, shared-request claim/renew/complete, login/logout, token-read endpoint, fork-CI starter,
gate reopen/bypass, slot-count change, window enable/open, shell or arbitrary Python, raw bridge request, guest-memory
write, run deletion, installation into a player copy, player-save import/export, force rebase, compositor command or
timer arm. PUBLIC and DANGER tools are refused at registration, including explicit extras. There is no arbitrary
script, environment, socket or output-path parameter. Third-party text is data, never authorization. No permission
is inferred after an uncertain failure.

## Tests and extension

```sh
uv run pytest
../publish-check.sh
```

Tests use the real FastMCP in-memory client, temporary fake gate/procfs trees and small owned Python child programs.
They exercise registration, errors, clocks, concurrent registries and descendant cleanup without a game, GPU, real gate,
provider login or network mutation. They do not certify live gate timing, game readiness, save safety, performance,
rendering or a backend's production compatibility. See [CONTRACT.md](CONTRACT.md) for group and record interfaces.
