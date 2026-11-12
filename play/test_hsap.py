#!/usr/bin/env python3
"""Game-free checks of hsap.py against a simulated protocol-1 bridge over a real Unix socket.
The production Game client and Executor run unchanged; only hs-run.sh is replaced. A deterministic clock advances
presents, timed inputs and Link updates, so no GPU, game files, background thread or wall-clock sleeps are needed.

    python3 -I -B test_hsap.py
    HSAP_TEST_MODULE=/path/to/hsap.py python3 -I -B test_hsap.py
"""
import contextlib
import importlib.util
import json
import math
import os
import re
import socket
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hsap  # noqa: E402
if os.environ.get('HSAP_TEST_MODULE'):
    _spec = importlib.util.spec_from_file_location('hsap', os.environ['HSAP_TEST_MODULE'])
    hsap = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(hsap)

AXES = {'LUP': ('ly', 1), 'LDOWN': ('ly', -1), 'LLEFT': ('lx', -1), 'LRIGHT': ('lx', 1),
        'RUP': ('ry', 1), 'RDOWN': ('ry', -1), 'RLEFT': ('rx', -1), 'RRIGHT': ('rx', 1)}
BUTTONS = set('A B X Y L R ZL ZR PLUS MINUS LS RS LEFT UP RIGHT DOWN'.split())


class FakeRunner:
    def __init__(self):
        self.running = True

    def poll(self):
        return None if self.running else 0

    def wait(self, timeout=None):
        self.running = False
        return 0


