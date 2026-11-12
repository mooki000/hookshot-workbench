#!/usr/bin/env python3
"""hsap: the pilot's game side for Hookshot. Runs one game through hs-run.sh (GPU gate, headless) and plays steps on it:
inputs and Link's state through HOS_CONTROL (protocol 1, including the oot subscription). Only debug commands such as
!cheat 996 and !shots still use HOS_INPUT_LIVE. Steps are appended to <live>/steps.txt; events go to <live>/out.jsonl.
Received snapshots are normalized for the local pilot and journalled in <live>/state.jsonl, not read from that file.

    hsap.py <copy> <tag> <wall seconds> --live <dir> [--save <Hookshot user folder>] [--boot newgame|continue|none]
            [--shots <presents>] [--analog]

Events written: gameplay (the first state with Link in control), step (in = the step's line in steps.txt, src, result,
secs, moved, detail), state (why wait after each step, why report every `report` seconds), bad (a line not understood),
note, done.
Steps (one a line; # starts a note):
  watch <flag> ...            flags to subscribe to (read before the game starts); later lines are kept too
  walk|run <x> <z> [radius] [timeout]   to that ground point, steered each sample; reached / stuck / timeout /
                              held (an event holds Link 0.6 s); --analog / HSAP_ANALOG=1: exact stick angles
  press <buttons> [ms]        one timed press (A, ZL+R, LUP ...); done once the bridge says it ended
  stick <dir> <ms>            hold the left stick (up, down, left, right, up-left ...) relative to the camera; done
  push <lx> <ly> <ms>         the left stick at those values (-32767..32767, y up); done
  raw <presses> <s>           a socket sequence in hos units (e.g. 0:LUP/1200,0.6:X/100), then wait s seconds; done
  until flag <name><op><value> [timeout <s>] [press <button> every <s>]   met / timeout
  until area <name> [timeout <s>] [press ...]; until prompt=<text> ...; until control [timeout <s>]
  wait <s>                    done
  save [timeout <s>]          the game's own autosave (!cheat 996): done once progress.sav is written, else timeout
  report <s>                  a state line every s seconds (0: off)
  shot                        a picture of the next present (!shots), done
  end                         stop the game, write done
Game data never leaves this machine; everything goes under the run's folders.
"""
import argparse
import errno
import json
import math
import os
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root if (_root / 'hs_names.py').is_file() else _root / 'harness'))
from hs_names import GAME_EXECUTABLES, log_path

HS_RUN = '/home/mooki/Games/.hookshot-harness/hs-run.sh'
SLOT_VARS = ('HSGATE_SLOT_DIR', 'HSGATE_INTEL_SLOT_DIR', 'HSGATE_SLOT_TEST', 'HSGATE_SLOT_NODESKTOP', 'HSGATE_IN_GATE')
PROMPT_HASH = '0x9824206c'
# The left stick's eight directions: (angle from "away from the camera", positive toward the right, degrees) -> presses
DIRS = [(0, 'LUP'), (45, 'LUP+LRIGHT'), (90, 'LRIGHT'), (135, 'LDOWN+LRIGHT'), (180, 'LDOWN'), (-135, 'LDOWN+LLEFT'),
        (-90, 'LLEFT'), (-45, 'LUP+LLEFT')]
NAMED_DIRS = {'up': 'LUP', 'down': 'LDOWN', 'left': 'LLEFT', 'right': 'LRIGHT', 'up-left': 'LUP+LLEFT',
              'up-right': 'LUP+LRIGHT', 'down-left': 'LDOWN+LLEFT', 'down-right': 'LDOWN+LRIGHT'}


def wrap(d):
    return (d + 180.0) % 360.0 - 180.0


def yaw_of(dx, dz):
    return math.degrees(math.atan2(dx, dz))


def valid_flag(name):
    return bool(re.fullmatch(r'[A-Za-z0-9_.]{1,47}', name) and
                (not name.startswith('0x') or re.fullmatch(r'0x[0-9a-fA-F]{8}', name)))


