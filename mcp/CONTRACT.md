# Workbench MCP extension contract

The public boundary is typed operations on registered identifiers, not a shell-script catalogue. Keep orchestration in
ordinary Python operations and expose only small, typed wrappers. A new group is one `src/tools/<group>.py` module and
one entry in `GROUPS` in `src/tools/registry.py`. No runtime imports from a caller-selected checkout.

## Shared dependencies

`register(mcp, deps)` receives the lifecycle-owned `src.deps.Deps` object. It is shared by this server, not per client.
Construction and registration do not connect to optional backends. `open()` creates the local registry when the server
lifespan starts; `close()` cancels and reaps this server's workers, then closes registry access.

| Member | Contract |
|---|---|
| `config` | Frozen `Config`: explicit host configuration; absent optional paths mean unavailable, not a guessed fallback. |
| `policy` | `snapshot()` reads current operator policy. `authorize(lane=, resource_class=, wall_seconds=, plan_hash=, authorization=None, stage="plan" or "admission")` rereads it each time. Call both stages. No client Boolean is authorization. |
| `paths` | `PathGuards.read(path)` checks registered roots, canonical containment and protected roots. `output(path)` also refuses existing output. `reserve_file(path)` atomically creates a new file and returns its fd, rejecting symlink directory components. Close that fd. |
| `registry` | SQLite WAL `Registry`, short transactions, full synchronization, five-second busy deadline. `create_job`, `get_job`, `list_jobs`, `update_job`, `append_event`, `events`, `finish`, `receipt`; `create_run`, `get_run`, `update_run`; `add_artifact`, `get_artifact`, `artifact_path`. |
| `supervisor` | Linux `Supervisor`: `await start(job_id, argv, cwd=, wall_seconds=, queue_timeout_seconds=3600, grace_seconds=2, queued=False, cancellation="group", trusted_read_environment=False)` starts a trusted worker. `mark_admitted(job_id)` separates queue/held clocks. `await cancel(job_id)` never selects by name. `reconcile(job_id)` imports a durable guardian receipt after disconnect/restart. Ordered adapter mode is described below. |
| `error` | `raise deps.error(code, operation)` returns the one `WorkbenchError`, a framework `ToolError`. It carries only fixed operator guidance, never backend exception text. |
| `tool(mcp, name)` | Decorator using the group's declared safety spec, registering only selected tools. Unselected decorators leave the function unregistered. |
| `selected`, `specs` | Actual selected names and the complete validated `ToolSpec` map. Use these when adding resources. |
| `gates` | Injected passive observation backend. `snapshot(device=None, request_pid=None, cursor=0, limit=50)` returns typed timestamped queue state without acquiring locks or creating files. |
| `add_cleanup(fn)` | Register a bounded async cleanup callback. Lifespan invokes callbacks in reverse order after stopping owned workers. |

`Config` includes `state_root`, `harness_dir`, `gpu_gate`, `intel_gate`, `build_gate`, `checkout`, `dependency_python`,
`read_roots`, `write_roots`, `protected_roots`, `profile` and `extra_tools`. All host paths are absolute and operator-set;
the default registry is outside repositories under XDG state. The state directory and trusted registered roots must not
be writable by untrusted principals. Path checks and redirected HOME are not an OS sandbox. Never execute unreviewed
code or use a path-only check as protection against a hostile same-user process.

Domain integrations may configure `session_catalogue`, `checks_catalogue`, `idle_gate`, `panels_script`,
`journal_reader`, and `knowledge`; these are optional operator-owned backends, not caller parameters. CPU build/idle
policy uses `max_cpu_seconds` (up to 86,400); GPU/Intel uses `max_hold_seconds` (up to 480).

`await supervisor.start_operation(job_id, operation, cancel=None)` owns an async operation and scoped cancel hook.
Both callables take no arguments. The operation or its cancel hook must store the final receipt with `registry.finish`.
Returning without a terminal receipt is `uncertain_outcome`, not success. Cancellation invokes the hook once with a
30-second cleanup deadline before cancelling the operation task. Do not use this for an unsupervised child process.

`registry.claim_authorization(authorization_id, plan_hash, job_id)` reserves one named hold for exactly one job.
Claim before launch, then still recheck `policy.authorize(..., stage="admission")` inside admission. A repeated claim
for the same job/plan is idempotent; a different job cannot consume the same authorization. `registry.finish` also
finishes linked nonterminal Run records transactionally, preserving their build provenance and terminal evidence.

