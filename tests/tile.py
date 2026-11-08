#!/usr/bin/env python3
"""Checks the tile style on a stock i3: real i3, xterm and picom on its own Xvfb.

far        A window that shares no line with its neighbours goes into its far side: two
           neighbours that close at once (picom stopped while they close, so it reads both
           in one pass) both shrink, away from the window that takes both places; a window
           left of a column of two closes into its left edge, and one right of it opens out
           of and closes into its right edge. The last windows on a workspace have no line.
stacking   Two windows swapped and swapped back while they overlap: the one in front stays
           in front (check-tile's stacking check, and a Z record). A floating window over a
           split, and one in front of it that turns tiled: check-tile's stacking check
           passes.
dock       Two windows side by side above a bar (an i3bar dock at the bottom) trade places:
           a dock is no tile, so no side of theirs lies on a seam.
interrupt  With var-length 1 s, a change that comes at about 0.3, 0.5 and 0.7 of the one
           before: floating on then off, two resizes, an open then a close, two opens.
backdrop   A window whose first picture is still empty (tests/slowclient argb, as wezterm
           and Chrome open) has a backdrop under its rectangle, in the median colour of
           the last window of its class, and nothing of what lies behind shows inside its
           rectangle as it opens (tests/screen.py); for a class picom has no median of,
           the style's colour. A window drawn at half alpha gets a backdrop at that alpha. A
           translucent window that moves gets a backdrop that fades in and out.

tests/check-tile.py passes on what picom drew in each part: no rectangle or picture jumps,
no scaled pictures, and the part's own checks.

Needs PICOM, a picom with the plugin API that src/win_cause.h names, WS_DIRECTION, the root
of a built ws-direction tree, for check-tile.py, and numpy for tests/screen.py.
WC_ONLY=far,stacking,... runs only those parts. Runs on its own Xvfb.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = f"{ROOT}/build/win-cause.so"
PICOM = os.environ["PICOM"]
if not os.environ.get("WS_DIRECTION"):
	sys.exit("Needs WS_DIRECTION, for check-tile.py")
DISPLAY = os.environ.get("WC_DISPLAY", ":97")
BACKEND = os.environ.get("WC_BACKEND", "xrender")
WIDTH, HEIGHT = 1280, 720
LENGTH = 0.5
PARTS = ("far", "stacking", "dock", "interrupt", "backdrop")
ONLY = [p for p in os.environ.get("WC_ONLY", "").split(",") if p] or PARTS

work = tempfile.mkdtemp(prefix="win-cause-tile-", dir=os.environ.get("XDG_RUNTIME_DIR"))
socket = f"{work}/i3.sock"
env = dict(os.environ, DISPLAY=DISPLAY, I3SOCK=socket)
env.pop("WAYLAND_DISPLAY", None)
failures = []
checks = 0
seen = {}
# The picom that runs: its log and its frame log.
current = {}
# The frame of each window, by name.
frame_ids = {}
processes = []


def spawn(*command):
	return subprocess.Popen(
		command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def i3(*args):
	return subprocess.run(
		["i3-msg", "-s", socket, *args], env=env, capture_output=True, text=True).stdout


def wait_for(check, what, timeout=5.0):
	deadline = time.time() + timeout
	while time.time() < deadline:
		if check():
			return
		time.sleep(0.02)
	raise AssertionError(f"timed out waiting for {what}")


def tree():
	return json.loads(i3("-t", "get_tree"))


def names():
	found = set()

	def walk(node):
		if node.get("window"):
			found.add(node["name"])
		for child in node.get("nodes", []) + node.get("floating_nodes", []):
			walk(child)

	walk(tree())
	return found


def open_window(name):
	process = spawn("xterm", "-T", name, "-xrm", "XTerm*allowTitleOps: false",
	                "-e", "sh", "-c", "ls -la /usr/lib | head -60; exec sleep 1000")
	wait_for(lambda: name in names(), f"window {name}")
	return process


def close(*windows):
	for process in windows:
		process.terminate()
		process.wait()


def close_apart(*windows):
	"""Closes windows one at a time, each after the last one's animation."""
	for process in windows:
		close(process)
		settle(LENGTH + 0.4)