class ControlError(RuntimeError):
    pass


class Tail:
    """New whole lines of a growing file"""
    def __init__(self, path):
        self.path, self.off, self.buf = Path(path), 0, b''

    def lines(self):
        try:
            with open(self.path, 'rb') as f:
                f.seek(self.off)
                data = f.read()
        except OSError:
            return []
        self.off += len(data)
        self.buf += data
        *whole, self.buf = self.buf.split(b'\n')
        return [w.decode('utf-8', 'replace') for w in whole]


class Game:
    """One hs-run.sh run: a nonblocking control socket, debug-only live input, a state journal and the log."""
    def __init__(self, copy, tag, wall, run_dir, save=None, watch=(), shots=0, extra=()):
        watch = list(dict.fromkeys(watch))
        if len(watch) > 64 or any(not valid_flag(name) for name in watch):
            raise ValueError('watch: at most 64 flag names (1..47 characters), or 0x followed by eight hex digits')
        self.copy, self.tag, self.dir = Path(copy), tag, Path(run_dir).resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.live_in = self.dir / 'live.txt'
        self.state_path = self.dir / 'state.jsonl'
        self.live_in.write_text('')
        self.state_out = open(self.state_path, 'w')
        # A private, short path: a pilot's nested live directory can exceed AF_UNIX's path limit.
        self.control_dir = tempfile.TemporaryDirectory(prefix='hsap-control-')
        self.control_path = Path(self.control_dir.name) / 'control.sock'
        self.sock, self.connecting = None, False
        self.next_connect = 0.0
        self.tx, self.rx = bytearray(), bytearray()
        self.next_id, self.pending = 0, {}
        self.sub = self.request('subscribe', events=['oot'], every=6, flags=watch, changes=True)
        self.request('status')
        self.R = self.copy / 'runs' / tag
        env = {k: v for k, v in os.environ.items() if k not in SLOT_VARS and k != 'OPENAI_API_KEY'}
        cmd = [HS_RUN, str(self.copy), tag, str(int(wall))]
        if save:
            cmd += ['--user', str(save)]
        cmd += [f'HOS_CONTROL={self.control_path}', f'HOS_INPUT_LIVE={self.live_in}',
                f'RX_VK_SHOT_EVERY={shots or 600}']
        cmd += list(extra)
        self.cmd = cmd
        with open(self.dir / 'hs-run.out', 'w') as out:
            self.runner = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        self.log_tail = None
        self.st, self.n_states = None, 0
        self.log_lines = []
        self.changes = {}        # hash -> last value, from the samples' "changed"
        self.presents = 0

    def alive(self):
        return self.runner.poll() is None

    def request(self, cmd, **args):
        """Queue a request without waiting for its reply; poll matches all outstanding ids."""
        self.next_id += 1
        rid = self.next_id
        self.pending[rid] = cmd
        self.tx.extend((json.dumps({'id': rid, 'cmd': cmd, **args}, separators=(',', ':')) + '\n').encode())
        self._flush()
        return rid

    def _connect(self):
        if self.sock is None:
            if time.monotonic() < self.next_connect:
                return False
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.setblocking(False)
            error = sock.connect_ex(str(self.control_path))
            if error in (errno.ENOENT, errno.ECONNREFUSED, errno.EAGAIN):
                sock.close()
                self.next_connect = time.monotonic() + 0.25   # the game may still be in the GPU queue
                return False
            if error not in (0, errno.EINPROGRESS, errno.EALREADY):
                sock.close()
                raise ControlError(f'control connect: {os.strerror(error)}')
            self.sock, self.connecting = sock, error != 0
        if self.connecting:
            _, ready, failed = select.select([], [self.sock], [self.sock], 0)
            if not ready and not failed:
                return False
            error = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if error:
                raise ControlError(f'control connect: {os.strerror(error)}')
            self.connecting = False
        return True

    def _flush(self):
        if self.sock is None or self.connecting:
            return
        while self.tx:
            try:
                n = self.sock.send(self.tx)
            except BlockingIOError:
                return
            except InterruptedError:
                continue
            except OSError as e:
                raise ControlError(f'control send: {e}') from e
            if n == 0:
                raise ControlError('control connection closed while sending')
            del self.tx[:n]

    def _messages(self):
        if not self._connect():
            return []
        self._flush()
        messages = []
        # Bound each pass even if the server keeps writing: receiving never monopolizes the steering loop.
        for _ in range(16):
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                break
            except InterruptedError:
                continue
            except OSError as e:
                raise ControlError(f'control receive: {e}') from e
            if not data:
                raise ControlError('control connection closed')
            self.rx.extend(data)
            while b'\n' in self.rx:
                line, _, rest = self.rx.partition(b'\n')
                self.rx = rest
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError) as e:
                    raise ControlError('control sent invalid JSON') from e
                if not isinstance(message, dict):
                    raise ControlError('control sent a non-object reply')
                messages.append(message)
            if len(self.rx) > 65536:
                raise ControlError('control sent an oversized partial reply')
        return messages

    @staticmethod
    def frames(ms):
        if ms <= 0:
            raise BadStep('input hold must be positive')
        return max(1, math.floor(ms * 60 / 1000 + 0.5))

    def press(self, buttons, ms=200):
        return self.request('press', buttons=buttons, frames=self.frames(ms), exclusive=True, wait=True)

    def stick(self, lx, ly, ms):
        lx, ly = max(-32767, min(32767, lx)), max(-32767, min(32767, ly))
        return self.request('stick', stick='L', x=lx / 32767, y=ly / 32767,
                            frames=self.frames(ms), exclusive=True, wait=True)

    def send(self, line):
        if line.startswith('!'):
            with open(self.live_in, 'a') as f:
                f.write(line + '\n')
            return None
        return self.request('sequence', script=line, units='hos', exclusive=True, wait=True)

    def poll(self):
        """Drain socket events and replies without waiting; return only fresh Link samples, not reply presents."""
        new = []
        for message in self._messages():
            is_sample = message.get('event') == 'oot' and 'ok' not in message and message.get('sub') == self.sub
            if 'ok' in message:
                rid = message.get('id')
                cmd = self.pending.pop(rid, None)
                if cmd is None:
                    continue
                if not message['ok']:
                    error, detail = message.get('error'), str(message.get('message', ''))
                    if cmd == 'subscribe' and (error in ('unknown_cmd', 'unavailable') or
                            (error == 'bad_request' and ('event' in detail.lower() or 'oot' in detail.lower()))):
                        raise ControlError("this build has no oot subscription: use hsap_file.py, or a build with "
                                           "Hookshot #2292's control-bridge commits")
                    raise ControlError(f'control {cmd} #{rid}: {error}: {detail}')
                if cmd != 'status':
                    continue
                if message.get('protocol') != 1:
                    raise ControlError(f'control protocol {message.get("protocol")}, expected 1')
            elif not is_sample:
                continue
            self.presents = max(self.presents, message.get('frame', 0))
            game = message.get('game')
            if game is None:
                continue
            s = dict(game)
            # Both status and event envelopes date delivery; game.frame dates the retained/sample snapshot.
            if not isinstance(s.get('frame'), int):
                raise ControlError('control game snapshot has no sample frame (an oot subscription build is required)')
            s['child'] = {'child': 1, 'adult': 0}.get(s.pop('age', None))
            for field in ('event', 'loading'):
                s[field] = None if s.get(field) is None else int(s[field])
            if self.st is not None and s['frame'] < self.st['frame']:
                continue
            self.changes.update(s.get('changed') or {})
            if self.st is not None and s['frame'] == self.st['frame']:
                self.st.update(s)   # a same-frame oot event can add flags missing from status.game
                continue
            self.st = s
            self.n_states += 1
            new.append(s)
            self.state_out.write(json.dumps(s) + '\n')
        if new:
            self.state_out.flush()
        if self.log_tail is None and (log := log_path(self.R)) is not None:
            self.log_tail = Tail(log)
        if self.log_tail:
            for line in self.log_tail.lines():
                self.log_lines.append(line)
                m = re.search(r'vk: (\d+) presents, ', line)
                if m:
                    self.presents = max(self.presents, int(m.group(1)))
        return new

    def game_pids(self):
        """The game process of this run (its --user is the run's user folder)."""
        want = str(self.R / 'user')
        out = []
        for p in os.listdir('/proc'):
            if not p.isdigit():
                continue
            try:
                cmd = (Path('/proc') / p / 'cmdline').read_bytes().split(b'\0')
            except OSError:
                continue
            if cmd and os.fsdecode(cmd[0]).rsplit('/', 1)[-1] in GAME_EXECUTABLES and want.encode() in cmd:
                out.append(int(p))
        return out

    def stop(self, timeout=60):
        # Waited socket inputs are released by disconnect, even when several steering replies are outstanding.
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        self.state_out.close()
        for pid in self.game_pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            self.runner.wait(timeout)
        except subprocess.TimeoutExpired:
            os.killpg(self.runner.pid, signal.SIGTERM)
            self.runner.wait()
        self.control_dir.cleanup()

    def saves(self):
        """{progress.sav path: mtime} in the run's user folder"""
        out = {}
        for p in (self.R / 'user' / 'save').glob('*/slot_*/progress.sav'):
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                pass
        return out


