#!/usr/bin/env python3
"""Checks when the styles that trade a window's old picture for its newest do so: at the
style's hidden moment if the program drew its new size by then, else as soon as it did,
and never a picture the program didn't draw.

Each style runs on its own Xvfb with i3, picom (xrender) and tests/slowclient windows side
by side: a draws each new size 150 ms after it gets it, before the hidden moment; b halfway
between the hidden moment and the end; c at once, often before picom sees the new size; d
into a child window, as wezterm does, and only when it is exposed, so not when it gets
smaller, unless picom asks. A resize by i3-msg gives all a new size (the geometry trigger),
and for flip.conf a swap of a and b, which then differ in size (flip.conf has no c nor d).
var-length is 1 s. Per window, from picom's frame log and the programs' own log of when
they drew:

  hidden  where picom saved the old picture, each frame of the animation draws it before
          the style's hidden moment and the window's image from then on: origami and cube
          at edge on (var-e 0.5), warp at its smallest (var-q 0.5), flip at edge on
          (var-ease 0.5). Tile has no such moment
  waits   the first frame that draws the window's image at its new size starts after the
          program drew that size. For a, c and d, that is the first frame past the hidden
          moment; for b, at most three frames after the first one that starts after its
          draw (picom names the new pixmap without waiting, and binds it when the X server
          answers). picom keeps the old picture until then. Tile draws whichever picture
          covers more of its rectangle (var-picture-best): a window that grows shows its
          new picture at most three frames after the first one that starts after its
          draw; one that shrinks (a smaller picture) keeps its old picture until its move
          ends, and shows the new one in that frame
  empty   reads of the screen (every 8th pixel of every 8th row, as fast as they go),
          each lined up with the frame of the log on the screen then: inside each window's
          picture where it is in front, a point shows the colour the program draws at the
          size of the picture picom draws there (slowclient's colour has the width in it),
          over the magenta backdrop as translucent as the window is: not what lies behind,
          another size's colour nor any other. A read with more than 10 % of its points
          wrong fails: an empty picture is wrong all over, while xrender, where it scales a
          picture, can draw one column of it black, which is not what this checks
  turn    cube: the line a window turns about (var-qcx + var-pu across, var-qcy + var-pv
          down) doesn't jump against the way the window goes (var-dcx, var-dcy) at the
          half of its turn, by more than 2 px: a box that tips over an edge comes down on
          the far side of it

Usage: drawn.py [STYLE ...], all of them by default. Needs PICOM, an i3 on PATH, numpy, and
make all. Holds the display in WC_DISPLAY, :97 by default. Keeps what a style that fails
left in a directory it names, and with WC_KEEP set what all of them left.

  drawn.py capture OUT    reads the screen into OUT while the scene "change" runs
"""

import argparse
import bisect
import collections
import ctypes
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.dont_write_bytecode = True


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LENGTH = 1.0
FAST = 150
BORDER = 2
STEP = 8
MAGENTA = (255, 0, 255)
SIZE = (1280, 720)

# style: (what the rule includes, the variable that passes 0.5 at the hidden moment, the
# style's length in var-length)
STYLES = {
    "origami": ("@include \"win-cause/origami-geometry.conf\"", "var-e", 1.5),
    "cube": ("@include \"win-cause/cube-geometry.conf\"", "var-e", 1.5),
    "warp": ("@include \"win-cause/warp-geometry.conf\"", "var-q", 1.0),
    "flip": ("animations = ({\n    @include \"win-cause/flip.conf\"\n  });", "var-ease", 1.0),
    "tile": ("@include \"win-cause/tile-geometry.conf\"", None, 1.0),
}
# The styles that draw whichever picture covers more: the newest of a window that grows as
# soon as the program drew it, and that of one that shrinks where its move ends.
BEST = {"tile"}
# The styles whose windows turn about a line (see turn).
TURNS = {"cube"}

PICOM_CONFIG = """plugins = ["{plugin}"];
vsync = true;
animation-triggers = ["flip"];
rules = ({{
  match = "!override_redirect && window_type = 'normal'";
  {rule}
}});
"""


def colour(width):
    rgb = 0x206080 + (width & 0xFF)
    return (rgb >> 16 & 0xFF, rgb >> 8 & 0xFF, rgb & 0xFF)


