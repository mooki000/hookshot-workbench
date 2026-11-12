"""Use the installed loop's pure views and locked scan; never call its initializing CLI."""
from datetime import datetime, timezone
import importlib.util
import sys
from src.models import utc_now
from src.effects.knowledge_common import checked_path, config, digest, read_json, sha, stamp, text, under


def load_module(deps):
    path = checked_path(deps, config(deps).prloop_script, "prloop_next")
    spec = importlib.util.spec_from_file_location("workbench_trusted_prloop", path)
    if spec is None or spec.loader is None:
        raise deps.error("unavailable", "prloop_next")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def load_snapshot(deps):
    root = checked_path(deps, config(deps).prloop_state_dir, "prloop_next", directory=True)
    state = read_json(deps, under(deps, root, "state.json", "prloop_next"), "prloop_next")
    policy = read_json(deps, under(deps, root, "config.json", "prloop_next"), "prloop_next")
    if not isinstance(state, dict) or not isinstance(policy, dict) or not isinstance(state.get("snapshot"), dict):
        raise deps.error("unavailable", "prloop_next")
    return state, policy, digest([state, policy])


def preflight_scan(deps, snapshot_id):
    cfg = config(deps)
    script = checked_path(deps, cfg.prloop_script, "prloop_refresh")
    root = checked_path(deps, cfg.prloop_state_dir, "prloop_refresh", directory=True)
    checkout = checked_path(deps, deps.config.checkout, "prloop_refresh", directory=True)
    state, policy, revision = load_snapshot(deps)
    if revision != snapshot_id:
        raise deps.error("busy", "prloop_refresh")
    # The stock scan executes its configured checkout's readers. Pin that trust boundary
    # instead of accepting a checkout from the loop's mutable JSON unchecked.
    from pathlib import Path
    if not isinstance(policy.get("checkout"), str) or Path(policy["checkout"]).resolve() != checkout:
        raise deps.error("refused_by_policy", "prloop_refresh")
    # Existing files may be replaced by the trusted atomic store, but only in a registered
    # write root. PathGuards.output is deliberately for *new* files, not existing stores.
    canonical = root.resolve()
    if not any(canonical.is_relative_to(p.resolve()) for p in deps.config.write_roots) or root.is_symlink():
        raise deps.error("refused_by_policy", "prloop_refresh")
    return script, root, checkout, state, policy


def prloop_next_impl(deps, snapshot_id=None, why=False):
    state, policy, revision = load_snapshot(deps)
    if snapshot_id is not None and snapshot_id != revision:
        raise deps.error("busy", "prloop_next")
    module = load_module(deps)
    module.validate_state(state)
    module.validate_config(policy)
    now = utc_now()
    result = module.next_action(state, policy, now)
    number = result.get("number") if type(result.get("number")) is int else None
    pr = state["snapshot"].get("prs", {}).get(str(number), {})
    record = state.get("active", {}).get(str(number), {})
    observed = stamp(state["snapshot"].get("at"))
    age = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(observed.replace("Z", "+00:00"))).total_seconds()) if observed else None
    prerequisites = [text(v, 1024).model_dump() for v in record.get("blocks", [])[:20]]
    exclusions = module.exclusions(state, policy)[:50] if why else []
    return {"snapshot_id": revision, "action": text(result.get("action"), 128).model_dump(), "pr": number,
        "head": sha(pr.get("head")), "prerequisites": prerequisites, "reason": text(result.get("why"), 2048).model_dump(),
        "exclusions": [text(v, 512).model_dump() for v in exclusions], "stale": age is None or age > config(deps).snapshot_max_age_seconds,
        "snapshot_at": observed, "observed_at": now, "request_feed_status": "available" if state["snapshot"].get("requests_available") is True else "unknown",
        "human_action_only": True, "untrusted": True}


def refresh_impl(deps, snapshot_id):
    """Runs only inside the core's owned guardian; its lock coordinates other scanners."""
    _, root, checkout, _, _ = preflight_scan(deps, snapshot_id)
    module = load_module(deps)
    if getattr(module, "REPO", None) not in config(deps).repositories:
        raise deps.error("refused_by_policy", "prloop_refresh")
    with module.locked(root):
        _, _, _, before, policy = preflight_scan(deps, snapshot_id)
        module.validate_state(before)
        module.validate_config(policy)
        old_heads = {key: value.get("head") for key, value in before["snapshot"].get("prs", {}).items()}
        state = module.scan(before, policy, module.Backend(checkout=str(checkout)), utc_now())
        module.validate_state(state)
        module.atomic_json(root / "state.json", state)
        revision = digest([state, policy])
    changed = [int(key) for key, value in state["snapshot"].get("prs", {}).items()
        if key.isdigit() and old_heads.get(key) != value.get("head")]
    return {"snapshot_id": revision, "changes": changed[:100], "changes_truncated": len(changed) > 100,
        "owed_actions": prloop_next_impl(deps, revision),
        "request_feed_status": "available" if state["snapshot"].get("requests_available") is True else "unknown",
        "exclusions": [text(value, 512).model_dump() for value in module.exclusions(state, policy)[:50]],
        "untrusted": True}
