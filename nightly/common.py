"""Local execution helpers. Public code; all machine and scenario values are configuration."""
from pathlib import Path
import json
import os
import re
import signal
import subprocess
import time

HARN = Path(__file__).resolve().parent.parent
LOCAL = HARN / 'nightly-local'
GATE_VARS = ('HSGATE_SLOT_DIR', 'HSGATE_INTEL_SLOT_DIR', 'HSGATE_SLOT_TEST',
             'HSGATE_SLOT_NODESKTOP', 'HSGATE_IN_GATE')


def stamp(fmt='--iso-8601=seconds'):
    return subprocess.check_output(['date', fmt], text=True).strip()


def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.next')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def text(path):
    return Path(path).read_text(errors='replace') if Path(path).is_file() else ''


def env_clean(extra=None, *, game=False):
    env = os.environ.copy()
    for key in (*GATE_VARS, 'GH_TOKEN', 'GITHUB_TOKEN', 'OPENAI_API_KEY'):
        env.pop(key, None)
    if game:
        for key in list(env):
            if key.startswith(('HOS_', 'RX_', 'HOOKSHOT_', 'SDL_', 'VK_', 'MESA_', '__GL_')):
                del env[key]
    env.update(extra or {})
    return env


def gated(argv):
    return ['bash', '-c', 'unset ' + ' '.join(GATE_VARS) + '; exec "$@"', 'nightly', *map(str, argv)]


def capture(argv, **kwargs):
    p = subprocess.run(list(map(str, argv)), text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, **kwargs)
    if p.returncode:
        raise RuntimeError(f'{argv[0]} exited {p.returncode}: {p.stdout[-3000:]}')
    return p.stdout


def command(argv, log, cwd=None, env=None, guard=None):
    """One managed child group; optional watchdog spans queue waiting and CPU work too."""
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    monitor = None
    with Path(log).open('w') as f:
        p = subprocess.Popen(list(map(str, argv)), cwd=cwd, env=env or env_clean(),
                             stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        if guard:
            from monitor import Monitor
            root = Path(log).parent / (Path(log).stem + '-safety')
            monitor = Monitor(guard, guard['build_copy'], root, root)
            monitor.on_fault = lambda: stop_group(p)
            monitor.start()
        try:
            rc = p.wait()
        except BaseException:
            stop_group(p)
            raise
        finally:
            if monitor:
                monitor.finish()
                put(root / 'safety.json', {'faults': monitor.faults, 'kernel': monitor.kernel,
                                         'ignored_cores': monitor.ignored_cores})
        return rc if not monitor or not monitor.faults else 125


def slug(value):
    return re.sub(r'[^a-zA-Z0-9_.-]+', '-', str(value)).strip('.-')[:100]


def stop_group(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
