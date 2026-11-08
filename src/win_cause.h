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
#define WC_API_MINOR 12
#define WC_STR(x) #x
#define WC_XSTR(x) WC_STR(x)
#define WC_API_VERSION "0." WC_XSTR(WC_API_MINOR)

#if PICOM_API_MAJOR != 0 || PICOM_API_MINOR < WC_API_MINOR
static_assert(false, "picom/api.h is not compatible: win-cause needs plugin "
                     "API " WC_API_VERSION " of the picom fork. To build against a picom "
                     "source tree, run make PICOM_API_CFLAGS=-I/path/to/picom/include");
#endif

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
	/// the screen last showed it, its animation's rectangle if one runs.
	struct rect shown, drawn;
	/// When the client went, counting closes from 1; 0 while it is there.
	uint64_t closed;
	/// A read of _NET_WM_STATE is on its way, in `state`.
	bool reading;
	xcb_get_property_cookie_t state;
};

/// Applies to the frame `only`, or if that is 0, to every frame but `except`. A cause of
/// the command has the command's window as its token, so it ends with that window.
struct cause {
	uint32_t token;
	const char *trigger;
	xcb_window_t only, except;
};

/// What to name a move of a frame in this pass.
struct rename {
	xcb_window_t frame;
	const char *trigger;
};

struct name {
	xcb_atom_t atom;
	char *text;
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
	xcb_atom_t a_arm, a_sync, a_compositor, a_i3_socket, a_state, a_fullscreen, a_floating;
	/// The last owner of the compositor selection we know of, which i3's answers go
	/// to.
	xcb_window_t compositor;
	/// A read of that owner is on its way, in `compositor_read`.
	bool asking;
	xcb_get_selection_owner_cookie_t compositor_read;
	bool i3;
	uint32_t next_token;
	uint64_t closes;
	struct frame *frames;
	struct cause *causes;
	struct rename *renames;
	struct name *names;
	size_t n_frames, n_causes, n_renames, n_names;
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