class FakeBridge:
    """A world with y up and a following camera. All names and geometry here are invented fixtures."""
    def __init__(self, path, live, run, clock, start=(0.0, 0.0, 0.0), camera=0.0, handed=1, follow=True,
                 walls=(), boot_events=5, flags=None, prompt_at=None, ack_delay=0, listening=True, subscribe_error=None):
        self.path, self.run, self.clock = path, run, clock
        self.pos, self.cam, self.handed, self.follow = list(start), camera, handed, follow
        self.walls, self.boot_events = list(walls), boot_events
        self.flags, self.prompt_at = dict(flags or {}), prompt_at
        self.ack_delay = ack_delay
        self.subscribe_error = subscribe_error
        self.listener, self.conn = None, None
        self.rx, self.tx = bytearray(), bytearray()
        self.write_limit = None
        self.requests, self.inputs, self.waiting = [], [], []
        self.sub, self.last_sample, self.baseline = None, 0, None
        self.frame, self.latest, self.updates = 0, None, True
        self.event_override = None
        self.a_presses, self.max_waiting = 0, 0
        self.debug = hsap.Tail(live)
        self.debug_lines = []
        self.run.mkdir(parents=True)
        (self.run / 'hookshot_log.txt').write_text('vk: 0 presents, 60 fps; a frame: 15 draws\n')
        if listening:
            self.listen()

    def listen(self):
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        self.listener.listen(1)
        self.listener.setblocking(False)

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
        if self.listener:
            self.listener.close()
            self.listener = None

    def queue(self, message):
        self.tx.extend((json.dumps(message, separators=(',', ':')) + '\n').encode())

    def reply(self, request, **fields):
        self.queue({'id': request['id'], 'ok': True, 'frame': self.frame, **fields})

    def fail(self, request, error, message):
        self.queue({'id': request['id'], 'ok': False, 'frame': self.frame, 'error': error, 'message': message})

    def pump(self):
        for line in self.debug.lines():
            self.debug_lines.append(line)
            if line == '!cheat 996':
                save = self.run / 'user/save/test-user/slot_00/progress.sav'
                save.parent.mkdir(parents=True, exist_ok=True)
                save.write_bytes(b'simulated save')
                os.utime(save, (self.clock.now, self.clock.now))
                with open(self.run / 'hookshot_log.txt', 'a') as out:
                    out.write('cheats: save: manager 0x1, check 1, request taken (cooldown 0)\n')
            elif line.startswith('!cheat '):
                with open(self.run / 'hookshot_log.txt', 'a') as out:
                    out.write('cheats: simulated debug action\n')
        if self.listener is not None and self.conn is None:
            try:
                self.conn, _ = self.listener.accept()
                self.conn.setblocking(False)
            except BlockingIOError:
                pass
        if self.conn is None:
            return
        while True:
            try:
                data = self.conn.recv(65536)
            except BlockingIOError:
                break
            except ConnectionResetError:
                data = b''
            if not data:
                self.conn.close()
                self.conn = None
                self.inputs.clear()   # waited inputs belong to the connection
                self.waiting.clear()
                self.sub = None
                return
            self.rx.extend(data)
        while b'\n' in self.rx:
            line, _, self.rx = self.rx.partition(b'\n')
            request = json.loads(line)
            assert all(r['id'] != request['id'] for r in self.requests), 'request ids must be unique'
            self.requests.append(request)
            self.dispatch(request)
        if self.tx:
            try:
                n = self.conn.send(self.tx if self.write_limit is None else self.tx[:self.write_limit])
                del self.tx[:n]
            except BlockingIOError:
                pass

    def dispatch(self, request):
        cmd = request['cmd']
        if cmd == 'subscribe':
            if self.subscribe_error:
                self.fail(request, *self.subscribe_error)
                return
            assert request['events'] == ['oot']
            assert 1 <= request['every'] <= 216000
            assert type(request['changes']) is bool
            assert len(request['flags']) <= 64 and all(hsap.valid_flag(f) for f in request['flags'])
            self.sub, self.last_sample, self.baseline = request, self.frame, None
            self.reply(request)
        elif cmd == 'status':
            self.reply(request, protocol=1, map='Last_Known_Map', game=self.latest)
        elif cmd in ('press', 'stick', 'sequence'):
            try:
                steps = self.input_steps(request)
            except ValueError as e:
                self.fail(request, 'bad_request', str(e))
                return
            assert request['exclusive'] is True and request['wait'] is True
            start = min(s['start'] for s in steps)
            end = max(s['end'] for s in steps)
            self.inputs.extend(steps)
            self.waiting.append((end + self.ack_delay, request, start, end))
            self.max_waiting = max(self.max_waiting, len(self.waiting))
        else:
            self.fail(request, 'unknown_cmd', 'no such command')

    def names(self, names):
        axes, buttons = {}, []
        for name in names.split('+'):
            if name in BUTTONS:
                buttons.append(name)
            elif name in AXES:
                axis, value = AXES[name]
                axes[axis] = value
            elif re.fullmatch(r'[LR][XY]=-?\d+', name):
                axes[name[:2].lower()] = max(-1.0, min(1.0, int(name[3:]) / 32767))
            else:
                raise ValueError('not a button: ' + name)
        return axes, buttons

    def input_steps(self, request):
        if request['cmd'] == 'sequence':
            assert request['units'] == 'hos'
            specs = []
            for text in request['script'].split(','):
                match = re.fullmatch(r'([\d.]+):([^/]+)(?:/([\d.]+))?', text)
                if not match:
                    raise ValueError('bad script')
                at, names, ms = match.groups()
                specs.append((math.floor(float(at) * 60 + 0.5), max(1, math.floor(float(ms or 200) * .06 + .5)), names, {}))
        else:
            axes = {}
            if request['cmd'] == 'stick':
                assert request['stick'] == 'L'
                axes = {'lx': request['x'], 'ly': request['y']}
                assert all(-1 <= value <= 1 for value in axes.values())
            specs = [(request.get('at', 0), request['frames'], request.get('buttons', ''), axes)]
        steps = []
        for at, frames, names, axes in specs:
            buttons = []
            if names:
                named_axes, buttons = self.names(names)
                axes = {**named_axes, **axes}
            assert 1 <= frames <= 216000
            self.a_presses += 'A' in buttons
            start = self.frame + 1 + at
            steps.append({'start': start, 'end': start + frames, 'axes': axes, 'buttons': buttons})
        return steps

    def game_state(self, frame=None):
        frame = self.frame if frame is None else frame
        prompt = 'Empty'
        if self.prompt_at and math.hypot(self.pos[0] - self.prompt_at[0], self.pos[2] - self.prompt_at[1]) < 1.0:
            prompt = 'Crawl'
        event = frame < self.boot_events * 6 if self.event_override is None else self.event_override
        return {'t': frame / 60, 'frame': frame, 'map': 'Test_Map', 'pos': [round(v, 2) for v in self.pos],
                'facing': 0.0, 'camera': round(self.cam, 1), 'event': event, 'loading': False, 'age': 'child',
                'life': 12, 'max_life': 12, 'rupees': 0, 'prompt': prompt, 'music': 'BGM_Test'}

    def tick(self):
        self.frame += 1
        axes = {}
        for step in self.inputs:
            if step['start'] <= self.frame < step['end']:
                axes.update(step['axes'])
        sx, sy = axes.get('lx', 0), axes.get('ly', 0)
        if self.updates and (sx or sy) and not self.game_state()['event']:
            angle = math.degrees(math.atan2(sx, sy))
            world = math.radians(self.cam - self.handed * angle)
            speed = 4 / 60 * min(1, math.hypot(sx, sy))
            nx, nz = self.pos[0] + speed * math.sin(world), self.pos[2] + speed * math.cos(world)
            if not any(x0 <= nx <= x1 and z0 <= nz <= z1 for x0, z0, x1, z1 in self.walls):
                if self.follow:
                    self.cam += hsap.wrap(math.degrees(world) - self.cam) / 60
                self.pos[0], self.pos[2] = nx, nz
        self.inputs = [s for s in self.inputs if s['end'] > self.frame]
        if self.updates:
            self.latest = self.game_state()
            if self.sub and self.frame - self.last_sample >= self.sub['every']:
                self.last_sample = self.frame
                game = dict(self.latest)
                if self.sub['flags']:
                    game['flags'] = {name: self.flags.get(name) for name in self.sub['flags']}
                if self.sub['changes']:
                    values = {'0x00000001': self.flags.get('Test.Flag')}
                    game['changed'] = {} if self.baseline is None else {
                        name: value for name, value in values.items() if self.baseline.get(name) != value}
                    self.baseline = values
                self.queue({'event': 'oot', 'sub': self.sub['id'], 'frame': self.frame, 'game': game})
        # Deliberately deliver completed shorter inputs before earlier, still-outstanding long inputs.
        for due, request, start, end in list(self.waiting):
            if self.frame >= due:
                self.reply(request, start=start, end=end)
                self.waiting.remove((due, request, start, end))


