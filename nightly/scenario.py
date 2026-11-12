"""Run a private JSON scenario through hs-run and the selected build's control client.

Optional `perf: true` collects perf.csv as sampled context, never a decision metric.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import time
import threading
from common import HARN, capture, env_clean, gated, put, slug, stamp, stop_group, text
from monitor import Monitor, resources, unsafe
from metrics import collect
from hs_names import checkout_names, copy_names, log_path


class SafetyStop(RuntimeError):
    pass


def client_module(config):
    root = Path(config['worktree'])
    path = root / checkout_names(root).control_client
    spec = importlib.util.spec_from_file_location('nightly_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Run:
    def __init__(self, config, copy, spec, out, tag, held=False):
        self.config, self.copy, self.spec = config, Path(copy), spec
        self.out, self.tag = Path(out), tag
        self.held = held
        self.shutdown_lock = threading.Lock()
        self.run = self.copy / 'runs' / tag
        self.meta = {'id': spec['id'], 'status': 'running', 'started': stamp(), 'timings': {},
                     'checks': [], 'settings': spec.get('settings', {}), 'save': spec.get('save'),
                     'intel': bool(spec.get('intel'))}
        self.monitor = Monitor(config, copy, self.run, self.out)
        self.bridge, self.proc = None, None
        self.start = None
        self.ctl = client_module(config)

    def check(self):
        if self.monitor.faults:
            raise SafetyStop('; '.join(dict.fromkeys(self.monitor.faults)))
        if self.proc.poll() is not None:
            raise RuntimeError('game wrapper ended before scenario completed')
        if self.start is not None and time.monotonic() - self.start >= self.spec['seconds'] - 15:
            raise RuntimeError('scenario reached its bounded deadline')

    def request(self, cmd, **kwargs):
        self.check()
        started = time.monotonic()
        reply = self.bridge.request(cmd, timeout=kwargs.pop('timeout', 20), **kwargs)
        with (self.out / 'control.jsonl').open('a') as f:
            f.write(json.dumps({'monotonic_s': started, 'request': {'cmd': cmd, **kwargs}, 'reply': reply}) + '\n')
        if not reply.get('ok'):
            raise RuntimeError(f'{cmd}: {reply.get("error")}: {reply.get("message", "")}')
        return reply

    def delay(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            self.monitor.stop.wait(min(.25, max(0, end - time.monotonic())))

    def stop_for_safety(self):
        player_only = self.monitor.faults and all(
            reason == 'user game running from protected play folder' for reason in self.monitor.faults)
        if not player_only:
            stop_group(self.proc)
            return
        # A separate connection avoids racing the main driver's pending request.
        with self.shutdown_lock:
            if self.proc.poll() is not None:
                return
            try:
                bridge = self.ctl.Bridge(str(self.control_socket), timeout=1)
                try:
                    bridge.request('quit', timeout=2)
                finally:
                    bridge.close()
                self.proc.wait(timeout=5)
            except Exception:
                stop_group(self.proc)

    def shot(self, name):
        reply = self.request('shot', path=str(self.out / 'shots' / (slug(name) + '.png')))
        self.meta.setdefault('shot_order', []).append({'name': slug(name), 'present': reply.get('present'),
                                                     'monotonic_s': time.monotonic()})
        return reply

    def boot(self):
        """Never equate a high-draw title movie with a loaded save: require the game's map."""
        deadline = time.monotonic() + self.spec.get('boot_timeout', 180)
        title = None
        pressed = 0.0
        while time.monotonic() < deadline:
            s = self.request('status')
            elapsed = time.monotonic() - self.start
            if s.get('state') == 'menu' and title is None:
                title = elapsed
                self.meta['timings']['start_to_title_s'] = elapsed
                self.shot('title')
                if self.spec.get('boot') == 'title':
                    return
            game = s.get('game') or {}
            if s.get('map') and s.get('state') == 'play' and not game.get('loading'):
                if self.spec.get('expected_map') and s['map'] != self.spec['expected_map']:
                    raise RuntimeError(f'wrong loaded map: {s["map"]!r}')
                if not game.get('event') or self.spec.get('allow_event', False):
                    self.meta['timings']['start_to_play_s'] = elapsed
                    if title is not None:
                        self.meta['timings']['title_to_play_s'] = elapsed - title
                    self.meta['loaded_map'] = s['map']
                    self.meta['play_monotonic_ns'] = time.monotonic_ns()
                    self.meta['checks'].append('loaded expected map; play state; loading=false')
                    self.monitor.in_play = True
                    self.shot('loaded')
                    return
            if self.spec.get('boot') != 'title' and s.get('frame', 0) > 0 and time.monotonic() - pressed >= 1.5:
                self.request('press', buttons='A', frames=8, exclusive=True)
                pressed = time.monotonic()
            self.delay(.25)
        raise RuntimeError('boot did not reach required title/play state before deadline')

    def steps(self):
        for index, step in enumerate(self.spec.get('steps', [])):
            op = step['op']
            if op == 'wait':
                self.delay(step['seconds'])
            elif op == 'shot':
                self.shot(step['name'])
            elif op == 'burst':
                names = []
                for frame in range(step.get('count', 3)):
                    name = slug(step['name']) + '-' + str(frame)
                    self.shot(name)
                    names.append(name)
                self.meta.setdefault('shot_bursts', []).append(names)
            elif op == 'assert_log':
                import re
                matches = re.findall(step['pattern'], text(log_path(self.run)))
                if len(matches) < step.get('minimum', 1):
                    raise RuntimeError('required log evidence not found: ' + step['pattern'])
                self.meta['checks'].append('log evidence: ' + step['pattern'] + ' x' + str(len(matches)))
            elif op == 'request':
                reply = self.request(**step['request'])
                for key, expected in step.get('expect', {}).items():
                    value = reply
                    for part in key.split('.'):
                        value = value[part]
                    if value != expected:
                        raise RuntimeError(f'{key}: expected {expected!r}, saw {value!r}')
                if step.get('shot'):
                    self.shot(step['shot'])
            elif op == 'assert_play':
                s = self.request('status')
                if s.get('state') != 'play' or not s.get('map') or (s.get('game') or {}).get('loading'):
                    raise RuntimeError('play assertion failed: ' + json.dumps(s))
                self.meta['checks'].append('step ' + str(index) + ': in play')
            elif op == 'press_until':
                end = time.monotonic() + step['seconds']
                while time.monotonic() < end:
                    self.request('press', buttons=step.get('buttons', 'A'), frames=8, exclusive=True)
                    self.delay(step.get('every', 1.5))
            elif op == 'teleport':
                before = time.monotonic()
                target = self.request('teleport', to=step['to'])
                if target.get('accepted') is False:
                    raise RuntimeError('teleport not accepted by the game')
                self.request('wait', event='map', map=target['map'], timeout=step.get('timeout', 45))
                self.request('wait', event='in_play', timeout=45)
                self.meta['timings']['area_change_' + str(index) + '_s'] = time.monotonic() - before
                self.shot(step.get('name', 'area-' + str(index)))
            elif op == 'options':
                original = self.request('option_get')
                put(self.out / 'options.json', original)
                for name, values in step['values'].items():
                    old = self.request('option_get', name=name)['option']['value']
                    try:
                        for value in values:
                            reply = self.request('option_set', name=name, value=value)
                            if reply['option']['value'] != value:
                                raise RuntimeError('option value was clamped or unreachable')
                            self.delay(1)
                            self.shot(name + '-' + str(value))
                    finally:
                        self.request('option_set', name=name, value=old)
            else:
                raise ValueError('unknown scenario operation: ' + op)

    def execute(self):
        if not self.held and not self.config.get('gpu_scenarios_enabled', False):
            raise RuntimeError('nightly GPU scenarios are disabled; only game-free work is authorized')
        copy_names(self.copy)
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / 'shots').mkdir(exist_ok=True)
        put(self.out / 'scenario.json', self.spec)
        self.meta['scenario_sha256'] = hashlib.sha256(json.dumps(self.spec, sort_keys=True).encode()).hexdigest()
        seconds = self.spec['seconds']
        if not 20 <= seconds <= 450:
            raise ValueError('this runner supports 20–450 game seconds, reserving termination time within eight minutes')
        if self.spec.get('window'):
            if not self.config.get('allow_window') or not (HARN / 'window-ok').exists() or seconds > 120:
                raise ValueError('window scenario not currently permitted')
        before = resources(self.config)
        if unsafe(before, self.config):
            raise SafetyStop('; '.join(unsafe(before, self.config)))
        put(self.out / 'machine-before.json', before)
        user = self.out / 'input-user'
        if self.spec.get('save'):
            capture([HARN / 'hs-save.sh', self.spec['save'], user])
        else:
            user.mkdir()
        # A short path, private 0700, under the harness: Unix socket paths have a hard limit.
        socket_dir = HARN / 'nightly-local' / 'sockets' / slug(self.tag)
        socket_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        os.chmod(socket_dir, 0o700)
        sock = socket_dir / 'ctl'
        self.control_socket = sock
        if len(str(sock).encode()) >= 108:
            raise ValueError('control socket path too long')
        argv = [HARN / 'hs-run.sh', self.copy, self.tag, str(seconds), '--user', user, '--keep-user']
        if self.spec.get('intel'):
            argv += ['--intel', '--mem-max', '28G']
        if self.spec.get('window'):
            argv += ['--window']
        if self.spec.get('whole'):
            argv += ['--whole']
        if self.spec.get('perf'):
            argv += ['--perf']
        argv += ['HOS_CONTROL=' + str(sock), 'HOS_INPUT_EXCLUSIVE=1', 'RX_VK_SHOT_EVERY=600']
        argv += [str(k) + '=' + str(v) for k, v in self.spec.get('settings', {}).items()]
        launch, launch_env = gated(argv), env_clean()
        if self.held:
            if not os.environ.get('HSGATE_IN_GATE'):
                raise RuntimeError('held runner requires the shared hunt gate')
            if self.spec.get('intel') or self.spec.get('window') or self.spec.get('whole'):
                raise RuntimeError('held hunt adapter currently supports default headless GPU only')
            self.run.mkdir(parents=True, exist_ok=False)
            capture(['cp', '-a', '--reflink=always', user, self.run / 'user'])
            fields = {'copy.txt': str(self.copy), 'seconds.txt': str(seconds), 'input.txt': '',
                      'memmax': '', 'env-file.txt': '# empty: scenario environment only'}
            for name, value in fields.items():
                (self.run / name).write_text(value + '\n')
            extras = ['HOS_CONTROL=' + str(sock), 'HOS_INPUT_EXCLUSIVE=1', 'RX_VK_SHOT_EVERY=600']
            extras += [str(k) + '=' + str(v) for k, v in self.spec.get('settings', {}).items()]
            (self.run / 'extra-env.txt').write_text('\n'.join(extras) + '\n')
            if self.spec.get('perf'):
                (self.run / 'perf').touch()  # --inner selects the run folder/fields after extra environment
            launch = [str(HARN / 'hs-run.sh'), '--inner', str(self.run)]
            launch_env = os.environ.copy()
            for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'OPENAI_API_KEY'):
                launch_env.pop(key, None)
        put(self.out / 'command.json', list(map(str, launch)))
        out = (self.out / 'wrapper.log').open('w')
        self.proc = subprocess.Popen(launch, env=launch_env, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        self.monitor.on_fault = self.stop_for_safety
        self.monitor.start()
        try:
            # Queue waiting does not consume the scenario's game-time deadline.
            while log_path(self.run) is None:
                self.check()
                self.monitor.stop.wait(.25)
            self.start = time.monotonic()
            while self.bridge is None:
                self.check()
                if sock.exists():
                    try:
                        self.bridge = self.ctl.Bridge(str(sock), timeout=2)
                    except self.ctl.Failed:
                        pass
                if self.bridge is None:
                    self.delay(.25)
            self.boot()
            self.steps()
            self.shot('final')
            self.meta['checks'].append('all required steps completed')
            self.request('quit')
            self.proc.wait(timeout=30)
            self.meta['status'] = 'passed'
        except Exception as error:
            self.meta['status'] = 'safety-stop' if isinstance(error, SafetyStop) or self.monitor.faults else 'failed'
            self.meta['error'] = str(error)
            if self.bridge and not self.monitor.faults:
                try:
                    self.bridge.request('quit', timeout=3)
                    self.proc.wait(timeout=30)
                except Exception:
                    stop_group(self.proc)
            else:
                self.stop_for_safety()
        finally:
            if self.bridge:
                self.bridge.close()
            self.monitor.finish()
            out.close()
            # Socket data are not saves; the game's own clean quit normally removed ctl.
            shutil.rmtree(socket_dir)
        self.meta['ended'] = stamp()
        self.meta['elapsed_game_s'] = time.monotonic() - self.start if self.start else None
        self.meta['disturbance'] = self.monitor.disturbance()
        self.meta['disturbed'] = bool(self.meta['disturbance'])
        self.meta['safety_faults'] = list(dict.fromkeys(self.monitor.faults))
        self.meta['kernel_driver_lines'] = len(self.monitor.kernel)
        self.meta['game_pids'] = sorted(self.monitor.pids)
        after = resources(self.config)
        put(self.out / 'machine-after.json', after)
        self.monitor.faults.extend(unsafe(after, self.config))
        self.meta['safety_faults'] = list(dict.fromkeys(self.monitor.faults))
        if self.meta['safety_faults']:
            self.meta['status'] = 'safety-stop'
            self.meta['cut_short'] = True
        if self.held:
            summary = (f"hs-run --inner {self.tag} exit={text(self.run / 'exit.txt').strip()} "
                       f"wall={text(self.run / 'wall.txt').strip()}s "
                       f"load {text(self.run / 'load-before.txt').strip()} -> {text(self.run / 'load-after.txt').strip()}")
            (self.run / 'summary.txt').write_text(summary + '\n')
        result = {**self.meta, **collect(self.run, self.out, self.meta), 'run_dir': str(self.run)}
        if result['fatal_lines'] or result['exit'] not in (0, 124):
            result['status'] = 'failed' if result['status'] != 'safety-stop' else 'safety-stop'
        if result['status'] == 'failed' and any('NV_ERR_NO_MEMORY' in s for s in self.monitor.kernel):
            result['status'] = 'safety-stop'
            result['safety_faults'].append('allocation refusal and failed run')
        if result['exit'] not in (0, 124, None) or result['fatal_lines']:
            for pid in self.monitor.pids:
                p = subprocess.run(['coredumpctl', 'info', str(pid), '--since', self.monitor.started, '--no-pager'],
                                   capture_output=True, text=True, timeout=30)
                (self.out / f'core-{pid}.txt').write_text(p.stdout + p.stderr)
                if p.returncode == 0 and shutil.which('gdb'):
                    p = subprocess.run(['coredumpctl', 'debug', str(pid), '--debugger=gdb',
                                        '--debugger-arguments=-batch -ex "set debuginfod enabled off" -ex "thread apply all bt"'],
                                       capture_output=True, text=True, timeout=90, env=env_clean({'DEBUGINFOD_URLS': ''}))
                    (self.out / f'backtrace-{pid}.txt').write_text(p.stdout + p.stderr)
        if (self.run / 'perf.csv').is_file():
            shutil.copy2(self.run / 'perf.csv', self.out / 'perf.csv')
        put(self.out / 'result.json', result)
        return result


def run(config, copy, spec, out, tag, held=False):
    return Run(config, copy, spec, out, tag, held=held).execute()
