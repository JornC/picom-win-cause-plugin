#!/usr/bin/env python3
"""Checks that the windows a fullscreen window covers and uncovers go nowhere.

Each style runs on its own Xvfb with i3, picom (xrender) and three xterms side by side,
a | b | c, next to a rule for "show" and "hide" as a workspace switch style has it
outside a switch: a fade of 0.1 s, keeping a hidden window's image. b goes fullscreen,
which hides a and c, and comes back, which shows them again, twice; then it goes
fullscreen and closes, which shows them again where they take its place. var-length is
0.5 s. From picom's frame log, in every frame of each of b's moved-by-fullscreen
animations and of its close animation:

  covered    a and c are drawn as they are at rest: at full opacity, in place, and not
             cut, also while i3 hides them, until b's animation ends
  front      b is drawn in front of each of them that it overlaps

It prints the last triggers of a and c, from picom's log.

Usage: covered.py [STYLE ...], all styles by default. Needs PICOM, an i3 on PATH, and make
all. Holds the display in WC_DISPLAY, :97 by default. Keeps what a style that fails left in
a directory it names, and with WC_KEEP set what all of them left.
"""

import argparse
import collections
import importlib.util
import math
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.dont_write_bytecode = True

LENGTH = 0.5
STYLES = ["tile"]