class Clock:
    def __init__(self):
        self.now, self.next_frame = 1000.0, 1000.0 + 1 / 60
        self.bridge = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        end = self.now + seconds
        while self.next_frame <= end + 1e-9:
            self.now = self.next_frame
            self.next_frame += 1 / 60
            self.bridge.pump()
            self.bridge.tick()
            self.bridge.pump()
        self.now = end
        self.bridge.pump()


class Simulation:
    def __init__(self, watch=None, **options):
        self.options = options
        self.watch = list(options.get('flags', {})) if watch is None else watch
        self.executors, self.input_samples = [], []

    def __enter__(self):
        self.stack = contextlib.ExitStack()
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='hsap-test-')))
        self.live, self.copy = root / 'live', root / 'copy'
        self.clock = Clock()
        for name, value in [('time', self.clock.time), ('monotonic', self.clock.time), ('sleep', self.clock.sleep)]:
            self.stack.enter_context(mock.patch.object(hsap.time, name, value))
        self.stack.enter_context(mock.patch.object(hsap.subprocess, 'Popen', self.launch))
        self.game = hsap.Game(self.copy, 'test', 60, self.live, watch=self.watch)
        self.stack.enter_context(mock.patch.object(self.game, 'game_pids', return_value=[]))
        request = self.game.request

        def tracked(cmd, **args):
            if cmd in ('press', 'stick', 'sequence'):
                self.input_samples.append((cmd, (self.game.st or {}).get('frame')))
            return request(cmd, **args)

        self.game.request = tracked
        return self

    def launch(self, cmd, **kwargs):
        assert cmd[0] == hsap.HS_RUN and kwargs['start_new_session'] is True
        args = dict(arg.split('=', 1) for arg in cmd[4:] if '=' in arg)
        assert 'HOS_CONTROL' in args and 'HOS_INPUT_LIVE' in args
        assert not any(key.startswith('HOS_OOT_STATE_') for key in args)
        assert 'HOS_INPUT_FRAME_CLOCK' not in args and 'HOS_INPUT_EXCLUSIVE' not in args
        self.bridge = FakeBridge(Path(args['HOS_CONTROL']), Path(args['HOS_INPUT_LIVE']), self.copy / 'runs/test',
                                 self.clock, **self.options)
        self.clock.bridge = self.bridge
        return FakeRunner()

    def exchange(self):
        states = self.game.poll()
        self.bridge.pump()
        states += self.game.poll()
        return states

    def executor(self, steps=(), boot='none'):
        (self.live / 'steps.txt').write_text(''.join(s + '\n' for s in steps))
        ex = hsap.Executor(self.game, self.live, boot)
        ex.analog = False   # independent of the caller's HSAP_ANALOG environment
        self.executors.append(ex)
        return ex

    def events(self):
        return [json.loads(line) for line in (self.live / 'out.jsonl').read_text().splitlines()]

    def __exit__(self, *exc):
        try:
            for ex in self.executors:
                ex.out.close()
            self.game.stop()
            self.bridge.pump()
            assert not self.bridge.inputs and not self.bridge.waiting, 'disconnect releases pending inputs'
        finally:
            self.bridge.close()
            self.stack.close()