def capture(out):
    screen = load("screen")
    x11, d = screen.connect()
    root = x11.XDefaultRootWindow(d)
    width, height = x11.XDisplayWidth(d, 0), x11.XDisplayHeight(d, 0)
    z_pixmap = 2
    # A magenta window under all others, as screen.py's backdrop, for as long as this runs.
    backdrop = x11.XCreateSimpleWindow(d, root, 0, 0, width, height, 0, 0, 0xFF00FF)
    cw_override_redirect = 1 << 9
    x11.XChangeWindowAttributes(d, backdrop, cw_override_redirect,
                                ctypes.byref(screen.XSetWindowAttributes(override_redirect=1)))
    x11.XMapWindow(d, backdrop)
    x11.XLowerWindow(d, backdrop)
    x11.XSync(d, 0)
    marks = f"{out}/marks"
    with open(f"{out}/reads.times", "w") as times, open(f"{out}/reads.bin", "wb") as data:
        while True:
            try:
                with open(marks) as f:
                    labels = [l.rstrip("\n").split(",", 2)[2] for l in f if l.startswith("M,")]
            except FileNotFoundError:
                labels = []
            if labels and labels[-1] == "end":
                return
            if not labels or labels[-1] != "change":
                time.sleep(0.01)
                continue
            t0 = time.monotonic_ns() // 1000
            image = x11.XGetImage(d, root, 0, 0, width, height, 0xFFFFFFFF, z_pixmap)
            t1 = time.monotonic_ns() // 1000
            line = image.contents.bytes_per_line
            a = np.ctypeslib.as_array(image.contents.data, shape=(height * line,))
            a = a.reshape(height, line)[::STEP, :width * 4].reshape(-1, width, 4)
            data.write(np.ascontiguousarray(a[:, ::STEP, 2::-1]).tobytes())
            x11.XDestroyImage(image)
            times.write(f"{t0},{t1}\n")
            times.flush()


def read_log(out):
    """Records of the frame log: A, and by frame R, Z, and W, I, E by window."""
    R, Z, A = {}, {}, []
    W, I, E = (collections.defaultdict(dict) for _ in range(3))
    with open(f"{out}/frames.csv") as f:
        for line in f:
            if not line.endswith("\n"):
                break
            p = line.rstrip("\n").split(",")
            if p[0] == "R":
                R[int(p[1])] = (int(p[3]), int(p[6]), int(p[7]))
            elif p[0] == "A":
                A.append((int(p[1]), p[2], p[3]))
            elif p[0] == "W":
                W[int(p[1])][p[2]] = p
            elif p[0] == "I":
                I[int(p[1])][p[2]] = p
            elif p[0] == "E":
                E[int(p[1])][p[2]] = dict(kv.split("=", 1) for kv in p[5:] if "=" in kv)
                E[int(p[1])][p[2]]["ended"] = float(p[3]) >= float(p[4])
            elif p[0] == "Z":
                Z[int(p[1])] = p[2:]
    return {"R": R, "Z": Z, "A": A, "W": W, "I": I, "E": E}


def names_of(out):
    """{frame window as in the log: a, b, c or d}, from picom's log."""
    names = {}
    for window, name in re.findall(r"(0x[0-9a-f]{8}) \(([a-d])\)", open(f"{out}/picom.log").read()):
        names.setdefault(window, name)
    return names


def draws_of(out):
    """{name: [(frame width, frame height, time)]} of what the programs drew."""
    draws = collections.defaultdict(list)
    for line in open(f"{out}/draws"):
        _, name, w, h, t = line.split()
        draws[name].append((int(w) + 2 * BORDER, int(h) + 2 * BORDER, int(t)))
    return draws


