#!/usr/bin/env python3
"""Checks that an open's motion starts when the window first has a picture, not when it
is mapped.

Each style runs on its own Xvfb with i3 and picom (xrender). Two tests/slowclient windows
with a transparent first picture open, as wezterm's and Chrome's are: a alone on the
workspace, then b next to it, each drawing its first picture 0.4 s after it is mapped.
From b's map, another plugin (tests/pauser.c) pauses the screen for 0.2 s, as ws-direction
may while it reads the desktop: the change then starts later than win-cause's own hold lets
it. var-length is 0.5 s. From picom's frame log and the programs' own log of when they drew,
in every frame of each window's open animation:

  starts   the animation's time is no more than the time since the window drew, so the
           first frame that can show the window shows the motion's start
  ends     the animation runs to its end, or to a frame or two before it, as the frame
           in which it ends has no record of it
  room     the windows that make room for it (moved-by-open in the pass its open starts)
           don't move before it drew, and run on its clock: in each frame that has both,
           their animations' times are the same

Usage: opens.py [STYLE ...], all styles by default. Needs PICOM, an i3 on PATH, and make
all. Holds the display in WC_DISPLAY, :97 by default. Keeps what a style that fails left
in a directory it names, and with WC_KEEP set what all of them left.
"""

import argparse
import importlib.util
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.dont_write_bytecode = True

LENGTH = 0.5
DELAY = 400
SLACK = 0.02
CLOCK = 0.001
STYLES = ["tile"]

PICOM_CONFIG = """plugins = ["{plugin}", "{pauser}"];
vsync = true;
rules = ({{
  match = "!override_redirect && window_type = 'normal'";
  @include "win-cause/{style}.conf"
}});
"""


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_style(style, out):
    scene = load("scene")
    conf = f"{out}/conf"
    shutil.copytree(f"{ROOT}/styles", f"{conf}/win-cause")
    part = f"{conf}/win-cause/parts/tile.conf"
    with open(part) as f:
        text = re.sub(r"^var-length = .*;", f"var-length = {LENGTH};", f.read(), flags=re.M)
    with open(part, "w") as f:
        f.write(text)
    with open(f"{conf}/picom.conf", "w") as f:
        f.write(PICOM_CONFIG.format(plugin=f"{ROOT}/build/win-cause.so",
                                    pauser=f"{ROOT}/build/pauser.so", style=style))
    os.environ["SLOWCLIENT_LOG"] = f"{out}/drew.log"
    a = argparse.Namespace(
        size="1280x720", gaps=10, i3=[], config=f"{conf}/picom.conf", length=LENGTH,
        backend="xrender", settle=0.6, rounds=1, wait=2.0, program=[],
        name=["a", "b"], scene=[f"open:a=slow:{DELAY}:argb", f"open:b=slow:{DELAY}:argb"])
    scene.Scene(out, a).run()


def read(out):
    """The frames of the log, in order: (start, {window: (elapsed, duration)} of its E
    records, [(window, trigger)] of the animations that start in it)."""
    frames, current, starts = [], {}, []
    with open(f"{out}/frames.csv") as f:
        for line in f:
            if not line.endswith("\n"):
                break
            p = line.rstrip("\n").split(",")
            if p[0] == "A":
                starts.append((p[2], p[3]))
            elif p[0] == "E":
                current[p[2]] = (float(p[3]), float(p[4]))
            elif p[0] == "R":
                frames.append((int(p[3]), current, starts))
                current, starts = {}, []
    return frames


def check(out):
    names = {}
    with open(f"{out}/picom.log") as f:
        for window, name in re.findall(r"(0x[0-9a-f]{8}) \(([ab])\)", f.read()):
            names.setdefault(name, window)
    if set(names) != {"a", "b"}:
        return [f"picom's log doesn't name a and b: {names}"]
    drew = {}
    with open(f"{out}/drew.log") as f:
        for line in f:
            _, name, _, _, time = line.split()
            drew.setdefault(name, int(time))
    log = read(out)
    failures = []
    for name in "ab":
        window = names[name]
        if name not in drew:
            failures.append(f"{name} never drew")
            continue
        first = next((i for i, (_, _, starts) in enumerate(log)
                      if (window, "open") in starts), None)
        if first is None:
            failures.append(f"{name} has no open animation in the log")
            continue
        neighbours = [w for w, trigger in log[first][2] if trigger == "moved-by-open"]
        frames, moves = [], []
        for start, records, starts in log[first:]:
            if any(w == window for w, _ in starts) and start > log[first][0]:
                break
            if window in records:
                frames.append((start, *records[window]))
            moves += [(start, w, *records[w], records.get(window)) for w in neighbours
                      if w in records]
        if not frames:
            failures.append(f"{name} has no open animation in the log")
            continue
        late = [(start, elapsed) for start, elapsed, _ in frames
                if elapsed > max(0.0, (start - drew[name]) / 1e6) + SLACK]
        if late:
            start, elapsed = next(((s, e) for s, e in late if s >= drew[name]), late[0])
            failures.append(f"{name}: a frame {max(0, start - drew[name]) / 1e6:.3f} s after "
                            f"it drew is {elapsed:.3f} s into its open of {frames[0][2]:.3f} s")
        if frames[-1][1] < frames[-1][2] - 0.1:
            failures.append(f"{name}: its open stops at {frames[-1][1]:.3f} s of "
                            f"{frames[-1][2]:.3f} s")
        early = [(start, w, elapsed) for start, w, elapsed, _, _ in moves
                 if elapsed > max(0.0, (start - drew[name]) / 1e6) + SLACK]
        if early:
            start, w, elapsed = early[0]
            failures.append(f"{name}: its neighbour {w} is {elapsed:.3f} s into its move "
                            f"{(start - drew[name]) / 1e6:.3f} s after {name} drew")
        apart = [(w, elapsed, own[0]) for _, w, elapsed, _, own in moves
                 if own is not None and abs(elapsed - own[0]) > CLOCK]
        if apart:
            w, elapsed, own = apart[0]
            failures.append(f"{name}: its neighbour {w} is {elapsed:.3f} s into its move "
                            f"when {name} is {own:.3f} s into its open")
    return failures


def main():
    styles = sys.argv[1:] or STYLES
    base = tempfile.mkdtemp(prefix="win-cause-opens-", dir=os.environ.get("XDG_RUNTIME_DIR"))
    failed = []
    for style in styles:
        out = f"{base}/{style}"
        os.makedirs(out)
        print(style)
        run_style(style, out)
        failures = check(out)
        for text in failures:
            print(f"FAIL {style} {text}")
        if failures:
            failed.append(style)
        elif not os.environ.get("WC_KEEP"):
            shutil.rmtree(out)
    if failed:
        print(f"FAILED: {', '.join(failed)} (kept in {base})")
        return 1
    if os.environ.get("WC_KEEP"):
        print(f"kept in {base}")
        return 0
    shutil.rmtree(base)
    print(f"OK: {len(styles)} styles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
