---
name: hookshot-scripted-play
description: >
  Drive an owned, gated, windowless Hookshot or Longshot run through the control bridge or the capability-selected hsap
  drivers. Choose the sampled-state or legacy file tap, wait for fresh controllable state, send inputs, capture
  pictures, inspect steps and keep a save only after proving it resumes. Use for controller-free play, test saves,
  area tours and routes that need position. Read hookshot-workbench first.
---

# Hookshot scripted play

**Hookshot / Longshot:** keep skill, harness and `HOOKSHOT_WB_*` names. The shared resolver selects the actual
executable, log and client; missing/mixed identities refuse. Naming proves no optional capability: the renamed main
client lacks session `show`/`log`/`stop`; those examples require the separately supported session-layer client.

**Use for:** scripted input, state samples and save-backed routes. Read `hookshot-workbench` and the agent brief.
All game runs are windowless. Never use the upstream client's unwrapped `launch` to bypass the harness/gate.

Set `WT` to the selected owned Hookshot or Longshot worktree before deriving the client path.

```sh
H="$HOME/Games/.hookshot-harness"
W="$HOME/Code/hookshot-wt"
P="$W/deps/python/bin/python3"
source "$H/hs-names.sh"
hs_names checkout "$WT" || exit
CTL="$HS_CTL"  # scripts/hookshot_ctl.py or scripts/longshot_ctl.py, selected automatically
```

Set `COPY`, a new `TAG`, a new evidence directory `OUT`, and a new private `LIVE` directory outside
`$COPY/runs/$TAG`. Create `LIVE` with `mkdir -m 700 "$LIVE"` before writing its steps. `hs-run.sh` deletes a reused
run tag. Keep saves, routes, flag names, coordinates, goals and world-model facts in `$H/playthrough/NOTES.md` and
local run files, never in this public skill or a tracker.

## 1. Choose the transport, then reach controllable play

1. Pin the binary and inspect its actual capabilities. The ordinary bridge supplies input, status, options, waits,
   screenshots and quit. It does **not** imply a sampled position/camera stream. The selected base client's
   `status.game` has event/loading/age but no sample frame or timestamp.
2. For steering, select `playthrough/hsap.py` only with the control-bridge `oot` sampled-state extension. It requests
   `subscribe` with `events:["oot"]`, `every:6`, selected `flags`, and `changes:true`. An unsupported subscription
   fails; there is no silent fallback. For older `HOS_OOT_STATE_FILE` builds, choose `hsap_file.py` explicitly.
   A build with neither tap supports neither driver.
3. Choose a copied save and `--boot continue`, or a fresh profile and `--boot newgame`. `--boot none` sends no boot
   presses; it still waits for fresh quiet state. Put the owned route's steps in `$LIVE/steps.txt` **before launch**;
   no steps means the driver keeps waiting. Use an `end` step to request completion.
4. Launch through the driver, which invokes `hs-run.sh` and its GPU gate. Use a bounded wall time with shutdown
   reserve; do not wrap it in another gate:

   ```sh
   "$P" -I -B "$H/playthrough/hsap.py" "$COPY" "$TAG" 450 --live "$LIVE" --save "$SAVE" --boot continue
   ```

   For a fresh profile omit `--save` and use `--boot newgame`. For the legacy tap replace only `hsap.py` with
   `hsap_file.py`. The boot timer begins when the game log appears, not while queued.
5. Require `gameplay` and fresh `state` events in `$LIVE/out.jsonl`; require the intended map and known-false event
   and loading state. The bridge driver's `$LIVE/state.jsonl` is a client journal, not a second game-output channel.
   Read `game.frame` for sample age, not the reply envelope's current frame. Retained state through a pause/load is
   not evidence that control returned. Inspect a capture before claiming the intended scene.

The sampler's `every` counts game presents, not wall time. Its selected flags are bounded; missing/unreadable values
are null, enum values are hashes. Changes establish a new baseline on subscription. Read the extension's matching
`docs/control.md`; do not copy an extension-only command into an ordinary client's CLI. In particular, the selected
base client's `watch --help` has **no `--changes` option**. `hsap.py` sends the subscription itself.

A session-layer launch's `--ready bridge|present|play` is distinct from this driver. `play` requires a presented frame,
its play heuristic and known-false event/loading; it neither presses through the title nor guarantees sample freshness.
The upstream `--profile test` isolates its user folder and environment file but not all inherited settings/caches.
Only a configured workbench session adapter supplies the workbench's gates, isolation and wall bound.

## 2. Send an input, observe the effect, capture and stop

For a run already started through the gate with `HOS_CONTROL` set to its private socket:

```sh
"$P" -I -B "$CTL" --json status
"$P" -I -B "$CTL" --json press A --frames 8 --exclusive
"$P" -I -B "$CTL" --json stick L 0.5 0 --frames 8 --exclusive
"$P" -I -B "$CTL" --json wait present --frames 1 --timeout 10
"$P" -I -B "$CTL" --json shot "$OUT/after.png"
```

