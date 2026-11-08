# win-cause

A picom plugin that tells animation scripts why a window moved, so each cause can have its
own animation. It holds what is specific to i3. picom stays neutral: it only sees trigger
names, and scripts decide how things look. Neither i3 nor picom knows what a swirl is.

Needs plugin API 0.12 of the picom fork (`rename_trigger`, `add_x_event_callback`,
`pause_screen`, `window_animation_output`). It works with a stock i3.

## Use

    make PICOM_API_CFLAGS=-I/path/to/picom/include
    make install

That puts the plugin and the styles where picom finds them by name, and the command in
`~/.local/bin`. Then load the plugin, and write a script for each cause you care about. A
window with no script for a cause starts its usual `geometry` animation, so all of them are
optional. The name says it: the window moved because of that.

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
out, so a mouse drag of a floating window isn't slowed down. A preset can't set
`keep-stacking`, so with `geometry-change` a window i3 restacks while two pass each other
can jump in front of the other; the tile style sets it.

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

## Style: tile

`styles/tile.conf` moves windows as rectangles, for every cause, so a change looks like
what i3 does:

    plugins = ["win-cause.so"];
    rules = ({
      match = "!override_redirect && window_type = 'normal'";
      @include "win-cause/tile.conf"
    }, {
      match = "!override_redirect && window_type = 'normal' && !I3_FLOATING_WINDOW@";
      @include "win-cause/tile-geometry.conf"
    });

The first rule has the causes, `open` and `close`, for all windows, as a window that turns
floating is floating by the time it moves. The second has the same motion for `geometry`
(resize, swap, move, layout, floating off), but not for floating windows, so a mouse drag
isn't slowed. It needs a picom with `window-image-width`, `fill`, `window-median-red` (or
`window-edge-red`), `frame-on-crop`, `saved-image-own-size` and `keep-stacking`.

- Each window of a change has a rectangle that goes from where the window was to where it
  is. All of them move on one curve, at one time, and start and end without speed. An edge
  that two windows share stays shared, so the gap a window leaves closes as it goes, and
  nothing overlaps. With `var-path` 1, windows that trade places bend around each other
  instead; an edge that a window shares with a neighbour where the change starts and
  where it ends goes straight, so tiles that stay neighbours stay together.
- i3's frame (the border, or the title bar), as i3 last drew it in the picture picom has,
  is drawn on the rectangle's edges in every frame: never scaled across it, as long as the
  side along it, and a title moves along with the content.
- The content inside the frame is never scaled or blended (but in a window with no line,
  see below). It is drawn at its own size, cut where it is larger than the inside of the
  frame, and the part that it doesn't cover is filled with the median colour of the
  content, the colour of more than half of it, so a rectangle looks like its window's
  background: a terminal's is dark, a white page's white, whatever their text. The fill
  is as opaque as that background, at the window's opacity, so a translucent window
  stays as translucent, and nothing jumps when the change ends. picom works that colour out on the GPU (or in the
  X server, for its xrender backend) ahead of a change, so it is there when the change
  starts; for a new picture while a change has none, picom waits for it. picom keeps the picture a window had
  when the change started, and takes up each newer one as the program draws it (until the
  program has drawn at its new size, the newest is the last). In each frame the one that
  covers more of the rectangle is drawn, switching in one frame: a window that grows
  shows its newest picture as soon as the program has drawn it, one that shrinks keeps
  its larger old picture, cut by the rectangle, until it lands, and a window whose split
  turns from side by side to one above the other shows the old tall picture or the new
  wide one, whichever fills more, so the fill covers only what neither does.
- Under all of the rectangle, before the window, picom draws a backdrop: what the picture
  leaves transparent shows it, not what lies behind. Its colour is the median colour of
  the window's content with its alpha, or while picom doesn't know that, of the last
  window of the same program (the class in WM_CLASS), or for a program picom knows no colour of, dark
  grey. A window whose background is transparent gets a transparent backdrop, and a
  translucent one an equally translucent one. A translucent window is less so while it
  moves: the backdrop fades in at the start of a change and out at its end, so nothing
  jumps.
