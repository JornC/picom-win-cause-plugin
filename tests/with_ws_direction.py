#!/usr/bin/env python3
"""Checks that win-cause and ws-direction work together, in both load orders.

Then with the tile style, as one runs them both: a window that opens, one that closes and
a resize by i3-msg while the overview shows. ws-direction keeps its overview: the windows
start ws-unfold, not the animations of win-cause, and a move starts nothing, as wall.conf
holds moves back. Once it folds, every window is where i3's layout has it. And a workspace
switch and an overview that come during a change: the switch and the overview run as
without it, and the change ends where i3's layout has it.

Needs PICOM, a picom with the plugin API that src/win_cause.h names, and WS_DIRECTION,
the root of a built ws-direction tree. Runs on its own Xvfb.
"""

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
WIN_CAUSE = f"{ROOT}/build/win-cause.so"
TREE = os.environ["WS_DIRECTION"]
WS_DIRECTION = f"{TREE}/build/ws-direction.so"
COMMAND = f"{TREE}/build/ws-direction"
PICOM = os.environ["PICOM"]
DISPLAY = os.environ.get("WC_DISPLAY", ":97")
ROUNDS = int(os.environ.get("WC_ROUNDS", "4"))

CONFIG = """backend = "glx";
rules = ({
    match = "!override_redirect";
    @include "ws-direction/pass-slide.conf"
}, {
    match = "!override_redirect";
    @include "ws-direction/wall.conf"
}, {
    match = "!override_redirect";
    animations = ({
        triggers = ["moved-by-open"];
        offset-y = { duration = 0.05; start = 1; end = 0; };
    }, {
        triggers = ["moved-by-close"];
        offset-y = { duration = 0.05; start = 2; end = 0; };
    });
});
"""


TILE_CONFIG = """backend = "glx";
rules = ({
    match = "!override_redirect && window_type = 'normal'";
    @include "win-cause/tile.conf"
}, {
    match = "!override_redirect && window_type = 'normal' && !I3_FLOATING_WINDOW@";
    @include "win-cause/tile-geometry.conf"
}, {
    match = "!override_redirect";
    @include "ws-direction/pass-slide.conf"
}, {
    match = "!override_redirect";
    @include "ws-direction/wall.conf"
});
"""


