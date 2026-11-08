#!/usr/bin/env python3
"""What a change looks like on the screen, for tests/scene.py: a magenta backdrop under all
windows, screenshots during a change, and a check of them against picom's frame log.

  screen.py backdrop          a magenta window under all others, until killed, so what shows
                              behind a window shows magenta
  screen.py shots LABEL N GAP N screenshots, GAP s apart, into $SCENE_OUT/shot-LABEL-I.npy,
                              with the times in shot-LABEL.times
  screen.py check OUT         for each screenshot in OUT, at the frame on the screen when it
                              was taken: the magenta inside each tile window's rectangle, 2 px
                              in, and in each gap the picture leaves (as picom works it out,
                              less what windows above cover, and for a translucent fill what
                              windows under it cover), the share within 3 of the fill's colour
                              at the fill times the window's opacity over the window's
                              backdrop (its U record) over magenta

In a scene, with a slow tile style (a long var-length, so frames change little while a
screenshot is taken):

    --scene "exec:(tests/screen.py backdrop &)" \\
    --scene "exec:(tests/screen.py shots down 4 2.5 &),key:alt+shift+Down,sleep:21"

Use picom's xrender backend: with glx, Xvfb copies a window's content into its texture only
when picom binds it, so a screenshot doesn't show what the window drew since. Needs numpy.
"""

import collections
import ctypes
import glob
import math
import os
import sys
import time

import numpy as np

MAGENTA = np.array([255, 0, 255])


class XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.POINTER(ctypes.c_uint8)),
                ("byte_order", ctypes.c_int), ("bitmap_unit", ctypes.c_int),
                ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
                ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int),
                ("bits_per_pixel", ctypes.c_int)]


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
                ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong),
                ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
                ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
                ("event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("colormap", ctypes.c_ulong),
                ("cursor", ctypes.c_ulong)]


def connect():
    """libX11 and the display, with the calls these use typed."""
    x11 = ctypes.CDLL("libX11.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    for name in ("XDefaultRootWindow", "XDisplayWidth", "XDisplayHeight"):
        getattr(x11, name).restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDisplayWidth.argtypes = x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCreateSimpleWindow.restype = ctypes.c_ulong
    x11.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                        ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
    x11.XChangeWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                            ctypes.POINTER(XSetWindowAttributes)]
    x11.XMapWindow.argtypes = x11.XLowerWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XGetImage.restype = ctypes.POINTER(XImage)
    x11.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                              ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
    x11.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
    display = x11.XOpenDisplay(None)
    if not display:
        sys.exit("screen.py: cannot open the display")
    return x11, display


def backdrop():
    x11, d = connect()
    root = x11.XDefaultRootWindow(d)
    w = x11.XCreateSimpleWindow(d, root, 0, 0, x11.XDisplayWidth(d, 0),
                                x11.XDisplayHeight(d, 0), 0, 0, 0xFF00FF)
    cw_override_redirect = 1 << 9
    x11.XChangeWindowAttributes(d, w, cw_override_redirect,
                                ctypes.byref(XSetWindowAttributes(override_redirect=1)))
    x11.XMapWindow(d, w)
    x11.XLowerWindow(d, w)
    x11.XSync(d, 0)
    while True:
        time.sleep(3600)


def shots(label, n, gap):
    x11, d = connect()
    root = x11.XDefaultRootWindow(d)
    width, height = x11.XDisplayWidth(d, 0), x11.XDisplayHeight(d, 0)
    z_pixmap = 2
    out = os.environ["SCENE_OUT"]
    with open(f"{out}/shot-{label}.times", "w") as times:
        for i in range(n):
            time.sleep(gap)
            t0 = time.monotonic_ns() // 1000
            image = x11.XGetImage(d, root, 0, 0, width, height, 0xFFFFFFFF, z_pixmap)
            t1 = time.monotonic_ns() // 1000
            line = image.contents.bytes_per_line
            a = np.ctypeslib.as_array(image.contents.data, shape=(height * line,))
            a = a.reshape(height, line)[:, :width * 4].reshape(height, width, 4)[..., 2::-1]
            np.save(f"{out}/shot-{label}-{i}.npy", a.copy())
            x11.XDestroyImage(image)
            times.write(f"{i},{t0},{t1}\n")