class Executor:
    def __init__(self, game, live_dir, boot):
        self.g, self.live = game, Path(live_dir)
        self.steps_tail = Tail(self.live / 'steps.txt')
        self.out = open(self.live / 'out.jsonl', 'a')
        self.n_line, self.queue = 0, []
        self.report, self.report_next = 0.0, 0.0
        self.t0 = None
        self.last = {}
        self.boot = boot
        self.watch = []
        self.flag_values = {}
        self.sign = 1.0          # +1: the stick's right is the camera's yaw - 90 (a right-handed world, y up)
        self.mirror, self.checked = 0, 0.0
        self.ended = False
        # Exact stick angles use the bridge's analog axes; no input-script-analog build is needed.
        self.analog = os.environ.get('HSAP_ANALOG') == '1'

    def emit(self, **e):
        self.out.write(json.dumps(e) + '\n')
        self.out.flush()

    def t(self):
        st = self.g.st or {}
        return round((st.get('t') or 0) - (self.t0 or 0), 2)

    def state_event(self, why):
        st = self.g.st or {}
        music = st.get('music') or ''
        flags = dict(st.get('flags') or {})
        e = {'ev': 'state', 'why': why, 'n': self.g.n_states, 't': self.t(), 'frame': st.get('frame'),
             'pos': st.get('pos'), 'facing': st.get('facing'), 'camera': st.get('camera'), 'area': st.get('map'),
             'prompt': st.get('prompt'), 'music': st.get('music'), 'scene': music.startswith('Dm_'),
             'event': st.get('event'), 'loading': st.get('loading'), 'child': st.get('child'), 'life': st.get('life'),
             'max_life': st.get('max_life'), 'rupees': st.get('rupees'), 'last': self.last, 'flags': flags,
             'changed': sorted(self.g.changes)}
        self.g.changes = {}
        self.emit(**e)

    def read_steps(self):
        for line in self.steps_tail.lines():
            self.n_line += 1
            s = line.split('#', 1)[0].strip()
            if s:
                self.queue.append((self.n_line, s))

    def tick(self, seconds=0.05):
        time.sleep(seconds)
        new = self.g.poll()
        if new and self.report and self.t() >= self.report_next:
            self.state_event('report')
            self.report_next = self.t() + self.report
        return new

    # ---- boot: to the first state with Link in control
    def to_control(self, limit=900):
        """limit counts from the game's start (its log appears), not from ours: the GPU gate's queue may hold the run for
        most of an hour first (newgame-1, 2026-11-10: hsap's limit ran out in the queue and the game never started)"""
        end = None
        calm, next_a, title_at = 0, 0.0, None
        while (end is None or time.time() < end) and self.g.alive():
            new = self.tick(0.1)
            if end is None:
                if self.g.log_tail is None:
                    continue
                end = time.time() + limit
                self.emit(ev='note', text='the game started (its log appeared)', wall=round(time.time(), 1))
            if title_at is None and any('a frame:' in l and re.search(r'a frame: (\d+) draws', l) and
                                        int(re.search(r'a frame: (\d+) draws', l).group(1)) >= 15 for l in self.g.log_lines[-20:]):
                title_at = time.time()
                self.emit(ev='note', text='title (15 draws a frame)', wall=round(time.time(), 1))
            for s in new:
                calm = calm + 1 if s.get('map') and s.get('event') == 0 and s.get('loading') == 0 else 0
            if calm >= 30:   # three seconds at the subscription's six-present cadence
                return True
            if self.boot != 'none' and title_at and time.time() >= next_a:
                self.g.press('A', 100)
                next_a = time.time() + (0.75 if self.boot == 'newgame' else 2.0)
        return False

    # ---- steps
    def run(self):
        if not self.to_control():
            self.emit(ev='note', text='no control: ' + ('the game ended' if not self.g.alive() else 'boot timeout'))
            return
        self.t0 = (self.g.st or {}).get('t') or 0
        self.emit(ev='gameplay', t=0.0, frame=(self.g.st or {}).get('frame'), area=(self.g.st or {}).get('map'))
        self.state_event('wait')
        while self.g.alive() and not self.ended:
            self.read_steps()
            if not self.queue:
                self.tick(0.05)
                continue
            n, src = self.queue.pop(0)
            t_start, p_start = time.time(), (self.g.st or {}).get('pos')
            try:
                result, detail = self.step(src)
            except BadStep as e:
                self.emit(ev='bad', **{'in': n}, src=src, why=str(e))
                continue
            p_end = (self.g.st or {}).get('pos')
            moved = round(math.dist(p_start[::2], p_end[::2]), 2) if p_start and p_end else 0.0
            secs = round(time.time() - t_start, 2)
            self.last = {'src': src, 'result': result, 'secs': secs, 'moved': moved, 'detail': detail}
            self.emit(ev='step', **{'in': n}, src=src, result=result, secs=secs, moved=moved, detail=detail,
                      pos=p_end, t=self.t())
            self.read_steps()
            if not self.queue:
                self.state_event('wait')
        self.emit(ev='done', t=self.t())

    def step(self, src):
        w = src.split()
        k = w[0]
        if k == 'watch':
            self.watch += w[1:]
            return 'done', 'watch lines are read before the game starts; later ones are reported only if already watched'
        if k in ('walk', 'run'):
            if len(w) < 3:
                raise BadStep('walk <x> <z> [radius <r>] [timeout <s>]')
            kw = dict(zip(w[3::2], w[4::2])) if len(w) > 3 and not w[3][0].isdigit() else {}
            radius = float(kw.get('radius', w[3] if len(w) > 3 and not kw else 1.0))
            timeout = float(kw.get('timeout', w[4] if len(w) > 4 and not kw else 30.0))
            return self.walk(float(w[1]), float(w[2]), radius, timeout)
        if k == 'press':
            if len(w) < 2:
                raise BadStep('press <buttons> [ms]')
            ms = int(w[2]) if len(w) > 2 else 200
            self.wait_input(self.g.press(w[1], ms))
            return 'done', ''
        if k == 'stick':
            if len(w) < 3 or w[1] not in NAMED_DIRS:
                raise BadStep('stick <up|down|left|right|up-left|...> <ms>')
            ms = int(w[2])
            names = NAMED_DIRS[w[1]].split('+')
            lx = 32767 * (('LRIGHT' in names) - ('LLEFT' in names))
            ly = 32767 * (('LUP' in names) - ('LDOWN' in names))
            self.wait_input(self.g.stick(lx, ly, ms))
            return 'done', ''
        if k == 'push':
            if len(w) < 4:
                raise BadStep('push <lx> <ly> <ms>')
            lx, ly, ms = int(w[1]), int(w[2]), int(w[3])
            self.wait_input(self.g.stick(lx, ly, ms))
            return 'done', f'stick {lx} {ly} for {ms} ms'
        if k == 'raw':    # a sequence of timed inputs; its explicit wait remains wall seconds
            if len(w) < 3:
                raise BadStep('raw <presses> <wait s>')
            self.g.send(w[1])
            self.wait(float(w[2]))
            return 'done', w[1]
        if k == 'cheat':  # cheat <n>: the debug tools' action n (!cheat: 3000+ items, 1000+ teleports, 996 save ...)
            if len(w) < 2 or not w[1].isdigit():
                raise BadStep('cheat <number>')
            mark = len(self.g.log_lines)
            self.g.send(f'!cheat {w[1]}')
            self.wait(1.5)
            said = [ln.split('cheats:', 1)[1].strip() for ln in self.g.log_lines[mark:] if 'cheats:' in ln]
            return 'done', '; '.join(said)[:300] or 'no cheats: line in the log'
        if k == 'until':
            return self.until(w[1:])
        if k == 'wait':
            self.wait(float(w[1]) if len(w) > 1 else 1.0)
            return 'done', ''
        if k == 'climb':
            return self.climb(float(w[w.index('timeout') + 1]) if 'timeout' in w else 12.0)
        if k == 'save':
            return self.save(float(w[2]) if len(w) > 2 and w[1] == 'timeout' else 60.0)
        if k == 'report':
            self.report = float(w[1]) if len(w) > 1 else 0.5
            self.report_next = self.t()
            return 'done', ''
        if k == 'shot':
            self.g.send('!shots 1')
            self.wait(0.5)
            return 'done', ''
        if k == 'end':
            self.ended = True
            return 'done', ''
        raise BadStep(f'unknown step {k}')

    def wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end and self.g.alive():
            self.tick(0.05)

    def wait_input(self, rid):
        # The input's own completion is in game frames, not wall time. Events and other replies still get drained.
        while rid in self.g.pending and self.g.alive():
            self.tick(0.05)

    def climb(self, timeout):
        """Hold up on the stick while Link climbs (a ladder, ivy): done once he has risen over 1 m and stopped rising
        for a second, stuck if he never rose, timeout otherwise"""
        start, st = time.time(), self.g.st or {}
        y0 = top = (st.get('pos') or [0, 0, 0])[1]
        top_at = start
        while time.time() - start < timeout and self.g.alive():
            self.g.stick(0, 32767, 250)
            self.wait(0.2)
            y = ((self.g.st or {}).get('pos') or [0, y0, 0])[1]
            if y > top + 0.05:
                top, top_at = y, time.time()
            if top - y0 > 1.0 and time.time() - top_at > 1.0:
                return 'done', f'rose {top - y0:.1f} m'
            if top - y0 < 0.2 and time.time() - start > 3.0:
                return 'stuck', f'rose {top - y0:.2f} m in 3 s'
        return 'timeout', f'rose {top - y0:.1f} m in {timeout:g} s'

    def cond(self, words):
        """(test(state) -> bool, text) for flag <name><op><value> | area <name> | prompt=<text> | control"""
        if not words:
            raise BadStep('until <condition>')
        if words[0] == 'control':
            return (lambda s: bool(s.get('map')) and s.get('event') == 0 and s.get('loading') == 0), 'control', 1
        if words[0] == 'area' and len(words) > 1:
            name = words[1]
            return (lambda s: s.get('map') == name), f'area {name}', 2
        if words[0].startswith('prompt='):
            want = words[0][7:]
            return (lambda s: s.get('prompt') == want), f'prompt {want}', 1
        if words[0] == 'flag' and len(words) > 1:
            m = re.match(r'([A-Za-z0-9_.]+)(==|!=|<=|>=|=|<|>)(.+)$', words[1])
            if not m:
                raise BadStep('flag <name><op><value>')
            name, op, val = m.groups()
            if name in ('prompt', PROMPT_HASH):
                return (lambda s: s.get('prompt') == val), f'prompt {val}', 2

            def test(s, name=name, op=op, val=val):
                v = (s.get('flags') or {}).get(name)
                if v is None:
                    return False
                try:
                    a, b = float(v), float(val)
                except (TypeError, ValueError):
                    a, b = str(v), val
                return {'=': a == b, '==': a != b, '!=': a != b, '<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b}[op]
            return test, f'flag {name}{op}{val}', 2
        raise BadStep('until flag|area|prompt=|control ...')

    def until(self, words):
        test, text, used = self.cond(words)
        rest = words[used:]
        timeout, press, every = 30.0, None, None
        i = 0
        while i < len(rest):
            if rest[i] == 'timeout' and i + 1 < len(rest):
                timeout = float(rest[i + 1])
                i += 2
            elif rest[i] == 'press' and i + 3 < len(rest) and rest[i + 2] == 'every':
                press, every = rest[i + 1], float(rest[i + 3])
                i += 4
            else:
                raise BadStep(f'until ...: {rest[i]} not understood')
        start, next_press = time.time(), time.time()
        while time.time() - start < timeout and self.g.alive():
            if self.g.st and test(self.g.st):
                return 'met', f'{text}: met after {time.time() - start:.1f} s'
            if press and time.time() >= next_press:
                self.g.press(press, 100)
                next_press = time.time() + every
            self.tick(0.05)
        return 'timeout', f'{text}: not met in {timeout:g} s'

    def walk(self, x, z, radius, timeout):
        """Steer Link to (x, z): each fresh sample, the stick direction nearest the needed one relative to the camera,
        with the error carried to the next tick (eight directions), or the exact angle with analog on. Ends 'held' when an
        event (a talk, a scene) has held Link for 0.6 s: pushing on into a talk does nothing"""
        start = stuck_from = time.time()
        carry, hist = 0.0, []
        sent_frame = None
        held_since = None
        while self.g.alive():
            self.tick(0.03)
            st = self.g.st or {}
            pos, cam = st.get('pos'), st.get('camera')
            if not pos:
                continue
            d = math.hypot(x - pos[0], z - pos[2])
            if d <= radius:
                return 'reached', f'{d:.2f} m from the point'
            if st.get('event') == 1:
                held_since = held_since or time.time()
                if time.time() - held_since >= 0.6:
                    return 'held', f'{d:.2f} m from the point; an event holds Link at {pos[0]:.1f} {pos[1]:.1f} {pos[2]:.1f}'
            else:
                held_since = None
            now = time.time()
            hist.append((now, pos[0], pos[2]))
            # (the stick's handedness, checked as he moves: off the wanted heading by over 100 degrees three times running
            # while the wanted way is to the camera's side)
            old = [h for h in hist if now - h[0] >= 0.6]
            if old and cam is not None and now - self.checked > 0.5:
                self.checked = now
                mx, mz = pos[0] - old[-1][1], pos[2] - old[-1][2]
                if math.hypot(mx, mz) > 0.5:
                    err = abs(wrap(yaw_of(mx, mz) - yaw_of(x - pos[0], z - pos[2])))
                    lateral = abs(abs(wrap(cam - yaw_of(x - pos[0], z - pos[2]))) - 90) < 60
                    self.mirror = self.mirror + 1 if err > 100 and lateral else 0
                    if self.mirror >= 3:
                        self.sign, self.mirror = -self.sign, 0
                        self.emit(ev='note', text=f'stick handedness flipped to {self.sign:+.0f} (heading off by {err:.0f} degrees)')
                        hist, self.checked, stuck_from = [], now + 0.5, now   # (judge the new way on its own)
            hist = [h for h in hist if now - h[0] <= 2.5]
            if now - stuck_from > 2.5 and hist and math.hypot(pos[0] - hist[0][1], pos[2] - hist[0][2]) < 0.3:
                return 'stuck', f'{d:.2f} m from the point; moved under 0.3 m in 2.5 s at {pos[0]:.1f} {pos[1]:.1f} {pos[2]:.1f}'
            if now - start > timeout:
                return 'timeout', f'{d:.2f} m from the point after {timeout:g} s'
            if cam is None or st.get('frame') == sent_frame or st.get('event') != 0 or st.get('loading') != 0:
                continue
            want = wrap(cam - yaw_of(x - pos[0], z - pos[2])) * self.sign + carry   # the stick's angle, right positive
            if self.analog:   # the exact angle; within 1.2 m of the point at half push (Link walks), not full
                mag = 32767 if d > 1.2 else 16384
                lx, ly = round(mag * math.sin(math.radians(want))), round(mag * math.cos(math.radians(want)))
                carry = 0.0
                self.g.stick(lx, ly, 140)
            else:
                a, press = min(DIRS, key=lambda dp: abs(wrap(dp[0] - want)))
                carry = max(-40.0, min(40.0, wrap(want - a)))
                self.g.press(press, 140)
            sent_frame = st['frame']
        return 'cut', 'the game ended'

    def save(self, timeout):
        before = self.g.saves()
        mark = len(self.g.log_lines)
        self.g.send('!cheat 996')
        start = time.time()
        said = ''
        while time.time() - start < timeout and self.g.alive():
            self.tick(0.1)
            for line in self.g.log_lines[mark:]:
                if 'cheats: save:' in line:
                    said = line.split('cheats: save:', 1)[1].strip()
            mark = len(self.g.log_lines)
            now = self.g.saves()
            changed = [p for p, t in now.items() if before.get(p) != t]
            if changed:
                self.wait(2.0)   # (the game writes progress.sav and caption.sav: let it finish)
                return 'done', f'saved {", ".join(Path(p).parent.name for p in changed)} ({said})'
            if said and 'not taken' in said and time.time() - start > 3:
                return 'refused', said
        return 'timeout', said or 'no answer to !cheat 996'


class BadStep(Exception):
    pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('copy')
    ap.add_argument('tag')
    ap.add_argument('wall', type=int)
    ap.add_argument('--live', required=True)
    ap.add_argument('--save')
    ap.add_argument('--boot', choices=('newgame', 'continue', 'none'), default=None)
    ap.add_argument('--shots', type=int, default=0)
    ap.add_argument('--keep-going', action='store_true', help='after the steps are done, keep the game until end')
    ap.add_argument('--analog', action='store_true', help='steer with exact stick values through the control socket')
    a = ap.parse_args()
    live = Path(a.live)
    live.mkdir(parents=True, exist_ok=True)
    (live / 'steps.txt').touch()
    time.sleep(1.0)   # (the pilot writes its watch lines first)
    watch = []
    for line in (live / 'steps.txt').read_text().splitlines():
        w = line.split('#', 1)[0].split()
        if w and w[0] == 'watch':
            watch += [x for x in w[1:] if valid_flag(x)]
    boot = a.boot or ('continue' if a.save else 'newgame')
    g = Game(a.copy, a.tag, a.wall, live, save=a.save, watch=sorted(set(watch))[:64], shots=a.shots)
    ex = Executor(g, live, boot)
    ex.analog = ex.analog or a.analog
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))   # (a stop from the pilot still stops the game: finally below)
    ex.emit(ev='note', text='hs-run: ' + ' '.join(g.cmd), boot=boot, analog=ex.analog)
    result = 0
    try:
        ex.run()
    except ControlError as e:
        ex.emit(ev='note', text=str(e))
        result = 1
    finally:
        g.stop()
        ex.emit(ev='note', text=f'game stopped; run folder {g.R}', saves={Path(p).parent.name: t for p, t in g.saves().items()})
    return result


if __name__ == '__main__':
    sys.exit(main())
