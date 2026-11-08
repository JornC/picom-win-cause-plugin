#!/usr/bin/env python3
"""Runs changes on a desktop of its own, and keeps what they did.

A list of programs opens on Xvfb with i3, picom and the plugin, then each scene runs
once: real keys (xdotool), i3 commands, or programs that open and close. Into OUT goes:

  marks          M,monotonic us,scene, a line per scene, then one "end" line
  frames.csv     picom's frame log with the layout records
  picom.log      picom's log
  windows        client window, name, a line per window

Usage: scene.py OUT --program NAME=KIND [--program ...] --scene ACTIONS [--scene ...]
                [--name LABEL] [--size WxH] [--wait S] [--gaps PX] [--backend B]
                [--rounds N] [--config PATH] [--i3 LINE ...]

KIND is xterm, wezterm, chrome, or slow:DELAY[:sync], which is tests/slowclient. ACTIONS is
a comma separated list of what a scene does, one at a time:

  key:KEYS       xdotool key KEYS (alt+shift+Down is move down, alt+shift+Right move right,
                 alt+shift+space floating toggle, alt+f fullscreen toggle)
  i3:COMMAND     an i3 command, through i3-msg: another client, as far as i3 sees
  open:NAME=KIND opens a program, close:NAME closes it
  sleep:S        waits S seconds; the scene's end comes --wait seconds after its last action
  exec:COMMAND   runs COMMAND with sh on the display, OUT in $SCENE_OUT, and waits for it

--name gives a scene its label, in order; its actions by default. --config runs picom with
that picom.conf instead of the plugin alone, as it is: picom finds what it includes and its
plugins as usual (next to it, then XDG_CONFIG_HOME and XDG_CONFIG_DIRS). Each --i3 line
goes at the end of i3's config. Needs PICOM. Holds
the display in WC_DISPLAY, :97 by default, so run it where nothing else does.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PICOM = os.environ["PICOM"]
DISPLAY = os.environ.get("WC_DISPLAY", ":97")

I3_CONFIG = """ipc-socket {socket}
font pango:monospace 9
focus_follows_mouse no
default_border pixel 2
gaps inner {gaps}
bindsym Mod1+Shift+Down move down
bindsym Mod1+Shift+Up move up
bindsym Mod1+Shift+Left move left
bindsym Mod1+Shift+Right move right
bindsym Mod1+Shift+space floating toggle
bindsym Mod1+f fullscreen toggle
"""

PICOM_CONFIG = """plugins = ["{plugin}"];
vsync = true;
"""


class Scene:
    def __init__(self, out, a):
        self.out, self.a = out, a
        os.makedirs(out, exist_ok=True)
        self.work = tempfile.mkdtemp(prefix="win-cause-scene-", dir=os.environ.get("XDG_RUNTIME_DIR"))
        self.socket = f"{self.work}/i3.sock"
        self.env = dict(os.environ, DISPLAY=DISPLAY, I3SOCK=self.socket,
                        LIBGL_ALWAYS_SOFTWARE="1")
        self.env.pop("WAYLAND_DISPLAY", None)
        self.processes = []
        self.programs = {}
        self.marks = open(f"{out}/marks", "w")

    def spawn(self, *command):
        process = subprocess.Popen(command, env=self.env, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, cwd=self.work)
        self.processes.append(process)
        return process

    def i3(self, *args):
        return subprocess.run(["i3-msg", "-s", self.socket, *args], env=self.env,
                              capture_output=True, text=True).stdout

    def wait_for(self, check, what, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check():
                return
            time.sleep(0.02)
        raise AssertionError(f"timed out waiting for {what}")

    def windows(self):
        """[(class or instance, title, frame's rect) of each window], as i3 has them."""
        found = []

        def walk(node):
            if node.get("window"):
                found.append(node)
            for child in node.get("nodes", []) + node.get("floating_nodes", []):
                walk(child)

        walk(json.loads(self.i3("-t", "get_tree")))
        return found

    def command_of(self, name, kind):
        if kind == "xterm":
            return ["xterm", "-T", name, "-xrm", "XTerm*allowTitleOps: false", "-e", "sh", "-c",
                    "ls -la /usr/lib | head -60; exec sleep 100000"]
        if kind == "wezterm":
            config = f"{self.work}/wezterm.lua"
            with open(config, "w") as f:
                f.write('return { enable_wayland = false, front_end = "Software",'
                        ' window_close_confirmation = "NeverPrompt" }\n')
            return ["wezterm", "--config-file", config, "start", "--always-new-process",
                    "--class", name, "--", "sh", "-c", "ls -la /usr/lib | head -60; exec sleep 100000"]
        if kind == "chrome":
            profile = f"{self.work}/chrome-{name}"
            page = f"{self.work}/{name}.html"
            with open(page, "w") as f:
                f.write(f"<title>{name}</title><body style='margin:0;font:18px sans-serif'>"
                        + "<p>The quick brown fox jumps over the lazy dog. " * 400)
            return ["google-chrome-stable", f"--user-data-dir={profile}", "--no-first-run",
                    "--no-default-browser-check", "--disable-sync", "--disable-gpu",
                    "--password-store=basic", "--ozone-platform=x11", f"--class={name}",
                    "--new-window", f"file://{page}"]
        if kind.startswith("slow:"):
            parts = kind.split(":")
            return [f"{ROOT}/build/slowclient", name, parts[1], *parts[2:]]
        raise SystemExit(f"unknown program kind {kind}")

    def open(self, name, kind):
        before = {n["window"] for n in self.windows()}
        process = self.spawn(*self.command_of(name, kind))
        self.programs[name] = process

        def there():
            for node in self.windows():
                if node["window"] not in before:
                    self.names[node["window"]] = name
                    return True
            return False

        self.wait_for(there, f"window {name}", 40)

    def close(self, name):
        self.i3(f"[id={self.window_of(name)}] kill")
        self.programs.pop(name, None)

    def window_of(self, name):
        for window, n in self.names.items():
            if n == name:
                return window
        raise SystemExit(f"no window {name}")

    def run_action(self, action):
        verb, _, rest = action.partition(":")
        if verb == "key":
            subprocess.run(["xdotool", "key", "--clearmodifiers", rest], env=self.env, check=True)
        elif verb == "i3":
            self.i3(rest)
        elif verb == "open":
            self.open(*rest.split("=", 1))
        elif verb == "close":
            self.close(rest)
        elif verb == "sleep":
            time.sleep(float(rest))
        elif verb == "exec":
            subprocess.run(["sh", "-c", rest], env=dict(self.env, SCENE_OUT=self.out), check=True)
        else:
            raise SystemExit(f"unknown action {action}")

    def mark(self, label):
        self.marks.write(f"M,{time.monotonic_ns() // 1000},{label}\n")
        self.marks.flush()

    def run(self):
        a = self.a
        width, height = a.size.split("x")
        self.names = {}
        with open(f"{self.work}/i3.conf", "w") as f:
            f.write(I3_CONFIG.format(socket=self.socket, gaps=a.gaps))
            f.write("".join(f"{line}\n" for line in a.i3))
        config = a.config
        if config is None:
            config = f"{self.work}/picom.conf"
            with open(config, "w") as f:
                f.write(PICOM_CONFIG.format(plugin=f"{ROOT}/build/win-cause.so"))
        try:
            self.spawn("Xvfb", DISPLAY, "-screen", "0", f"{width}x{height}x24", "+extension", "GLX",
                       "+extension", "RANDR", "-nolisten", "tcp", "-noreset")
            time.sleep(0.5)
            self.spawn("i3", "-c", f"{self.work}/i3.conf")
            self.wait_for(lambda: os.path.exists(self.socket), "i3")
            picom = subprocess.Popen(
                [PICOM, "--backend", a.backend, "--log-level=debug",
                 f"--log-file={self.out}/picom.log", f"--config={config}"],
                env=dict(self.env, PICOM_DEBUG=f"frame_timing={self.out}/frames.csv;frame_layout=1"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.processes.append(picom)
            time.sleep(1.0)
            for program in a.program:
                self.open(*program.split("=", 1))
                time.sleep(a.settle)
            time.sleep(1.0)
            names = a.name or []
            for _ in range(a.rounds):
                for number, actions in enumerate(a.scene):
                    self.mark(names[number] if number < len(names) else actions)
                    for action in actions.split(","):
                        self.run_action(action)
                    time.sleep(a.wait)
            self.mark("end")
            with open(f"{self.out}/windows", "w") as f:
                for client, name in self.names.items():
                    f.write(f"{client:#x} {name}\n")
        finally:
            for process in reversed(self.processes):
                process.terminate()
            time.sleep(0.2)
            for process in self.processes:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            shutil.rmtree(self.work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--program", action="append", default=[])
    ap.add_argument("--scene", action="append", default=[])
    ap.add_argument("--name", action="append")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--wait", type=float, default=2.0)
    ap.add_argument("--settle", type=float, default=0.6, help="seconds after each program opens")
    ap.add_argument("--gaps", type=int, default=10)
    ap.add_argument("--backend", default="xrender")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--config")
    ap.add_argument("--i3", action="append", default=[])
    a = ap.parse_args()
    Scene(a.out, a).run()


if __name__ == "__main__":
    sys.exit(main())