def rect_sub(a, b):
    """a - b, rectangles as (x0, y0, x1, y1), a list of rectangles."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if bx0 >= ax1 or bx1 <= ax0 or by0 >= ay1 or by1 <= ay0:
        return [a] if ax0 < ax1 and ay0 < ay1 else []
    out = []
    if by0 > ay0:
        out.append((ax0, ay0, ax1, by0))
    if by1 < ay1:
        out.append((ax0, by1, ax1, ay1))
    my0, my1 = max(ay0, by0), min(ay1, by1)
    if bx0 > ax0:
        out.append((ax0, my0, bx0, my1))
    if bx1 < ax1:
        out.append((bx1, my0, ax1, my1))
    return [r for r in out if r[0] < r[2] and r[1] < r[3]]


def gaps(crop, i):
    """The part of `crop` that a window's picture leaves to the fill, from its I record, as
    picom's layout works it out: inside the frame on the crop box where the content isn't,
    or, with no frame there, where the picture isn't."""
    x, y, pw, ph, iw, ih = (float(v) for v in i[3:9])
    frame = [int(v) for v in i[13:17]]
    content = [int(v) for v in i[19:23]]
    sw, sh = int(i[24]), int(i[25])
    sx = 1 if abs(pw - iw) < 1e-6 else pw / iw
    sy = 1 if abs(ph - ih) < 1e-6 else ph / ih
    if any(frame) or any(content):
        size = (sw, sh) if i[23] == "saved" else (iw, ih)

        def axis(start, end, origin, s, near, far, length, cnear, cfar):
            inner_start = min(start + near, end)
            inner_end = max(end - far, inner_start)
            c0 = min(max(math.floor(origin + cnear * s + 1e-6), inner_start), inner_end)
            c1 = min(max(math.ceil(origin + (length - cfar) * s - 1e-6), c0), inner_end)
            return inner_start, inner_end, c0, c1

        ix0, ix1, kx0, kx1 = axis(crop[0], crop[2], x, sx, frame[0], frame[1], size[0],
                                  content[0], content[1])
        iy0, iy1, ky0, ky1 = axis(crop[1], crop[3], y, sy, frame[2], frame[3], size[1],
                                  content[2], content[3])
        return rect_sub((ix0, iy0, ix1, iy1), (kx0, ky0, kx1, ky1))
    w, h = (sw * sx, sh * sy) if i[23] == "saved" else (pw, ph)
    return rect_sub(crop, (x, y, x + math.ceil(w - 1e-6), y + math.ceil(h - 1e-6)))


def check(out):
    calls, returns, W, E, I, U = {}, {}, collections.defaultdict(dict), \
        collections.defaultdict(dict), collections.defaultdict(dict), \
        collections.defaultdict(dict)
    with open(f"{out}/frames.csv") as f:
        for line in f:
            if not line.endswith("\n"):
                break
            p = line.rstrip("\n").split(",")
            if p[0] == "R":
                calls[int(p[1])], returns[int(p[1])] = int(p[6]), int(p[7])
            elif p[0] == "W":
                W[int(p[1])][p[2]] = p
            elif p[0] == "I":
                I[int(p[1])][p[2]] = p
            elif p[0] == "U":
                U[int(p[1])][p[2]] = [float(v) for v in p[3:7]]
            elif p[0] == "E":
                E[int(p[1])][p[2]] = dict(kv.split("=", 1) for kv in p[5:])
    frames = sorted(calls)

    def outline(wr):
        if "inf" in wr[17:21]:
            x, y, w, h = (int(v) for v in wr[4:8])
            return (x, y, x + w, y + h)
        cx, cy, cw, ch = (float(v) for v in wr[17:21])
        return (round(cx), round(cy), round(cx + cw), round(cy + ch))

    holes = worst = 0
    for path in sorted(glob.glob(f"{out}/shot-*.times")):
        label = os.path.basename(path)[5:-6]
        with open(path) as f:
            shots_taken = [[int(v) for v in line.split(",")] for line in f]
        for i, t0, _ in shots_taken:
            img = np.load(f"{out}/shot-{label}-{i}.npy").astype(float)
            magenta = np.all(np.abs(img - MAGENTA) <= 8, axis=2)
            # A frame is on the screen once the X server ran its present, between the
            # present's call and its return: the last one whose present returned before the
            # screenshot, or a later one whose present was called. The screen shows the one
            # with the fewest pixels outside the rectangles of its windows that aren't the
            # backdrop.
            first = max(m for m in frames if returns[m] <= t0)
            last = max(m for m in frames if calls[m] <= t0)
            results = [frame_check(n, img, magenta, W, E, I, U, outline)
                       for n in range(first, last + 1)]
            n, lines, inside, share, _ = min(results, key=lambda r: r[4])
            holes += inside
            worst = max(worst, share)
            for text in lines:
                print(f"{label}-{i} {text}")
    print(f"magenta inside rectangles: {holes} px; the largest share of a gap not within 3 "
          f"of its fill: {worst:.4f}")