def read_new(path):
	try:
		with open(path, "rb") as f:
			f.seek(seen.get(path, 0))
			data = f.read()
	except FileNotFoundError:
		return ""
	data = data[:data.rfind(b"\n") + 1]
	seen[path] = seen.get(path, 0) + len(data)
	return data.decode(errors="replace")


def settle(seconds=0.6):
	time.sleep(seconds)


def fail(message):
	failures.append(message)


def check(ok, message):
	global checks
	checks += 1
	if not ok:
		fail(message)


def started():
	"""Notes the frame of each window that started an animation since the last call."""
	for frame, name in re.findall(r"Starting animation \S+ for window (\S+) \((.*?)\)",
	                              read_new(current["log"])):
		frame_ids[name] = frame


def start_picom(tag, length=None):
	"""A picom of its own for a part of the test, with the tile style, its own logs, and
	the style's var-length if given."""
	conf = f"{work}/conf-{tag}"
	os.makedirs(conf)
	shutil.copytree(f"{ROOT}/styles", f"{conf}/win-cause")
	if length is not None:
		part = f"{conf}/win-cause/parts/tile.conf"
		with open(part) as f:
			text = re.sub(r"^var-length = .*;", f"var-length = {length};", f.read(),
			              flags=re.M)
		with open(part, "w") as f:
			f.write(text)
	with open(f"{conf}/picom.conf", "w") as f:
		f.write(f"""plugins = ["{PLUGIN}"];
vsync = true;
rules = ({{
  match = "!override_redirect && window_type = 'normal'";
  @include "win-cause/tile.conf"
}}, {{
  match = "!override_redirect && window_type = 'normal' && !I3_FLOATING_WINDOW@";
  @include "win-cause/tile-geometry.conf"
}}, {{
  match = "name = 'glass'";
  opacity = 0.6;
}});
""")
	current["log"] = f"{work}/picom-{tag}.log"
	current["frames"] = f"{work}/frames-{tag}.csv"
	process = subprocess.Popen(
		[PICOM, "--backend", BACKEND, "--log-level=debug", f"--log-file={current['log']}",
		 f"--config={conf}/picom.conf"],
		env=dict(env, PICOM_DEBUG=f"frame_timing={current['frames']};frame_layout=1"),
		stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	wait_for(lambda: os.path.exists(current["log"]), "picom")
	time.sleep(1.0)
	started()
	return process


def stop(process):
	process.terminate()
	process.wait()


def frame_log_size():
	return os.path.getsize(current["frames"])


def frames_from(offset, path):
	"""Copies the frame log's header and what it has after `offset` to `path`."""
	with open(current["frames"], "rb") as f:
		header = f.readline()
		f.seek(offset)
		data = f.read()
	with open(path, "wb") as f:
		f.write(header + data[:data.rfind(b"\n") + 1])


def records_of(offset, kind, window):
	"""The records of `kind` of `window` in the frame log after `offset`, split."""
	with open(current["frames"], "rb") as f:
		f.seek(offset)
		text = f.read().decode(errors="replace")
	return [p for p in (line.split(",") for line in text[:text.rfind("\n") + 1].splitlines())
	        if p[0] == kind and p[2] == window]


def script_at_start(name, trigger):
	"""The script values of the last animation `trigger` (or one of a tuple of them) of
	window `name` in the frame log, in the frame it started."""
	triggers = (trigger,) if isinstance(trigger, str) else trigger
	frame = frame_ids.get(name)
	values, waiting = None, False
	with open(current["frames"]) as f:
		for line in f:
			p = line.rstrip("\n").split(",")
			if p[0] == "A" and p[2] == frame and p[3] in triggers:
				waiting = True
			elif p[0] == "E" and p[2] == frame and waiting:
				values = {k: float(v) for k, v in (kv.split("=", 1) for kv in p[5:])}
				waiting = False
	return values


def check_tile(log, allowed=(), required=None, monitors=((0, 0, WIDTH, HEIGHT),)):
	"""tests/check-tile.py on a frame log: no check fails but those `allowed`, or if
	`required` is given, none of those fails. Returns what it printed."""
	result = subprocess.run(
		[sys.executable, f"{HERE}/check-tile.py", log,
		 *[a for m in monitors for a in ("--monitor", ",".join(str(v) for v in m))]],
		env=dict(os.environ), capture_output=True, text=True)
	failed = {l.split()[1].rstrip(":") for l in result.stdout.splitlines()
	          if l.startswith("FAIL")}
	bad = failed & set(required) if required is not None else failed - set(allowed)
	check(result.returncode in (0, 1) and "rest:" in result.stdout and not bad,
	      f"check-tile.py {os.path.basename(log)}: {result.stderr}" + "\n".join(
	          l for l in result.stdout.splitlines() if not l.startswith(("ok", "info"))))
	return result.stdout


def far_side():
	i3("workspace 4")
	picom = start_picom("far")

	def goes(step, name, trigger, side, far=None):
		"""The `trigger` script of window `name` (open or close) has a line on `side` of
		its rect, or none if `side` is None: for an open on that edge, for a close there
		or beyond it, where the windows that take its place end. With `far`, whether the
		line is its far side (1) or a split line it shares (0)."""
		global checks
		checks += 1
		started()
		values = script_at_start(name, trigger)
		if values is None:
			fail(f"{step}: no {trigger} of {name} in the frame log")
			return
		own = [values[f"var-{k}{'1' if trigger == 'open' else '0'}"] for k in "xywh"]
		line = [values[f"var-{k}{'0' if trigger == 'open' else '1'}"] for k in "xywh"]
		if side is None:
			if values["var-line"] != 0:
				fail(f"{step}: {name} has a line {line}, wanted none")
			return
		x, y, w, h = own
		want = {"left": (x, y, 0, h), "right": (x + w, y, 0, h), "top": (x, y, w, 0),
		        "bottom": (x, y + h, w, 0)}[side]
		# How far beyond the edge a close's line may lie: x for left and right.
		axis = 0 if side in ("left", "right") else 1
		beyond = line[axis] - want[axis]
		beyond = -beyond if side in ("left", "top") else beyond
		near = [abs(a - b) <= 0.5 for a, b in zip(line, want)]
		near[axis] = abs(beyond) <= 0.5 or (trigger == "close" and beyond > 0)
		if values["var-line"] != 1 or not all(near):
			fail(f"{step}: {name} {trigger}s on line {values['var-line']} {line}, wanted "
			     f"its {side} side {want}")
		if far is not None and values.get("var-far") != far:
			fail(f"{step}: {name}'s line has far {values.get('var-far')}, wanted {far}")

	def at_once(*windows):
		"""Closes windows while picom is stopped, as a busy picom is: it reads all of it
		in one pass."""
		picom.send_signal(signal.SIGSTOP)
		try:
			close(*windows)
			time.sleep(0.2)
		finally:
			picom.send_signal(signal.SIGCONT)
		settle()

	def row(*row_names):
		windows = []
		for name in row_names:
			windows.append(open_window(name))
			settle()
		return windows

	k1, = row("k1")
	# Two neighbours that close at once: the one left takes both places, beyond both
	# edges of the first, so they share no line. The middle one goes away from it.
	k2, k3 = row("k2", "k3")
	# Both close into the line where it ends, cut where it reaches (check-tile's cover).
	at_once(k2, k3)
	goes("two close at once, the middle one", "k2", "close", "right", far=1)
	goes("two close at once, the last one", "k3", "close", "right")
	k2, k3 = row("k2", "k3")
	at_once(k1, k2)
	goes("two close at once, the first one", "k1", "close", "left")
	goes("two close at once, the middle one", "k2", "close", "left", far=1)
	# A lone window has no line.
	close(k3)
	settle()
	goes("a lone window", "k3", "close", None)
	# One that closes on its own goes into the split line it shares with the window that
	# takes its place.
	k1, k2, k3 = row("k1", "k2", "k3")
	close(k3)
	settle()
	goes("one that closes", "k3", "close", "right", far=0)
	# Next to a split of the other axis: both windows of the column take its place, or
	# make room for it.
	i3("[title=^k2$] focus")
	i3("split v")
	k3, = row("k3")
	close(k1)
	settle()
	goes("left of a column", "k1", "close", "left", far=1)
	i3("[title=^k2$] focus")
	i3("focus parent")
	k1, = row("k1")
	goes("right of a column", "k1", "open", "right", far=1)
	close(k1)
	settle()
	goes("right of a column", "k1", "close", "right", far=1)
	# Neighbours that all close leave the space empty: nothing takes their place.
	at_once(k2, k3)
	goes("the last two at once", "k2", "close", None)
	goes("the last two at once", "k3", "close", None)
	stop(picom)
	check_tile(current["frames"])


def swap_back():
	"""Two windows side by side swap places by a move, and swap back at about 0.4 of the
	animation, while they overlap. i3 restacks them at both moves; the one in front stays
	in front while they overlap: check-tile's stacking check, on pairs it found, and a Z
	record of picom's that draws them other than in the stacking order."""
	i3("workspace 24")
	s1 = open_window("s1")
	settle()
	s2 = open_window("s2")
	settle(1)
	offset = frame_log_size()
	i3("[title=^s1$] move right")
	time.sleep(LENGTH * 0.4)
	i3("[title=^s1$] move left")
	settle(LENGTH + 0.8)
	path = f"{work}/frames-swap-back.csv"
	frames_from(offset, path)
	out = check_tile(path, required=("stacking",))
	found = re.search(r"stacking: (\d+) steps", out)
	check(found and int(found.group(1)) > 0, f"swap back: no tile in front of a window it "
	      f"overlaps: {out}")
	with open(path) as f:
		check(any(line.startswith("Z,") for line in f), "swap back: no Z record")
	close(s1, s2)
	settle(LENGTH + 0.4)


def floating_back():
	"""A floating window over the middle of a split, and another floating one in front of
	it that turns tiled. i3 raises the first over it at once; picom starts the move a frame
	after the restack, so the raise shows before the move, and the stacking check passes."""
	i3("workspace 25")
	t1 = open_window("t1")
	settle()
	t2 = open_window("t2")
	settle()
	f1 = open_window("f1")
	settle()
	i3("[title=^f1$] floating enable, resize set 700 300, move position center")
	settle(LENGTH + 0.6)
	f2 = open_window("f2")
	settle()
	i3("[title=^f2$] floating enable, resize set 300 250, move position center")
	settle(LENGTH + 0.6)
	offset = frame_log_size()
	i3("[title=^f2$] floating disable")
	settle(LENGTH + 0.8)
	path = f"{work}/frames-floating-back.csv"
	frames_from(offset, path)
	check_tile(path, required=("stacking",))
	close(t1, t2, f1, f2)
	settle(LENGTH + 0.4)


def stacking():
	picom = start_picom("stacking", length=LENGTH)
	swap_back()
	floating_back()
	stop(picom)


def docked():
	"""Whether i3 has a dock, such as a bar, in a dock area."""
	def walk(node):
		return (node.get("type") == "dockarea" and bool(node.get("nodes"))) or any(
			walk(child) for child in node.get("nodes", []))
	return walk(tree())


def dock_swap():
	"""Two windows side by side above a bar (an i3bar dock at the bottom) trade places. A
	dock is no tile, so no side of theirs lies on a seam (plugin-win-cause-seam-*). With a
	picom of its own that runs only while the bar is there, as the bar comes and goes at
	once."""
	with open(f"{work}/i3.conf") as f:
		config = f.read()
	with open(f"{work}/i3.conf", "a") as f:
		f.write("bar {\n  id dock\n  position bottom\n}\n")
	i3("reload")
	i3("workspace 26")
	bar = spawn("i3bar", "--bar_id=dock", f"--socket={socket}")
	wait_for(docked, "i3bar")
	picom = start_picom("dock", length=LENGTH)
	d1 = open_window("d1")
	settle()
	d2 = open_window("d2")
	settle(1)
	i3("[title=^d1$] move right")
	settle(LENGTH + 0.8)
	started()
	for name in ("d1", "d2"):
		script = script_at_start(name, ("position", "size")) or {}
		seams = [script.get(f"var-seam-{side}") for side in ("left", "top", "right", "bottom")]
		check(seams == [0, 0, 0, 0], f"swap above a bar: the sides of {name} on a seam (left, "
		      f"top, right, bottom) are {seams}, wanted none")
	close(d1, d2)
	settle(LENGTH + 0.4)
	stop(picom)
	close(bar)
	with open(f"{work}/i3.conf", "w") as f:
		f.write(config)
	i3("reload")
	wait_for(lambda: not docked(), "the bar to go")


def interrupt():
	"""A change that comes while the one before runs, at 0.3, 0.5 and 0.7 of it, with
	var-length 1 s: no rectangle or picture jumps."""
	i3("workspace 3")
	picom = start_picom("interrupt", length=1)
	h1 = open_window("h1")
	settle(1.5)
	h2 = open_window("h2")
	settle(1.5)
	for share in (0.3, 0.5, 0.7):
		i3("[title=^h2$] floating enable")
		time.sleep(share)
		i3("[title=^h2$] floating disable")
		settle(1.5)
		i3("[title=^h2$] resize grow width 10 px or 10 ppt")
		time.sleep(share)
		i3("[title=^h2$] resize grow width 10 px or 10 ppt")
		settle(1.5)
		i3("[title=^h2$] resize shrink width 20 px or 20 ppt")
		settle(1.5)
		h3 = open_window("h3")
		time.sleep(share)
		close(h3)
		settle(1.5)
		h3 = open_window("h3")
		time.sleep(share)
		h4 = open_window("h4")
		settle(1.5)
		close(h3, h4)
		settle(1.5)
	close(h1, h2)
	settle(1.5)
	stop(picom)
	check_tile(current["frames"], required=("rect", "picture", "scale", "blend"))


def backdrop():
	picom = start_picom("backdrop", length=LENGTH)

	def slow(name, delay, klass, alpha=255):
		process = subprocess.Popen(
			[f"{ROOT}/build/slowclient", name, str(delay), "argb"],
			env=dict(env, SLOWCLIENT_CLASS=klass, SLOWCLIENT_ALPHA=str(alpha)),
			stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
		processes.append(process)
		wait_for(lambda: name in names(), f"window {name}")
		return process

	i3("workspace 19")
	x = open_window("x")
	processes.append(x)
	settle(LENGTH + 0.4)
	# One with a picture at once: picom works out its median, the median of its class.
	a = slow("bd-a", 0, "bd-same")
	settle(LENGTH + 0.6)
	close(a)
	settle(LENGTH + 0.4)
	with open(current["log"], errors="replace") as f:
		medians = re.findall(r"The median colour of window \S+ \(bd-a\) is (\S+), (\S+), "
		                     r"(\S+), alpha (\S+)", f.read())
	known = [[float(v) for v in m[:3]] for m in medians if float(m[3]) > 0]
	median = known[-1] if known else None
	# One of the same class, its first picture empty for 0.4 s; screenshots as it opens
	# once it drew.
	out = tempfile.mkdtemp(prefix="screen-", dir=work)
	behind = subprocess.Popen([sys.executable, f"{HERE}/screen.py", "backdrop"], env=env)
	settle(0.3)
	started()
	offset = frame_log_size()
	b = slow("bd-b", 400, "bd-same")
	settle(0.45)
	subprocess.run([sys.executable, f"{HERE}/screen.py", "shots", "b", "3", "0.06"],
	               env=dict(env, SCENE_OUT=out), check=True)
	settle(LENGTH + 0.4)
	behind.terminate()
	behind.wait()
	started()
	frames_from(offset, f"{out}/frames.csv")
	result = subprocess.run([sys.executable, f"{HERE}/screen.py", "check", out],
	                        capture_output=True, text=True).stdout
	found = re.search(r"magenta inside rectangles: (\d+) px", result)
	check(found and int(found[1]) == 0 and f"window {frame_ids.get('bd-b')}:" in result,
	      f"an empty first picture: what lies behind shows inside its rectangle: {result}")
	under = records_of(offset, "U", frame_ids.get("bd-b"))
	check(under and median is not None and float(under[0][3]) > 0.99 and
	      all(abs(float(v) - m) <= 1e-4 for v, m in zip(under[0][4:7], median)),
	      f"an empty first picture: backdrop {under[:1]}, wanted the median of its class "
	      f"{median}")
	close(b)
	settle(LENGTH + 0.4)
	# One of a class picom has no median of: the style's colour.
	offset = frame_log_size()
	c = slow("bd-c", 400, "bd-other")
	settle(LENGTH + 0.4)
	started()
	under = records_of(offset, "U", frame_ids.get("bd-c"))
	script = records_of(offset, "E", frame_ids.get("bd-c"))
	own = dict(kv.split("=", 1) for kv in script[0][5:]) if script else {}
	own = [float(own.get(f"var-backdrop-{k}", -1)) for k in ("red", "green", "blue")]
	check(under and float(under[0][3]) > 0.99 and
	      all(abs(float(v) - m) <= 1e-4 for v, m in zip(under[0][4:7], own)),
	      f"a class with no median: backdrop {under[:1]}, wanted the style's {own}")
	close(c)
	settle(LENGTH + 0.4)
	# One drawn at half alpha: its backdrop has the alpha of its median, as its fill has.
	offset = frame_log_size()
	h = slow("bd-h", 0, "bd-half", 128)
	settle(LENGTH + 0.4)
	started()
	levels = {p[1]: float(p[3]) for p in records_of(offset, "U", frame_ids.get("bd-h"))}
	known = []
	for p in records_of(offset, "E", frame_ids.get("bd-h")):
		v = dict(kv.split("=", 1) for kv in p[5:])
		if float(v.get("var-fill-alpha", 0)) > 0 and float(v.get("var-bd", 0)) > 0:
			known.append((float(v["var-fill-alpha"]), float(v["var-bd"]), levels.get(p[1], 0)))
	check(known and all(abs(alpha - 128 / 255) <= 0.02 and abs(level - bd * alpha) <= 0.01
	                    for alpha, bd, level in known),
	      f"a window at half alpha: its backdrop is not at that alpha (alpha, var-bd, "
	      f"backdrop): {known[:5]}")
	close(h)
	settle(LENGTH + 0.4)
	# A translucent one: its backdrop fades in and out as it moves.
	g = open_window("glass")
	processes.append(g)
	settle(LENGTH + 0.4)
	offset = frame_log_size()
	i3("[title=^glass$] move left")
	settle(LENGTH + 0.4)
	started()
	under = records_of(offset, "U", frame_ids.get("glass"))
	check(any(float(p[3]) > 0.99 for p in under),
	      f"a translucent window that moves has no backdrop: {under[:3]}")
	frames_from(offset, f"{work}/frames-glass.csv")
	check_tile(f"{work}/frames-glass.csv", required=("backdrop", "opacity"))
	close_apart(g, x)
	stop(picom)


with open(f"{work}/i3.conf", "w") as f:
	f.write(f"ipc-socket {socket}\nfont pango:monospace 9\nfocus_follows_mouse no\n")

x = subprocess.Popen(
	["Xvfb", DISPLAY, "-screen", "0", f"{WIDTH}x{HEIGHT}x24", "+extension", "GLX",
	 "+extension", "RANDR", "-nolisten", "tcp", "-noreset"],
	stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
processes.append(x)
try:
	time.sleep(0.5)
	processes.append(spawn("i3", "-c", f"{work}/i3.conf"))
	wait_for(lambda: os.path.exists(socket), "i3")
	for part, run in (("far", far_side), ("stacking", stacking), ("dock", dock_swap),
	                  ("interrupt", interrupt), ("backdrop", backdrop)):
		if part in ONLY:
			run()
except AssertionError as error:
	failures.append(str(error))
finally:
	for process in reversed(processes):
		process.terminate()
	for process in processes:
		process.wait()

print(f"tile on {BACKEND}, {', '.join(p for p in PARTS if p in ONLY)}: {checks} checks, "
      f"{len(failures)} failed")
for failure in failures[:30]:
	print("FAIL", failure)
if failures:
	print(f"logs: {work}")
else:
	shutil.rmtree(work)
sys.exit(1 if failures else 0)