def picture(wr, ir):
    """Where a window's picture is drawn, as a polygon, and the width of the client it was
    drawn at: through its corners, or, drawn flat, its image's box or its saved image's."""
    corners = [float(v) for v in wr[8:16]]
    tl, tr, bl, br = (corners[k:k + 2] for k in range(0, 8, 2))
    x, y, w, h, iw, ih = (float(v) for v in ir[3:9])
    saved = ir[23] == "saved"
    sw, sh = float(ir[24]), float(ir[25])
    flat = (abs(tl[0] - x) < 1 and abs(tl[1] - y) < 1 and abs(br[0] - x - w) < 1 and
            abs(br[1] - y - h) < 1 and abs(tr[1] - tl[1]) < 1 and abs(bl[0] - tl[0]) < 1)
    if flat and saved:
        w, h = sw * w / iw, sh * h / ih
    if flat:
        polygon = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    else:
        polygon = [tuple(tl), tuple(tr), tuple(br), tuple(bl)]
    if "inf" not in wr[17:21]:
        cx, cy, cw, ch = (float(v) for v in wr[17:21])
        crop = (cx, cy, cx + cw, cy + ch)
    else:
        crop = None
    return polygon, crop, int(sw if saved else iw) - 2 * BORDER


def inside(polygon, crop, xs, ys, scale=1.0, inset=0):
    cx = sum(p[0] for p in polygon) / 4
    cy = sum(p[1] for p in polygon) / 4
    pts = [(cx + (px - cx) * scale, cy + (py - cy) * scale) for px, py in polygon]
    signs = []
    for k in range(4):
        (x0, y0), (x1, y1) = pts[k], pts[(k + 1) % 4]
        signs.append((x1 - x0) * (ys - y0) - (y1 - y0) * (xs - x0))
    mask = np.all([s >= 0 for s in signs], axis=0) | np.all([s <= 0 for s in signs], axis=0)
    if crop is not None:
        x0, y0, x1, y1 = crop
        mask &= (xs >= x0 + inset) & (xs < x1 - inset) & (ys >= y0 + inset) & (ys < y1 - inset)
    return mask


def judge(log, frame, names, image, xs, ys):
    """{name: (points, wrong)} for the windows a, b, c and d in `frame` against a read."""
    W, I = log["W"][frame], log["I"][frame]
    order = log["Z"].get(frame, list(W))
    drawn = [w for w in order if w in W and W[w][3] == "D" and w in I]
    regions = {w: picture(W[w], I[w]) for w in drawn}
    result = {}
    for k, w in enumerate(drawn):
        name = names.get(w)
        if name is None:
            continue
        polygon, crop, client_width = regions[w]
        # Away from the edges, where i3's frame is, and the picture's own edges.
        mask = inside(polygon, crop, xs, ys, 0.85, 4)
        for above in drawn[k + 1:]:
            mask &= ~inside(regions[above][0], regions[above][1], xs, ys)
        points = int(mask.sum())
        if points < 20:
            continue
        # The program's colour at that size, over the backdrop as translucent as the window.
        opacity = float(W[w][16])
        want = opacity * np.array(colour(client_width)) + (1 - opacity) * np.array(MAGENTA)
        wrong = ((image - want) ** 2).sum(axis=2) > 12 ** 2
        result[name] = (points, int((mask & wrong).sum()))
    return result