def frame_check(n, img, magenta, W, E, I, U, outline):
    """Frame n against a screenshot: (n, a line per tile window, the magenta inside their
    rectangles, the largest share of a gap not within 3 of its fill, the pixels outside the
    rectangles of all windows but the backdrop that aren't magenta)."""
    lines, holes, worst = [], 0, 0
    order = list(W[n])
    covered = np.zeros(img.shape[:2], bool)
    for w in order[1:]:
        if W[n][w][3] == "D":
            x0, y0, x1, y1 = outline(W[n][w])
            covered[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = True
    stray = int((~magenta & ~covered).sum())
    for k, w in enumerate(order):
        wr, s = W[n][w], E[n].get(w)
        if s is None or "var-on" not in s or wr[3] != "D" or w not in I[n]:
            continue
        x0, y0, x1, y1 = outline(wr)
        inside = int(magenta[max(0, y0 + 2):max(0, y1 - 2),
                             max(0, x0 + 2):max(0, x1 - 2)].sum())
        holes += inside
        fill, opacity = float(I[n][w][9]), float(wr[16])
        colour = np.array([float(v) for v in I[n][w][10:13]]) * 255
        backdrop, *rgb = U[n].get(w, [0, 0, 0, 0])
        under = (np.array(rgb) * 255 * backdrop * opacity +
                 MAGENTA * (1 - backdrop * opacity))
        want = colour * fill * opacity + under * (1 - fill * opacity)
        mask = np.zeros(img.shape[:2], bool)
        for gx0, gy0, gx1, gy1 in gaps((x0, y0, x1, y1), I[n][w]):
            mask[max(0, int(gy0) + 2):max(0, int(gy1) - 2),
                 max(0, int(gx0) + 2):max(0, int(gx1) - 2)] = True
        # What covers the gap, and where the fill is translucent what lies under
        # it but the backdrop, the lowest window, makes its colour another.
        others = (order[k + 1:] if max(fill, backdrop) * opacity >= 1
                  else order[1:k] + order[k + 1:])
        for other in others:
            if W[n][other][3] == "D":
                ax0, ay0, ax1, ay1 = outline(W[n][other])
                mask[max(0, ay0 - 2):ay1 + 2, max(0, ax0 - 2):ax1 + 2] = False
        text = f"frame {n} window {w}: {inside} magenta px inside"
        if mask.any():
            px = img[mask]
            close = float((np.abs(px - want).max(axis=1) <= 3).mean())
            worst = max(worst, 1 - close)
            text += (f"; gap {int(mask.sum())} px, fill {fill:g} at opacity {opacity:g}"
                     f" over a backdrop of {backdrop:g}"
                     f" of {np.round(want, 1).tolist()}, {close:.4f} of it within 3")
        lines.append(text)
    return n, lines, holes, worst, stray


def main():
    if sys.argv[1:2] == ["backdrop"]:
        backdrop()
    elif sys.argv[1:2] == ["shots"]:
        shots(sys.argv[2], int(sys.argv[3]), float(sys.argv[4]))
    elif sys.argv[1:2] == ["check"]:
        check(sys.argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
