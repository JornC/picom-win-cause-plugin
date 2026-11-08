#!/usr/bin/env python3
"""Checks which trigger each real i3 action starts, from picom's own log.

Needs PICOM, a picom with the plugin API that src/win_cause.h names. Runs on its own Xvfb.
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
COMMAND = f"{ROOT}/build/win-cause"
PICOM = os.environ["PICOM"]
DISPLAY = os.environ.get("WC_DISPLAY", ":97")
BACKEND = os.environ.get("WC_BACKEND", "xrender")
ROUNDS = int(os.environ.get("WC_ROUNDS", "5"))
OWN = {"size", "position"}

work = tempfile.mkdtemp(prefix="win-cause-", dir=os.environ.get("XDG_RUNTIME_DIR"))
log_file = f"{work}/picom.log"
socket = f"{work}/i3.sock"
env = dict(os.environ, DISPLAY=DISPLAY, I3SOCK=socket)
env.pop("WAYLAND_DISPLAY", None)
log_seen = 0
settling = 0.25
failures = []
checks = 0


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


def tree_windows():
	"""{name: client window} of i3's windows."""
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
	"""What picom started since the last call, as {window name: [trigger, ...]}."""
	global log_seen
	with open(log_file, "rb") as f:
		f.seek(log_seen)
		text = f.read().decode(errors="replace")
		log_seen = f.tell()
	found = {}
	for trigger, name in re.findall(r"Starting animation (\S+) for window \S+ \((.*?)\)", text):
		found.setdefault(name, []).append(trigger)
	return found


def settle():
	time.sleep(settling)


def check(step, expected, quiet=()):
	"""Each window of `expected` started only those triggers, those in `quiet` nothing."""
	global checks
	checks += 1
	settle()
	seen = starts()
	for name, wanted in expected.items():
		got = set(seen.get(name, []))
		if not got or not got <= wanted:
			failures.append(f"{step}: {name} started {sorted(got)}, wanted {sorted(wanted)}")
	for name in quiet:
		if name in seen:
			failures.append(f"{step}: {name} started {sorted(seen[name])}, wanted nothing")


def wm_command(*args):
	return [COMMAND, "run", *args]


with open(f"{work}/i3.conf", "w") as f:
	f.write(f"ipc-socket {socket}\nfont pango:monospace 9\ndefault_border pixel 2\n"
	        "focus_follows_mouse no\n"
	        'for_window [title="^floats$"] floating enable\n'
	        'for_window [title="^bigone$"] fullscreen enable\n'
	        'for_window [title="^jumps$"] move container to workspace 2, workspace 2\n')

