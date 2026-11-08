#!/usr/bin/env python3
# Unit tests for check-tile.py: a synthetic frame log of the tile style's motion (a window
# that opens next to a full one), which passes, and the same with a jump, a scaled
# picture, a cross-fade, two clocks, a broken seam, a picture that jumps and a frame that
# isn't on the rectangle, which each fail. And two windows that close at once, as the one
# left of them takes both places: cut where it reaches, which passes, and the far one
# shrinking on its own, which leaves part of its place showing what lies behind. An
# animation that takes over with another anchor, its picture staying where it is, passes.
# A gap left unfilled fails, and so does a translucent window drawn more opaque in its
# animation than after it. A translucent window's backdrop that fades in and out passes, and one that goes at once fails. Two
# windows that swap places, one raised halfway: drawn on top at once fails, kept under
# while they overlap passes, and a window in a tile animation may come in front of one in
# none. One that comes in front of a tile as the tile's animation ends is named. The
# style's curved way (var-path 1): two windows that go from side by side to one above the
# other curve around their overlap, which passes, and straight or out of the box around
# their places fails; two that trade places side by side lift to the left of their way by
# var-lift, which passes, and the other way or straight fails; an undo that goes back on
# the side it is on passes, and one that crosses its first way fails. A window that opens with no line, drawn whole and evenly
# scaled out of the middle of its left side, passes; drawn wider than high, or with its
# frame on the rectangle, it fails.
# Needs WS_DIRECTION, the root of a ws-direction tree, for check-frames.py.

import importlib.util
import io
import math
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ct = load("check_tile", os.path.join(HERE, "check-tile.py"))
cf = load("check_frames", os.path.join(os.environ["WS_DIRECTION"], "tests", "check-frames.py"))

MONITOR = [0.0, 0.0, 1000.0, 800.0]
LENGTH = 0.25
A, B, C = "0x00000a", "0x00000b", "0x00000c"


def bezier(p1x, p1y, p2x, p2y):
    def f(x):
        lo, hi = 0.0, 1.0
        for _ in range(60):
            t = (lo + hi) / 2
            bx = 3 * (1 - t) ** 2 * t * p1x + 3 * (1 - t) * t * t * p2x + t ** 3
            lo, hi = (t, hi) if bx < x else (lo, t)
        t = (lo + hi) / 2
        return 3 * (1 - t) ** 2 * t * p1y + 3 * (1 - t) * t * t * p2y + t ** 3
    return f


EASE = bezier(0.42, 0, 0.58, 1)


class Log:
    def __init__(self):
        self.lines = ["# picom frame timing log, see src/frame_timing.h"]
        self.frame = 0

    def add(self, t, windows, started=(), handover=None, order=None):
        """windows: {id: dict(box, picture (x, y, w, h), image (w, h), script, crop)}, from
        the bottom of the stack up; started: ids that start "open", or (id, trigger);
        handover: (id, script) of an animation that one of them interrupts; order: the ids
        in the order the frame draws them, when that isn't the stacking order."""
        self.frame += 1
        if handover is not None:
            w, s = handover
            kv = ",".join(f"{k}={v:.6g}" for k, v in s.items() if k not in ("elapsed", "duration"))
            self.lines.append(f"X,{round(t * 1e6)},{w},{s['elapsed']:g},{s['duration']:g},{kv}")
        for item in started:
            w, trigger = item if isinstance(item, tuple) else (item, "open")
            self.lines.append(f"A,{round(t * 1e6)},{w},{trigger}")
        for w, d in windows.items():
            x, y, bw, bh = d["box"]
            px, py, pw, ph = d.get("picture", d["box"])
            corners = [(px, py), (px + pw, py), (px, py + ph), (px + pw, py + ph)]
            crop = d.get("crop", (math.inf,) * 4)
            fields = ["W", self.frame, w, "D", x, y, bw, bh]
            fields += [f"{v:.2f}" for c in corners for v in c]
            fields += [f"{d.get('opacity', 1):.4f}"]
            fields += [f"{v:.2f}" if math.isfinite(v) else "inf" for v in crop]
            self.lines.append(",".join(str(f) for f in fields))
            s = d.get("script")
            if s is not None:
                kv = ",".join(f"{k}={v:.6g}" for k, v in s.items()
                              if k not in ("elapsed", "duration"))
                self.lines.append(f"E,{self.frame},{w},{s['elapsed']:g},{s['duration']:g},{kv}")
                iw, ih = d.get("image", (pw, ph))
                fill = [s.get(k, 0) for k in ("fill", "fill-red", "fill-green", "fill-blue")]
                frame = [4] * 4 if s.get("frame-on-crop", 0) > 0.5 else [0] * 4
                self.lines.append(",".join(str(v) for v in ["I", self.frame, w, px, py, pw,
                                                            ph, iw, ih] + fill + frame))
                if s.get("backdrop", 0) > 0:
                    self.lines.append(f"U,{self.frame},{w},{s['backdrop']:g},0.1,0.1,0.1")
        if order is not None:
            self.lines.append(f"Z,{self.frame}," + ",".join(order))
        us = round(t * 1e6)
        self.lines.append(f"R,{self.frame},{us},{us},0,0,{us},{us},1")

    def check(self, **options):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("\n".join(self.lines) + "\n")
            path = f.name
        try:
            check = ct.TileCheck(cf, path, [MONITOR], None, 0.05, **options)
            out = io.StringIO()
            with redirect_stdout(out):
                ok = check.run()
        finally:
            os.unlink(path)
        return ok, check, out.getvalue()


