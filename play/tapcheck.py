#!/usr/bin/env python3
"""tapcheck: the state tap's lines of one run beside its pictures (PlayHarness3, 2026-11-11; local harness tool).

    tapcheck.py <state.jsonl> [<shots folder>] [--from F] [--to F]

Prints: the runs of (map, event, loading) over the frames; per field the values seen; and for each picture
vk_<present>.png the state line nearest that present (frame = the game's present count, as the picture's name).
"""
import argparse
import json
import math
import re
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('state')
    ap.add_argument('shots', nargs='?')
    ap.add_argument('--from', dest='lo', type=int, default=0)
    ap.add_argument('--to', dest='hi', type=int, default=1 << 62)
    a = ap.parse_args()
    lines = []
    for ln in Path(a.state).read_text().splitlines():
        try:
            s = json.loads(ln)
        except ValueError:
            print('unreadable line:', ln[:120])
            continue
        if a.lo <= s['frame'] <= a.hi:
            lines.append(s)
    assert lines, 'no state lines'
    print(f'{len(lines)} lines, frames {lines[0]["frame"]}..{lines[-1]["frame"]}, '
          f't {lines[0]["t"]:.1f}..{lines[-1]["t"]:.1f} s')
    gaps = [(b['frame'] - a_['frame'], a_['frame']) for a_, b in zip(lines, lines[1:]) if b['frame'] - a_['frame'] > 12]
    print(f'gaps over 12 presents between lines: {len(gaps)}; largest {sorted(gaps)[-5:]}')
    # runs of (map, event, loading, child)
    runs = []
    for s in lines:
        key = (s.get('map'), s.get('event'), s.get('loading'), s.get('child'), s.get('music'))
        if runs and runs[-1][0] == key:
            runs[-1][2] = s['frame']
            runs[-1][3] += 1
            runs[-1][5] = s.get('pos')
        else:
            runs.append([key, s['frame'], s['frame'], 1, s.get('pos'), s.get('pos')])
    print('runs (map, event, loading, child, music): first..last frame, lines, pos first -> last')
    for key, f0, f1, n, p0, p1 in runs:
        print(f'  {key}: {f0}..{f1} ({n}) {p0} -> {p1}')
    for k in ('life', 'max_life', 'rupees', 'prompt', 'facing', 'camera'):
        vals = [s.get(k) for s in lines]
        distinct = sorted({json.dumps(v) for v in vals})
        print(f'{k}: {len(distinct)} distinct: {", ".join(distinct[:12])}{" ..." if len(distinct) > 12 else ""}')
    moved = [(b['frame'], math.dist(a_['pos'], b['pos'])) for a_, b in zip(lines, lines[1:])
             if a_.get('pos') and b.get('pos') and a_.get('map') == b.get('map')]
    print(f'moves between lines: {sum(1 for _, d in moved if d > 0.01)} of {len(moved)} over 1 cm; '
          f'largest {sorted(d for _, d in moved)[-3:] if moved else []}')
    ch = {}
    for s in lines:
        for h, v in (s.get('changed') or {}).items():
            ch.setdefault(h, []).append((s['frame'], v))
    print(f'flags changed: {len(ch)} hashes')
    if a.shots:
        print('pictures:')
        for p in sorted(Path(a.shots).glob('vk_*.png')):
            f = int(re.search(r'vk_(\d+)', p.name).group(1))
            if not (a.lo <= f <= a.hi):
                continue
            s = min(lines, key=lambda s: abs(s['frame'] - f))
            far = abs(s['frame'] - f)
            print(f'  {p.name}: ' + (f'no line within 60 presents (nearest {far})' if far > 60 else
                  f'line @{s["frame"]}: map {s.get("map")} pos {s.get("pos")} facing {s.get("facing")} camera '
                  f'{s.get("camera")} event {s.get("event")} loading {s.get("loading")} life {s.get("life")}/'
                  f'{s.get("max_life")} rupees {s.get("rupees")} prompt {s.get("prompt")} music {s.get("music")}'))


if __name__ == '__main__':
    main()
