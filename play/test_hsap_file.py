#!/usr/bin/env python3
"""Offline checks of hsap_file.py's step executor against a simulated game (no GPU, no game files): the live input lines it
writes are read back as stick and button presses, Link moves at 4 m/s where the stick points relative to a camera that
follows him, and state lines come ten times a second as the state tap writes them.

    python3 -I test_hsap_file.py
"""
import json
import math
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hsap_file as hsap  # noqa: E402  (HSAP_TEST_MODULE=<file> tests another copy of the file driver)
if __import__('os').environ.get('HSAP_TEST_MODULE'):
    import importlib.util
    _spec = importlib.util.spec_from_file_location('hsap', __import__('os').environ['HSAP_TEST_MODULE'])
    hsap = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(hsap)

AXES = {'LUP': (0, 1), 'LDOWN': (0, -1), 'LLEFT': (-1, 0), 'LRIGHT': (1, 0)}


class FakeGame:
    """A world with y up. handed=+1: the stick's right moves Link toward the camera's yaw - 90 (right-handed);
    -1 the mirror. Walls: a list of (x0, z0, x1, z1) boxes he cannot enter."""
    def __init__(self, start=(0.0, 0.0, 0.0), camera=0.0, handed=1, follow=True, walls=(), boot_events=5,
                 flags=None, prompt_at=None):
        self.pos, self.cam, self.handed, self.follow = list(start), camera, handed, follow
        self.walls, self.boot_left = list(walls), boot_events
        self.flags = dict(flags or {})
        self.prompt_at = prompt_at
        self.st, self.n_states, self.log_lines, self.changes = None, 0, [], {}
        self.log_tail = 'started'   # (hsap's boot limit counts from the game's log appearing: the fake has started)
        self.presses, self.sent = [], []
        self.t0 = self.last = time.time()
        self.saved = {}
        self.a_presses = 0

    def alive(self):
        return True

    def send(self, line):
        self.sent.append(line)
        if line.startswith('!cheat 996'):
            self.log_lines.append('cheats: save: manager 0x1, check 1, request taken (cooldown 0)')
            self.saved['slot_00/progress.sav'] = time.time()
            return
        if line.startswith('!'):
            return
        now = time.time()
        for m in re.finditer(r'([\d.]+):([A-Z0-9=+\-]+)(?:/(\d+))?', line):
            start = now + float(m.group(1))
            self.presses.append((start, start + int(m.group(3) or 200) / 1000, m.group(2).split('+')))
            if 'A' in m.group(2).split('+'):
                self.a_presses += 1

    def saves(self):
        return dict(self.saved)

    def blocked(self, x, z):
        return any(x0 <= x <= x1 and z0 <= z <= z1 for x0, z0, x1, z1 in self.walls)

    def poll(self):
        now = time.time()
        new = []
        while self.last + 0.1 <= now:
            self.last += 0.1
            sx = sy = 0
            for a, b, names in self.presses:
                if a <= self.last < b:
                    for n in names:
                        if n in AXES:
                            sx, sy = sx or AXES[n][0], sy or AXES[n][1]
                        elif n.startswith(('LX=', 'LY=')):   # (a part push, input-script-analog's tokens)
                            v = max(-1.0, min(1.0, int(n[3:]) / 32767))
                            if n[1] == 'X':
                                sx = sx or v
                            else:
                                sy = sy or v
            if (sx or sy) and self.boot_left <= 0:
                ang = math.degrees(math.atan2(sx, sy))          # the stick's angle, right positive
                world = math.radians(self.cam - self.handed * ang)
                n = math.hypot(sx, sy)
                nx = self.pos[0] + 0.4 * math.sin(world) * (1 if n else 0)
                nz = self.pos[2] + 0.4 * math.cos(world) * (1 if n else 0)
                if not self.blocked(nx, nz):
                    if self.follow:   # the camera turns a little toward his heading, as a follow camera does
                        self.cam += 0.1 * hsap.wrap(math.degrees(world) - self.cam)
                    self.pos[0], self.pos[2] = nx, nz
            self.boot_left -= 1
            prompt = 'Empty'
            if self.prompt_at and math.hypot(self.pos[0] - self.prompt_at[0], self.pos[2] - self.prompt_at[1]) < 1.0:
                prompt = 'Crawl'
            s = {'t': self.last - self.t0, 'frame': int((self.last - self.t0) * 60), 'map': 'Test_Map',
                 'pos': [round(v, 2) for v in self.pos], 'facing': 0.0, 'camera': round(self.cam, 1),
                 'event': 1 if self.boot_left > 0 else 0, 'loading': 0, 'child': 1, 'life': 12, 'max_life': 12,
                 'rupees': 0, 'prompt': prompt, 'music': 'BGM_Test', 'flags': dict(self.flags), 'changed': {}}
            new.append(s)
        if new:
            self.st = new[-1]
            self.n_states += len(new)
        return new