def scene(jump=False, scaled=False, blend=False, late=False, curve_b=None, picture_jump=False,
          frame_off=False, b_draws=None):
    """A full-width window A; B opens right of it at t0: A's rectangle shrinks from 1000
    to 500 px wide, B's grows from the line x = 1000 to 500 px wide, on one curve. A draws
    at its new size at t0 + 0.1 s. With `b_draws`, B draws the size its rectangle had
    every 0.1 s, anchored at its left edge ("left") or at its right edge, which stays
    ("still")."""
    log = Log()
    hz = 175
    t0 = 1.0
    times = [t0 - 0.05 + i / hz for i in range(int(0.45 * hz))]
    times = sorted({math.floor(t * 1e3) / 1e3 for t in times})
    start = min(t for t in times if t >= t0)
    start_b = min(t for t in times if t > start) if late else start
    curve_b = curve_b or EASE
    middle = times[len(times) // 2]
    for t in times:
        windows, started = {}, []
        e = t - start
        if e < 0:
            windows[A] = {"box": (0, 0, 1000, 800)}
        elif e < LENGTH:
            ease = EASE(e / LENGTH)
            rw = 1000 + (500 - 1000) * ease + (40 if jump and t == middle else 0)
            iw = 1000 if e < 0.1 else 500
            px = 30 if picture_jump and t == middle else 0
            s = {"elapsed": e, "duration": LENGTH, "var-on": 1, "var-anchor-x": 0,
                 "var-anchor-y": 0, "var-ease": ease, "var-x0": 0, "var-y0": 0,
                 "var-w0": 1000, "var-h0": 800, "var-x1": 0, "var-y1": 0, "var-w1": 500,
                 "var-h1": 800, "scale-x": iw / 500, "scale-y": 1, "offset-x": px,
                 "offset-y": 0, "crop-x": 0, "crop-y": 0, "crop-width": rw,
                 "crop-height": 800, "fill": 1, "fill-red": 0.2, "fill-green": 0.2,
                 "fill-blue": 0.2, "var-ax": px, "var-ay": 0,
                 "frame-on-crop": 0 if frame_off and t == middle else 1}
            if blend and t == middle:
                s["saved-image-blend"] = 0.5
            pw = 900 if scaled and t == middle else iw
            windows[A] = {"box": (0, 0, 500, 800), "picture": (px, 0, pw, 800),
                          "image": (iw, 800), "crop": (0, 0, rw, 800), "script": s}
            if e == 0:
                started.append(A)
        else:
            windows[A] = {"box": (0, 0, 500, 800)}
        eb = t - start_b
        if 0 <= eb < LENGTH:
            ease = curve_b(eb / LENGTH)
            rx, rw = 1000 + (500 - 1000) * ease, 500 * ease
            s = {"elapsed": eb, "duration": LENGTH, "var-on": 1, "var-anchor-x": 0,
                 "var-anchor-y": 0, "var-ease": ease, "var-line": 1, "var-x0": 1000,
                 "var-y0": 0, "var-w0": 0, "var-h0": 800, "var-x1": 500, "var-y1": 0,
                 "var-w1": 500, "var-h1": 800, "scale-x": 1, "scale-y": 1, "offset-x": 0,
                 "offset-y": 0, "crop-x": rx, "crop-y": 0, "crop-width": rw,
                 "crop-height": 800, "var-ax": 500, "var-ay": 0, "frame-on-crop": 1}
            windows[B] = {"box": (500, 0, 500, 800), "crop": (rx, 0, rw, 800), "script": s}
            if b_draws:
                anchor = 1 if b_draws == "still" else 0
                pw = max(1.0, 500 * curve_b(min(1.0, math.floor(eb / 0.1) * 0.1 / LENGTH)))
                px = 1000 - pw if anchor else rx
                s.update({"var-anchor-x": anchor, "var-ax": px + anchor * pw,
                          "scale-x": pw / 500, "offset-x": px - 500, "fill": 1,
                          "fill-red": 0.2, "fill-green": 0.2, "fill-blue": 0.2})
                windows[B].update({"picture": (px, 0, pw, 800), "image": (pw, 800)})
            if eb == 0:
                started.append(B)
        elif eb >= LENGTH:
            windows[B] = {"box": (500, 0, 500, 800)}
        log.add(t, windows, started)
    return log


def close_scene(hole=False, stays=False):
    """A, B and C side by side, a third of the screen each; B and C close at t0, and A
    takes both places, growing from 333 to 1000 px wide. B and C shrink into the line
    where A ends (x = 1000) from where A starts (x = 333), cut to their own places, so A's
    edge cuts them in every frame. With `hole`, C shrinks into its right edge on its own,
    as one with its own line does: A reaches its place only at the end. With `stays`, a
    place that A passed keeps its rectangle, 0 wide, at its own right edge, as the tile
    style leaves it."""
    log = Log()
    hz = 175
    t0 = 1.0
    times = [t0 - 0.05 + i / hz for i in range(int(0.45 * hz))]
    times = sorted({math.floor(t * 1e3) / 1e3 for t in times})
    start = min(t for t in times if t >= t0)
    places = {B: (333, 666), C: (666, 1000)}
    for t in times:
        windows, started = {}, []
        e = t - start
        if e < 0:
            windows[A] = {"box": (0, 0, 333, 800)}
            for w, (lo, hi) in places.items():
                windows[w] = {"box": (lo, 0, hi - lo, 800)}
            log.add(t, windows)
            continue
        if e >= LENGTH:
            windows[A] = {"box": (0, 0, 1000, 800)}
            log.add(t, windows)
            continue
        ease = EASE(e / LENGTH)
        edge = 333 + (1000 - 333) * ease
        common = {"elapsed": e, "duration": LENGTH, "var-on": 1, "var-anchor-x": 0,
                  "var-anchor-y": 0, "var-ease": ease, "scale-x": 1, "scale-y": 1,
                  "offset-x": 0, "offset-y": 0, "frame-on-crop": 1}
        s = dict(common, **{"var-x0": 0, "var-y0": 0, "var-w0": 333, "var-h0": 800,
                            "var-x1": 0, "var-y1": 0, "var-w1": 1000, "var-h1": 800,
                            "crop-x": 0, "crop-y": 0, "crop-width": edge,
                            "crop-height": 800, "var-ax": 0, "var-ay": 0})
        windows[A] = {"box": (0, 0, 1000, 800), "picture": (0, 0, 1000, 800),
                      "crop": (0, 0, edge, 800), "script": s}
        for w, (lo, hi) in places.items():
            if hole and w == C:
                rx, line, fx = lo + (hi - lo) * ease, hi, lo
            else:
                rx, line, fx = max(lo, edge), 1000, 333
                rx = min(rx, hi) if stays else rx
            rw = max(0.0, hi - rx)
            s = dict(common, **{"var-line": 1, "var-x0": lo, "var-y0": 0,
                                "var-w0": hi - lo, "var-h0": 800, "var-x1": line,
                                "var-y1": 0, "var-w1": 0, "var-h1": 800, "var-fx0": fx,
                                "var-fy0": 0, "var-fw0": 1000 - fx, "var-fh0": 800,
                                "crop-x": rx, "crop-y": 0, "crop-width": rw,
                                "crop-height": 800, "var-ax": lo, "var-ay": 0})
            windows[w] = {"box": (lo, 0, hi - lo, 800), "picture": (lo, 0, hi - lo, 800),
                          "crop": (rx, 0, rw, 800), "script": s}
        if e == 0:
            started = [(A, "moved-by-close"), (B, "close"), (C, "close")]
        log.add(t, windows, started)
    return log


def follow_scene(lag=0.0, fill=1, opacity=1.0, rest_opacity=None):
    """A window whose rectangle grows from 400 to 800 px wide, its real size following
    every 0.05 s, ahead of the rectangle, or `lag` s behind it. Its picture is its real
    size, and the rest of the rectangle is filled by `fill`. It is drawn at `opacity` in
    the animation, and at `rest_opacity` (`opacity` by default) after it."""
    rest_opacity = opacity if rest_opacity is None else rest_opacity
    log = Log()
    hz = 100
    t0 = 1.0
    times = [t0 + i / hz for i in range(int(0.3 * hz))]
    for t in times:
        e = t - t0
        if e >= LENGTH:
            log.add(t, {A: {"box": (0, 0, 800, 800), "opacity": rest_opacity}})
            continue
        ease = EASE(e / LENGTH)
        rw = 400 + 400 * ease
        step = math.floor(e / 0.05) * 0.05
        real = 400 + 400 * EASE(min(1.0, max(0.0, step + 0.05 - lag) / LENGTH))
        real = round(real)
        if e >= LENGTH - 0.05:
            real = 800
        s = {"elapsed": e, "duration": LENGTH, "var-on": 1, "var-carried": 0,
             "var-anchor-x": 0, "var-anchor-y": 0, "var-ease": ease, "var-x0": 0, "var-y0": 0,
             "var-w0": 400, "var-h0": 800, "var-x1": 0, "var-y1": 0, "var-w1": 800,
             "var-h1": 800, "scale-x": 1, "scale-y": 1, "offset-x": 0, "offset-y": 0,
             "crop-x": 0, "crop-y": 0, "crop-width": rw, "crop-height": 800, "var-fill": 1,
             "fill": fill, "fill-red": 0.2, "fill-green": 0.2, "fill-blue": 0.2, "var-ax": 0,
             "var-ay": 0, "frame-on-crop": 1}
        log.add(t, {A: {"box": (0, 0, real, 800), "picture": (0, 0, real, 800),
                        "image": (real, 800), "crop": (0, 0, rw, 800), "script": s,
                        "opacity": opacity}},
                [(A, "size")] if e == 0 else ())
    return log


def anchor_scene():
    """A window of 400 px whose rectangle grows from its middle at anchor 0.5, its picture
    standing still; halfway an animation takes over with anchor 1, from where the picture
    is. The point the check follows moves with the anchor; the picture doesn't jump."""
    log = Log()
    hz = 100
    t0 = 1.0
    px = 300

    def script(e, anchor, rx, rw):
        return {"elapsed": e, "duration": LENGTH, "var-on": 1, "var-anchor-x": anchor,
                "var-anchor-y": 0, "var-ease": 0, "scale-x": 1, "scale-y": 1,
                "offset-x": 0, "offset-y": 0, "crop-x": rx, "crop-y": 0, "crop-width": rw,
                "crop-height": 800, "var-ax": px + anchor * 400, "var-ay": 0,
                "frame-on-crop": 1}

    def windows(s, rx, rw):
        return {A: {"box": (px, 0, 400, 800), "picture": (px, 0, 400, 800),
                    "image": (400, 800), "crop": (rx, 0, rw, 800), "script": s}}

    middle = 13
    for i in range(int(0.45 * hz)):
        t = t0 + i / hz
        rw = 2 + 3 * i
        rx = 500 - rw / 2
        if i < middle:
            log.add(t, windows(script(i / hz, 0.5, rx, rw), rx, rw),
                    [(A, "open")] if i == 0 else ())
        elif i == middle:
            log.add(t, windows(script(0, 1, rx, rw), rx, rw), [(A, "size")],
                    handover=(A, script(i / hz, 0.5, rx, rw)))
        else:
            log.add(t, windows(script((i - middle) / hz, 1, rx, rw), rx, rw))
    return log


def backdrop_scene(fade=True, opacity=0.6):
    """A translucent window that moves right by 100 px on the style's curve, with a
    backdrop that fades in over the first quarter of the animation and out over the last,
    or that is there in every frame of it and goes at once when it ends."""
    log = Log()
    hz = 175
    t0 = 1.0
    n = int(LENGTH * hz)
    for i in range(-5, n + 10):
        t = t0 + i / hz
        if 0 <= i < n:
            q = i / n
            ease = EASE(q)
            rx = 100 * ease
            s = {"elapsed": q * LENGTH, "duration": LENGTH, "var-on": 1, "var-anchor-x": 0,
                 "var-anchor-y": 0, "var-ease": ease, "var-x0": 0, "var-y0": 0,
                 "var-w0": 400, "var-h0": 300, "var-x1": 100, "var-y1": 0, "var-w1": 400,
                 "var-h1": 300, "var-ax": rx, "var-ay": 0,
                 "crop-x": rx, "crop-y": 0, "crop-width": 400, "crop-height": 300,
                 "frame-on-crop": 1,
                 "backdrop": min(1, q / 0.25, (1 - q) / 0.25) if fade else 1}
            windows = {A: {"box": (100, 0, 400, 300), "picture": (rx, 0, 400, 300),
                           "script": s, "crop": (rx, 0, 400, 300), "opacity": opacity}}
            log.add(t, windows, [(A, "position")] if i == 0 else ())
        else:
            x = 0 if i < 0 else 100
            log.add(t, {A: {"box": (x, 0, 400, 300), "opacity": opacity}})
    return log


def lone_scene(stretch=1.0, frame_on=False):
    """A window with no line opens alone on the monitor: all of it grows out of the middle
    of its left side, at var-ps of its size on the style's curve. With `stretch`, its
    picture is drawn that much wider than high; with `frame_on`, its frame is on the
    rectangle."""
    log = Log()
    hz = 175
    t0 = 1.0
    n = int(LENGTH * hz)
    for i in range(0, n + 10):
        t = t0 + i / hz
        if i < n:
            q = i / n
            k = EASE(q)
            rect = (0, 400 - 400 * k, 1000 * k, 800 * k)
            s = {"elapsed": q * LENGTH, "duration": LENGTH, "var-on": 1, "var-line": 0,
                 "var-anchor-x": 0, "var-anchor-y": 0.5, "var-ease": k, "var-ps": k,
                 "var-x0": 0, "var-y0": 400, "var-w0": 0, "var-h0": 0, "var-x1": 0,
                 "var-y1": 0, "var-w1": 1000, "var-h1": 800, "var-ax": 0, "var-ay": 400,
                 "scale-x": k * stretch, "scale-y": k, "crop-x": rect[0],
                 "crop-y": rect[1], "crop-width": rect[2], "crop-height": rect[3],
                 "frame-on-crop": 1 if frame_on else 0}
            picture = (0, rect[1], rect[2] * stretch, rect[3])
            log.add(t, {A: {"box": (0, 0, 1000, 800), "picture": picture, "image": (1000, 800),
                            "crop": rect, "script": s}}, [A] if i == 0 else ())
        else:
            log.add(t, {A: {"box": (0, 0, 1000, 800)}})
    return log


def swap_scene(restack=None, b_still=False):
    """A left of B, both 500 px wide, swap places on the style's curve, B on top. With
    `restack`, A is raised: halfway, while they overlap, and drawn on top at once
    ("drawn"), or with B kept on top while they overlap (a Z record, "kept"); or once they
    no longer overlap ("apart"). With `b_still`, B stands still where they meet, in no
    animation."""
    log = Log()
    hz = 175
    t0 = 1.0
    n = int(LENGTH * hz)
    for i in range(-5, n + 10):
        t = t0 + i / hz
        q = min(1, max(0, i / n))
        ease = EASE(q)
        windows = {}
        for w, x0 in ((A, 0), (B, 500)):
            rx = x0 + (500 - 2 * x0) * ease
            if b_still and w == B:
                windows[w] = {"box": (250, 0, 500, 800)}
                continue
            d = {"box": (500 - x0, 0, 500, 800), "picture": (rx, 0, 500, 800)}
            if 0 <= i < n:
                d["script"] = {"elapsed": q * LENGTH, "duration": LENGTH, "var-on": 1,
                               "var-anchor-x": 0, "var-anchor-y": 0, "var-ease": ease,
                               "var-x0": x0, "var-y0": 0, "var-w0": 500, "var-h0": 800,
                               "var-x1": 500 - x0, "var-y1": 0, "var-w1": 500, "var-h1": 800,
                               "var-ax": rx, "var-ay": 0, "crop-x": rx,
                               "crop-y": 0, "crop-width": 500, "crop-height": 800,
                               "frame-on-crop": 1}
                d["crop"] = (rx, 0, 500, 800)
            windows[w] = d
        order = None
        if restack is not None and i >= (n if restack == "apart" else n // 2):
            windows = {B: windows[B], A: windows[A]}
            if restack == "kept" and i < n:
                order = [A, B]
        started = [(A, "position"), (B, "position")] if i == 0 else ()
        log.add(t, windows, started, order=order)
    return log


def way_scene(windows, path=1, lift=0.08, mode=None, undo=None, seams=None):
    """Windows that go from rest at r0 to r1 (x, y, w, h), each edge on a quadratic Bezier
    curve through the edge of its control rect c (left, top, right, bottom), as the style
    does with var-path `path`: windows is [(id, r0, r1, c)]. With `mode` "straight" they
    go straight; with "out" the first one goes 30 px out of the box midway. With `undo`
    (at a share of the time, "chord" or "cross"), the first one turns back to r0 there:
    straight from where it is, or 80 px past that midway, across its first way. `seams`
    (left, top, right, bottom) is what the first one's script says lie on a seam; with
    mode "seam" those of its edges go straight, as the style does."""
    log = Log()
    hz = 175
    t0 = 1.0
    n = int(LENGTH * hz)
    turn = round(undo[0] * n) if undo else None
    held = {}
    for i in range(-3, n + (turn or 0) + 6):
        t = t0 + i / hz
        drawn, started = {}, []
        for k, (w, r0, r1, c) in enumerate(windows):
            back = turn is not None and k == 0 and i >= turn
            q = (i - turn) / n if back else i / n
            if q < 0 or q >= 1:
                drawn[w] = {"box": r0 if q < 0 or back else r1}
                continue
            s = EASE(q)
            if back:
                start, end = held[w], r0
                rect = [x + (y - x) * s for x, y in zip(start, end)]
                if undo[1] == "cross":
                    rect[1] += 320 * s * (1 - s)
            else:
                start, end = r0, r1
                e0 = (r0[0], r0[1], r0[0] + r0[2], r0[1] + r0[3])
                e1 = (r1[0], r1[1], r1[0] + r1[2], r1[1] + r1[3])
                kept = seams if mode == "seam" and k == 0 else (0, 0, 0, 0)
                if mode == "straight":
                    l, tp, r, b = (x + (y - x) * s for x, y in zip(e0, e1))
                else:
                    l, tp, r, b = (x + (y - x) * s if keep else
                                   (1 - s) ** 2 * x + 2 * s * (1 - s) * z + s * s * y
                                   for x, z, y, keep in zip(e0, c, e1, kept))
                if mode == "out" and k == 0:
                    l -= 30 * 4 * s * (1 - s)
                rect = [l, tp, r - l, b - tp]
                held[w] = rect
            script = {"elapsed": q * LENGTH, "duration": LENGTH, "var-on": 1,
                      "var-carried": 1 if back else 0, "var-anchor-x": 0, "var-anchor-y": 0,
                      "var-ease": s, "var-path": path, "var-lift": lift, "var-pside": 1,
                      "var-ax": rect[0], "var-ay": rect[1], "frame-on-crop": 1}
            if seams is not None and k == 0:
                for name, v in zip(("left", "top", "right", "bottom"), seams):
                    script[f"var-seam-{name}"] = v
            for name, v in zip("xywh", start):
                script[f"var-{name}0"] = v
            for name, v in zip("xywh", end):
                script[f"var-{name}1"] = v
            for name, v in zip(("crop-x", "crop-y", "crop-width", "crop-height"), rect):
                script[name] = v
            drawn[w] = {"box": end, "picture": rect, "image": tuple(rect[2:]), "crop": rect,
                        "script": script}
            if q == 0:
                started.append((w, "position"))
        log.add(t, drawn, started)
    return log

H = 800
P = 0.08 * H
# Side by side, then one above the other: the left one goes to the top, around the overlap.
LEFT_TOP = (A, (0, 0, 500, H), (0, 0, 1000, H // 2), (0, 0, 500, H // 2))
RIGHT_BOTTOM = (B, (500, 0, 500, H), (0, H // 2, 1000, H // 2), (500, H // 2, 1000, H))
# Side by side, trading places: no overlap, so the one going right goes up by P midway, the
# one going left down, each shrunk into the box around its places.
GOES_RIGHT = (A, (0, 0, 500, H), (500, 0, 500, H), (250, 0, 750, H - 4 * P))
GOES_LEFT = (B, (500, 0, 500, H), (0, 0, 500, H), (250, 4 * P, 750, H))


def bend_scene(bend=True):
    """A window that grows on both sides, from 200 to 800 px high, and is bent 40 px up
    midway by the curved way, its picture 200 px high anchored at its middle: the point
    that a straight way keeps still, which the bend carries up and back. With no `bend` the
    script says nothing of it, and the picture's top edge going down while the rectangle's
    goes up is only that."""
    log = Log()
    hz = 100
    t0 = 1.0
    for i in range(int(0.3 * hz)):
        t = t0 + i / hz
        e = t - t0
        if e >= LENGTH:
            log.add(t, {A: {"box": (0, 0, 400, 800)}})
            continue
        s = EASE(e / LENGTH)
        k = 2 * s * (1 - s)
        sby, sbh = -80.0, 0.0
        ry = 300 * (1 - s) + k * sby
        rh = 200 + 600 * s + k * sbh
        ay = ry + 0.5 * rh
        script = {"elapsed": e, "duration": LENGTH, "var-on": 1, "var-carried": 0,
                  "var-anchor-x": 0, "var-anchor-y": 0.5, "var-ease": s, "var-x0": 0,
                  "var-y0": 300, "var-w0": 400, "var-h0": 200, "var-x1": 0, "var-y1": 0,
                  "var-w1": 400, "var-h1": 800, "var-ax": 0, "var-ay": ay, "crop-x": 0,
                  "crop-y": ry, "crop-width": 400, "crop-height": rh, "frame-on-crop": 1}
        if bend:
            script.update({"var-pk": k, "var-sbx": 0, "var-sbw": 0, "var-sby": sby, "var-sbh": sbh})
        log.add(t, {A: {"box": (0, 0, 400, 800), "picture": (0, ay - 100, 400, 200),
                        "image": (400, 200), "crop": (0, ry, 400, rh), "script": script}},
                [(A, "size")] if i == 0 else ())
    return log


class TileCheckTest(unittest.TestCase):
    def failed(self, log, **options):
        ok, check, out = log.check(**options)
        return ok, {k for k, v in check.failures.items() if v}, out

    def test_clean(self):
        ok, failed, out = self.failed(scene())
        self.assertTrue(ok, out)
        self.assertIn("ok  seams", out)
        self.assertIn("ok  line: 1 windows", out)

    def test_jump(self):
        ok, failed, out = self.failed(scene(jump=True))
        self.assertIn("rect", failed, out)

    def test_scaled(self):
        ok, failed, out = self.failed(scene(scaled=True))
        # A picture drawn wider for one frame also has its edge go out and back.
        self.assertEqual(failed, {"scale", "edges"}, out)

    def test_lone(self):
        ok, failed, out = self.failed(lone_scene())
        self.assertTrue(ok, out)
        self.assertRegex(out, r"ok  scale: [1-9]\d* pictures drawn")
        ok, failed, out = self.failed(lone_scene(stretch=1.1))
        self.assertIn("scale", failed, out)
        ok, failed, out = self.failed(lone_scene(frame_on=True))
        self.assertEqual(failed, {"frame"}, out)

    def test_blend(self):
        ok, failed, out = self.failed(scene(blend=True))
        self.assertEqual(failed, {"blend"}, out)

    def test_two_clocks(self):
        ok, failed, out = self.failed(scene(late=True))
        self.assertIn("clock", failed, out)

    def test_seam(self):
        ok, failed, out = self.failed(scene(curve_b=lambda x: x * x * (3 - 2 * x)))
        self.assertIn("seams", failed, out)
        self.assertNotIn("rect", failed, out)

    def test_frame_off(self):
        ok, failed, out = self.failed(scene(frame_off=True))
        self.assertEqual(failed, {"frame"}, out)

    def test_close_together(self):
        ok, failed, out = self.failed(close_scene())
        self.assertTrue(ok, out)
        self.assertIn("ok  line: 2 windows", out)
        self.assertIn("ok  cover", out)
        ok, failed, out = self.failed(close_scene(stays=True))
        self.assertTrue(ok, out)

    def test_close_with_a_hole(self):
        ok, failed, out = self.failed(close_scene(hole=True))
        self.assertIn("cover", failed, out)

    def test_edges_back_and_forth(self):
        ok, failed, out = self.failed(scene(b_draws="left"))
        self.assertIn("edges", failed, out)

    def test_edges_still(self):
        ok, failed, out = self.failed(scene(b_draws="still"))
        self.assertTrue(ok, out)

    def test_unfilled(self):
        ok, failed, out = self.failed(follow_scene(lag=0.1))
        self.assertNotIn("unfilled", failed, out)
        self.assertRegex(out, r"ok  unfilled: [1-9]\d* rectangles with a gap to fill")
        ok, failed, out = self.failed(follow_scene(lag=0.1, fill=0))
        self.assertIn("unfilled", failed, out)
        self.assertNotIn("fill", failed, out)

    def test_translucent(self):
        ok, failed, out = self.failed(follow_scene(lag=0.1, opacity=0.5, fill=0.7))
        self.assertNotIn("opacity", failed, out)
        self.assertIn("ok  opacity", out)
        ok, failed, out = self.failed(follow_scene(lag=0.1, opacity=1, rest_opacity=0.5))
        self.assertIn("opacity", failed, out)

    def test_backdrop(self):
        ok, failed, out = self.failed(backdrop_scene())
        self.assertNotIn("backdrop", failed, out)
        self.assertRegex(out, r"ok  backdrop: [1-9]\d* frames")
        ok, failed, out = self.failed(backdrop_scene(fade=False))
        self.assertIn("backdrop", failed, out)

    def test_anchor_at_a_handover(self):
        ok, failed, out = self.failed(anchor_scene())
        self.assertNotIn("picture", failed, out)

    def test_stacking(self):
        ok, failed, out = self.failed(swap_scene())
        self.assertNotIn("stacking", failed, out)
        self.assertRegex(out, r"ok  stacking: [1-9]\d* steps")
        ok, failed, out = self.failed(swap_scene(restack="drawn"))
        self.assertIn("stacking", failed, out)
        for restack in ("kept", "apart"):
            ok, failed, out = self.failed(swap_scene(restack=restack))
            self.assertNotIn("stacking", failed, out)
        # A tile may come in front of a window in no animation at once.
        ok, failed, out = self.failed(swap_scene(restack="drawn", b_still=True))
        self.assertNotIn("stacking", failed, out)
        self.assertIn("info  stacking at the end of a move: 0 windows", out)

    def test_stacking_at_the_end(self):
        # B, in no animation, comes in front of A as A's animation ends, still overlapping:
        # named, not failed.
        log = Log()
        s = {"elapsed": 0.2, "duration": LENGTH, "var-on": 1, "crop-x": 0, "crop-y": 0,
             "crop-width": 500, "crop-height": 800, "frame-on-crop": 1}
        log.add(1.0, {B: {"box": (250, 0, 500, 800)},
                      A: {"box": (0, 0, 500, 800), "crop": (0, 0, 500, 800), "script": s}})
        log.add(1.02, {A: {"box": (0, 0, 500, 800)}, B: {"box": (250, 0, 500, 800)}})
        ok, failed, out = self.failed(log)
        self.assertNotIn("stacking", failed, out)
        self.assertIn(f"info  stacking at the end of a move: 1 windows came in front of a tile "
                      f"they overlap as its animation ended, frame 2 {B} in front of {A}", out)

    def test_picture_jump(self):
        ok, failed, out = self.failed(scene(picture_jump=True))
        self.assertIn("picture", failed, out)
        self.assertNotIn("rect", failed, out)


    def test_way_around_the_overlap(self):
        ok, failed, out = self.failed(way_scene([LEFT_TOP, RIGHT_BOTTOM]))
        self.assertEqual(failed & {"box", "path"}, set(), out)
        self.assertIn("ok  path: 2 animations, 2 curved around an overlap", out)
        ok, failed, out = self.failed(way_scene([LEFT_TOP, RIGHT_BOTTOM], mode="straight"))
        self.assertIn("path", failed, out)
        self.assertNotIn("box", failed, out)
        ok, failed, out = self.failed(way_scene([LEFT_TOP, RIGHT_BOTTOM], path=0,
                                                mode="straight"))
        self.assertEqual(failed & {"box", "path"}, set(), out)

    def test_way_out_of_the_box(self):
        ok, failed, out = self.failed(way_scene([LEFT_TOP, RIGHT_BOTTOM], mode="out"))
        self.assertIn("box", failed, out)

    def test_lift(self):
        ok, failed, out = self.failed(way_scene([GOES_RIGHT, GOES_LEFT]))
        self.assertEqual(failed & {"box", "path"}, set(), out)
        self.assertIn(f"2 lifted, peak {P:.0f} of {P:.0f} px", out)
        # Each on the other one's side.
        wrong = [(A, GOES_RIGHT[1], GOES_RIGHT[2], (250, 4 * P, 750, H)),
                 (B, GOES_LEFT[1], GOES_LEFT[2], (250, 0, 750, H - 4 * P))]
        ok, failed, out = self.failed(way_scene(wrong))
        self.assertIn("path", failed, out)
        ok, failed, out = self.failed(way_scene([GOES_RIGHT, GOES_LEFT], mode="straight"))
        self.assertIn("path", failed, out)

    def test_undo_retraces(self):
        ok, failed, out = self.failed(way_scene([GOES_RIGHT], undo=(0.4, "chord")))
        self.assertNotIn("retrace", failed, out)
        self.assertIn("ok  retrace: 1 undos", out)
        ok, failed, out = self.failed(way_scene([GOES_RIGHT], undo=(0.4, "cross")))
        self.assertIn("retrace", failed, out)

    def test_seam_goes_straight(self):
        # The left window's right edge on a seam: it goes straight, the rest curves.
        seam = (0, 0, 1, 0)
        ok, failed, out = self.failed(way_scene([LEFT_TOP, RIGHT_BOTTOM], mode="seam",
                                                seams=seam))
        self.assertNotIn("path", failed, out)
        ok, failed, out = self.failed(way_scene([LEFT_TOP, RIGHT_BOTTOM], seams=seam))
        self.assertIn("path", failed, out)

    def test_edges_with_the_bend(self):
        ok, failed, out = self.failed(bend_scene())
        self.assertNotIn("edges", failed, out)
        ok, failed, out = self.failed(bend_scene(bend=False))
        self.assertIn("edges", failed, out)


if __name__ == "__main__":
    unittest.main()
