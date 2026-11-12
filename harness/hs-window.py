#!/usr/bin/env python3
"""The compositor's side of a windowed harness run (hs-run.sh --window). Written 2026-11-11 (WindowRuns).

    hs-window.py rules <run folder>
    hs-window.py watch <run folder> <pid of the run's top process>

The user's permission (2026-11-11: "you can add the rules for windowed") covers exactly what `rules` sends to his
running Hyprland: two window rules for the test window's own class, hookshot-harness. Everything else here is reading
(`hyprctl instances/monitors/clients/activewindow -j`). Nothing is dispatched, no setting is changed, no file under
~/.config is touched, no input is sent.

rules: finds the one live Hyprland instance and its Wayland socket (whatever the caller's environment says; a
  safe-mode instance only if .hookshot-harness/safe-mode-ok holds its pid, as the GPU gate does), refuses when no
  monitor is on, the session is locked (a window is not drawn then: a frame log from it is no presentation
  measurement) or a hookshot-harness window is already open, writes the active window, the clients and the monitors
  to <run>/window-before.json, sends the two rules with `hyprctl eval` and writes each with its answer to
  <run>/window-rules.txt. Exit 0 and one line on stdout, "<instance> <socket> <the monitor's pixels WxH> <its
  logical size WxH>", only if both answered `ok`. Otherwise exit 3, the reason on stderr and in <run>/window-refused.txt, and the
  caller starts no game.

  The rules (Hyprland 0.56.2; read in its source, proven with a harmless window, reports/WindowRuns.md):
    1. no_initial_focus, no_focus (never focused, the pointer goes through it), suppress_event fullscreen maximize
       activate activatefocus fullscreenoutput (what the game asks for changes nothing).
    2. float, size = the monitor's logical size, move 0 0: the window covers the monitor on its active workspace, so
       it is composited at the monitor's rate and its swapchain has the monitor's pixels. (A window on a hidden
       workspace gets no frame callbacks: presentation cannot be judged there.)
  A rule sent again under its name is appended to, not replaced (src/config/lua/bindings/LuaBindingsConfigRules.cpp):
  the same effects every run are harmless, a different effect under these names stays until the compositor restarts.
  Do not change the rules' text without reading that.

watch: until the run's top process is gone, reads the clients and the active window (every 0.25 s for 30 s, then
  every 2 s) into <run>/window-samples.jsonl. It ends the run at once (SIGTERM to the top process, SIGKILL to what is
  left 20 s later) and says why in <run>/window-problem.txt if a window of the run has another class (the rules do
  not cover it), is not floating, is fullscreen, sits on another workspace, or is the active window.
  It also reads `hyprctl locked` and the monitors at each look: a run during which the screen locked or blanked, or
  the user switched to another workspace (the window stays on its own and is not drawn while he is elsewhere), is
  not ended (nothing is changed about the screen), it is marked. Afterwards it writes the active window and the
  clients to <run>/window-after.json and one line to <run>/window.txt:
    window=ok|none|PROBLEM(...) class= size= at= ws= float= active=same|CHANGED(...) others=same|CHANGED
    screen=on|LOCKED(k/n)|BLANKED(k/n)|HIDDEN(k/n) samples=N [notes=...]
"""
import json, os, signal, subprocess, sys, time

CLASS = "hookshot-harness"
SAFE_OK = "/home/mooki/Games/.hookshot-harness/safe-mode-ok"


