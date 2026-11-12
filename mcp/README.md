# Hookshot workbench MCP

A local stdio server for owned jobs and retained evidence in the Hookshot workbench. It is an optional client of the
workbench, not a replacement scheduler or a game control protocol. It starts with observation tools only. Nothing here
includes game files, saves, routes, generated world data or a local machine's configuration.

The runtime is Python 3.12 or later on Linux, FastMCP 4.1.3 and MCP SDK 2.3.0, resolved in `uv.lock`. Linux procfs,
process groups and pidfds are required for owned worker supervision. The package does not install or update a game,
start a gate, contact a provider, or discover credentials on startup.

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
| `HOOKSHOT_WB_SESSION_CATALOGUE` | Unset. Operator-owned local session/build/save/profile catalogue. |
| `HOOKSHOT_WB_CHECKS_CATALOGUE` | Unset. Operator-owned reviewed checkouts, check/build profiles and CI repository allowlist. |
| `HOOKSHOT_WB_KNOWLEDGE_CONFIG` | Unset. Operator-owned knowledge/read-adapter configuration. |
| `HOOKSHOT_WB_PANELS_SCRIPT` | Unset. Reviewed image-panel helper. |
| `HOOKSHOT_WB_JOURNAL_READER` | Unset. Reviewed journal reader for bounded retained fault evidence; unavailable access remains unknown. |

The package also uses `HOME` and `XDG_STATE_HOME` only for the documented defaults. `UV_CACHE_DIR` and
`UV_PYTHON_INSTALL_DIR` configure uv, not the server. The package forces `FASTMCP_ENV_FILE` to the null device; framework
logging settings can otherwise be configured through FastMCP's documented environment variables.

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

## Registered surface

These tables come from real registration, not a hand-maintained list. Regenerate them after changing a group:

```sh
uv run python -m src.generate_readme --write
uv run python -m src.generate_readme --check
```

<!-- BEGIN GENERATED CATALOGUE -->

### Tools

| Tool | Safety | Profiles | Required arguments | Purpose |
|---|---|---|---|---|
| `job_cancel` | WRITE_LOCAL | operate | job_id | Request cancellation of one registry-owned worker; preserve its output and receipt. |
| `job_read` | READ | observe, operate | job_id | Read one owned job, bounded events and its final child receipt. |
| `job_wait` | READ | observe, operate | job_id | Wait for observed terminal state under a deadline; cancelling this wait never cancels work. |
| `jobs_list` | READ | observe, operate | none | List this registry's jobs with a bounded cursor page. |
| `queue_status` | READ | observe, operate | none | Observe GPU, Intel and build locks and tickets without acquiring a lock or creating files; wait estimates stay unknown. |
| `workbench_status` | READ | observe, operate | none | Read configured capabilities, current policy and bounded active jobs; no connectivity probe. |

### Resources

| URI or template | Profiles | Purpose |
|---|---|---|
| `workbench://jobs/{id}/events{?cursor,limit}` | observe, operate | Append-only events, at most 100 per page; continue with next_cursor. |
| `workbench://policy` | observe, operate | Current local policy; configuration is not authorization for a new action. |
| `workbench://runs/{id}` | observe, operate | One owned run manifest; no raw files or save data. |
| `workbench://tools` | observe, operate | Exactly the registered tools, safety classes and hints. |

<!-- END GENERATED CATALOGUE -->

## Bounds and evidence

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
