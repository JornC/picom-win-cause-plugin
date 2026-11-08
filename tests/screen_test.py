#!/usr/bin/env python3
# Unit tests for screen.py's check, on a synthetic frame log and screenshots: a window whose
# rectangle grows from 20 to 26 px wide between two frames, over the magenta backdrop. A
# screenshot taken after the second frame started but before picom presented it shows the
# first frame, which passes; so do screenshots taken while the second frame's present runs,
# when either frame can be on the screen. A hole in the window's picture fails, also in the
# second frame while its present runs.

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("screen", os.path.join(HERE, "screen.py"))
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)

WINDOW = "0x00000a"
GREY = [128, 128, 128]
# frame: (start, present call, present return, rectangle width)
FRAMES = {1: (100, 110, 120, 20), 2: (200, 260, 270, 26)}


def frame_log():
    # The backdrop, under the window, as screen.py's backdrop puts it.
    lines = ["# picom frame timing log"]
    for n, (start, call, ret, width) in FRAMES.items():
        lines += [
            f"W,{n},0x00000b,D,0,0,40,40,0,0,40,0,0,40,40,40,1,0,0,inf,inf",
            f"W,{n},{WINDOW},D,0,0,30,40,0,0,30,0,0,40,30,40,1,0,0,{width},40",
            f"I,{n},{WINDOW},0,0,30,40,30,40,0,0,0,0,0,0,0,0,30,40,0,0,0,0,image,0,0",
            f"E,{n},{WINDOW},0.1,0.3,var-on=1",
            f"R,{n},{start},{start},0,0,{call},{ret},1",
        ]
    return "\n".join(lines) + "\n"


def check(shots):
    """screen.py check on screenshots [(time, width the window is drawn at, hole)]; the
    magenta it finds inside rectangles."""
    with tempfile.TemporaryDirectory() as out:
        with open(f"{out}/frames.csv", "w") as f:
            f.write(frame_log())
        with open(f"{out}/shot-t.times", "w") as times:
            for i, (t0, width, hole) in enumerate(shots):
                img = np.zeros((40, 40, 3), np.uint8)
                img[...] = screen.MAGENTA
                img[:, :width] = GREY
                if hole:
                    img[10:20, 10] = screen.MAGENTA
                np.save(f"{out}/shot-t-{i}.npy", img)
                times.write(f"{i},{t0},{t0 + 50}\n")
        text = io.StringIO()
        with redirect_stdout(text):
            screen.check(out)
    last = text.getvalue().splitlines()[-1]
    return int(last.split(": ")[1].split(" ")[0])


class ScreenCheck(unittest.TestCase):
    def test_frame_not_presented_yet(self):
        self.assertEqual(check([(230, 20, False)]), 0)

    def test_frame_being_presented(self):
        self.assertEqual(check([(265, 20, False), (265, 26, False)]), 0)

    def test_hole(self):
        self.assertEqual(check([(230, 20, True)]), 10)
        self.assertEqual(check([(265, 26, True)]), 10)


if __name__ == "__main__":
    unittest.main()