def hyprctl(env, *a):
    try:
        return subprocess.run(["hyprctl", *a], env=env, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def loads(text, default):
    try:
        return json.loads(text)
    except ValueError:
        return default


def base_env():
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    return env


def refuse(R, why):
    with open(os.path.join(R, "window-refused.txt"), "w") as f:
        f.write(why + "\n")
    sys.stderr.write("hs-run.sh --window: %s; no game is started\n" % why)
    sys.exit(3)


def rules(R):
    env = base_env()
    live = [i for i in loads(hyprctl(env, "instances", "-j"), []) if os.path.exists("/proc/%d" % i["pid"])]
    if len(live) != 1:
        refuse(R, "not one running Hyprland instance (%d)" % len(live))
    pid = live[0]["pid"]
    try:
        cmd = open("/proc/%d/cmdline" % pid, "rb").read().split(b"\0")
    except OSError:
        cmd = []
    try:
        allowed = open(SAFE_OK).read().strip() == str(pid)
    except OSError:
        allowed = False
    if b"--safe-mode" in cmd and not allowed:
        refuse(R, "Hyprland (pid %d) runs in safe mode and the user has not allowed runs on it" % pid)
    env["HYPRLAND_INSTANCE_SIGNATURE"] = live[0]["instance"]
    monitors = loads(hyprctl(env, "monitors", "-j"), [])
    mon = next((m for m in monitors if m.get("focused")), None)
    if not mon or mon.get("disabled") or not mon.get("dpmsStatus"):
        refuse(R, "no monitor is on (a window there is not composited)")
    if mon.get("specialWorkspace", {}).get("name"):
        refuse(R, "a special workspace is open on %s" % mon["name"])
    if hyprctl(env, "locked").strip() != "false":
        refuse(R, "the session is locked (Hyprland does not draw windows then)")
    clients = loads(hyprctl(env, "clients", "-j"), None)
    if clients is None:
        refuse(R, "hyprctl clients did not answer")
    if any(c.get("class") == CLASS for c in clients):
        refuse(R, "a %s window is already open" % CLASS)
    before = {"time": time.strftime("%F %T"), "instance": live[0], "monitor": mon,
              "active": loads(hyprctl(env, "activewindow", "-j"), {}), "clients": clients}
    lw, lh = round(mon["width"] / mon["scale"]), round(mon["height"] / mon["scale"])
    before["expect"] = {"size": [lw, lh], "at": [mon["x"], mon["y"]], "workspace": mon["activeWorkspace"]["id"]}
    with open(os.path.join(R, "window-before.json"), "w") as f:
        json.dump(before, f, indent=1)
    texts = [
        'hl.window_rule({ name = "hookshot-harness", match = { class = "^hookshot-harness$" }, '
        'no_initial_focus = true, no_focus = true, '
        'suppress_event = "fullscreen maximize activate activatefocus fullscreenoutput" })',
        'hl.window_rule({ name = "hookshot-harness-float", match = { class = "^hookshot-harness$" }, '
        'float = true, size = "%d %d", move = "0 0" })' % (lw, lh),
    ]
    with open(os.path.join(R, "window-rules.txt"), "w") as f:
        for t in texts:
            answer = hyprctl(env, "eval", t).strip()
            f.write("%s\n-> %s\n" % (t, answer))
            f.flush()
            if answer != "ok":
                refuse(R, "the compositor did not take a window rule (%s)" % (answer or "no answer"))
    print("%s %s %dx%d %dx%d" % (live[0]["instance"], live[0]["wl_socket"], mon["width"], mon["height"], lw, lh))


def tree(top):
    """top and every descendant of it that lives now"""
    kids = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                stat = open("/proc/%s/stat" % d).read()
                kids.setdefault(int(stat.rsplit(")", 1)[1].split()[1]), []).append(int(d))
            except (OSError, IndexError, ValueError):
                pass
    out, todo = set(), [top]
    while todo:
        p = todo.pop()
        if p not in out:
            out.add(p)
            todo += kids.get(p, [])
    return out


def alive(pid):
    try:
        return open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def screen(env, workspace):
    """on; LOCKED (the session lock: workspaces are not drawn, src/render/Renderer.cpp:1109); BLANKED (no monitor on);
    HIDDEN (no monitor shows the window's workspace, or a special workspace is open over it)"""
    if hyprctl(env, "locked").strip() == "true":
        return "LOCKED"
    mons = [m for m in loads(hyprctl(env, "monitors", "-j"), []) if m.get("dpmsStatus") and not m.get("disabled")]
    if not mons:
        return "BLANKED"
    shown = [m for m in mons if m.get("activeWorkspace", {}).get("id") == workspace and not m.get("specialWorkspace", {}).get("name")]
    return "on" if shown else "HIDDEN"


def watch(R, top):
    before = json.load(open(os.path.join(R, "window-before.json")))
    env = base_env()
    env["HYPRLAND_INSTANCE_SIGNATURE"] = before["instance"]["instance"]
    want = before["expect"]
    active0 = before["active"].get("address")
    others0 = sorted(c["address"] for c in before["clients"])
    t0 = time.time()
    seen, problems, notes, n, killed_at = None, [], [], 0, None
    dark, looks = {}, 0   # (samples with the screen locked or blanked: the window is not composited then)
    with open(os.path.join(R, "window-samples.jsonl"), "w") as sf:
        while alive(top):
            mine_pids = tree(top)
            clients = loads(hyprctl(env, "clients", "-j"), None)
            active = loads(hyprctl(env, "activewindow", "-j"), {})
            scr = screen(env, want["workspace"])
            looks += 1
            if scr != "on":
                if scr not in dark:
                    notes.append("screen %s from %.0f s" % (scr, time.time() - t0))
                dark[scr] = dark.get(scr, 0) + 1
            if clients is not None:
                n += 1
                mine = [c for c in clients if c.get("pid") in mine_pids or c.get("class") == CLASS]
                row = {"t": round(time.time() - t0, 2), "active": active.get("address"),
                       "mine": [{k: c.get(k) for k in ("address", "class", "title", "pid", "at", "size", "floating", "fullscreen",
                                                       "fullscreenClient", "focusHistoryID", "visible", "hidden")} |
                                {"workspace": c.get("workspace", {}).get("id")} for c in mine]}
                sf.write(json.dumps(row) + "\n")
                sf.flush()
                fatal = []
                for c in mine:
                    if seen is None and c.get("class") == CLASS:
                        seen = c
                        with open(os.path.join(R, "window-seen.json"), "w") as f:
                            json.dump(c, f, indent=1)
                    if c.get("class") != CLASS:
                        fatal.append("a window of the run has the class %r, which the rules do not cover" % c.get("class"))
                    if not c.get("floating"):
                        fatal.append("the window is not floating")
                    if c.get("fullscreen") or c.get("fullscreenClient"):
                        fatal.append("the window is fullscreen")
                    if c.get("workspace", {}).get("id") != want["workspace"]:
                        fatal.append("the window is on workspace %s, not %s" % (c.get("workspace", {}).get("id"), want["workspace"]))
                    if active.get("address") == c.get("address"):
                        fatal.append("the window is the active window")
                    if c.get("class") == CLASS and (c.get("size") != want["size"] or c.get("at") != want["at"]):
                        note = "size %s at %s (expected %s at %s)" % (c.get("size"), c.get("at"), want["size"], want["at"])
                        if note not in notes:
                            notes.append(note)
                if active.get("address") != active0 and not any(active.get("address") == c.get("address") for c in mine):
                    note = "the active window became %s (%s), not a window of the run" % (active.get("address"), active.get("class"))
                    if note not in notes:
                        notes.append(note)
                for p in fatal:
                    if p not in problems:
                        problems.append(p)
                if fatal and killed_at is None:
                    killed_at = time.time()
                    with open(os.path.join(R, "window-problem.txt"), "w") as f:
                        f.write("%s: %s: the run is ended\n" % (time.strftime("%F %T"), "; ".join(problems)))
                    try:
                        os.kill(top, signal.SIGTERM)
                    except OSError:
                        pass
            if killed_at is not None and time.time() - killed_at > 20:
                for p in tree(top):
                    try:
                        os.kill(p, signal.SIGKILL)
                    except OSError:
                        pass
            time.sleep(0.25 if time.time() - t0 < 30 or killed_at is not None else 2.0)
    time.sleep(1.0)
    after = {"time": time.strftime("%F %T"), "active": loads(hyprctl(env, "activewindow", "-j"), {}),
             "clients": loads(hyprctl(env, "clients", "-j"), [])}
    with open(os.path.join(R, "window-after.json"), "w") as f:
        json.dump(after, f, indent=1)
    a1 = after["active"].get("address")
    act = "same" if a1 == active0 and not any("active window" in x for x in problems + notes) else \
        "CHANGED(%s->%s)" % (active0, a1)
    left = [c for c in after["clients"] if c.get("class") == CLASS]
    others = "same" if sorted(c["address"] for c in after["clients"]) == others0 else \
        ("LEFT-OPEN" if left else "CHANGED")
    if problems:
        state = "PROBLEM(%s)" % "; ".join(problems)
    elif seen is None:
        state = "none"
    else:
        state = "ok"
    line = "window=%s" % state.replace(" ", "_")
    if seen is not None:
        line += " class=%s size=%dx%d at=%d,%d ws=%s float=%d" % (seen["class"], seen["size"][0], seen["size"][1], seen["at"][0],
                                                                 seen["at"][1], seen["workspace"]["id"], int(bool(seen["floating"])))
    line += " active=%s others=%s screen=%s samples=%d" % (
        act, others, "on" if not dark else ",".join("%s(%d/%d)" % (k, v, looks) for k, v in sorted(dark.items())), n)
    if notes:
        line += " notes=%s" % "|".join(notes).replace(" ", "_")
    with open(os.path.join(R, "window.txt"), "w") as f:
        f.write(line + "\n")
    print(line)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "rules":
        rules(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "watch":
        watch(sys.argv[2], int(sys.argv[3]))
    else:
        sys.exit(__doc__)