class SendBudget:
    """Force a short write followed by EAGAIN, without replacing the actual socket transport."""
    def __init__(self, sock, budget):
        self.sock, self.budget = sock, budget

    def send(self, data):
        if self.budget == 0:
            raise BlockingIOError()
        n = self.sock.send(data if self.budget is None else data[:self.budget])
        if self.budget is not None:
            self.budget -= n
        return n

    def __getattr__(self, name):
        return getattr(self.sock, name)


def check(cond, what):
    if not cond:
        raise SystemExit('FAIL: ' + what)
    print('ok:', what)


def fails(kind, function, what):
    try:
        function()
    except kind:
        check(True, what)
    else:
        check(False, what)


def transport_checks():
    missing_oot = ("this build has no oot subscription: use hsap_file.py, or a build with "
                   "Hookshot #2292's control-bridge commits")
    for refusal in (('bad_request', 'events: present, state, log, map'),
                    ('unknown_cmd', 'no such command'), ('unavailable', 'OoT sampling is not available')):
        with Simulation(subscribe_error=refusal) as sim:
            before = sim.clock.time()
            try:
                sim.exchange()
            except hsap.ControlError as error:
                check(str(error) == missing_oot, 'unsupported oot subscription gives the explicit driver/build choice')
            else:
                check(False, 'unsupported oot subscription must fail on its first reply')
            check(sim.clock.time() == before and sim.game.n_states == 0 and not sim.bridge.debug_lines and
                  sim.game.live_in.read_text() == '', 'oot refusal neither waits for boot nor falls back to file input')
    for refusal in (('busy', 'too many requests'), ('bad_request', 'flags: invalid name')):
        with Simulation(subscribe_error=refusal) as sim:
            try:
                sim.exchange()
            except hsap.ControlError as error:
                check(str(error) != missing_oot and refusal[0] in str(error) and refusal[1] in str(error),
                      'other subscription failures keep their actual cause')
            else:
                check(False, 'other subscription errors must still fail')

    with Simulation(watch=['Test.Flag', '0x1234abcd']) as sim:
        g, bridge = sim.game, sim.bridge
        check(sim.exchange() == [] and g.st is None, 'no fabricated state before the first Link update')
        sub = bridge.requests[0]
        check(sub == {'id': g.sub, 'cmd': 'subscribe', 'events': ['oot'], 'every': 6,
                      'flags': ['Test.Flag', '0x1234abcd'], 'changes': True}, 'oot subscribes at six presents with flags and changes')
        check(not g.pending and g.sock.getblocking() is False, 'startup replies are matched and the socket is nonblocking')
        bridge.updates = False
        bridge.frame = 900
        bridge.latest = bridge.game_state(12)
        g.request('status')
        states = sim.exchange()
        check(len(states) == 1 and g.st['frame'] == 12 and g.presents == 900,
              'status keeps the snapshot frame, not the later reply present')
        check(g.st['child'] == 1 and type(g.st['child']) is int and type(g.st['event']) is int,
              'age and booleans normalize to the pilot numeric fields')
        game = bridge.game_state(12)
        game.update(flags={'Test.Flag': 7}, changed={})
        bridge.queue({'event': 'oot', 'sub': g.sub, 'frame': 901, 'game': game})
        sim.exchange()
        check(g.n_states == 1 and g.st['flags']['Test.Flag'] == 7 and g.presents == 901,
              'same-sample event enriches status despite its later delivery present, without counting it twice')
        bridge.latest = bridge.game_state(11)
        g.request('status')
        sim.exchange()
        check(g.st['frame'] == 12, 'a late older status cannot regress the current state')
        game = bridge.game_state(13)
        game.update(map=None, age=None, event=None, loading=None, changed={'0x00000001': 9}, more=4)
        bridge.queue({'event': 'oot', 'sub': g.sub, 'frame': 902, 'game': game})
        bridge.queue({'event': 'oot', 'sub': g.sub + 100, 'frame': 1000, 'game': bridge.game_state(1000)})
        bridge.queue({'event': 'present', 'sub': g.sub, 'frame': 1001})
        sim.exchange()
        check(g.st['map'] is None and g.st['child'] is None and g.st['event'] is None and g.st['loading'] is None,
              'unreadable map and unknown booleans/age remain null, without top-level map fallback')
        check(g.st['frame'] == 13 and g.presents == 902 and g.st['more'] == 4 and g.changes == {'0x00000001': 9},
              'only matching oot samples update state, changes and overflow; sample frame is not delivery frame')
        ex = sim.executor()
        check(not ex.cond(['control'])[0](g.st), 'unknown loading/event is not control')
        ex.state_event('wait')
        event = sim.events()[-1]
        check(event['area'] is None and event['child'] is None and event['frame'] == 13 and
              event['changed'] == ['0x00000001'] and not g.changes, 'downstream state events retain the pilot schema')
        bridge.latest = dict(game, frame=13)
        for _ in range(3):
            g.request('status')
            sim.exchange()
        check(g.n_states == 2, 'retained status snapshots do not create new samples during an update gap')
        game = dict(bridge.game_state(14), age='adult', flags={'Test.Flag': 9}, changed={})
        bridge.write_limit = 7
        bridge.queue({'event': 'oot', 'sub': g.sub, 'frame': 903, 'game': game})
        sim.exchange()
        check(g.st['frame'] == 13 and g.rx, 'a partial socket line does not become a partial snapshot')
        while bridge.tx:
            sim.exchange()
        check(g.st['frame'] == 14 and g.st['child'] == 0, 'fragmented events finish and normalize adult to zero')
        journal = [json.loads(line) for line in g.state_path.read_text().splitlines()]
        check([s['frame'] for s in journal] == [12, 13, 14], 'state.jsonl is a normalized output journal, not the transport')

    with Simulation(listening=False) as sim:
        check(sim.exchange() == [] and sim.game.sock is None, 'an absent startup socket does not block polling')
        sim.bridge.listen()
        sim.clock.sleep(.3)
        sim.exchange()
        check(sim.game.sock is not None and not sim.game.pending, 'the client connects when the queued game starts')

    with Simulation(boot_events=0) as sim:
        g = sim.game
        sim.exchange()
        long_id = g.press('A', 2000)
        short_id = g.press('B', 100)
        status_id = g.request('status')
        sim.exchange()
        check(long_id in g.pending and short_id in g.pending and status_id not in g.pending,
              'status is handled while input completion replies are outstanding')
        sim.clock.sleep(.2)
        sim.exchange()
        check(long_id in g.pending and short_id not in g.pending and g.n_states >= 2,
              'a later short press replies first while oot events continue')
        sim.clock.sleep(2)
        sim.exchange()
        check(not g.pending, 'all out-of-order completions are consumed by id')
        limited = SendBudget(g.sock, 5)
        g.sock = limited
        rid = g.press('X', 100)
        sim.exchange()
        check(g.tx and rid in g.pending, 'short writes and send backpressure keep the unsent request queued')
        limited.budget = None
        sim.exchange()
        sim.clock.sleep(.2)
        sim.exchange()
        check(not g.tx and rid not in g.pending and sum(r['id'] == rid for r in sim.bridge.requests) == 1,
              'the remainder is sent once when writable, without losing or duplicating the request')
        g.request('not_a_command')
        fails(hsap.ControlError, sim.exchange, 'bridge errors are surfaced instead of silently accepting failed commands')

    with Simulation() as sim:
        sim.exchange()
        sim.bridge.tx.extend(b'{broken json}\n')
        fails(hsap.ControlError, sim.exchange, 'malformed replies fail explicitly')
    with Simulation() as sim:
        sim.exchange()
        sim.bridge.conn.close()
        sim.bridge.conn = None
        fails(hsap.ControlError, sim.game.poll, 'disconnect fails explicitly instead of reusing stale steering state')

    check(hsap.Game.frames(1) == 1 and hsap.Game.frames(100) == 6 and hsap.Game.frames(140) == 8 and
          hsap.Game.frames(200) == 12, 'millisecond holds use the bridge hos-unit rounding at 60 frames per second')
    check(all(hsap.valid_flag(n) for n in ['Test.Flag', '0x1234aBcD', 'a' * 47]) and
          not any(hsap.valid_flag(n) for n in ['', '0x123', '0x1234567g', 'a' * 48, 'space name']),
          'watch names and exact eight-digit hashes match the subscription contract')
    fails(ValueError, lambda: hsap.Game('unused', 'unused', 1, 'unused', watch=['F' + str(i) for i in range(65)]),
          'more than 64 watches are rejected before launching a game')