The client defaults to `$HOS_CONTROL`; use `--sock` explicitly when selecting among owned runs. Do not send input to
an arbitrary discovered socket. An input reply means its frames completed, **not** that the scene changed or play
resumed. Record sent, accepted, observed state change and inspected picture separately. Serialize requests per socket;
keep streaming subscriptions on their own connection. Reconnect after an uncertain reply without replaying the input.

- Bridge `stick` uses x/y in [-1, 1]. `sequence --hos` uses seconds and milliseconds; without it, times are frames.
  `--exclusive` covers the input, not an unlimited physical-device lock. `release` clears this client's pending input.
- File input is `HOS_INPUT_SCRIPT='<seconds>:<buttons>[/<milliseconds>],...'` or `--input` to `hs-run.sh`.
  Buttons: `A B X Y L R ZL ZR PLUS MINUS LS RS LEFT UP RIGHT DOWN`; stick edges: `LUP LDOWN LLEFT LRIGHT RUP
  RDOWN RLEFT RRIGHT`. Combine with `+`; the default hold is 200 ms. Unknown file tokens can be ignored silently.
- `HOS_INPUT_FRAME_CLOCK=1` advances file time at 60 game presents per second. `HOS_INPUT_EXCLUSIVE=1` suppresses
  physical input while the script is active and for its grace period, not forever. `HOS_INPUT_LIVE` reads complete
  lines every 30 ms; each line's times start when read. Keep that file outside the disposable run directory.
- `hsap.py` routes ordinary `press`, `stick`, `push` and `raw` through the bridge. Its `--analog`/`HSAP_ANALOG=1`
  selects exact steering angles. `hsap_file.py` needs an extra `LX=`/`LY=` file-parser extension for analog pushes;
  the bridge's analog support does not imply that extension.
- Read `option get` before `option set`; setters use the real menu and persist. Restore only your owned copy's
  intended settings. Debug travel/age operations need observed binary support and an authorized disposable profile.
  Never enable `HOS_CONTROL_MEM=rw` or use memory writes as an input substitute.
- Use bridge `shot` for Vulkan captures. The drivers' legacy `shot` step writes `!shots` to live input; in the selected
  base that command targets the WebGPU renderer, not Vulkan. Do not treat its step completion as a captured PNG.
  Bridge shots omit the Insert-menu overlay; use the harness's menu-capture switch when that overlay is the evidence.

### Owned session logs and shutdown

A control-session `RUN` is a directory with `manifest.json` and `log.txt`. It is **not** an hs-run directory with
`hookshot_log.txt` or `longshot_log.txt` (the shared reader uses `log_path`). Use `--run` only for a manifest made
by the session layer. With that supported client, `show` and `log` also work after exit.

```sh
"$P" -I -B "$CTL" --json --run "$RUN" show
"$P" -I -B "$CTL" --json --run "$RUN" log --lines 40
"$P" -I -B "$CTL" --json --run "$RUN" log --since "$CURSOR" --expect "$TEXT" --timeout 10
"$P" -I -B "$CTL" --json --run "$RUN" stop --timeout 10
```

Take the log's byte cursor **before** the action whose new line matters. `--expect` requires `--since`; `more` means
continue from the returned cursor. A cursor past EOF refuses. MCP log cursors are opaque and are not these byte offsets.
There is no workbench MCP log-line wait tool yet; `run_logs` is a bounded read, not an `expect_log` replacement.

A successful stop requires observed game/owner/wrapper exit. If graceful quit times out, the owned game remains running;
only an authorized disposable run may use `stop --terminate`. Save draining is unsupported: never call it a clean save.
For `hsap`, let its owned runner stop the game before ending the gate. A summary game exit 143 can mean its deliberate
SIGTERM; shell exit 0 or an `end` event alone does not prove the route's assertions passed.

## 3. Run a local route and prove a saved checkpoint

Read `$H/playthrough/NOTES.md` for the local world model, planner, segment schema and save library. Never send their
contents to a remote model service. Use the local rules decider. The public drivers stay standard-library-only;
the **local** pilot/probe import `requests`, which the pinned build Python lacks. The local save utility also imports
Python 3.14's `compression.zstd`. Use the installed system Python for these local tools; do not install into shared
Python or system packages.

```sh
python3 -I -B "$H/playthrough/probe.py" "$ROUTE" --tag "$TAG" --copy "$COPY" --save "$SAVE" --wall 450
python3 -I -B "$H/playthrough/pilot/pilot.py" run "$SEGMENT" --decider rules --tag "$TAG" --copy "$COPY" --save "$SAVE" --wall 450
```

Choose **one** of those launchers for a new tag. Their defaults can exceed the hold limit; pass `--wall` explicitly.
The probe invokes `hsap.py`, so it needs the socket sampler too. An older file-tap build is not a compatible fallback.
Use `setsid --wait nohup` for a saved managed launcher and retain its child receipt; one chain queues at a time.