x = subprocess.Popen(
	["Xvfb", DISPLAY, "-screen", "0", "1280x720x24", "+extension", "GLX",
	 "+extension", "RANDR", "-nolisten", "tcp", "-noreset"],
	stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
processes = [x]
try:
	time.sleep(0.5)
	wm = spawn("i3", "-c", f"{work}/i3.conf")
	processes.append(wm)
	wait_for(lambda: os.path.exists(socket), "i3")
	a = open_window("a")
	b = open_window("b")
	processes += [a, b]
	i3("workspace 2")
	other = open_window("other")
	processes.append(other)
	i3("workspace 1")
	picom = subprocess.Popen(
		[PICOM, "--backend", BACKEND, "--log-level=debug", f"--log-file={log_file}",
		 f"--config={HERE}/picom.conf", "--plugins", PLUGIN],
		env=env,
		stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	processes.append(picom)
	wait_for(lambda: os.path.exists(log_file), "picom")
	time.sleep(0.5)
	ids = tree_windows()
	A, B = str(ids["a"]), str(ids["b"])
	swap = ["i3-msg", "-s", socket, "[title=^b$] swap container with mark x"]
	starts()

	for number in range(ROUNDS):
		# Causes picom can't know.
		c = open_window("c")
		check("open", {"a": {"moved-by-open"}, "b": {"moved-by-open"}, "c": {"open"}})
		i3("[title=^c$] kill")
		check("close through i3", {"a": {"moved-by-close"}, "b": {"moved-by-close"}})
		wait_for(lambda: c.poll() is not None, "c to go")
		c = open_window("c")
		check("open again", {"a": {"moved-by-open"}, "b": {"moved-by-open"}})
		c.terminate()
		c.wait()
		check("close by the app", {"a": {"moved-by-close"}, "b": {"moved-by-close"}})
		i3("[title=^a$] fullscreen enable")
		check("fullscreen on", {"a": {"moved-by-fullscreen"}, "b": {"hide"}})
		i3("[title=^a$] fullscreen disable")
		check("fullscreen off", {"a": {"moved-by-fullscreen"}, "b": {"show"}})
		i3("[title=^a$] floating enable")
		check("floating on", {"a": {"moved-by-float"}, "b": {"moved-by-float"}})
		i3("[title=^a$] floating disable")
		check("floating off: the signal comes after the move", {"a": OWN, "b": OWN})
		i3("[title=^a$] focus")

		# A window that opens or hides keeps that trigger, whatever moved it.
		d = open_window("floats")
		check("new window that floats", {"floats": {"open"}}, quiet=["a", "b"])
		d.terminate()
		d.wait()
		settle()
		starts()
		d = open_window("bigone")
		check("new window that goes fullscreen", {"bigone": {"open"}, "a": {"hide"}, "b": {"hide"}})
		d.terminate()
		d.wait()
		settle()
		starts()
		d = open_window("jumps")
		# The first time picom sees it mapped, it opens.
		check("new window with a switch",
		      {"other": {"show", "open"}, "a": {"hide"}, "b": {"hide"}})
		d.terminate()
		d.wait()
		i3("workspace 1")
		settle()
		starts()

		# A window with no script for a cause keeps its own trigger.
		plain = open_window("plain")
		settle()
		starts()
		c = open_window("c")
		check("no script: open", {"plain": OWN, "a": {"moved-by-open"}})
		c.terminate()
		c.wait()
		check("no script: close", {"plain": OWN, "a": {"moved-by-close"}})
		plain.terminate()
		plain.wait()
		settle()
		starts()

		# Moves with no cause keep their own trigger.
		i3("[title=^a$] resize grow width 20 px or 2 ppt")
		check("resize", {"a": OWN, "b": OWN})
		i3("[title=^a$] mark x")
		i3("[title=^b$] swap container with mark x")
		check("swap without run", {"a": OWN, "b": OWN})

		# A command that says why, with and without a list of windows.
		subprocess.run(wm_command("swirl", A, B, "--", *swap), env=env, capture_output=True)
		check("run with windows", {"a": {"swirl"}, "b": {"swirl"}})
		i3("[title=^a$] resize grow width 20 px or 2 ppt")
		check("no swirl left after run", {"a": OWN, "b": OWN})
		subprocess.run(wm_command("swirl", "--", *swap), env=env, capture_output=True)
		check("run without windows", {"a": {"swirl"}, "b": {"swirl"}})
		i3("[title=^a$] resize shrink width 20 px or 2 ppt")
		check("no swirl left after run without windows", {"a": OWN, "b": OWN})
		subprocess.run(wm_command("swirl", A, "--", *swap), env=env, capture_output=True)
		check("the list limits the name", {"a": {"swirl"}, "b": OWN})

		# A command that moves nothing leaves nothing armed.
		subprocess.run(
			wm_command("swirl", A, B, "--", "i3-msg", "-s", socket, "[title=^nothing$] kill"),
			env=env, capture_output=True)
		settle()
		starts()
		i3("[title=^a$] resize grow width 20 px or 2 ppt")
		check("run that moved nothing", {"a": OWN, "b": OWN})

		# The cause lasts as long as the command, not for a set time.
		subprocess.run(
			wm_command("swirl", A, B, "--", "sh", "-c",
			           f"sleep 1; i3-msg -s {socket} '[title=^b$] swap container with mark x'"),
			env=env, capture_output=True)
		check("run that takes a second", {"a": {"swirl"}, "b": {"swirl"}})
		i3("[title=^a$] unmark x")
		i3("[title=^a$] resize shrink width 20 px or 2 ppt")
		check("back to the start", {"a": OWN, "b": OWN})

	# A command that is killed leaves nothing armed.
	run = subprocess.Popen(
		wm_command("swirl", "--", "sleep", "31.5"), env=env,
		stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	time.sleep(0.5)
	run.send_signal(signal.SIGKILL)
	run.wait()
	subprocess.run(["pkill", "-f", "^sleep 31.5$"])
	settle()
	starts()
	i3("[title=^a$] resize grow width 20 px or 2 ppt")
	check("run that was killed", {"a": OWN, "b": OWN})

	# Causes go on after picom reloads and after i3 restarts.
	picom.send_signal(signal.SIGUSR1)
	time.sleep(1.5)
	starts()
	for attempt in range(2):
		c = open_window("c")
		check("open after a reload", {"a": {"moved-by-open"}} if attempt else {})
		c.terminate()
		c.wait()
		check("close after a reload", {"a": {"moved-by-close"}} if attempt else {})
	i3("restart")
	time.sleep(2)
	starts()
	c = open_window("c")
	check("open after a restart", {"a": {"moved-by-open"}, "b": {"moved-by-open"}})
	c.terminate()
	c.wait()
	check("close after a restart", {"a": {"moved-by-close"}, "b": {"moved-by-close"}})

except AssertionError as error:
	failures.append(str(error))
finally:
	for process in reversed(processes):
		process.terminate()
	for process in processes:
		process.wait()

print(f"{ROUNDS} rounds on {BACKEND}: {checks} checks, {len(failures)} failed")
for failure in failures[:30]:
	print("FAIL", failure)
if failures:
	print(f"log: {log_file}")
else:
	shutil.rmtree(work)
sys.exit(1 if failures else 0)
