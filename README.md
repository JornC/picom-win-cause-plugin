# win-cause

A picom plugin that tells animation scripts why a window moved, so each cause can have its
own animation. It holds what is specific to i3. picom stays neutral: it only sees trigger
names, and scripts decide how things look. Neither i3 nor picom knows what a swirl is.

Needs plugin API 0.12 of the picom fork (`rename_trigger`, `add_x_event_callback`,
`window_animation_output`). It works with a stock i3.

## Use

    make PICOM_API_CFLAGS=-I/path/to/picom/include
    make install

That puts the plugin where picom finds it by name, and the command in `~/.local/bin`. Then
load the plugin, and write a script for each cause you care about. A window with no script
for a cause starts its usual `geometry` animation, so all of them are optional. The name
says it: the window moved because of that.

| Trigger | Starts on |
|---|---|
| `moved-by-open` | windows that moved because a new window appeared |
| `moved-by-close` | windows that moved because a window went away |
| `moved-by-fullscreen` | the window that went in or out of fullscreen |
| `moved-by-float` | the window that became floating, and the windows that take its place |

The window that opens or closes keeps `open` or `close`. These are in rules of their own,
so they sit next to any other style, such as those of ws-direction:

    plugins = ["ws-direction.so", "win-cause.so"];
    animation-triggers = ["swirl"];
    rules = ({
      animations = ({
        triggers = ["moved-by-open", "moved-by-close", "moved-by-float"];
        preset = "geometry-change";
        duration = 0.25;
      }, {
        triggers = ["swirl"];
        preset = "geometry-change";
        duration = 0.4;
      });
    }, {
      match = "!I3_FLOATING_WINDOW@";
      animations = ({
        triggers = ["geometry"];
        preset = "geometry-change";
        duration = 0.2;
      });
    });

The window that becomes floating is floating by the time it moves, so `moved-by-float` must
be in a rule that matches floating windows, like the first one here. The second leaves them
out, so a mouse drag of a floating window isn't slowed down.

For what i3 doesn't show, a command says why. Any name works, as long as picom.conf declares
it in `animation-triggers`:

    bindsym $mod+m mark --toggle swap
    bindsym $mod+s exec --no-startup-id win-cause run swirl -- i3-msg swap container with mark swap
    bindsym $mod+e exec --no-startup-id win-cause run reflow -- i3-msg layout toggle split
    bindsym $mod+Shift+f exec --no-startup-id win-cause run unfloat -- i3-msg floating disable

Every window the command moves starts that trigger, and `win-cause run swirl <window>... -- ...`
limits it to those client windows. Nothing stays set afterwards, however long the command
takes, even if it moves nothing or gets killed.

A style holds a move back by either name. `suppressions = ["geometry"]` holds back all moves,
and `suppressions = ["moved-by-close"]` only those.

## Variables

For a window that opens or closes, scripts can read the line it splits from its
neighbours, so it can grow out of that line while they make room, and go back into it:

| Variable | Is |
|---|---|
| `plugin-win-cause-line` | 1 if the window has a line, 0 if not |
| `plugin-win-cause-line-far` | 1 if that line is its far side (see below), 0 if it is a split line it shares with its neighbours, or if it has none |
| `plugin-win-cause-line-x`, `plugin-win-cause-line-y` | where the rect the window grows from (`open`) or shrinks into (`close`) is |
| `plugin-win-cause-line-width`, `plugin-win-cause-line-height` | its size: the window's own rect, collapsed onto the line, so one of them is 0 |
| `plugin-win-cause-from-x`, `plugin-win-cause-from-y`, `plugin-win-cause-from-width`, `plugin-win-cause-from-height` | for a close, the rect that shrinks into the line, where it starts: between the edges of the windows that take the window's place; the window shows the part of its own rect inside it. For anything else, the window's own rect |

Like all plugin variables, they are read once, when the animation starts. A window with no
line (the first on its workspace, the last to close, a floating window, a new tab, a window
that opens fullscreen) has `plugin-win-cause-line` 0 and its own rect, as does any other
trigger; a window that i3 doesn't manage has 0 for all of them. So read the rects only where
the line is 1. A style can treat the two kinds of line alike, or a far side
otherwise.

The neighbours are the tiled windows that overlap the window: for an open, where they were
before i3 moved them for it, and for a close, where i3 moved them. A window that closes is
where the screen showed it last: when two windows close at once, i3 gives the first one's
place to the second just before that one goes, which the screen never shows. A neighbour that has the
same span across one axis, of which the window is a part along it, gives the axis. The line
is that neighbour's edge that lies within the window's span, the one nearest the window's
middle if there are more. In a plain i3 split there is exactly one: where the window went
in, or its far edge if it took half of one window. A window that opens right of a full one
grows from the right edge of the screen, and its neighbour's right edge comes with it.

With gaps, the window and its neighbour have them in the same layout only after an open and
before a close, so the span across is compared there. With `smart_gaps on`, the second window
on a workspace brings the gaps with it (and the last but one takes them away): the
neighbour's edge at the side of the workspace then lies outside the window's span by as much
as its span across changed, and the line is the window's own edge there.

A window that shares no line with its neighbours goes into its far side, and
`plugin-win-cause-line-far` is 1: its edge away from the neighbours that take its place,
or grows out of it, away from those that make room for it. That happens when two windows
next to each other close at once and the window that stays takes both places: it reaches
past both edges of the first one, so that one goes into its edge away from it. And next
to a split of the other axis: a window left of a column of two windows closes into its
left edge, as both windows of the column grow over it, and one that opens right of such
a column grows out of its right edge. The rule, for each such window:

