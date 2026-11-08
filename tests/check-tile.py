#!/usr/bin/env python3
# Checks the frames picom drew with the tile style (styles/tile.conf), from picom's frame
# timing log with the layout records: PICOM_DEBUG="frame_timing=<path>;frame_layout=1"
# (see src/frame_timing.h in picom). It reads the log and checks its motion with
# ws-direction's tests/check-frames.py, which it imports, and adds what the style
# promises. A tile animation is one whose script has var-on and the crop outputs.
#
#   rect     the rectangle (the crop box of a tile script, and a window's own place where
#            no tile script runs) is continuous: check-frames' jumps, on the rectangle as
#            if it were the window, with its allowances at starts, ends and interruptions
#   pops     a window appears or disappears only where its rectangle's area times its
#            opacity reaches 0 within the step
#   stops    a rectangle that comes to rest arrives without speed
#   drawn    picom crops each window within 1 px of where its script puts the rectangle
#   picture  the picture doesn't jump: the point of it that stays put in the rectangle
#            (var-anchor-x, var-anchor-y: its top left with the defaults) is continuous.
#            The script says where it puts that point (var-ax, var-ay); picom's corners
#            must agree within 1 px, for the picture picom draws: the window's image, or
#            the one it saved when the change started. The rest of the picture changes at
#            once where picom switches between pictures, which the rectangle cuts.
#   edges    within a tile animation, each edge of the picture as it shows (cut to the
#            rectangle) goes the way its rectangle's edge goes, and stays where that edge
#            stays, within 1 px: while the program catches up, no edge goes back and
#            forth. A step that moves with the point the picture is anchored at, as far
#            as the style's curved way (var-path) bends that point off the straight way,
#            is the window's own motion, and counts as none. An edge whose rectangle's
#            edge itself turns (a carried speed) isn't checked, nor is an animation that
#            interrupts one: a size the one before asked for may still land in it
#   scale    the picture is drawn at its own size: in every I record of a window with a
#            tile script, the box it is drawn in has the image's width and height, and a
#            saved picture drawn in its place is drawn at its own size
#            (saved-image-own-size) at the same scale. A window that opens or closes with
#            no line (var-line 0) is drawn whole, at var-ps of its size: the box is var-ps
#            times the image's width and height, so the picture is never stretched
#   blend    no tile script cross-fades: saved-image-blend is 0 or 1, a switch between
#            the window's image and the saved one, and the I record draws one of them
#   clock    the windows that start tile animations together (A records less than
#            --change-gap apart) start in one frame, and in every frame after that, until
#            one is interrupted or ends, they have the same elapsed time and var-ease
#   seams    in such a change, an edge that two windows share at the start and at the end
#            of their rectangles' paths (var-x0..var-h0, var-x1..var-h1) is shared, within
#            half a pixel, in every frame where both are drawn: the gap a window leaves
#            closes as it opens, and nothing overlaps
#   line     a window that opens with a split line (var-line 1) grows from a rectangle
#            that is 0 wide or 0 high and lies within its own; one that closes ends in
#            one, within the rect that shrinks into it where the script has one
#            (var-fx0..var-fh0, which must hold its own rect), else within its own
#   cover    in a change in which windows close on a line, every point of their places
#            (var-x0..var-h0 as their animations start) lies within a drawn window's
#            rectangle in every frame of their animations, or in a gap between them no
#            wider than --cover-gap (i3's gaps): each rectangle counts as half that
#            larger on every side. The windows that take the place reach it as the
#            closing ones leave it, and nothing behind shows
#   fill     where the picture doesn't cover the rectangle, picom fills what the script
#            asks: the I record's fill and colour are the E record's
#   unfilled where the picture doesn't cover the rectangle of a script that fills
#            (var-fill above 0), the fill is drawn: never left at 0 because the window's
#            colour isn't known, which shows what lies behind. Two kinds are only counted:
#            a picture with nothing inside its frame (no colour to know yet), and the first
#            two frames after a window's first picture with something in it (picom works
#            the colour out in a frame or so, without waiting for it)
#   opacity  a tile script draws the window at the opacity it has at rest, so a
#            translucent window stays as translucent and doesn't jump when the animation
#            ends: one that doesn't set opacity keeps the window's opacity in every frame,
#            and where an animation ends on a drawn window, the window's opacity in the
#            next frame is the one in its last
#   frame    the window manager's frame is on the rectangle's edges in every frame of a
#            tile script: the script sets frame-on-crop, and the I record has the frame
#            that picom drew there (all 0 for a window without a frame). A window drawn
#            whole (var-line 0) has its frame in its picture instead, scaled with it: the
#            script doesn't set frame-on-crop, which would scale the frame's sides only
#            along their length
#   box      in a tile animation of a window that moves or changes size from rest, the
#            rectangle stays within the box around where it starts and where it ends
#   path     in such an animation, the rectangle is where the style's way puts it
#            (var-path, var-lift; see styles/parts/tile-rect.conf), within 0.1 px: straight
#            at var-path 0 or var-pass 0, and at 1 straight too where the places neither
#            overlap off the line between their centres nor lie apart. From rest, a window
#            whose places overlap only that way passes on the side of that line the overlap
#            is on, and one whose places don't overlap passes to the left of its way, as far
#            off it as var-lift says midway. An edge on a seam with a neighbour
#            (var-seam-*) goes straight. An animation that interrupts one starts from
#            where that one was, with its speed (var-carry), on the side its script keeps
#   retrace  a change that undoes one it interrupts (it ends where that one started) goes
#            back on the side of that one's straight way that the window is on, within
#            1 px
#   backdrop the backdrop under a window's rectangle (U records), times the window's
#            opacity, changes no faster than from 0 to 1 in 30 ms between two frames in
#            which the window is drawn: it fades in and out, so a translucent window
#            doesn't jump when the backdrop comes or goes. A window that wasn't drawn the
#            frame before may come with it
#   stacking no window comes in front of a window in a tile animation that it overlaps: of
#            two windows drawn in two frames in a row, whose drawn boxes overlap in both,
#            the one in front in the first frame is in front in the second too if it is in
#            a tile animation in both (picom's keep-stacking): nothing jumps in front of a
#            tile while they pass. The order is the frame's Z record if it has one, else
#            that of its W records. Where a tile's animation ends while a window it
#            overlaps comes in front of it (a restack picom held until then), an info line
#            says so: such a raise shows when the move ends
#   rest     at the end, every window is drawn flat at its place, and no animation runs
#
# Usage: check-tile.py LOG --monitor X,Y,W,H [--monitor ...] [--from T]
#        [--change-gap S] [--cover-gap PX] [--check-frames PATH]
# PATH defaults to $WS_DIRECTION/tests/check-frames.py. Exits 1 if a check fails.