For queue position, append a `gate_request` event with `{pid, device, request_id}` after the real gate process is
identified. `registry.latest_event(job_id, "gate_request")` lets `queue_status` map that owned request without accepting
an arbitrary PID from a client. Kernel and file observations still determine whether the ticket is live.

## Records

Wire models are in `src/models/__init__.py`, forbid extra fields and preserve nulls. Opaque IDs have the form
`kind_<32 hex digits>`. The server makes them; callers do not. `Run` and `Job` add immutable `attempt_id` to the shared
record shape. A retry is a new Job/attempt, never an overwrite. State transitions and final receipts are transactional.

### Run

| Field | Type / meaning |
|---|---|
| `run_id`, `attempt_id`, `owner` | Server-made run/attempt identity; registry ownership. |
| `state` | planned, queued, starting, running, stopping, completed, failed, cancelled, interrupted. |
| `build` | `{commit, source_stamp, binary_hashes, profile_id}`; absent provenance is null, hashes are a map. |
| `config_hash`, `policy_revision` | Pinned configuration/policy identity, or null when not known. |
| `gate` | `{request_id, class, device, queue_seconds, held_seconds}`; unknown clocks are null. |
| `capabilities` | Named observed capabilities, nullable Boolean/string values. |
| `started_at`, `ended_at`, `exit`, `stop_reason` | Observed lifecycle evidence, nullable. |
| `timing_valid` | Boolean or null, never implied by an ordinary run. |
| `artifacts`, `warnings` | At most 1,000 artifact identifiers and 100 bounded warnings. |

`create_run(**fields)` supplies IDs and owner. `update_run(run_id, **changes)` cannot change identity or a terminal run.
Keep attempts separate. There is deliberately no delete operation.

### Job

| Field | Type / meaning |
|---|---|
| `job_id`, `attempt_id` | Opaque immutable identities. |
| `state`, `phase` | State above and an operation-specific phase, not a guessed percentage. |
| `queued_at`, `started_at` | UTC timestamps; start stays null while waiting for admission. |
| `completed_steps`, `total_steps` | Nondecreasing completed count; total may be null. |
| `progress_text` | At most 2,048 characters of safe progress, not arbitrary subprocess output. |
| `run_ids`, `artifact_uris` | At most 1,000 references each. |
| `error` | Error envelope below, or null. |

`create_job(operation, idempotency_key=None, request=None, total_steps=None)` atomically deduplicates by registry owner,
operation and key. The same key with different canonical request data fails. The same key after failure still returns
the failed attempt. Use a new key only after explicit reconciliation and a deliberate retry decision.

`update_job(job_id, state=None, **changes)` accepts only phase/start/counter/text/reference/error fields. It cannot set
a terminal state: use `finish(job_id, state, receipt, error=None)` to store terminal state and receipt atomically.
`list_jobs(cursor=0, limit=25, active_only=False)` returns `{jobs, next_cursor, observed_at}`. Follow a non-null cursor.

### Artifact

| Field | Type / meaning |
|---|---|
| `id`, `uri`, `mime` | Server ID, `workbench://artifacts/<id>` reference and MIME type. |
| `bytes`, `sha256` | Observed length/digest, nullable in the schema. |
| `producer` | `{tool, version, source_commit}`. |
| `run_id`, `clock` | Optional owning run and actual instrument clock. |
| `privacy`, `complete` | `private` or `public_candidate`; completion is explicit. Candidate never means published. |

`add_artifact(path, guards=deps.paths, mime=, producer=Producer(...), run_id=None, clock=None,
privacy="private", complete=False)` checks roots, hashes a stable file and retains its private path internally. Large
hashing belongs in a bounded background job. A URI is metadata, not permission to serve raw bytes: this core exposes
no artifact-content resource. Never add a generic file resource, and never publish saves, dumps or guest memory.

### Events, process identity and receipts