def check(out, style):
    hidden_var = STYLES[style][1]
    log = read_log(out)
    names = {w: n for w, n in names_of(out).items() if any(w in W for W in log["W"].values())}
    draws = draws_of(out)
    marks = [(int(l.split(",")[1]), l.rstrip("\n").split(",", 2)[2])
             for l in open(f"{out}/marks") if l.startswith("M,")]
    change = next(t for t, label in marks if label == "change")
    R, W, I, E = log["R"], log["W"], log["I"], log["E"]
    frames = sorted(R)
    failures = []
    for window, name in sorted(names.items(), key=lambda kv: kv[1]):
        starts = [t for t, w, trigger in log["A"] if w == window and t >= change and
                  trigger in ("size", "position", "flip")]
        if not starts:
            failures.append(f"{name}: no animation started")
            continue
        start = starts[0]
        anim = [n for n in frames if R[n][0] >= start and window in E[n] and window in I[n]]
        if not anim:
            failures.append(f"{name}: no frame of its animation")
            continue
        new = tuple(int(v) for v in W[anim[-1]][window][6:8])
        drew = [t for w, h, t in draws[name] if (w, h) == new and t >= change]
        if not drew:
            failures.append(f"{name}: never drew its new size {new[0]}x{new[1]}")
            continue
        t_draw = drew[0]
        line = f"{style} {name}: {new[0]}x{new[1]} drawn at {(t_draw - start) / 1e3:.0f} ms"
        hidden_at = None
        if hidden_var is not None:
            past = [n for n in anim if float(E[n][window].get(hidden_var, "nan")) >= 0.5]
            hidden_at = past[0] if past else None
            wrong = []
            for n in anim:
                ir = I[n][window]
                if float(ir[24]) <= 0 or W[n][window][3] != "D":
                    continue
                want = "image" if float(E[n][window][hidden_var]) >= 0.5 else "saved"
                if ir[23] != want:
                    wrong.append((n, ir[23], want))
            if wrong:
                n, got, want = wrong[0]
                failures.append(f"{name}: {len(wrong)} frames draw the wrong picture for the "
                                f"hidden moment, first frame {n} at "
                                f"{(R[n][0] - start) / 1e3:.0f} ms: {got}, not {want}")
            if hidden_at is not None:
                line += f", hidden moment at {(R[hidden_at][0] - start) / 1e3:.0f} ms"
        shown = [n for n in frames if R[n][0] >= start and window in I[n] and
                 I[n][window][23] == "image" and
                 (int(I[n][window][7]), int(I[n][window][8])) == new]
        if not shown:
            failures.append(f"{name}: its new picture never shows")
            print(line)
            continue
        first = shown[0]
        line += f", new picture from {(R[first][0] - start) / 1e3:.0f} ms"
        print(line)
        if R[first][0] < t_draw:
            failures.append(f"{name}: frame {first} draws its new size "
                            f"{(t_draw - R[first][0]) / 1e3:.0f} ms before it drew it")
        elif hidden_at is not None and t_draw < R[hidden_at][0]:
            if first != hidden_at:
                failures.append(f"{name}: drew before the hidden moment, but its new picture "
                                f"shows from frame {first}, not {hidden_at}")
        elif style in BEST and shrinks(I[anim[0]][window], new) and \
                t_draw < R[ended(E, anim, window)][0]:
            end = ended(E, anim, window)
            if first != end:
                failures.append(f"{name}: shrinks, but its new picture shows from frame "
                                f"{first}, not {end}, where its move ends")
        elif hidden_at is not None or style in BEST:
            after = next(k for k, n in enumerate(frames) if R[n][0] >= t_draw)
            if frames.index(first) - after > 3:
                failures.append(f"{name}: its new picture shows "
                                f"{frames.index(first) - after} frames after it drew it")
    if style in TURNS:
        failures += check_turn(log, names, change)
    failures += check_reads(out, log, names, style)
    return failures


def shrinks(first, new):
    """Whether a window's new size is smaller than its picture where its animation starts
    (the I record `first`): its old picture covers more of its rectangle all the way."""
    old = (int(first[24]), int(first[25])) if int(first[24]) > 0 else (int(first[7]), int(first[8]))
    return new[0] * new[1] < old[0] * old[1]


def ended(E, anim, window):
    """The frame of `anim` in which the animation of `window` ends: the last, or the first
    whose time has run out."""
    return next((n for n in anim if E[n][window]["ended"]), anim[-1])


def check_turn(log, names, change):
    R, E = log["R"], log["E"]
    frames = sorted(R)
    failures = []
    for window, name in sorted(names.items(), key=lambda kv: kv[1]):
        starts = [t for t, w, trigger in log["A"] if w == window and t >= change]
        if not starts:
            continue
        anim = [n for n in frames if R[n][0] >= starts[0] and "var-h2" in E[n].get(window, {})]
        before = [n for n in anim if float(E[n][window]["var-h2"]) < 0.5]
        after = [n for n in anim if float(E[n][window]["var-h2"]) >= 0.5]
        if not before or not after:
            continue
        a, b = E[before[-1]][window], E[after[0]][window]
        for axis, centre, pivot, way in (("across", "var-qcx", "var-pu", "var-dcx"),
                                         ("down", "var-qcy", "var-pv", "var-dcy")):
            go = float(a[way])
            jump = (float(b[centre]) + float(b[pivot])) - (float(a[centre]) + float(a[pivot]))
            if abs(go) > 1 and jump * go < 0 and abs(jump) > 2:
                failures.append(f"{name}: the line it turns about jumps {abs(jump):.0f} px "
                                f"against its way {axis} at the half of its turn")
    return failures