import argparse
import copy
import importlib.util
import math
import os
import sys
from collections import defaultdict

CROP = ("crop-x", "crop-y", "crop-width", "crop-height")
NO_CROP = [-math.inf, -math.inf, math.inf, math.inf]
NO_CROP_LOGGED = [0.0, 0.0, math.inf, math.inf]
# The log writes E values with 6 significant digits.
E_ROUND = 1e-6
# A rectangle's edges are worked out from E values: 6 digits of numbers up to a few
# thousand pixels.
EDGE_ROUND = 0.01
# The I record writes the fill with 4 decimals, and the W record the opacity.
FILL_ROUND = 0.5e-4 + E_ROUND
ROUND_OPACITY = 1e-4
# The shortest a window's backdrop, times its opacity, takes from 0 to 1: a quicker one jumps.
BACKDROP_FADE = 0.03
# How far a rectangle may be from the way the style's shape puts it: E values of a few
# thousand pixels to 6 digits, a dozen of them in the sum.
PATH_ROUND = 0.1


def path_control(r0, r1, lift, side):
    """The control rectangle (x, y, width, height) of the tile style's way from r0 to r1
    with var-path 1 (styles/parts/tile-rect.conf), and the share of the way the places
    overlap (1: no lift, 0: no arc)."""
    x0, y0, w0, h0 = r0
    x1, y1, w1, h1 = r1
    tiny = 1e-6
    dx, dy = x1 + w1 / 2 - x0 - w0 / 2, y1 + h1 / 2 - y0 - h0 / 2
    ox = min(x0 + w0, x1 + w1) - max(x0, x1)
    oy = min(y0 + h0, y1 + h1) - max(y0, y1)
    share = min(1, max(0, ox) / max(abs(dx), tiny), max(0, oy) / max(abs(dy), tiny))
    # The overlap's centre off the line between the centres, as a share of a corner of
    # the box they span.
    ocx = (max(x0, x1) + min(x0 + w0, x1 + w1)) / 2 - x0 - w0 / 2
    ocy = (max(y0, y1) + min(y0 + h0, y1 + h1)) / 2 - y0 - h0 / 2
    f = min(1, abs(ocx * dy - ocy * dx) / max(abs(dx * dy), tiny))
    g = min(1, 2 * abs(dx) / max(min(w0, w1), tiny), 2 * abs(dy) / max(min(h0, h1), tiny))
    arc = share * g * f
    mid = [(a + b) / 2 for a, b in zip((x0, y0, x0 + w0, y0 + h0), (x1, y1, x1 + w1, y1 + h1))]
    over = (max(x0, x1), max(y0, y1), min(x0 + w0, x1 + w1), min(y0 + h0, y1 + h1))
    l, t, r, b = (m + arc * (o - m) for m, o in zip(mid, over))
    length = max(math.hypot(dx, dy), tiny)
    nx, ny = dy / length, -dx / length
    shift = 2 * (1 - share) * side * lift * (abs(nx) * (w0 + w1) / 2 + abs(ny) * (h0 + h1) / 2)
    box = (min(x0, x1), min(y0, y1), max(x0 + w0, x1 + w1), max(y0 + h0, y1 + h1))
    cx = min(box[2], max(box[0], (l + r) / 2 + shift * nx))
    cy = min(box[3], max(box[1], (t + b) / 2 + shift * ny))
    hw = max(0, min((r - l) / 2, cx - box[0], box[2] - cx))
    hh = max(0, min((b - t) / 2, cy - box[1], box[3] - cy))
    return (cx - hw, cy - hh, 2 * hw, 2 * hh), share


def path_rect(r0, r1, s, path, lift, side, seams=(0, 0, 0, 0)):
    """Where the tile style puts a rectangle that goes from r0 to r1 at var-ease s, with no
    carried speed. `seams` (left, top, right, bottom) are 1 for an edge that goes
    straight, as it lies on a seam with a neighbour."""
    control, _ = path_control(r0, r1, lift, side)
    edges = lambda r: (r[0], r[1], r[0] + r[2], r[1] + r[3])
    k = 2 * path * s * (1 - s)
    l, t, r, b = (a + (z - a) * s + k * (1 - seam) * (c - (a + z) / 2)
                  for a, z, c, seam in zip(edges(r0), edges(r1), edges(control), seams))
    return [l, t, r - l, b - t]


def centre(r):
    return (r[0] + r[2] / 2, r[1] + r[3] / 2)