1. Inspect every `step.result`, `bad` event and goal check. `walk`/`run` can be reached, stuck, timed out or held.
   A later step after a stuck one proves nothing about its intended target. Use the local planner instead of a
   straight walk across obstacles. An event-held action needs an explicit rule to resume and fresh control evidence.
2. The debug `save` step requires its build's save extension. Require the reply plus a newly written `progress.sav`,
   not an old slot. A short delay is not a save-drain barrier. Keep the run's disposable `user/save/` after exit.
3. Continue from that copied user folder in a new bounded run and inspect the scene and state. Only then mark the
   checkpoint proven in the local library. `hs_make_save.py --lib-add` needs a new `--name`; `--lib-proven` records
   the observed result and optional `--presses`. Do not rewrite library entries merely to make a route pass.
4. Split a longer route into separate bounded holds, each continuing from the preceding run's copied user folder.
   Never hold the card while waiting for a model, another lane or manual decisions.

## Game-free reader checks

These use fakes, not a game or a GPU. Run each named test through the core-dump wrapper; keep the real output.
On the public workbench the driver folder is `play/`; in the live harness it is `playthrough/`.

```sh
"$H/hs-nocore.sh" "$P" -I -B "$H/playthrough/test_hsap.py"
"$H/hs-nocore.sh" "$P" -I -B "$H/playthrough/test_hsap_file.py"
"$H/hs-nocore.sh" python3 -I -B "$H/playthrough/pilot/test_pilot.py"
```

`HSAP_TEST_MODULE=<file>` selects a staged driver for its matching test. Never replace a driver used by a queued or
live run; stage it, test it, then replace only when no process depends on it. A fake-driver pass is not a game route,
a tap-compatible binary, a real save barrier or a visual proof.

## MCP routes

Use the workbench skill's **MCP routes** for operator setup and the canonical `uv run --project mcp hookshot-wb`
entry. From that repository root, `call TOOL --args JSON` invokes a table tool; authorized mutations need
`--profile operate`. Keep the same state root: separate CLI processes share the owning broker.
A launch Job ends only when the session ends. Read `.events.events[]` / `.kind` for `bridge_ready`, then observe
`menu` or `play` for 30 further frames using a state predicate with `stable_frames:30` before first input/capture.
Boot input is refused. The opening cutscene reports `loading`; menu/play waits can time out during a drawn cutscene,
and input may be refused. Inspect advancing frames/captures; do not mislabel that as a frozen bridge or replay input.
Missing sampled-state freshness remains unknown. `broker stop` ends owned sessions, not only a client wait.

| Step | Shell route | MCP tool (group; integration) | What it saves |
|---|---|---|---|
| Start/stop | Gated `hs-run.sh`/driver; owned runner stop | `session_launch(build_id, profile_id, wall_seconds, idempotency_key)`, `session_stop(run_id, reason, idempotency_key)` (session; integrated) | Owns disposable run, gate and stop receipt; no clean-save claim. |
| Queue/receipt | Gate tickets, process identity and child receipt | `queue_status(device="gpu", job_id=job_id)`, `job_read(job_id)` (status; integrated) | Joins the job to its live gate request without PID guessing. |
| State/readiness | `"$CTL" status` (shared-selected Hookshot/Longshot client); driver samples | `session_state(run_id)`, `session_wait(run_id, predicate={"kind":"state","state":"menu"}, stable_frames=30, idempotency_key=key)` (session; integrated) | Separates drawable-state readiness from fresh control; use `controllable` only with sampled-state support. |
| Input then observe | `press`/`stick`/`sequence`, then status/wait | `session_input(run_id, steps, units="frames", idempotency_key=key)`, then `session_state(run_id)` (session; integrated) | Typed units, deduplicated submission and separate effect evidence. Not a route planner. |
| Capture/settings | `shot`, `option get/set` | `session_shot(run_id)`, `session_options_get(run_id)`, `session_options_set(run_id, values)` (session; integrated) | New capture paths and bounded settings; unsafe setting classes refuse. |
| Read/wait for log | Session `log --lines`; `log --since --expect` | `run_logs(run_id, last_lines=40)` (evidence; integrated); **no log-wait tool** | Bounded retained reads. Keep the shell cursor wait for a new line. |
| Copy/inspect save | Disposable save copy; `slots` | `save_clone(save_id, idempotency_key)`, `session_saves_list(run_id)` (session; integrated) | Registered-source copy and metadata only. No autosave or resume proof. |
| Inspect route evidence | Run log, captures and labelled panels | `run_faults(run_id)`, `evidence_panels(images, label, idempotency_key)` (evidence; integrated) | Signature classification and private panel artifacts; inspect them yourself. |
| Find previous proof | Read local notes/registered reports | `knowledge_search(query, collection="reports")`, `workbench://reports/{id}` (knowledge; integrated) | Bounded revision-pinned report access. The URI is a resource; private game data stays private. |