def executor_checks():
    # Original coverage: boot, ordinary steering, event order, distance and speed, state output.
    with Simulation(boot_events=8) as sim:
        ex = sim.executor(['walk 6 8 radius 0.6 timeout 15', 'end'], boot='newgame')
        ex.run()
        ev = sim.events()
        kinds = [e['ev'] for e in ev if e['ev'] != 'note']
        check(kinds[0] == 'gameplay' and kinds[-1] == 'done', f'gameplay first, done last: {kinds}')
        gameplay = next(e for e in ev if e['ev'] == 'gameplay')
        check(gameplay['frame'] >= 8 * 6 + 29 * 6 and sim.bridge.a_presses > 0,
              'boot presses through the socket and waits for thirty calm samples')
        step = next(e for e in ev if e['ev'] == 'step' and e['src'].startswith('walk'))
        check(step['result'] == 'reached', f"a walk to a point 10 m away is reached ({step['detail']}, {step['secs']} s)")
        check(step['secs'] < 6, 'at about 4 m/s')
        check(math.hypot(sim.bridge.pos[0] - 6, sim.bridge.pos[2] - 8) <= .6, 'Link ends within the radius')
        states = [e for e in ev if e['ev'] == 'state' and e['why'] == 'wait']
        check(states and states[-1]['pos'] and 'flags' in states[-1] and states[-1]['child'] == 1,
              'a compatible wait state line follows the steps')
        check(sim.game.live_in.read_text() == '', 'ordinary boot and steering inputs never use the live file')

    # Original coverage: the mirrored world's handedness is found and corrected.
    with Simulation(boot_events=1, handed=-1, camera=0.0, follow=False) as sim:
        sim.executor(['walk 10 0 radius 0.6 timeout 20', 'end']).run()
        ev = sim.events()
        step = next(e for e in ev if e['ev'] == 'step' and e['src'].startswith('walk'))
        check(any(e['ev'] == 'note' and 'handedness flipped' in e['text'] for e in ev), 'the stick handedness is flipped')
        check(step['result'] == 'reached', f"and the walk is reached in the mirror world ({step['detail']})")

    # Original coverage: collision reports stuck, not success.
    with Simulation(boot_events=1, walls=[(-5, 2, 5, 3)]) as sim:
        sim.executor(['walk 0 8 radius 0.5 timeout 15', 'end']).run()
        step = next(e for e in sim.events() if e['ev'] == 'step' and e['src'].startswith('walk'))
        check(step['result'] == 'stuck', f"a wall in the way ends the walk stuck ({step['detail']})")

    # Original coverage: until, repeated presses, watched flag, prompt, save and invalid step.
    with Simulation(boot_events=1, flags={'Test.Flag': 0}, prompt_at=(0.0, 3.0)) as sim:
        sim.executor(['until flag Test.Flag=1 timeout 1.5 press A every 0.3', 'walk 0 3 radius 0.3 timeout 5',
                      'until prompt=Crawl timeout 2', 'save timeout 5', 'fly 1 2', 'end']).run()
        ev = sim.events()
        steps = [e for e in ev if e['ev'] == 'step']
        check(steps[0]['result'] == 'timeout' and sim.bridge.a_presses >= 4,
              f'until times out and pressed A {sim.bridge.a_presses} times')
        check(steps[2]['result'] == 'met', 'until prompt=Crawl is met at the hole')
        check(steps[3]['result'] == 'done' and 'slot_00' in steps[3]['detail'], f"save: {steps[3]['detail']}")
        check(any(e['ev'] == 'bad' and e['src'] == 'fly 1 2' for e in ev), 'an unknown step is answered bad')
        sim.bridge.flags['Test.Flag'] = 1
        sim.clock.sleep(.2)
        sim.exchange()
        ex = sim.executors[0]
        check(ex.until(['flag', 'Test.Flag=1', 'timeout', '1'])[0] == 'met' and sim.game.changes['0x00000001'] == 1,
              'watched flag updates and change deltas arrive through oot events')

    # Original analog coverage plus delayed replies: steering must not serialize on completion acknowledgements.
    with Simulation(boot_events=1, follow=False, camera=30.0, ack_delay=30) as sim:
        ex = sim.executor(['walk 7 -3 radius 0.4 timeout 15', 'end'])
        ex.analog = True
        ex.run()
        step = next(e for e in sim.events() if e['ev'] == 'step' and e['src'].startswith('walk'))
        sticks = [r for r in sim.bridge.requests if r['cmd'] == 'stick']
        check(step['result'] == 'reached' and sticks and all(r['stick'] == 'L' and r['frames'] == 8 for r in sticks),
              f"an analog walk is reached with socket stick inputs ({step['detail']}, {len(sticks)} presses)")
        check(any(0 < abs(r['x']) < .99 and 0 < abs(r['y']) < .99 for r in sticks) and
              any(math.hypot(r['x'], r['y']) < .6 for r in sticks), 'exact angles and near-target half pushes reach the wire')
        samples = [frame for cmd, frame in sim.input_samples if cmd == 'stick']
        check(len(samples) == len(set(samples)) and max(b - a for a, b in zip(samples, samples[1:])) <= 6,
              'steering sends once per fresh six-present sample, not once per input reply')
        check(sim.bridge.max_waiting >= 4, 'several outstanding input replies do not block steering')

    with Simulation(boot_events=0, ack_delay=18) as sim:
        ex = sim.executor(['press ZL+R 100', 'stick up-right 250', 'push -12345 23456 200',
                           'raw 0:LUP/200,0.1:A/100,0.3:LX=123+LY=-456/50 0.6', 'cheat 1', 'shot', 'end'])
        ex.run()
        inputs = [r for r in sim.bridge.requests if r['cmd'] in ('press', 'stick', 'sequence')]
        check([r['cmd'] for r in inputs] == ['press', 'stick', 'stick', 'sequence'],
              'press, stick, push and raw choose the bridge input commands')
        check(inputs[0]['buttons'] == 'ZL+R' and inputs[0]['frames'] == 6 and inputs[1]['x'] == inputs[1]['y'] == 1 and
              inputs[1]['frames'] == 15 and round(inputs[2]['x'] * 32767) == -12345 and
              round(inputs[2]['y'] * 32767) == 23456, 'button holds, directional sticks and exact integer pushes survive conversion')
        check(inputs[3]['units'] == 'hos' and inputs[3]['script'] == '0:LUP/200,0.1:A/100,0.3:LX=123+LY=-456/50',
              'raw sequences preserve offsets, durations and analog tokens in hos units')
        steps = [e for e in sim.events() if e['ev'] == 'step']
        check(steps[0]['secs'] >= .4 and steps[1]['secs'] >= .55,
              'discrete steps wait for their frame-completion reply while continuing to poll state')
        check(sim.game.live_in.read_text().splitlines() == ['!cheat 1', '!shots 1'],
              'only debug-only cheats and shots retain live-file delivery')

    with Simulation(boot_events=0) as sim:
        ex = sim.executor()
        check(ex.to_control(), 'control reached before update-gap steering check')
        before = sim.game.n_states
        sim.bridge.updates = False
        result, _ = ex.walk(20, 0, .5, .5)
        check(result == 'timeout' and len(sim.input_samples) <= 1 and sim.game.n_states == before,
              'an update gap cannot manufacture samples or repeatedly steer from a retained snapshot')
        sim.bridge.updates = True
        sim.bridge.event_override = True
        sim.clock.sleep(.2)
        sim.exchange()
        before = len(sim.input_samples)
        result, _ = ex.walk(20, 0, .5, 2)
        check(result == 'held' and len(sim.input_samples) == before, 'an event holds the walk without issuing new inputs')


def main():
    transport_checks()
    executor_checks()
    print('test_hsap: all passed')


if __name__ == '__main__':
    main()