def off_line(point, a, b):
    """How far point lies to the left of the way from a to b, in screen coordinates (y
    down: left of going right is up), in pixels."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0
    return ((point[0] - a[0]) * dy - (point[1] - a[1]) * dx) / length


def load_check_frames(path):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("check_frames", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tile(script):
    return script is not None and "var-on" in script and all(k in script for k in CROP)


def whole(script):
    """Whether a tile script draws its window whole, frame and all, at var-ps of its size:
    one that opens or closes with no line."""
    return script.get("var-line", 1) < 0.5


def rect_of(script):
    return [script[k] for k in CROP]


def overlap(a, b):
    """Whether boxes (x0, y0, x1, y1) a and b share an area."""
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def anchor_point(corners, anchor):
    (x0, y0), (x1, _), (_, y2), _ = corners
    return (x0 + anchor[0] * (x1 - x0), y0 + anchor[1] * (y2 - y0))


def parse_extra(path):
    """The I records, {frame: {window: [x, y, width, height, image_width, image_height,
    fill, fill_red, fill_green, fill_blue]}}, the frame on the crop box from them,
    {frame: {window: (left, right, top, bottom)}}, which picture they draw and the saved
    one's size, {frame: {window: (drawn, saved_width, saved_height)}}, the U records,
    {frame: {window: (backdrop, red, green, blue)}}, the Z records, {frame: [window]}, and
    the A records with the frame that has their first pose: [(frame, window, trigger,
    time)]."""
    pictures = defaultdict(dict)
    backdrops = defaultdict(dict)
    frames_on_crop = defaultdict(dict)
    contents = defaultdict(dict)
    drawn = defaultdict(dict)
    orders = {}
    starts, pending = [], []
    with open(path) as f:
        for line in f:
            if not line.endswith("\n"):
                break
            p = line.rstrip("\n").split(",")
            if p[0] == "I":
                # Fill came after the size, and the frame after the fill; a picom
                # without them has no such fields.
                values = [float(v) for v in p[3:13]]
                pictures[int(p[1])][p[2]] = values + [None] * (10 - len(values))
                if len(p) >= 17:
                    frames_on_crop[int(p[1])][p[2]] = tuple(int(v) for v in p[13:17])
                if len(p) >= 23:
                    contents[int(p[1])][p[2]] = tuple(int(v) for v in p[19:23])
                # Which picture is drawn came after the frame; without it, the image.
                if len(p) >= 26:
                    drawn[int(p[1])][p[2]] = (p[23], int(p[24]), int(p[25]))
            elif p[0] == "U":
                backdrops[int(p[1])][p[2]] = tuple(float(v) for v in p[3:7])
            elif p[0] == "Z":
                orders[int(p[1])] = p[2:]
            elif p[0] == "A":
                pending.append((p[2], p[3], int(p[1]) / 1e6))
            elif p[0] == "R":
                starts += [(int(p[1]), w, trigger, t) for w, trigger, t in pending]
                pending = []
    return pictures, frames_on_crop, contents, drawn, backdrops, orders, starts


class TileCheck:
    def __init__(self, cf, path, monitors, start, change_gap, cover_gap=10):
        self.cf = cf
        self.cover_gap = cover_gap
        self.parsed = cf.parse(path)
        (self.pictures, self.frames_on_crop, self.contents, self.drawn, self.backdrops,
         self.orders, self.a_records) = parse_extra(path)
        self.monitors, self.start, self.change_gap = monitors, start, change_gap
        _, self.frames, _ = self.parsed
        self.base = cf.Checker(*self.parsed, monitors, start)
        self.order = self.base.order
        self.failures = defaultdict(list)
        self.notes = {}
        self.set_anchors()
        self.set_drawn()

    def fail(self, check, n, w, text):
        self.failures[check].append(
            f"frame {n} ({self.base.starts[n] - self.base.start:.3f} s), window {w}: {text}")

    def set_anchors(self):
        """Each layer's anchor: that of the window's tile script then, or the last one
        before, or the first one after."""
        by_window = defaultdict(list)
        for n in sorted(self.frames):
            for w, layer in self.frames[n].items():
                by_window[w].append(layer)
        for layers in by_window.values():
            known = [(l.script.get("var-anchor-x", 0), l.script.get("var-anchor-y", 0))
                     for l in layers if tile(l.script)]
            anchor = known[0] if known else (0, 0)
            for l in layers:
                if tile(l.script):
                    anchor = (l.script.get("var-anchor-x", 0), l.script.get("var-anchor-y", 0))
                l.anchor = anchor

    def set_drawn(self):
        """Each layer's drawn picture: its size on the screen, from the top left of the
        window's image (which picom draws the saved one from too), or None for the
        window's image."""
        for n, layers in self.frames.items():
            for w, layer in layers.items():
                layer.saved_size = None
                pic = self.pictures.get(n, {}).get(w)
                drawn = self.drawn.get(n, {}).get(w)
                if pic is None or drawn is None or drawn[0] != "saved":
                    continue
                _, _, pw, ph, iw, ih = pic[:6]
                if iw > 0 and ih > 0:
                    layer.saved_size = (drawn[1] * pw / iw, drawn[2] * ph / ih)

    def as_rects(self):
        """The frames with each window replaced by its rectangle."""
        cf = self.cf

        class RectLayer(cf.Layer):
            def pose(self):
                if tile(self.script):
                    x, y, w, h = rect_of(self.script)
                    return [(x, y), (x + w, y), (x, y + h), (x + w, y + h)], NO_CROP
                return cf.Layer.pose(self)

        out = {}
        for n, layers in self.frames.items():
            out[n] = {}
            for w, layer in layers.items():
                r = copy.copy(layer)
                r.__class__ = RectLayer
                if tile(layer.script) and all(math.isfinite(v) for v in layer.crop):
                    r.box = rect_of(layer.script)
                    cx, cy, cw, ch = layer.crop
                    r.corners = [(cx, cy), (cx + cw, cy), (cx, cy + ch), (cx + cw, cy + ch)]
                    r.crop = NO_CROP_LOGGED
                out[n][w] = r
        return out

    def as_points(self):
        """The frames with each window replaced by its picture's anchor point."""
        cf = self.cf

        class PointLayer(cf.Layer):
            def pose(self):
                # Not from offset and scale: picom moves those of an interrupted
                # animation as if the script didn't read the window's new place.
                s = self.script
                if tile(s) and "var-ax" in s and "var-ay" in s:
                    # A script with another anchor, as the one this took over from:
                    # its point on the picture, moved to this layer's anchor.
                    x = s["var-ax"] + (self.anchor[0] - s.get("var-anchor-x", 0)) * self.size[0]
                    y = s["var-ay"] + (self.anchor[1] - s.get("var-anchor-y", 0)) * self.size[1]
                else:
                    corners, _ = cf.Layer.pose(self)
                    x, y = anchor_point(corners, self.anchor)
                    if s is None:
                        # Without a script these are the drawn corners, shifted already.
                        return [(x, y)] * 4, NO_CROP
                return [(x - self.shift[0], y - self.shift[1])] * 4, NO_CROP

        def drawn_point(layer):
            """The anchor point of the picture picom drew: the saved one is drawn from
            the top left of the window's image, at its own size."""
            if layer.saved_size is None:
                return anchor_point(layer.corners, layer.anchor)
            (x0, y0) = layer.corners[0]
            return (x0 + layer.anchor[0] * layer.saved_size[0],
                    y0 + layer.anchor[1] * layer.saved_size[1])

        def drawn_size(layer):
            if layer.saved_size is not None:
                return layer.saved_size
            (x0, y0), (x1, _), (_, y2), _ = layer.corners
            return (x1 - x0, y2 - y0)

        # The anchor may differ between animations; the point followed stays the first
        # anchor's, shifted by how far apart the two anchors lie on the picture where it
        # changes (the style draws it there where the old animation would).
        out, last = {}, {}
        for n in sorted(self.frames):
            out[n] = {}
            for w, layer in self.frames[n].items():
                p = copy.copy(layer)
                p.__class__ = PointLayer
                shift = (0.0, 0.0)
                if w in last:
                    (ax, ay), shift = last[w]
                    pw, ph = drawn_size(layer)
                    shift = (shift[0] + (layer.anchor[0] - ax) * pw,
                             shift[1] + (layer.anchor[1] - ay) * ph)
                last[w] = (layer.anchor, shift)
                p.shift = shift
                p.size = drawn_size(layer)
                x, y = drawn_point(layer)
                p.corners = [(x - shift[0], y - shift[1])] * 4
                p.crop = NO_CROP_LOGGED
                out[n][w] = p
        return out

    def checker(self, frames):
        starts, _, animations = self.parsed
        return self.cf.Checker(starts, frames, animations, self.monitors, self.start)

    # --- the motion, through check-frames ---

    def check_motion(self):
        rects = self.checker(self.as_rects())
        rects.check_steps()
        rects.check_rest()
        for mine, theirs in (("rect", "jumps"), ("pops", "pops"), ("stops", "stops"),
                             ("drawn", "drawn"), ("rest", "rest")):
            self.notes[mine] = rects.notes.get(theirs, "")
            self.failures[mine] += rects.failures.get(theirs, [])
        self.kinks, self.held = rects.kinks, rects.held
        points = self.checker(self.as_points())
        points.check_steps()
        self.notes["picture"] = points.notes["jumps"].replace("drawn windows", "pictures")
        self.failures["picture"] += points.failures.get("jumps", [])
        self.failures["picture"] += points.failures.get("drawn", [])

    # --- what the style promises besides ---

    def tile_layers(self):
        for n in self.order:
            for w, layer in self.frames[n].items():
                if tile(layer.script):
                    yield n, w, layer

    def has_content(self, n, w):
        """Whether the picture a window is drawn from in frame n has anything inside the
        frame around its content."""
        pic = self.pictures.get(n, {}).get(w)
        if pic is None or pic[4] is None:
            return False
        width, height = pic[4], pic[5]
        drawn = self.drawn.get(n, {}).get(w)
        if drawn is not None and drawn[0] == "saved":
            width, height = drawn[1], drawn[2]
        left, right, top, bottom = self.contents.get(n, {}).get(w, (0, 0, 0, 0))
        return width - left - right > 0 and height - top - bottom > 0

    def first_content(self):
        """{window: the index in self.order of the first frame in which its picture has
        something inside its frame}."""
        first = {}
        for k, n in enumerate(self.order):
            for w, layer in self.frames[n].items():
                if w not in first and layer.state == "D" and self.has_content(n, w):
                    first[w] = k
        return first

    def check_scale_blend_fill(self):
        scaled = blended = filled = 0
        missing = unfilled = gaps_to_fill = no_picture = coming = 0
        first = self.first_content()
        index = {n: k for k, n in enumerate(self.order)}
        colours = defaultdict(set)
        for n, w, layer in self.tile_layers():
            s = layer.script
            blended += 1
            blend = s.get("saved-image-blend", 0)
            if blend not in (0, 1):
                self.fail("blend", n, w, f"saved-image-blend {blend:.3g}, neither 0 nor 1")
            if layer.state != "D":
                continue
            pic = self.pictures.get(n, {}).get(w)
            if pic is None:
                missing += 1
                continue
            x, y, pw, ph, iw, ih, fill, red, green, blue = pic
            drawn = self.drawn.get(n, {}).get(w)
            if drawn is not None and drawn[0] == "both":
                self.fail("blend", n, w, "picom blends the window's image and the saved one")
            scaled += 1
            k = s.get("var-ps", 1) if whole(s) else 1
            if abs(pw - k * iw) > EDGE_ROUND or abs(ph - k * ih) > EDGE_ROUND:
                self.fail("scale", n, w, f"picture drawn {pw:g}x{ph:g} from an image of "
                          f"{iw:g}x{ih:g}" + (f" at var-ps {k:g}" if k != 1 else ""))
            if layer.saved_size is not None:
                if s.get("saved-image-own-size", 0) <= 0.5:
                    self.fail("scale", n, w, "the saved picture is stretched to the "
                              "window's size: saved-image-own-size is "
                              f"{s.get('saved-image-own-size', 0):g}")
                # The saved picture, at its own size, is what is drawn.
                pw, ph = layer.saved_size
            rx, ry, rw, rh = rect_of(s)
            gap = (x > rx + EDGE_ROUND or y > ry + EDGE_ROUND or
                   x + pw < rx + rw - EDGE_ROUND or y + ph < ry + rh - EDGE_ROUND)
            if not gap:
                continue
            if fill is None:
                unfilled += 1
                continue
            filled += 1
            if s.get("var-fill", 0) > 0:
                gaps_to_fill += 1
                if fill <= 0 and not self.has_content(n, w):
                    no_picture += 1
                elif fill <= 0 and index[n] - first.get(w, index[n]) <= 2:
                    coming += 1
                elif fill <= 0:
                    self.fail("unfilled", n, w, "the rectangle has a gap, and its fill is 0: "
                              "what lies behind shows")
            asked = (s.get("fill", 0), s.get("fill-red", 0), s.get("fill-green", 0),
                     s.get("fill-blue", 0))
            got = (fill, red, green, blue)
            colours[w].add(tuple(round(v, 3) for v in got[1:]))
            if any(abs(a - b) > FILL_ROUND for a, b in zip(asked, got)):
                self.fail("fill", n, w, f"filled with {got}, the script asks {asked}")
            if not all(0 <= v <= 1 for v in got):
                self.fail("fill", n, w, f"fill {got} out of 0..1")
        self.notes["blend"] = f"{blended} frames of tile scripts"
        self.notes["scale"] = f"{scaled} pictures drawn"
        if missing:
            self.failures["scale"].append(
                f"{missing} drawn frames of tile scripts have no I record; does this picom "
                "write the picture records?")
        self.notes["fill"] = f"{filled} rectangles with a gap"
        self.notes["unfilled"] = (f"{gaps_to_fill} rectangles with a gap to fill, {no_picture} "
                                  f"of a picture with nothing in it, {coming} in the first two "
                                  "frames of a window's first picture")
        self.fill_colours = colours
        if unfilled:
            self.failures["fill"].append(
                f"{unfilled} rectangles with a gap have no fill in their I record; does this "
                "picom fill?")

    def check_frame(self):
        drawn = framed = missing = 0
        sides = defaultdict(set)
        for n, w, layer in self.tile_layers():
            s = layer.script
            if whole(s) and s.get("frame-on-crop", 0) > 0.5:
                self.fail("frame", n, w, "a window drawn whole has its frame on the "
                          "rectangle: frame-on-crop is " f"{s.get('frame-on-crop', 0):g}")
            elif not whole(s) and s.get("frame-on-crop", 0) <= 0.5:
                self.fail("frame", n, w, "the window manager's frame is not on the "
                          f"rectangle: frame-on-crop is {s.get('frame-on-crop', 0):g}")
            if layer.state != "D":
                continue
            drawn += 1
            frame = self.frames_on_crop.get(n, {}).get(w)
            if frame is None:
                missing += 1
                continue
            sides[w].add(frame)
            if any(frame):
                framed += 1
        self.notes["frame"] = (f"{drawn} drawn frames of tile scripts, {framed} with a "
                               "frame on the rectangle")
        if missing:
            self.failures["frame"].append(
                f"{missing} drawn frames of tile scripts have no frame in their I record; "
                "does this picom draw the frame on the crop box?")
        self.frame_sides = sides

    def changes(self):
        """The tile animations that start together: lists of (frame, window, trigger),
        each from A records less than change_gap apart."""
        first = self.order[0]
        found, current, last = [], [], None
        for n, w, trigger, t in sorted(self.a_records, key=lambda r: r[3]):
            if n < first or not self.is_tile_start(n, w):
                continue
            if last is not None and t - last >= self.change_gap:
                found.append(current)
                current = []
            current.append((n, w, trigger))
            last = t
        if current:
            found.append(current)
        return found

    def is_tile_start(self, n, w):
        """Whether the animation that starts on w in frame n has a tile script, in its
        first frame that draws the window."""
        run = self.runs(n, w)
        return bool(run) and tile(self.frames[run[0]][w].script)

    def runs(self, n0, w):
        """The frames of the animation of w that starts in frame n0, in order, until it
        ends or another starts."""
        out = []
        for m in self.order:
            if m < n0:
                continue
            layer = self.frames[m].get(w)
            if layer is None:
                continue
            if m != n0 and layer.started:
                break
            s = layer.script
            if s is None or s["elapsed"] >= s["duration"]:
                if out or m != n0:
                    break
                continue
            out.append(m)
        return out

    def check_clock(self):
        changes = self.changes()
        count = 0
        for change in changes:
            frames = {n for n, _, _ in change}
            windows = sorted({w for _, w, _ in change})
            if len(frames) > 1:
                n = min(frames)
                self.fail("clock", n, ",".join(windows), "the windows of one change start in "
                          f"frames {sorted(frames)}")
                continue
            n0 = frames.pop()
            frames_of = {w: set(self.runs(n0, w)) for w in windows}
            for m in sorted(set().union(*frames_of.values())):
                here = [w for w in windows if m in frames_of[w]]
                if len(here) < 2:
                    continue
                count += 1
                ss = [self.frames[m][w].script for w in here]
                for key, tol in (("elapsed", E_ROUND), ("var-ease", 10 * E_ROUND)):
                    values = [s.get(key) for s in ss]
                    if None in values or max(values) - min(values) > tol:
                        self.fail("clock", m, ",".join(here), f"{key} differs: {values}")
        self.notes["clock"] = f"{len(changes)} changes, {count} frames with two windows or more"
        self.changes_found = changes

    def check_seams(self):
        count = 0
        for change in getattr(self, "changes_found", []):
            n0 = change[0][0]
            if any(n != n0 for n, _, _ in change):
                continue
            paths = {}
            for _, w, _ in change:
                run = self.runs(n0, w)
                if not run:
                    continue
                s = self.frames[run[0]][w].script
                if not all(k in s for k in ("var-x0", "var-w0", "var-x1", "var-w1",
                                            "var-y0", "var-h0", "var-y1", "var-h1")):
                    continue
                start = [s["var-x0"], s["var-y0"], s["var-w0"], s["var-h0"]]
                end = [s["var-x1"], s["var-y1"], s["var-w1"], s["var-h1"]]
                paths[w] = (run, start, end)
            ws = sorted(paths)
            for i, a in enumerate(ws):
                for b in ws[i + 1:]:
                    for edge_a, edge_b, axis in self.shared_edges(paths[a], paths[b]):
                        both = sorted(set(paths[a][0]) & set(paths[b][0]))
                        for m in both:
                            ra = rect_of(self.frames[m][a].script)
                            rb = rect_of(self.frames[m][b].script)
                            # A rectangle with nothing left across the edge has no edge.
                            size = 2 if axis == "x" else 3
                            if min(ra[size], rb[size]) <= EDGE_ROUND:
                                continue
                            count += 1
                            ea = self.edges(ra)[edge_a]
                            eb = self.edges(rb)[edge_b]
                            if abs(ea - eb) > self.cf.HALF_PX + EDGE_ROUND:
                                self.fail("seams", m, f"{a},{b}", f"the edge they share is "
                                          f"{abs(ea - eb):.2f} px apart ({axis})")
                                break
        self.notes["seams"] = f"{count} frames of shared edges"

    @staticmethod
    def edges(r):
        x, y, w, h = r
        return {"left": x, "right": x + w, "top": y, "bottom": y + h}

    def shared_edges(self, a, b):
        """The edges of a and b that lie together at the start and at the end of their
        paths, where the two touch along them."""
        out = []
        for ea, eb, axis in (("right", "left", "x"), ("left", "right", "x"),
                             ("bottom", "top", "y"), ("top", "bottom", "y")):
            ok = True
            for k in (1, 2):
                ra, rb = self.edges(a[k]), self.edges(b[k])
                if abs(ra[ea] - rb[eb]) > self.cf.HALF_PX + EDGE_ROUND:
                    ok = False
                    break
                lo, hi = ("top", "bottom") if axis == "x" else ("left", "right")
                if min(ra[hi], rb[hi]) - max(ra[lo], rb[lo]) <= 0:
                    ok = False
                    break
            if ok:
                out.append((ea, eb, axis))
        return out

    @staticmethod
    def within(inner, outer):
        x, y, w, h = inner
        ox, oy, ow, oh = outer
        return (ox - EDGE_ROUND <= x and x + w <= ox + ow + EDGE_ROUND and
                oy - EDGE_ROUND <= y and y + h <= oy + oh + EDGE_ROUND)

    def check_line(self):
        count = 0
        for n, w, trigger, _ in self.a_records:
            if n < self.order[0] or trigger not in ("open", "close"):
                continue
            run = self.runs(n, w)
            if not run:
                continue
            s = self.frames[run[0]][w].script
            if not tile(s) or s.get("var-line", 0) < 0.5:
                continue
            count += 1
            line = [s.get(f"var-{k}{'0' if trigger == 'open' else '1'}") for k in "xywh"]
            own = self.frames[run[0]][w].box
            if None in line:
                self.fail("line", run[0], w, "the script has no var-x0..var-h1")
                continue
            x, y, lw, lh = line
            region = own
            if trigger == "close" and all(f"var-f{k}0" in s for k in "xywh"):
                region = [s[f"var-f{k}0"] for k in "xywh"]
                if not self.within(own, region):
                    self.fail("line", run[0], w, f"close: shrinks from {region}, which "
                              f"doesn't hold its own rect {own}")
                    continue
            flat = abs(lw) <= EDGE_ROUND or abs(lh) <= EDGE_ROUND
            if not (flat and self.within(line, region)):
                what = "grows from" if trigger == "open" else "ends in"
                self.fail("line", run[0], w, f"{trigger}: {what} {line}, which is not a "
                          f"line within {region}")
        self.notes["line"] = f"{count} windows that open or close on a split line"

    @staticmethod
    def drawn_rect(layer):
        """Where a window is drawn: the rectangle of its tile script, or the box around
        its corners, within its crop."""
        if tile(layer.script):
            return rect_of(layer.script)
        xs = [c[0] for c in layer.corners]
        ys = [c[1] for c in layer.corners]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        cx, cy, cw, ch = layer.crop
        if all(math.isfinite(v) for v in layer.crop):
            x0, y0, x1, y1 = max(x0, cx), max(y0, cy), min(x1, cx + cw), min(y1, cy + ch)
        return [x0, y0, x1 - x0, y1 - y0]

    @staticmethod
    def uncovered(place, rects):
        """The widest stretch of `place` that no rect covers, along its rows and columns at
        a quarter, half and three quarters of it."""
        worst = 0.0
        for axis in (0, 1):
            lo, size = place[axis], place[2 + axis]
            for k in (1, 2, 3):
                at = place[1 - axis] + place[3 - axis] * k / 4
                spans = sorted((r[axis], r[axis] + r[2 + axis]) for r in rects
                               if r[2 + axis] > 0 and
                               r[1 - axis] <= at < r[1 - axis] + r[3 - axis])
                pos = lo
                for a, b in spans:
                    if a > pos:
                        worst = max(worst, min(a, lo + size) - pos)
                    pos = max(pos, b)
                    if pos >= lo + size:
                        break
                worst = max(worst, lo + size - pos)
        return worst

    def check_cover(self):
        count = 0
        for change in getattr(self, "changes_found", []):
            closing = []
            for n, w, trigger in change:
                run = self.runs(n, w)
                s = self.frames[run[0]][w].script if run else None
                if trigger == "close" and s is not None and s.get("var-line", 0) >= 0.5:
                    closing.append((w, [s[f"var-{k}0"] for k in ("x", "y", "w", "h")], run))
            frames = sorted({m for _, _, run in closing for m in run})
            g = self.cover_gap / 2
            for m in frames:
                rects = [[x - g, y - g, w + 2 * g, h + 2 * g]
                         for x, y, w, h in (self.drawn_rect(l) for l in self.frames[m].values()
                                            if l.state == "D" and l.opacity > 0.5)
                         if w > 0 and h > 0]
                for w, place, _ in closing:
                    count += 1
                    worst = self.uncovered(place, rects)
                    if worst > EDGE_ROUND:
                        self.fail("cover", m, w, f"{worst:.0f} px of its place show what "
                                  f"lies behind")
        self.notes["cover"] = f"{count} frames of places that close"

    def check_edges(self):
        # window: [[(frame, shown edges, rectangle edges, bend at the anchor (x, y))]]
        runs = defaultdict(list)
        for n in sorted(self.frames):
            for w, layer in self.frames[n].items():
                pic = self.pictures.get(n, {}).get(w)
                if not tile(layer.script) or pic is None or \
                        layer.script.get("var-carried", 0) > 0.5:
                    runs[w].append([])
                    continue
                if getattr(layer, "started", False) or not runs[w]:
                    runs[w].append([])
                x, y, pw, ph = pic[:4]
                if layer.saved_size is not None:
                    pw, ph = layer.saved_size
                rx, ry, rw, rh = rect_of(layer.script)
                rect = (rx, ry, rx + rw, ry + rh)
                shown = (max(x, rect[0]), max(y, rect[1]), min(x + pw, rect[2]),
                         min(y + ph, rect[3]))
                s = layer.script
                # How far the style's curved way (var-path) moves the picture's anchor
                # point off the straight way: the window's own motion, which the picture
                # goes with.
                k = s.get("var-pk", 0)
                bend = (k * (s.get("var-sbx", 0) + s.get("var-anchor-x", 0) * s.get("var-sbw", 0)),
                        k * (s.get("var-sby", 0) + s.get("var-anchor-y", 0) * s.get("var-sbh", 0)))
                runs[w][-1].append((n, shown, rect, bend))
        checked = 0
        for w, animations in runs.items():
            for frames in animations:
                for e in range(4):
                    r = [f[2][e] for f in frames]
                    v = [f[1][e] for f in frames]
                    steps = [b - a for a, b in zip(r, r[1:])]
                    if len(r) < 2 or (max(steps) > EDGE_ROUND and min(steps) < -EDGE_ROUND):
                        continue
                    way = r[-1] - r[0]
                    checked += 1
                    for k in range(1, len(v)):
                        d = v[k] - v[k - 1]
                        bent = frames[k][3][e % 2] - frames[k - 1][3][e % 2]
                        # A pixel on the screen.
                        if abs(d - bent) <= 1:
                            continue
                        if (way >= -EDGE_ROUND and d < -1) or (way <= EDGE_ROUND and d > 1):
                            side = ("left", "top", "right", "bottom")[e]
                            self.fail("edges", frames[k][0], w,
                                      f"the picture's {side} edge goes {d:+.1f} px, its "
                                      f"rectangle's {way:+.1f} px in the animation")
        self.notes["edges"] = f"{checked} edges of pictures in tile animations"

    def tile_runs(self):
        """The tile animations of windows that move or change size (not those that open
        or close), with the script of their first frame: [(window, frames, script)]."""
        out = []
        for n, w, trigger, _ in self.a_records:
            if n < self.order[0] or trigger in ("open", "close"):
                continue
            run = self.runs(n, w)
            if not run:
                continue
            s = self.frames[run[0]][w].script
            if tile(s) and all(f"var-{k}{e}" in s for k in "xywh" for e in "01") and \
                    "var-ease" in s:
                out.append((w, run, s))
        return out

    @staticmethod
    def places(s):
        return ([s[f"var-{k}0"] for k in "xywh"], [s[f"var-{k}1"] for k in "xywh"])

    def check_box(self):
        count = 0
        for w, run, s0 in self.tile_runs():
            if s0.get("var-carried", 0) > 0.5:
                continue
            r0, r1 = self.places(s0)
            box = [min(r0[0], r1[0]), min(r0[1], r1[1]), max(r0[0] + r0[2], r1[0] + r1[2]),
                   max(r0[1] + r0[3], r1[1] + r1[3])]
            count += 1
            for n in run:
                x, y, width, height = rect_of(self.frames[n][w].script)
                out = max(box[0] - x, box[1] - y, x + width - box[2], y + height - box[3])
                if out > EDGE_ROUND:
                    self.fail("box", n, w, f"the rectangle is {out:.1f} px out of the box "
                              f"around where it starts and ends, {box}")
                    break
        self.notes["box"] = f"{count} animations from rest"

    def check_path(self):
        count = arcs = lifts = 0
        peaks = []
        for w, run, s0 in self.tile_runs():
            carried = s0.get("var-carried", 0) > 0.5
            path = s0.get("var-path", 0) * s0.get("var-pass", 1)
            lift = s0.get("var-lift", 0)
            side = s0.get("var-pside", 1) if carried else 1
            seams = tuple(s0.get(f"var-seam-{e}", 0) for e in ("left", "top", "right", "bottom"))
            r0, r1 = self.places(s0)
            c0, c1 = centre(r0), centre(r1)
            control, share = path_control(r0, r1, lift, side)
            count += 1
            # What a window's centre shows of its way, from rest: on the side of the line
            # between its centres that the overlap is on, or for places that don't overlap
            # to the left of its way, as far as var-lift says midway.
            bent = path > 0.5 and not carried and not any(seams)
            arc = bent and share >= 1 - 1e-9 and abs(off_line(centre(control), c0, c1)) > 1
            lifted = bent and share <= 1e-9 and lift > 0
            want = off_line(centre(control), c0, c1)
            arcs += arc
            lifts += lifted
            top = 0.0
            for n in run:
                s = self.frames[n][w].script
                got = rect_of(s)
                way = path_rect(r0, r1, s["var-ease"], path, lift, side, seams)
                way = [a + s.get(f"var-v{k}", 0) * s.get("var-carry", 0)
                       for a, k in zip(way, "xywh")]
                miss = max(abs(a - b) for a, b in zip(got, way))
                if miss > PATH_ROUND:
                    self.fail("path", n, w, f"the rectangle is {[round(v, 1) for v in got]}, "
                              f"the way of var-path {path:g} puts it at "
                              f"{[round(v, 1) for v in way]}, {miss:.1f} px off")
                    break
                off = off_line(centre(got), c0, c1)
                inside = 0.02 < s["var-ease"] < 0.98
                if (arc or lifted) and inside and off * want < -0.25:
                    self.fail("path", n, w, f"the centre is {off:.1f} px to the "
                              f"{'left' if off > 0 else 'right'} of its straight way, the "
                              f"{'overlap' if arc else 'lift'} on the other side")
                    break
                top = max(top, abs(off))
            if lifted:
                peaks.append((top, abs(want) / 2))
        self.notes["path"] = (f"{count} animations, {arcs} curved around an overlap, {lifts} "
                              f"lifted" + "".join(f", peak {a:.0f} of {b:.0f} px" for a, b in peaks[:4]))

    def check_retrace(self):
        count = 0
        for w, run, s0 in self.tile_runs():
            if s0.get("var-carried", 0) <= 0.5:
                continue
            k = self.order.index(run[0])
            before = next((self.frames[m][w].script for m in reversed(self.order[:k])
                           if w in self.frames[m] and tile(self.frames[m][w].script)), None)
            if before is None or not all(f"var-{c}{e}" in before for c in "xywh" for e in "01"):
                continue
            p0, p1 = self.places(before)
            _, r1 = self.places(s0)
            if max(abs(a - b) for a, b in zip(r1, p0)) > 2:
                continue
            # An undo: it goes back on the side of its first way that it is on.
            a, b = centre(p0), centre(p1)
            first = off_line(centre(rect_of(self.frames[run[0]][w].script)), a, b)
            if abs(first) < 1:
                continue
            count += 1
            for n in run:
                off = off_line(centre(rect_of(self.frames[n][w].script)), a, b)
                if off * first < 0 and abs(off) > 1:
                    self.fail("retrace", n, w, f"an undo goes back {abs(off):.1f} px on the "
                              f"other side of its first way, it was {abs(first):.1f} px on "
                              f"the {'left' if first > 0 else 'right'}")
                    break
        self.notes["retrace"] = f"{count} undos off the straight way"

    def check_opacity(self):
        kept = ended = 0
        last = {}
        for n in sorted(self.frames):
            for w, layer in self.frames[n].items():
                s = layer.script
                before = last.get(w)
                last[w] = (n, layer)
                if tile(s) and "opacity" not in s and s["elapsed"] < s["duration"]:
                    rest = self.rest_opacity(n, w)
                    kept += 1
                    if rest is not None and abs(layer.opacity - rest) > ROUND_OPACITY:
                        self.fail("opacity", n, w, f"drawn at opacity {layer.opacity:g}, and "
                                  f"{rest:g} once the animation ends")
                if before is None or layer.state != "D" or tile(s):
                    continue
                m, prev = before
                if prev.state == "D" and tile(prev.script):
                    ended += 1
                    if abs(layer.opacity - prev.opacity) > ROUND_OPACITY:
                        self.fail("opacity", n, w, f"its opacity jumps from {prev.opacity:g} "
                                  f"in the animation's last frame to {layer.opacity:g}")
        self.notes["opacity"] = (f"{kept} frames of scripts that keep the window's opacity, "
                                 f"{ended} animations that end on a drawn window")

    def rest_opacity(self, n0, w):
        """The window's opacity in the first frame after n0 in which it is drawn with no
        tile script, None if there is none."""
        if not hasattr(self, "_rest"):
            self._rest = {}
            following = {}
            for n in sorted(self.frames, reverse=True):
                for v, layer in self.frames[n].items():
                    if layer.state == "D" and not tile(layer.script):
                        following[v] = layer.opacity
                    self._rest[(n, v)] = following.get(v)
        return self._rest.get((n0, w))

    def check_backdrop(self):
        count = 0
        last, m = {}, None
        for n in sorted(self.frames):
            shown = {}
            for w, layer in self.frames[n].items():
                if layer.state != "D":
                    continue
                backdrop = self.backdrops.get(n, {}).get(w, (0,))[0] * layer.opacity
                shown[w] = backdrop
                count += backdrop > 0
                before = last.get(w)
                if before is None:
                    continue
                most = (self.base.starts[n] - self.base.starts[m]) / BACKDROP_FADE
                if abs(backdrop - before) > most + E_ROUND:
                    self.fail("backdrop", n, w, f"its backdrop jumps from {before:.2f} to "
                              f"{backdrop:.2f}, times its opacity, {most:.2f} allowed")
            last, m = shown, n
        self.notes["backdrop"] = f"{count} frames of windows with a backdrop"

    def drawn_box(self, layer):
        """A box around what is drawn of a layer, in the whole pixels picom rounds its
        edges to: a tile animation's rectangle, as its frame is on it, else its corners cut
        to its crop box."""
        x0, y0 = (min(c[i] for c in layer.corners) for i in (0, 1))
        x1, y1 = (max(c[i] for c in layer.corners) for i in (0, 1))
        cx, cy, cw, ch = layer.crop
        if all(math.isfinite(v) for v in layer.crop):
            crop = round(cx), round(cy), round(cx + cw), round(cy + ch)
            if tile(layer.script):
                return crop
            return max(round(x0), crop[0]), max(round(y0), crop[1]), \
                min(round(x1), crop[2]), min(round(y1), crop[3])
        return round(x0), round(y0), round(x1), round(y1)

    def check_stacking(self):
        # (frame, window, the tile it came in front of) where a tile's animation ended
        # while they overlapped.
        self.shown_at_end = []
        count = 0
        last = None
        for n in self.order:
            layers = self.frames[n]
            shown = [w for w in self.orders.get(n, layers)
                     if w in layers and layers[w].state == "D" and layers[w].opacity > 0]
            now = {w: (rank, self.drawn_box(layers[w]), tile(layers[w].script))
                   for rank, w in enumerate(shown)}
            for i, a in enumerate(shown):
                for b in shown[i + 1:]:
                    if last is None or a not in last or b not in last:
                        continue
                    # The one in front in the frame before, and the other.
                    front, back = (a, b) if last[a][0] > last[b][0] else (b, a)
                    if not last[front][2]:
                        continue
                    if not (overlap(now[a][1], now[b][1]) and overlap(last[a][1], last[b][1])):
                        continue
                    if not now[front][2]:
                        # Its tile animation ended: a restack held while they overlap shows.
                        if now[back][0] > now[front][0]:
                            self.shown_at_end.append((n, back, front))
                        continue
                    count += 1
                    if now[back][0] > now[front][0]:
                        self.fail("stacking", n, back, f"comes in front of {front}, which it "
                                  "overlaps and which is in a tile animation")
            last = now
        self.notes["stacking"] = f"{count} steps of a tile in front of a window it overlaps"

    def run(self):
        self.check_motion()
        self.check_edges()
        self.check_scale_blend_fill()
        self.check_opacity()
        self.check_frame()
        self.check_clock()
        self.check_seams()
        self.check_line()
        self.check_cover()
        self.check_box()
        self.check_path()
        self.check_retrace()
        self.check_backdrop()
        self.check_stacking()
        ok = True
        for check in ("rect", "pops", "stops", "drawn", "picture", "edges", "scale", "blend", "clock",
                      "seams", "line", "cover", "fill", "unfilled", "opacity", "frame", "box",
                      "path", "retrace", "backdrop", "stacking",
                      "rest"):
            fails = self.failures.get(check, [])
            ok = ok and not fails
            print(f"{'FAIL' if fails else 'ok'}  {check}: {self.notes.get(check, '')}" +
                  (f", {len(fails)} failed" if fails else ""))
            for text in fails[:5]:
                print(f"        {text}")
            if len(fails) > 5:
                print(f"        ... and {len(fails) - 5} more")
        if self.fill_colours:
            print("info  fill colours (red, green, blue) by window: " + "; ".join(
                f"{w} {', '.join(str(c) for c in sorted(cs))}"
                for w, cs in sorted(self.fill_colours.items())))
        if self.frame_sides:
            print("info  frame on the rectangle (left, right, top, bottom) by window: " +
                  "; ".join(f"{w} {', '.join(str(f) for f in sorted(fs))}"
                            for w, fs in sorted(self.frame_sides.items())))
        for kind in ("at an interruption", "at a start from rest", "inside an animation"):
            found = sorted(self.kinks.get(kind, []), reverse=True)
            if found:
                top = ", ".join(f"{c:.1f} px/frame in frame {n} window {w}"
                                for c, n, w in found[:3])
                print(f"info  kinks {kind}: {len(found)}, the largest {top}")
        print(f"info  stacking at the end of a move: {len(self.shown_at_end)} windows came in "
              "front of a tile they overlap as its animation ended" + "".join(
                  f", frame {n} {w} in front of {t}" for n, w, t in self.shown_at_end[:3]))
        if self.held:
            frames = sorted({n for _, n, _ in self.held})
            print(f"info  held: in {len(frames)} frames picom didn't advance some animations, "
                  f"up to {max(x for x, _, _ in self.held) * 1000:.0f} ms")
        return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--monitor", action="append", required=True, help="X,Y,W,H of a monitor")
    ap.add_argument("--from", dest="start", type=float)
    ap.add_argument("--change-gap", type=float, default=0.05,
                    help="A records closer than this, in seconds, are one change")
    ap.add_argument("--cover-gap", type=float, default=10,
                    help="the widest stretch, in pixels, of a closing window's place that may "
                    "show what lies behind (i3's gaps)")
    ap.add_argument("--check-frames",
                    default=os.path.join(os.environ.get("WS_DIRECTION", ""), "tests",
                                         "check-frames.py"))
    a = ap.parse_args()
    if not os.path.exists(a.check_frames):
        sys.exit(f"no check-frames.py at {a.check_frames}: set WS_DIRECTION or --check-frames")
    cf = load_check_frames(a.check_frames)
    monitors = [[float(x) for x in m.split(",")] for m in a.monitor]
    check = TileCheck(cf, a.log, monitors, a.start, a.change_gap, a.cover_gap)
    check.base.summary()
    sys.exit(0 if check.run() else 1)


if __name__ == "__main__":
    main()
