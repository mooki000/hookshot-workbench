"""Read-only run sampling and fail-closed machine safety checks."""
from pathlib import Path
import json
import os
import re
import subprocess
import threading
import time
from common import HARN, text, stamp
from metrics import WARNING, ACTIVITY, VRAM
from hs_names import GAME_EXECUTABLES, log_path


def processes():
    result = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            exe = os.readlink(proc / 'exe')
            cwd = os.readlink(proc / 'cwd')
            stat = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            status = dict(re.findall(r'^(\w+):\s*(\d+)', (proc / 'status').read_text(), re.M))
            result.append({'pid': int(proc.name), 'exe': exe, 'cwd': cwd, 'comm': (proc / 'comm').read_text().strip(),
                           'cpu_s': (int(stat[11]) + int(stat[12])) / os.sysconf('SC_CLK_TCK'),
                           'rss_mib': int(status.get('VmRSS', 0)) / 1024})
        except (OSError, ValueError, IndexError):
            continue
    return result


def player_games(config, records):
    root = Path(config['play_folder'])
    return [p for p in records if Path(p['exe']).name in GAME_EXECUTABLES and
            (Path(p['exe']).is_relative_to(root) or Path(p.get('cwd', '/')).is_relative_to(root))]


def wait_for_player_idle(config, log):
    """A continuous two-minute observation, reset whenever the protected game is seen."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    quiet_since = None
    pause = threading.Event()
    with log.open('a') as output:
        while True:
            now = time.monotonic()
            players = player_games(config, processes())
            if players:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = now
            output.write(json.dumps({'monotonic_s': now, 'player_pids': [p['pid'] for p in players],
                                     'quiet_for_s': now - quiet_since if quiet_since is not None else 0}) + '\n')
            output.flush()
            if quiet_since is not None and now - quiet_since >= 120:
                return
            pause.wait(1)


def resources(config, executable=None, *, executables=()):
    mem = {k: int(v) / 1048576 for k, v in re.findall(r'^(\w+):\s*(\d+)', text('/proc/meminfo'), re.M)}
    psi = re.search(r'^full avg10=([\d.]+)', text('/proc/pressure/memory'), re.M)
    procs = processes()
    games = [p for p in procs if Path(p['exe']).name in GAME_EXECUTABLES]
    owned = {Path(p) for p in executables}
    if executable is not None:
        owned.add(Path(executable))
    mine = [p for p in games if Path(p['exe']) in owned]
    compilers = [p for p in procs if re.match(r'(?:clang|cc1|g\+\+|gcc|ld\.lld|lld-link)', p['comm'])]
    reading = {'monotonic_s': time.monotonic(), 'load': list(os.getloadavg()),
               'available_gib': mem.get('MemAvailable'), 'swap_free_gib': mem.get('SwapFree'),
               'memory_psi_full': float(psi[1]) if psi else None,
               'game_pids': [p['pid'] for p in games], 'compiler_count': len(compilers),
               'other_games': len(games) - len(mine), 'own_pids': [p['pid'] for p in mine],
               'rss_mib': sum(p['rss_mib'] for p in mine) if mine else None,
               'cpu_s': sum(p['cpu_s'] for p in mine) if mine else None,
               'hyprland': sum(p['comm'] == 'Hyprland' for p in procs),
               'play_game': bool(player_games(config, games))}
    try:
        gpu = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.free,utilization.gpu,driver_version',
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
        if gpu.returncode == 0:
            vals = gpu.stdout.splitlines()[0].split(',')
            reading.update(gpu_total_mib=float(vals[0]), gpu_free_mib=float(vals[1]),
                           gpu_util_pct=float(vals[2]), gpu_driver=vals[3].strip())
        p = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,used_gpu_memory', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=5)
        own = [float(v) for pid, v in re.findall(r'^(\d+),\s*(\d+)\s*$', p.stdout, re.M)
               if int(pid) in reading['own_pids']]
        reading['gpu_process_mib'] = sum(own) if own else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        reading['gpu_process_mib'] = None
    return reading


def unsafe(sample, config):
    faults = []
    for key, minimum in (('available_gib', config['min_available_gib']), ('swap_free_gib', config['min_swap_free_gib'])):
        if sample.get(key) is None or sample[key] < minimum:
            faults.append(key + ' below safety limit or unreadable')
    if sample.get('memory_psi_full') is None or sample['memory_psi_full'] > config['max_memory_psi_full']:
        faults.append('memory pressure above safety limit or unreadable')
    if not sample.get('hyprland'):
        faults.append('compositor not found')
    if sample.get('play_game'):
        faults.append('user game running from protected play folder')
    if Path(config['gate_closed']).exists():
        faults.append('GPU guard closed the gate')
    return faults


def driver_faults(lines):
    errors = []
    allocation = 0
    for line in lines:
        if re.search(r'NVRM: Xid|kmemsysDoCacheOp|(?:i915|xe ).*(?:hang|reset|error)', line, re.I):
            errors.append(line)
        elif 'NVRM' in line:
            if 'memMap_IMPL: CPU mapping not supported for addressSpace: 0x1' in line:
                continue
            if re.search(r'NV_ERR_NO_MEMORY|_memdescAllocInternal.*mem_desc.c|rmStatus.*system_mem.c|pRmApi->Alloc.*nv_gpu_ops.c', line):
                allocation += 1
            else:
                errors.append(line)
    if allocation > 9:
        errors.append('more than three allocation-refusal events in one run')
    return errors


def watchdog_decision(message, cores, owned_executables, kernel):
    """Ignore only identified foreign cores; every resource/driver/unknown condition still stops."""
    lines = [line for line in message.splitlines() if line.startswith('WATCHDOG ')]
    if not lines:
        return {'faults': ['shared watchdog ended without a readable status line'], 'ignored': []}
    line = lines[-1]
    patterns = {
        'memory': r'mem available ([\d.]+) GB \(limit ([\d.]+)\)',
        'pressure': r'pressure full avg10 ([\d.]+) \(limit ([\d.]+)\)',
        'swap': r'swap free ([\d.]+) GB \(limit ([\d.]+)\)',
        'driver': r'driver fault lines (\d+)', 'desktop': r'Hyprland (\d+)',
        'cores': r'core dumps since [^,]+: (\d+)', 'gate': r'gate (open|closed)',
    }
    matched = {key: re.search(pattern, line) for key, pattern in patterns.items()}
    if any(value is None for value in matched.values()):
        return {'faults': ['shared watchdog status format is unknown: ' + line], 'ignored': []}
    faults = []
    for key in ('memory', 'swap'):
        value, limit = map(float, matched[key].groups())
        if value < limit:
            faults.append(f'shared watchdog {key}: {value} below {limit}')
    value, limit = map(float, matched['pressure'].groups())
    if value > limit:
        faults.append(f'shared watchdog pressure: {value} above {limit}')
    bad_driver = bool(int(matched['driver'][1]) or driver_faults(kernel))
    if bad_driver:
        faults.append('shared watchdog/monitor observed a driver fault')
    if not int(matched['desktop'][1]) or matched['gate'][1] != 'open':
        faults.append('shared watchdog saw a missing compositor or closed gate')
    count = int(matched['cores'][1])
    if count and (len(cores) < count or any(not isinstance(c.get('exe'), str) for c in cores)):
        faults.append('core executable metadata is incomplete; cannot exclude an owned crash')
    ignored = []
    for core in cores:
        executable = core.get('exe')
        if not isinstance(executable, str):
            continue
        if executable in owned_executables or bad_driver:
            faults.append(f'core dump: {executable} (pid {core.get("pid", "?")})'
                          + (' with a driver fault' if bad_driver else ' — owned cycle copy'))
        else:
            ignored.append(core)
    if not faults and not count:
        faults.append('shared watchdog exited without an explained stop condition')
    return {'faults': faults, 'ignored': ignored}


class Monitor:
    def __init__(self, config, copy, run, out):
        self.config, self.copy, self.run, self.out = config, Path(copy), Path(run), Path(out)
        self.stop = threading.Event()
        self.started = stamp()
        self.faults, self.samples, self.kernel, self.pids = [], [], [], set()
        # A build guard starts before binaries exist; enumerate names, never guess an identity.
        self.sample_executables = {str(self.copy / name) for name in GAME_EXECUTABLES}
        self.owned_executables = self.sample_executables | {
            str(Path(p) / name) for p in config.get('owned_copies', []) for name in GAME_EXECUTABLES}
        self.ignored_cores = []
        self.in_play = False
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.watchdog = None
        self.log_offset = 0

    def start(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.wlog = (self.out / 'watchdog.log').open('a')
        self.start_watchdog()
        self.thread.start()

    def start_watchdog(self):
        self.watchdog_started = stamp()
        self.watchdog = subprocess.Popen([HARN / 'hs-watchdog.sh', str(self.config['min_available_gib']),
                                         str(self.config['max_memory_psi_full']), str(self.config['min_swap_free_gib'])],
                                        stdout=self.wlog, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        start_new_session=True)

    def watchdog_ended(self):
        core_list = subprocess.run(['coredumpctl', 'list', '--since', self.watchdog_started,
                                    '--json=short', '--no-pager'], capture_output=True, text=True, timeout=20)
        if core_list.returncode not in (0, 1):
            self.faults.append('cannot read executable paths of watchdog core events')
            return
        try:
            cores = json.loads(core_list.stdout) if core_list.stdout.strip() else []
            if not isinstance(cores, list):
                raise ValueError('core metadata is not a list')
        except ValueError as error:
            self.faults.append('unreadable core metadata: ' + str(error))
            return
        decision = watchdog_decision(text(self.out / 'watchdog.log'), cores, self.owned_executables, self.kernel)
        self.faults.extend(decision['faults'])
        if decision['ignored']:
            observation = {'observed': stamp(), 'cores': decision['ignored'], 'reason': 'foreign core events; they do not by themselves stop this cycle'}
            self.ignored_cores.extend(decision['ignored'])
            with (self.out / 'ignored-cores.jsonl').open('a') as f:
                f.write(json.dumps(observation) + '\n')
        if not self.faults and not self.stop.is_set():
            self.start_watchdog()

    def work(self):
        try:
            while not self.stop.is_set():
                sample = resources(self.config, executables=self.sample_executables)
                self.samples.append(sample)
                self.pids.update(sample['own_pids'])
                with (self.out / 'samples.jsonl').open('a') as f:
                    f.write(json.dumps(sample) + '\n')
                self.faults.extend(unsafe(sample, self.config))
                journal = subprocess.run(['journalctl', '-k', '--since', self.started, '--no-pager', '-o', 'short-iso'],
                                         text=True, capture_output=True, timeout=10)
                if journal.returncode:
                    self.faults.append('kernel journal unreadable')
                self.kernel = [line for line in journal.stdout.splitlines() if re.search(r'NVRM|kmemsysDoCacheOp|i915|xe ', line)]
                (self.out / 'kernel.txt').write_text('\n'.join(self.kernel) + '\n')
                self.faults.extend(driver_faults(self.kernel))
                if self.watchdog.poll() is not None:
                    self.watchdog_ended()
                logfile = log_path(self.run)
                if logfile is not None:
                    with logfile.open(errors='replace') as f:
                        f.seek(self.log_offset)
                        fresh = f.read()
                        self.log_offset = f.tell()
                    with (self.out / 'log-events.jsonl').open('a') as f:
                        for line in fresh.splitlines():
                            if WARNING.search(line) or ACTIVITY.search(line) or VRAM.search(line):
                                f.write(json.dumps({'monotonic_s': time.monotonic(), 'in_play': self.in_play, 'line': line}) + '\n')
                if self.faults:
                    self.stop.set()
                    self.on_fault()
                    return
                self.stop.wait(2)
        except Exception as error:
            self.faults.append('safety monitor failed: ' + str(error))
            self.stop.set()
            self.on_fault()

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=20)
        if self.watchdog and self.watchdog.poll() is None:
            from common import stop_group
            stop_group(self.watchdog)
        self.wlog.close()
        try:
            journal = subprocess.run(['journalctl', '-k', '--since', self.started, '--until', stamp(),
                                      '--no-pager', '-o', 'short-iso'], text=True, capture_output=True, timeout=10)
            if journal.returncode:
                self.faults.append('final kernel journal unreadable')
            self.kernel = [line for line in journal.stdout.splitlines() if re.search(r'NVRM|kmemsysDoCacheOp|i915|xe ', line)]
            (self.out / 'kernel.txt').write_text('\n'.join(self.kernel) + '\n')
            self.faults.extend(driver_faults(self.kernel))
        except (OSError, subprocess.TimeoutExpired) as error:
            self.faults.append('final kernel journal failed: ' + str(error))

    def disturbance(self):
        reasons = ['ordinary queue; no authorized exclusive timing hold']
        if any(s['compiler_count'] for s in self.samples):
            reasons.append('compilers running')
        if any(s['other_games'] for s in self.samples):
            reasons.append('other game running')
        if any(s['load'][0] > self.config['quiet_load_max'] for s in self.samples):
            reasons.append('load above configured quiet limit')
        return reasons
