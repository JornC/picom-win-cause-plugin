# win-cause

A picom plugin that tells animation scripts why a window moved, so each cause can have its
own animation. It holds what is specific to i3. picom stays neutral: it only sees trigger
names, and scripts decide how things look. Neither i3 nor picom knows what a swirl is.

Needs plugin API 0.11 of the picom fork (`rename_trigger`, `add_x_event_callback`). It
works with a stock i3.

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

## How it works

- picom hands the plugin each X event it reads (`add_x_event_callback`). So the plugin has one
  stream in one order, with no second connection to keep in step and no polling.
- An event that explains what comes next opens a cause. The plugin then sends i3 an `I3_SYNC`
  that i3 answers to picom's own window. i3 answers after the work ahead of the message, on the
  connection that made the moves, so the answer closes the cause right after its last move.
  No clock decides how long a cause lasts.
- `win-cause run` opens a cause with a window of its own as the token, on the same stream, and
  destroys that window once i3 has answered it. If the command dies, X destroys the window.
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
action started. `WC_BACKEND=glx` and `WC_ROUNDS=n` change how.