def run_order(plugins, tile=False):
	work = tempfile.mkdtemp(prefix="win-cause-", dir=os.environ.get("XDG_RUNTIME_DIR"))
	log_file = f"{work}/picom.log"
	socket = f"{work}/i3.sock"
	env = dict(os.environ, DISPLAY=DISPLAY, I3SOCK=socket, LIBGL_ALWAYS_SOFTWARE="1",
	           WS_DIRECTION_LOG=f"{work}/ws-direction.log")
	seen = 0

	def spawn(*command):
		return subprocess.Popen(
			command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

	def i3(*args):
		return subprocess.run(
			["i3-msg", "-s", socket, *args], env=env, capture_output=True, text=True).stdout

	def wait_for(check, what, timeout=8.0):
		deadline = time.time() + timeout
		while time.time() < deadline:
			if check():
				return
			time.sleep(0.02)
		raise AssertionError(f"timed out waiting for {what}")

	def tree_windows():
		found = {}

		def walk(node):
			if node.get("window"):
				found[node["name"]] = node["window"]
			for child in node.get("nodes", []) + node.get("floating_nodes", []):
				walk(child)

		walk(json.loads(i3("-t", "get_tree")))
		return found

	def open_window(name):
		process = spawn("xterm", "-T", name, "-xrm", "XTerm*allowTitleOps: false")
		wait_for(lambda: name in tree_windows(), f"window {name}")
		return process

	def starts():
		nonlocal seen
		with open(log_file, "rb") as f:
			f.seek(seen)
			text = f.read().decode(errors="replace")
			seen = f.tell()
		found = {}
		for trigger, name in re.findall(
				r"Starting animation (\S+) for window \S+ \((.*?)\)", text):
			found.setdefault(name, []).append(trigger)
		return found

	def overview(command):
		subprocess.run([COMMAND, "overview", command], env=env, timeout=5)

	def off_layout():
		"""The windows that i3 draws elsewhere than its layout."""
		found = []

		def walk(node):
			if node.get("window") and "drawn_rect" in node:
				found.append(node["name"])
			for child in node.get("nodes", []) + node.get("floating_nodes", []):
				walk(child)

		walk(json.loads(i3("-t", "get_tree")))
		return found

	def rects():
		found = {}

		def walk(node):
			if node.get("window"):
				r = node["rect"]
				found[node["name"]] = (r["x"], r["y"], r["width"], r["height"])
			for child in node.get("nodes", []) + node.get("floating_nodes", []):
				walk(child)

		walk(json.loads(i3("-t", "get_tree")))
		return found

	def during_change_round():
		"""What goes wrong when a switch or the overview comes during a change."""
		wrong = []
		before = rects()
		starts()
		i3("[title=^a$] resize grow width 80 px or 8 ppt")
		time.sleep(0.08)
		i3("workspace 2")
		time.sleep(0.6)
		seen = starts()
		if "ws-pass" not in seen.get("other", []):
			wrong.append(f"a switch during a change: no ws-pass on other: {seen}")
		i3("workspace 1")
		time.sleep(0.6)
		seen = starts()
		for name in "ab":
			if "ws-pass" not in seen.get(name, []):
				wrong.append(f"the switch back: no ws-pass on {name}: {seen}")
		i3("[title=^a$] resize shrink width 80 px or 8 ppt")
		time.sleep(0.08)
		overview("open")
		time.sleep(0.6)
		seen = starts()
		for name in "ab":
			if "ws-unfold" not in seen.get(name, []):
				wrong.append(f"an overview during a change: no ws-unfold on {name}: {seen}")
		overview("close")
		time.sleep(0.8)
		starts()
		off = off_layout()
		if off:
			wrong.append(f"after a change that was cut across, i3 draws these elsewhere: {off}")
		after = rects()
		if after != before:
			wrong.append(f"a resize and back ends elsewhere: {before}, then {after}")
		return wrong

	def tile_round():
		"""What goes wrong in a round with the tile style, as a list of texts."""
		wrong = []
		overview("open")
		time.sleep(0.6)
		starts()
		c = open_window("c")
		time.sleep(0.6)
		seen = starts()
		for name in "abc":
			if "ws-unfold" not in seen.get(name, []):
				wrong.append(f"an open: no ws-unfold on {name}: {seen}")
		for name in "ab":
			if set(seen.get(name, [])) != {"ws-unfold"}:
				wrong.append(f"an open: {name} left the overview: {seen}")
		c.terminate()
		c.wait()
		time.sleep(0.6)
		seen = starts()
		for name in "ab":
			if set(seen.get(name, [])) != {"ws-unfold"}:
				wrong.append(f"a close: {name} left the overview: {seen}")
		i3("[title=^a$] resize grow width 80 px or 8 ppt")
		time.sleep(0.6)
		seen = starts()
		if seen:
			wrong.append(f"a resize started animations in the overview: {seen}")
		i3("[title=^a$] resize shrink width 80 px or 8 ppt")
		time.sleep(0.6)
		starts()
		overview("close")
		time.sleep(0.8)
		starts()
		off = off_layout()
		if off:
			wrong.append(f"after the overview, i3 draws these elsewhere: {off}")
		return wrong

	shutil.copytree(f"{TREE}/styles", f"{work}/ws-direction")
	shutil.copytree(f"{ROOT}/styles", f"{work}/win-cause")
	with open(f"{work}/picom.conf", "w") as f:
		f.write(TILE_CONFIG if tile else CONFIG)
	with open(f"{work}/i3.conf", "w") as f:
		f.write(f"ipc-socket {socket}\nfont pango:monospace 9\ndefault_border pixel 2\n"
		        "focus_follows_mouse no\n"
		        'for_window [title="^jumps$"] move container to workspace 2, workspace 2\n')

	lost_unfold = lost_pass = 0
	wrong = []
	processes = [spawn(
		"Xvfb", DISPLAY, "-screen", "0", "1280x720x24", "+extension", "GLX",
		"+extension", "RANDR", "-nolisten", "tcp", "-noreset")]
	try:
		time.sleep(0.5)
		processes.append(spawn("i3", "-c", f"{work}/i3.conf"))
		wait_for(lambda: os.path.exists(socket), "i3")
		processes += [open_window("a"), open_window("b")]
		i3("workspace 2")
		processes.append(open_window("other"))
		i3("workspace 1")
		command = [PICOM, "--log-level=debug", f"--log-file={log_file}",
		           f"--config={work}/picom.conf"]
		for plugin in plugins:
			command += ["--plugins", plugin]
		processes.append(spawn(*command))
		wait_for(lambda: os.path.exists(log_file), "picom")
		time.sleep(1)
		if tile:
			# The first time picom sees "other" mapped, it opens: the switches of the
			# rounds are not that one.
			i3("workspace 2")
			time.sleep(0.6)
			i3("workspace 1")
			time.sleep(0.6)
		starts()
		for _ in range(ROUNDS // 2 if tile else 0):
			wrong += tile_round()
			wrong += during_change_round()
		for _ in range(0 if tile else ROUNDS):
			c = open_window("c")
			time.sleep(0.4)
			starts()
			subprocess.run([COMMAND, "overview", "open"], env=env, timeout=5)
			time.sleep(0.5)
			starts()
			c.terminate()
			c.wait()
			time.sleep(0.5)
			seen_now = starts()
			lost_unfold += sum("ws-unfold" not in seen_now.get(n, []) for n in "ab")
			subprocess.run([COMMAND, "overview", "close"], env=env, timeout=5)
			time.sleep(0.8)
			starts()
		for _ in range(0 if tile else ROUNDS):
			j = open_window("jumps")
			time.sleep(0.5)
			lost_pass += "ws-pass" not in starts().get("other", [])
			j.terminate()
			j.wait()
			time.sleep(0.3)
			i3("workspace 1")
			time.sleep(0.5)
			starts()
	finally:
		for process in reversed(processes):
			process.terminate()
		for process in processes:
			process.wait()
	return lost_unfold, lost_pass, wrong, work


failed = False
for label, plugins in (("win-cause last", [WS_DIRECTION, WIN_CAUSE]),
                       ("win-cause first", [WIN_CAUSE, WS_DIRECTION])):
	lost_unfold, lost_pass, _, work = run_order(plugins)
	print(f"{label}: {lost_unfold} of {2 * ROUNDS} overview refreshes lost, "
	      f"{lost_pass} of {ROUNDS} switches lost")
	if lost_unfold or lost_pass:
		failed = True
		print(f"log: {work}/picom.log")
	else:
		shutil.rmtree(work)
	_, _, wrong, work = run_order(plugins, tile=True)
	print(f"{label}, tile: {len(wrong)} wrong")
	for text in wrong:
		print("FAIL", text)
	if wrong:
		failed = True
		print(f"log: {work}/picom.log")
	else:
		shutil.rmtree(work)
sys.exit(1 if failed else 0)
