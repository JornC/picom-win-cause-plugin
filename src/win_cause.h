// SPDX-License-Identifier: MPL-2.0
// Copyright (c) JornC

/// What the plugin keeps: the windows i3 manages, the causes that are open, and the
/// connection to the X server.

#pragma once

#include <stdint.h>
#include <stdlib.h>

#include <xcb/xcb.h>

#include <picom/api.h>

/// The plugin API the plugin needs is 0.WC_API_MINOR, as the picom fork numbers it.
#define WC_API_MINOR 14
#define WC_STR(x) #x
#define WC_XSTR(x) WC_STR(x)
#define WC_API_VERSION "0." WC_XSTR(WC_API_MINOR)

#if PICOM_API_MAJOR != 0 || PICOM_API_MINOR < WC_API_MINOR
static_assert(false, "picom/api.h is not compatible: win-cause needs plugin "
                     "API " WC_API_VERSION " of the picom fork. To build against a picom "
                     "source tree, run make PICOM_API_CFLAGS=-I/path/to/picom/include");
#endif

/// How long the screen waits at most for a change to be complete, in seconds, from the
/// event that opened its cause: for i3's answer, normally within a round trip, and for a
/// new window's first picture, which a terminal draws in a few frames. Below what one
/// notices as a delay; after it, picom goes on without them.
#define WC_HOLD 0.1

/// Frames whose client went, kept for the variables of their close animation, which
/// starts a pass or a few later. More than this many windows don't close at once.
#define WC_CLOSED_KEPT 32

/// A frame's place in the root window, with its border: picom's window-x, window-y,
/// window-width and window-height of it.
struct rect {
	int32_t x, y, width, height;
};

/// A window i3 manages: its client window, and the frame i3 made for it.
struct frame {
	xcb_window_t id, client;
	bool fullscreen, floating, mapped;
	/// Where the frame is, once `placed`, and where it was before the cause with the
	/// token `cause` first moved it.
	bool placed;
	struct rect rect, before;
	uint32_t cause;
	/// The token of the cause that the frame's own arrival opened, and of the one its
	/// client's going opened, 0 if none.
	uint32_t opened, closing;
	/// Where the frame was in the last pass that picom ran its animations in: where
	/// the screen last showed it, unless an animation draws it elsewhere. And where
	/// the screen last showed it, its animation's rectangle if one runs. Where it was
	/// before its last move (`was`).
	struct rect shown, drawn, was;
	/// When the client went, counting closes from 1; 0 while it is there.
	uint64_t closed;
	/// A read of _NET_WM_STATE is on its way, in `state`.
	bool reading;
	xcb_get_property_cookie_t state;
	/// Whether its client is a dock, such as a bar, which i3 doesn't tile (wc_dock).
	/// A read of _NET_WM_WINDOW_TYPE is on its way in `type` while `typing`.
	bool dock, typing;
	xcb_get_property_cookie_t type;
};

/// Why windows move: a trigger to rename their moves to. Or why windows hide or show,
/// and nothing about their moves: a fullscreen window covers them, and i3 hides them, or
/// uncovers them, and i3 shows them again.
enum cause_kind { CAUSE_TRIGGER, CAUSE_COVER, CAUSE_UNCOVER };

/// Applies to the frame `only`, or if that is 0, to every frame but `except`. A cause of
/// the command has the command's window as its token, so it ends with that window.
struct cause {
	uint32_t token;
	enum cause_kind kind;
	const char *trigger;
	xcb_window_t only, except;
};

/// What to name a move of a frame in this pass. Or with `from`, what to name that trigger
/// of its change: "show" or "hide". `opened` is the frame whose arrival caused the move,
/// if one did.
struct rename {
	xcb_window_t frame;
	const char *trigger;
	const char *from;
	xcb_window_t opened;
};

/// A frame that makes room for the frame `opened`: its animation waits while the open of
/// that one does, for its first picture. `started` once picom makes it wait, in the pass
/// in which their animations start; `ran` once picom ran an animation of `opened` since.
struct waiting {
	xcb_window_t frame, opened;
	bool started, ran;
};

struct name {
	xcb_atom_t atom;
	char *text;
};

/// A window that opens, which the hold waits for: until i3 has answered the cause that
/// its arrival opened, and picom has its first picture.
struct awaited {
	xcb_window_t frame;
	uint32_t token;
	bool drawn;
};

#define PUSH(list, count, value)                                                         \
	do {                                                                             \
		list = realloc(list, sizeof(*(list)) * ((count) + 1));                   \
		if (list == NULL) {                                                      \
			abort();                                                         \
		}                                                                        \
		(list)[(count)++] = (value);                                             \
	} while (0)

struct wc_state {
	const struct picom_api *api;
	xcb_connection_t *c;
	xcb_window_t root;
	xcb_atom_t a_arm, a_sync, a_compositor, a_i3_socket, a_state, a_fullscreen,
	    a_floating, a_window_type, a_dock;
	/// The last owner of the compositor selection we know of, which i3's answers go
	/// to.
	xcb_window_t compositor;
	/// A read of that owner is on its way, in `compositor_read`.
	bool asking;
	xcb_get_selection_owner_cookie_t compositor_read;
	bool i3;
	uint32_t next_token;
	uint64_t closes;
	/// A window opened since the last frame callback (`hold`), and the screen waits
	/// for the windows in `awaited` until `hold_until` (`holding`), so that all
	/// windows of the change start their animations in one pass.
	bool hold, holding;
	double hold_until;
	/// The screen is paused for the hold.
	bool paused;
	struct awaited *awaited;
	size_t n_awaited;
	/// The event code of the Damage extension's DamageNotify, 0 without it.
	uint8_t damage_notify;
	struct frame *frames;
	struct cause *causes;
	struct rename *renames;
	struct name *names;
	struct waiting *waiting;
	size_t n_frames, n_causes, n_renames, n_names, n_waiting;
	/// The split line of the last animation that asked, see wc_line_variable.
	struct {
		bool valid, found, far;
		uint64_t pass;
		uint32_t window;
		int kind;
		struct rect rect, from;
	} line;
};

extern struct wc_state wc;