- Each neighbour that takes its place (a close) or makes room for it (an open) pushes it
  away from the side it lies on, by the area of the window's place that it takes or gave.
  It lies on a side when it lies wholly beyond that edge of the window, within a pixel, and
  the two overlap across: for a close where the screen showed it last, for an open where
  i3 put it.
- The line is the window's edge that the pushes point to, along the axis they push most;
  on a tie, a left or right edge.
- A neighbour that lies on no side, over the window (the next tab, a fullscreen window),
  pushes nothing. With no push, as when all the windows next to it close with it and the
  space stays empty, or pushes that cancel out, the window has no line, as a lone one.

Where a window that closes shrinks to is worked out from the windows that take its place:
those tiled ones that overlap it where i3 put them, and that lie on a side of it where the
screen showed them, as above. They come along one axis, the one along which they take more
of its place (left or right on a tie). The place they take starts at their edges where the
screen showed them, and at the window's own edge on a side with none; it shrinks into the
line where those before the window (left of it, or above) end, or with none there, where
those after it end. As they move on the same curve, the edges of that place are their
edges in every frame, and the window shows the part of its own rect inside it: it is cut
where they reach. So when windows next to each other close at once and one window takes
both places, both close into the line where that one ends, which lies beyond the first of
them: the first goes as the window reaches across it, and the second once it reaches it.
None of their places shows what lies behind, but for i3's gaps. For one window that closes
between two others, this is its split line; with the windows on one side only, its far
side. Where one window takes places along both axes at once (it was above one window that
closes and left of another), each is cut along one axis, and a corner of a place can show
what lies behind until the window reaches it.

## How it works

- picom hands the plugin each X event it reads (`add_x_event_callback`). So the plugin has one
  stream in one order, with no second connection to keep in step and no polling.
- An event that explains what comes next opens a cause. The plugin then sends i3 an `I3_SYNC`
  that i3 answers to picom's own window. i3 answers after the work ahead of the message, on the
  connection that made the moves, so the answer closes the cause right after its last move.
  No clock decides how long a cause lasts.
- `win-cause run` opens a cause with a window of its own as the token, on the same stream, and
  destroys that window once i3 has answered it. If the command dies, X destroys the window.
- From the same stream the plugin keeps where each frame is, where it was before the cause
  that moves it now, and whether it is mapped and floating, for the split line. A frame
  whose client went stays known a while, as picom asks for its close animation's variables
  after the frame is gone.
- A window that moves while a cause is open gets that change renamed
  (`rename_trigger` from `geometry`). A rename starts nothing by itself: picom still decides whether
  the window moved, and whether that is held back. A window that opens, closes, shows or hides
  keeps that trigger. And an animation that another plugin starts wins over a rename, in
  either load order, so this plugin sits next to ws-direction: a workspace switch it
  starts stays as it is.

What opens each cause, in the order i3 does things:

| Cause | Event | Then |
|---|---|---|
| `moved-by-open` | the client is reparented into its frame | i3 maps it and moves the others |
| `moved-by-close` | the client is unmapped or destroyed | i3 moves the others, then drops the frame |
| `moved-by-fullscreen` | `_NET_WM_STATE` gains or loses fullscreen | i3 moves the window |
| `moved-by-float` | `I3_FLOATING_WINDOW` appears | i3 moves the window |

The plugin asks the X server for `_NET_WM_STATE` without waiting. It only waits for the answer
if that window moves, so a focus change alone costs no wait.

## Limits

- A window that opens while its neighbours still move grows out of the line where i3 had
  them, not where they are drawn.
- Floating off has no cause: i3 removes `I3_FLOATING_WINDOW` after the move. It keeps `geometry`,
  unless `win-cause run` names it.
- Resize, swap, move and layout changes show nothing beforehand. They keep `geometry`, unless
  `win-cause run` names them. Mouse drags have no command, so they keep `geometry` too.
- Two moves of a window inside one picom pass are one change, with one trigger.
- The first cause after picom reloads is lost, because the plugin learns of picom's new window
  a pass later. That window gets its own trigger.
- Needs i3: other window managers don't answer `I3_SYNC`. Without i3 the plugin says so when
  it loads and names no causes, and the command only runs its command.

## Test

    PICOM=/path/to/picom make check-harness

runs the tests, on X. They run the `i3` and `i3-msg` first on `PATH`.

Runs real i3, xterm and picom on its own Xvfb, and checks in picom's log which trigger each
action started, and in its frame timing log the split line of each window that opens or
closes. `WC_BACKEND=glx` and `WC_ROUNDS=n` change
how.

`tests/scene.py` runs a list of changes on Xvfb, with i3, picom and real or test programs,
and keeps the marks and the frame log:

    tests/scene.py out --program a=chrome --program b=wezterm \\
        --scene key:alt+shift+Down --scene key:alt+shift+Right

`--config` runs it with a picom.conf of your own, as picom finds it (its includes and plugins
next to it, then in `XDG_CONFIG_HOME` and `XDG_CONFIG_DIRS`), `--i3` adds a line to i3's
config, and a scene's `exec:` action runs a command on the display, with the output
directory in `$SCENE_OUT`.