| Record | Fields |
|---|---|
| Event | `{cursor, job_id, observed_at, kind, data}`. Global increasing SQLite sequence; at most 16 KiB of JSON data per event. |
| Event page | `{events, next_cursor, has_more}`; default 50, maximum 100. Cursor means last seen sequence, not an offset. |
| Process identity | `{pid, start_ticks, boot_id, process_group, session_id}` from Linux procfs. Zombies are not live work. |
| Receipt | `{job_id, observed_at, exit_code, stop_reason, process_exited, descendants_exited, queue_seconds, held_seconds}`. Unknown facts remain null. |

Events are append-only. `append_event(job_id, kind, data)` accepts a lowercase underscore kind of at most 40 characters
and bounded safe metadata. Do not append secrets, command lines or raw exception text. `events(job_id, cursor=0,
limit=50)` has no side effect. The resource `workbench://jobs/<id>/events?cursor=<n>&limit=<n>` uses the same cursor.

## Declaring a group

```python
# src/tools/example.py
from src.policy import Safety, ToolSpec

SPECS = (
    ToolSpec("example_policy", Safety.READ, "Read the current windowless policy."),
)


def register(mcp, deps):
    @deps.tool(mcp, "example_policy")
    async def example_policy() -> dict:
        return deps.policy.snapshot().model_dump()
```

Add `"example"` to `GROUPS`. A contract test uses the real server and client, not a fake decorator:

```python
from fastmcp import Client
from src.config import Config
from src.main import build_server


async def test_example(tmp_path):
    async with Client(build_server(Config(state_root=tmp_path / "state"))) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["example_policy"].annotations.read_only_hint is True
        assert tools["example_policy"].input_schema.get("required", []) == []
        result = await client.call_tool("example_policy")
        assert result.data["windowless"] is True
```

`observe` selects READ only; `operate` also selects RUN and WRITE_LOCAL. Exact extra names are explicit opt-ins;
unknown names fail startup. PUBLIC and DANGER cannot register even as extras. `ToolSpec` has `destructive`, `idempotent`
and `open_world` flags; annotations are generated from these plus the safety class. A run submission is RUN and usually
non-idempotent unless its key enforces deduplication. Cancellation is WRITE_LOCAL, destructive and idempotent. Every
group must have catalogue/schema/annotation tests and tests for unavailable prerequisites and refused effects.

## Long work, cancellation and errors

Return the Job promptly, append phase/counter events, and expose final receipts through `job_read`. Never block a tool
call on an entire run. `job_wait` is a maximum 30-second observed-state wait; cancellation of that wait cancels only the
wait. It does not call `job_cancel`. Idempotency prevents a transport retry from starting a second worker.

The core guardian isolates an owned trusted CPU child in a new process group, acts as a Linux subreaper, redirects
stdio to private logs, limits core dumps, enforces a declared queue/held deadline, and cleans descendants on cancellation,
normal child exit or server death. It escalates after a bounded grace period using birth-checked pidfds. Its completion
file is written once and fsynced. A missing receipt is an uncertain outcome, never success. Server shutdown closes only
workers this instance started; another server using the same registry may request cancellation by exact stored identity.

**Never run a game through the generic `cancellation="group"` mode.** A trusted `cancellation="adapter"` worker owns
the entire gate subtree and must handle TERM by stopping owned inputs/game, observing game death, then ending its gate.
The guardian initially signals only that direct adapter. On grace expiry it kills observed live descendant leaves,
observes their death, then their parents; it does not release a gate first. The adapter rereads admission policy before
starting work. Save draining remains unsupported unless an upstream contract proves it. Only disposable sessions may
be forcibly stopped. Persistent player saves, raw bridge commands and gate-file unlinking are out of scope.

`trusted_read_environment=True` is only for reviewed GET-only provider adapters: it retains existing HOME,
XDG_CONFIG_HOME and GH_CONFIG_DIR for the provider CLI, never token variables. It is not a tool parameter and must not
be used for foreign checkout code. The registration exposes no raw Supervisor argv or environment interface.

Raise only the fixed envelope `{code, operation, retryable, retry_after_seconds, operator_action}`. Codes are
`not_found`, `invalid_argument`, `refused_by_policy`, `unavailable`, `busy`, `timeout`, `uncertain_outcome`, `backend_failed`.
Middleware also converts schema validation and unexpected backend failures. Never infer mutation retryability from an
exception type or re-run an uncertain mutation. Unknown capability/state remains null; timestamps accompany snapshots.
Use bounded lists/cursors and artifact references instead of embedding log/image bytes. Logs go to stderr, not stdout.