PICOM_CONFIG = """plugins = ["{plugin}"];
vsync = true;
rules = ({{
  match = "!override_redirect && window_type = 'normal'";
  keep-image-when-hidden = true;
  animations = ({{
    triggers = ["show"];
    opacity = {{ duration = 0.1; start = 0; end = "window-raw-opacity"; }};
  }}, {{
    triggers = ["hide"];
    opacity = {{ duration = 0.1; start = "window-raw-opacity-before"; end = 0; }};
  }});
}}, {{
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
        f.write(PICOM_CONFIG.format(plugin=f"{ROOT}/build/win-cause.so", style=style))
    a = argparse.Namespace(
        size="1280x720", gaps=10, i3=[], config=f"{conf}/picom.conf", length=LENGTH,
        backend="xrender", settle=0.6, rounds=1, wait=1.5,
        program=["a=xterm", "b=xterm", "c=xterm"],
        name=["in", "out", "in", "out", "in", "close"],
        scene=['i3:[title="^b$"] fullscreen enable', 'i3:[title="^b$"] fullscreen disable'] * 2 +
        ['i3:[title="^b$"] fullscreen enable', "close:b"])
    scene.Scene(out, a).run()


def read(out):
    """The frames of the log, in order: per frame its W records by window, the order it
    draws them in, its E records, and the windows that fill or have a backdrop; and the
    frames each animation that starts begins in, with its trigger."""
    frames = collections.OrderedDict()
    starts = []
    pending = []
    current = {"W": {}, "order": [], "E": {}, "filled": set()}
    with open(f"{out}/frames.csv") as f:
        for line in f:
            if not line.endswith("\n"):
                break
            p = line.rstrip("\n").split(",")
            if p[0] == "A":
                pending.append((p[2], p[3]))
            elif p[0] == "W":
                current["W"][p[2]] = p
                current["order"].append(p[2])
            elif p[0] == "Z":
                current["Z"] = p[2:]
            elif p[0] == "E":
                current["E"][p[2]] = (float(p[3]), float(p[4]))
            elif (p[0] == "I" and float(p[9]) > 0) or (p[0] == "U" and float(p[3]) > 0):
                current["filled"].add(p[2])
            elif p[0] == "R":
                frame = int(p[1])
                if "Z" in current:
                    current["order"] = current["Z"]
                frames[frame] = current
                starts += [(frame, window, trigger) for window, trigger in pending]
                pending = []
                current = {"W": {}, "order": [], "E": {}, "filled": set()}
    return frames, starts


def check(out):
    names = {}
    with open(f"{out}/picom.log") as f:
        for window, name in re.findall(r"(0x[0-9a-f]{8}) \(([abc])\)", f.read()):
            names[name] = window
    if set(names) != {"a", "b", "c"}:
        return [f"picom's log doesn't name a, b and c: {names}"]
    frames, starts = read(out)
    failures = []
    order = list(frames)
    moves = [frame for frame, window, trigger in starts
             if window == names["b"] and trigger == "moved-by-fullscreen"]
    if len(moves) != 5:
        failures.append(f"b started moved-by-fullscreen {len(moves)} times, not 5")
    closes = [frame for frame, window, trigger in starts
              if window == names["b"] and trigger == "close"]
    if len(closes) != 1:
        failures.append(f"b started close {len(closes)} times, not once")
    moves += closes
    for start in moves:
        at = order.index(start)
        while at < len(order) and names["b"] in frames[order[at]]["E"]:
            frame = frames[order[at]]
            for name in "ac":
                failures += covered(order[at], frame, name, names[name])
            drawn = [w for w in frame["order"] if frame["W"].get(w, ["", "", "", ""])[3] == "D"]
            for n in "ac":
                if (names["b"] in drawn and names[n] in drawn and
                        drawn.index(names[n]) > drawn.index(names["b"]) and
                        overlap(frame, names["b"], names[n])):
                    failures.append(f"frame {order[at]}: b is drawn behind {n}")
            at += 1
        if at >= len(order):
            failures.append(f"b's animation from frame {start} doesn't end in the log")
    triggers = collections.defaultdict(list)
    for frame, window, trigger in starts:
        for name, w in names.items():
            if w == window:
                triggers[name].append(trigger)
    print(f"  triggers: a {triggers['a'][-5:]}, c {triggers['c'][-5:]}")
    return failures


def box(frame, window):
    """Around what a window draws in a frame, as picom's keep-stacking has it: its
    corners, and its crop box if it fills it or has a backdrop there, cut to the crop
    box."""
    w = frame["W"][window]
    xs, ys = [float(v) for v in w[8:16:2]], [float(v) for v in w[9:16:2]]
    b = [min(xs), min(ys), max(xs), max(ys)]
    crop = [float(v) for v in w[17:21]]
    crop = [crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3]]
    if window in frame["filled"]:
        b = [min(b[0], crop[0]), min(b[1], crop[1]), max(b[2], crop[2]), max(b[3], crop[3])]
    return [max(b[0], crop[0]), max(b[1], crop[1]), min(b[2], crop[2]), min(b[3], crop[3])]


def overlap(frame, window, other):
    a, b = box(frame, window), box(frame, other)
    return (min(a[2], b[2]) - max(a[0], b[0]) > 0.5 and
            min(a[3], b[3]) - max(a[1], b[1]) > 0.5)


def covered(number, frame, name, window):
    w = frame["W"].get(window)
    if w is None or w[3] != "D":
        return [f"frame {number}: {name} is not drawn"]
    x, y, width, height = (float(v) for v in w[4:8])
    corners = [float(v) for v in w[8:16]]
    want = [x, y, x + width, y, x, y + height, x + width, y + height]
    failures = []
    if max(abs(c - v) for c, v in zip(corners, want)) > 0.5:
        failures.append(f"frame {number}: {name} is drawn at {corners}, not in place")
    if float(w[16]) < 0.999:
        failures.append(f"frame {number}: {name} is drawn at opacity {w[16]}")
    crop = [float(v) for v in w[17:21]]
    if all(math.isfinite(v) for v in crop) and (
            crop[0] > x + 0.5 or crop[1] > y + 0.5 or crop[0] + crop[2] < x + width - 0.5 or
            crop[1] + crop[3] < y + height - 0.5):
        failures.append(f"frame {number}: {name} is cut to {crop}")
    return failures


def main():
    styles = sys.argv[1:] or STYLES
    base = tempfile.mkdtemp(prefix="win-cause-covered-", dir=os.environ.get("XDG_RUNTIME_DIR"))
    failed = []
    for style in styles:
        out = f"{base}/{style}"
        os.makedirs(out)
        print(style)
        run_style(style, out)
        failures = check(out)
        for text in failures[:12]:
            print(f"FAIL {style} {text}")
        if len(failures) > 12:
            print(f"FAIL {style} and {len(failures) - 12} more")
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