def check(cond, what):
    if not cond:
        raise SystemExit('FAIL: ' + what)
    print('ok:', what)


def executor(g, steps, boot='none'):
    d = Path(tempfile.mkdtemp(prefix='hsap-test-'))
    (d / 'steps.txt').write_text(''.join(s + '\n' for s in steps))
    ex = hsap.Executor(g, d, boot)
    return ex, d


def events(d):
    return [json.loads(x) for x in (d / 'out.jsonl').read_text().splitlines()]


def main():
    # boot: no control while an event plays; gameplay once three seconds of calm lines came
    g = FakeGame(boot_events=8)
    ex, d = executor(g, ['walk 6 8 radius 0.6 timeout 15', 'end'])
    ex.run()
    ev = events(d)
    kinds = [e['ev'] for e in ev if e['ev'] != 'note']
    check(kinds[0] == 'gameplay' and kinds[-1] == 'done', f'gameplay first, done last: {kinds}')
    step = [e for e in ev if e['ev'] == 'step' and e['src'].startswith('walk')][0]
    check(step['result'] == 'reached', f"a walk to a point 10 m away is reached ({step['detail']}, {step['secs']} s)")
    check(step['secs'] < 6, 'at about 4 m/s')
    check(math.hypot(g.pos[0] - 6, g.pos[2] - 8) <= 0.6, 'Link ends within the radius')
    states = [e for e in ev if e['ev'] == 'state' and e['why'] == 'wait']
    check(states and states[-1]['pos'] and 'flags' in states[-1], 'a wait state line after the steps')

    # the mirror world: the handedness is found and flipped
    g = FakeGame(boot_events=1, handed=-1, camera=0.0, follow=False)
    ex, d = executor(g, ['walk 10 0 radius 0.6 timeout 20', 'end'])
    ex.run()
    ev = events(d)
    step = [e for e in ev if e['ev'] == 'step' and e['src'].startswith('walk')][0]
    check(any(e['ev'] == 'note' and 'handedness flipped' in e['text'] for e in ev), 'the stick handedness is flipped')
    check(step['result'] == 'reached', f"and the walk is reached in the mirror world ({step['detail']})")

    # stuck against a wall
    g = FakeGame(boot_events=1, walls=[(-5, 2, 5, 3)])
    ex, d = executor(g, ['walk 0 8 radius 0.5 timeout 15', 'end'])
    ex.run()
    step = [e for e in events(d) if e['ev'] == 'step' and e['src'].startswith('walk')][0]
    check(step['result'] == 'stuck', f"a wall in the way ends the walk stuck ({step['detail']})")

    # until with presses, a flag, the prompt, a save, a bad line
    g = FakeGame(boot_events=1, flags={'Test.Flag': 0}, prompt_at=(0.0, 3.0))
    ex, d = executor(g, ['until flag Test.Flag=1 timeout 1.5 press A every 0.3', 'walk 0 3 radius 0.3 timeout 5',
                         'until prompt=Crawl timeout 2', 'save timeout 5', 'fly 1 2', 'end'])
    ex.run()
    ev = events(d)
    st = [e for e in ev if e['ev'] == 'step']
    check(st[0]['result'] == 'timeout' and g.a_presses >= 4, f'until times out and pressed A {g.a_presses} times')
    check(st[2]['result'] == 'met', 'until prompt=Crawl is met at the hole')
    check(st[3]['result'] == 'done' and 'slot_00' in st[3]['detail'], f"save: {st[3]['detail']}")
    check(any(e['ev'] == 'bad' and e['src'] == 'fly 1 2' for e in ev), 'an unknown step is answered bad')

    # analog steering (HSAP_ANALOG / --analog): only LX=/LY= presses are sent, and the walk is reached
    if hasattr(hsap.Executor(FakeGame(), tempfile.mkdtemp(prefix='hsap-test-'), 'none'), 'analog'):
        g = FakeGame(boot_events=1, follow=False, camera=30.0)
        ex, d = executor(g, ['walk 7 -3 radius 0.4 timeout 15', 'end'])
        ex.analog = True
        ex.run()
        step = [e for e in events(d) if e['ev'] == 'step' and e['src'].startswith('walk')][0]
        sticks = [s for s in g.sent if not s.startswith('!')]
        check(step['result'] == 'reached' and sticks and all('LX=' in s and 'LUP' not in s for s in sticks),
              f"an analog walk is reached with part pushes only ({step['detail']}, {len(sticks)} presses)")
    print('test_hsap: all passed')


if __name__ == '__main__':
    main()