- A window that opens grows out of the line it split from (see [Variables](#variables)),
  its picture standing at its place and showing as the rectangle uncovers it, while its
  neighbours make room on the same curve. A window that closes goes
  back into its line: its picture stays where it was, and its rectangle is cut where the
  windows that take its place reach, in every frame, also when windows next to it close
  with it. So no part of its place shows what lies behind. One that shares no line with its
  neighbours goes into its far side, away from them. One with no line at all (the first or
  last on its workspace, a floating one, a new tab) grows out of and shrinks into the
  middle of its side toward the outside of the desk, or its own middle (`var-lone-to`). It
  is drawn whole: its picture and its frame are scaled evenly with its rectangle, never
  stretched. A script sees only the monitor its window is on, so the outside is the left on
  a monitor that starts at the left edge of the screen (also the only monitor), else the
  right.
- A change that comes while one runs goes on from where every rectangle and picture is,
  with the speed it has. i3 restacks the windows it moves, but no window comes in front
  of a moving window that it overlaps until they are apart or the move ends: none jumps on
  top of another while they pass (picom's `keep-stacking`). So a raise that i3 means, such
  as a floating window over a window that turns tiled, shows once they are apart or the
  move ends.

The choices are in `styles/parts/tile.conf`. To change them, copy it to `win-cause/parts/`
next to your `picom.conf`: picom looks there first, and `make install` doesn't write there.
Save `picom.conf` once after that, so picom reads the copy.

| Variable | Default | What it chooses |
|---|---|---|
| `var-length` | 0.25 | how long a change takes, in seconds |
| `var-ease` | `cubic-bezier(0.42, 0, 0.58, 1)` | the curve; one that starts and ends without speed keeps the motion smooth where changes meet |
| `var-path` | 0 | the way a rectangle goes in a change of i3's layout (a swap, a move, a split that turns): 0 straight; 1 curved around where its old and new places overlap, so two windows that trade places pass each other and shrink a little on the way, or bent to the left of its way where they don't (one going right goes up); an undo goes back the way it came. Windows that make room for one that opens or take the place of one that closes go straight |
| `var-lift` | 0.08 | with `var-path` 1, how far off its way a window whose places don't overlap goes midway, as a share of its size across the way |
| `var-interrupt` | 1 | a change that comes while one runs: 1 keeps each rectangle's speed and slows it to rest at its new place, 0 starts the curve again from where it is |
| `var-fill` | 1 | how much of the gap is filled: 1 all, as opaque as the window, 0 none, so what lies behind shows |
| `@include` of the fill's colour | `parts/tile-fill-median.conf` | the colour of the fill: `tile-fill-median.conf` the median colour of the window's content, its background, `tile-fill-mean.conf` the mean colour of its content, which text moves, `tile-fill-edge.conf` the colour of the edge of its picture (the ring just inside i3's frame); an include, so picom works out only the one chosen |
| `var-anchor-x`, `var-anchor-y` | the still point | where a picture that doesn't fit sits in its rectangle: 0 at the left (top) edge, 1 at the right (bottom), 0.5 in the middle; by default the point of the window that stays still in the change (`var-still-x`, `var-still-y`), so no edge of the picture goes back while the program catches up |
| `var-lone-to` | 1 | a window that opens or closes without a split line grows out of and shrinks into, evenly scaled: 1 the middle of its side toward the outside of the desk, 0 its own middle |
| `var-lone-fade` | 1 | such a window also fades in or out: 1 yes, 0 no |
| `var-far-line` | 1 | a window that shares no split line with its neighbours: 1 goes into its far side as into a line, 0 goes as one with no line (`var-lone-to`) |
| `var-backdrop` | 1 | how much of the backdrop under the rectangle is drawn: 1 all, 0 none, so what a picture leaves transparent shows what lies behind |
| `var-backdrop-fade` | 0.25 | the share of `var-length` over which the backdrop fades in as a change starts (a window that opens has it at once) and out as it ends |
| `var-backdrop-red`, `var-backdrop-green`, `var-backdrop-blue`, `var-backdrop-alpha` | 0.1, 0.1, 0.1, 1 | the backdrop's colour and alpha for a program picom knows no colour of yet; with `tile-fill-edge.conf`, for every window |
| `var-picture` | whichever covers more | which picture of a moving window is drawn: 1 the one it had when the change started, 0 its newest, as soon as the program has drawn it; by default (`var-picture-best`), in each frame, the one that covers more of the rectangle, each at its own size from its anchor |

The rest of `styles/parts/` is the motion itself: `tile-rect.conf` for a window that moves,
`tile-open.conf` and `tile-close.conf`, with what they share in `tile-shared.conf` and the
point a window with no line goes from and to in `tile-lone.conf`, the three colours of
the fill, `tile-fill-median.conf`, `tile-fill-mean.conf` and `tile-fill-edge.conf`, and
`tile-defaults.conf`, the value of a setting that a copy of `tile.conf` made before it came lacks. Nobody needs to
copy them. Without `make install`, copy `styles/parts/` to
`win-cause/parts/` next to `picom.conf`, as picom looks for included files from there, not
next to the style.

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
the line is 1. A style can treat the two kinds of line alike, as tile does, or a far side
otherwise (tile's `var-far-line`).

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

For a window that moves, scripts can read where it was before it moved:

| Variable | Is |
|---|---|
| `plugin-win-cause-sx`, `-sy`, `-sw`, `-sh` | where the window was before it moved, minus where it is: `window-x + plugin-win-cause-sx` is where its rectangle starts |

A window that i3 doesn't manage has 0 for all of them, so a script can always add them.

A style that bends a window off its straight way can keep it with its neighbours:

| Variable | Is |
|---|---|
| `plugin-win-cause-seam-left`, `-seam-top`, `-seam-right`, `-seam-bottom` | 1 if that side of the window faces a tiled neighbour across the same gap (at most 64 px) where the change starts and where it ends, so the two go on together; 0 if not. A dock, such as a bar, is no tiled neighbour |

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
- When a window arrives, the plugin holds picom's screen (`pause_screen`) until i3 has
  moved the others and picom has the new window's first picture, at most 0.1 s. picom then
  starts the animations of the whole change in one pass, on one clock, and the new window
  shows from the first frame. Without it, the new window starts a pass or more after its
  neighbours, as picom first asks the X server about it. Other causes don't hold: i3 sends
  their moves in one go.
- i3 gives its frames a black background. When a program goes, the X server paints its frame
  black where the client was, before i3 drops the frame, so a close animation would show an
  empty frame. The plugin takes the background off each i3 frame it sees, so the X server
  leaves the client's last picture there. i3 draws all of a frame itself and doesn't need it.
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
- With a picom that matches a frame whose client went by the frame alone (the fork before
  2026-11, and upstream), a rule that matches on the client (`window_type`,
  `!override_redirect`) can miss a window that closes, if picom runs a pass between the
  client going and i3 dropping the frame: an i3 frame is override-redirect. The window then
  closes without its close animation. The fork keeps a window's rules until its frame goes.
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

    WS_DIRECTION=/path/to/ws-direction make check

runs the tests that need no X server: `tests/check-tile.py` on made-up logs.

    PICOM=/path/to/picom WS_DIRECTION=/path/to/ws-direction make check-harness

runs the rest, on X; `make check-all` runs both. Both are needed. `WS_DIRECTION` is the root
of a built ws-direction tree. The tests run the `i3` and `i3-msg` first on `PATH`.

Runs real i3, xterm and picom on its own Xvfb, and checks in picom's log which trigger each
action started, and in its frame timing log that the windows of a change start in one frame
and the split line of each window that opens or closes, also after i3 restarts; and on the
screen, that a window whose program closes shows its last picture while it closes, not an
empty frame. `WC_BACKEND=glx` and `WC_ROUNDS=n` change how.
`tests/tile.py` checks the tile style, with `tests/check-tile.py` on what picom drew in each
part. A window that shares no line with its neighbours goes into its far side: two that
close at once (picom stopped while they close, so it reads both in one pass), one left of a
column of two, one that opens and closes right of it; the last ones on a workspace have no
line. Two windows swapped and swapped back while they overlap keep the one in front in
front, and so does a floating window that turns tiled under another. Two windows that trade
places above a bar have no side on a seam: a dock is no tile. With a length of 1 s, a change
that comes at 0.3, 0.5 and 0.7 of the one before (floating on then off, two resizes, an open
then a close, two opens) makes no rectangle or picture jump. A window whose first picture is
empty (`tests/slowclient` with `argb`) has the backdrop in the colour of the last window of
its program, and for a new program in the style's; a window at half
alpha has its backdrop at that alpha, and a translucent window's backdrop fades in and out
as it moves. `WC_ONLY=far,stacking,dock,interrupt,backdrop` runs only those parts.

`tests/scene.py` runs a list of changes on Xvfb, with i3, picom and real or test programs,
and keeps the marks and the frame log:

    tests/scene.py out --program a=chrome --program b=wezterm \\
        --scene key:alt+shift+Down --scene key:alt+shift+Right

`--config` runs it with a picom.conf of your own, as picom finds it (its includes and plugins
next to it, then in `XDG_CONFIG_HOME` and `XDG_CONFIG_DIRS`), `--i3` adds a line to i3's
config, and a scene's `exec:` action runs a command on the display, with the output
directory in `$SCENE_OUT`.

`tests/check-tile.py` checks what the tile style drew, from picom's frame timing log with the
layout records (`PICOM_DEBUG="frame_timing=<path>;frame_layout=1"`): that no rectangle or
picture jumps, every rectangle comes to rest without speed, no picture is scaled or blended,
the windows of a change start in one frame and move on one clock, the edges they share stay
shared, a window that opens or closes does so on its line, no part of a closing window's
place shows what lies behind, and the gap is filled as the script asks, and never left
unfilled once the window's colour can be known (`unfilled`), and a script that doesn't set opacity draws the window at the opacity
it has at rest, with no jump when the animation ends (`opacity`), and its backdrop fades in
and out, so a translucent window doesn't jump either (`backdrop`). It uses ws-direction's `tests/check-frames.py` for the motion:

    WS_DIRECTION=/path/to/ws-direction tests/check-tile.py frames.csv --monitor 0,0,1920,1080