def check_reads(out, log, names, style):
    times = [tuple(int(v) for v in l.split(",")) for l in open(f"{out}/reads.times")]
    cols, rows = (SIZE[0] + STEP - 1) // STEP, (SIZE[1] + STEP - 1) // STEP
    data = np.fromfile(f"{out}/reads.bin", np.uint8)
    reads = data[:len(times) * rows * cols * 3].reshape(-1, rows, cols, 3).astype(np.int32)
    ys, xs = np.mgrid[0:rows, 0:cols]
    ys, xs = ys * STEP + 0.5, xs * STEP + 0.5
    R = log["R"]
    frames = sorted(R)
    calls = [R[n][1] for n in frames]
    returns = [R[n][2] for n in frames]
    totals = collections.defaultdict(lambda: [0, 0, 0, 0.0])
    failures = []
    for i, (t0, t1) in enumerate(times[:len(reads)]):
        first = bisect.bisect_right(returns, t0) - 1
        last = bisect.bisect_right(calls, t1) - 1
        if first < 0:
            continue
        best = None
        for k in range(first, max(first, last) + 1):
            result = judge(log, frames[k], names, reads[i], xs, ys)
            wrong = sum(bad for _, bad in result.values())
            if best is None or wrong < best[0]:
                best = (wrong, frames[k], result)
        _, frame, result = best
        for name, (points, bad) in result.items():
            row = totals[name]
            row[0] += 1
            row[1] += points
            row[2] += bad
            share = bad / points
            if share > 0.1:
                row[3] = max(row[3], share)
                failures.append(f"{name}: read {i} (frame {frame}) has {100 * share:.0f} % of "
                                f"its {points} points wrong")
    for name, (count, points, bad, worst) in sorted(totals.items()):
        print(f"{style} {name}: {count} reads, {points} points, {bad} wrong")
    if not totals:
        failures.append("no read showed a window")
    return failures[:5]


def run_style(style, out):
    scene = load("scene")
    rule, _, factor = STYLES[style]
    conf = f"{out}/conf"
    shutil.copytree(f"{ROOT}/styles", f"{conf}/win-cause")
    part = f"{conf}/win-cause/parts/tile.conf"
    with open(part) as f:
        text = re.sub(r"^var-length = .*;", f"var-length = {LENGTH};", f.read(), flags=re.M)
    with open(part, "w") as f:
        f.write(text)
    with open(f"{conf}/picom.conf", "w") as f:
        f.write(PICOM_CONFIG.format(plugin=f"{ROOT}/build/win-cause.so", rule=rule))
    slow = round(1000 * LENGTH * factor * 0.75)
    setup = [f"exec:({sys.executable} {os.path.abspath(__file__)} capture $SCENE_OUT "
             ">/dev/null 2>&1 &)"]
    if style == "flip":
        setup += ['i3:[title="^a$"] resize set width 65 ppt', 'i3:[title="^b$"] mark swapb']
        change = (f"exec:{ROOT}/build/win-cause run flip -- "
                  "i3-msg '[title=\"^a$\"] swap container with mark swapb' >/dev/null")
    else:
        change = 'i3:[title="^a$"] resize set width 65 ppt'
    os.environ["SLOWCLIENT_LOG"] = f"{out}/draws"
    open(f"{out}/draws", "w").close()
    a = argparse.Namespace(
        size=f"{SIZE[0]}x{SIZE[1]}", gaps=10, i3=[], config=f"{conf}/picom.conf",
        length=LENGTH, backend="xrender", settle=0.6, rounds=1, wait=3.0,
        program=[f"a=slow:{FAST}", f"b=slow:{slow}"] +
        (["c=slow:0", "d=slow:0:child"] if style != "flip" else []),
        name=["setup", "change"],
        scene=[",".join(setup), change])
    scene.Scene(out, a).run()


def main():
    if sys.argv[1:2] == ["capture"]:
        capture(sys.argv[2])
        return 0
    styles = sys.argv[1:] or list(STYLES)
    base = tempfile.mkdtemp(prefix="win-cause-drawn-", dir=os.environ.get("XDG_RUNTIME_DIR"))
    failed = []
    for style in styles:
        out = f"{base}/{style}"
        os.makedirs(out)
        run_style(style, out)
        failures = check(out, style)
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
