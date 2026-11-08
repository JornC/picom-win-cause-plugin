// SPDX-License-Identifier: MPL-2.0
// Copyright (c) JornC

/// Tells picom why a window moved, so a script can animate each cause its own way.
///
/// picom hands us each X event it reads, so all we know keeps its order against what
/// picom sees. An event that explains what comes next opens a cause. We then send i3 an
/// I3_SYNC that it answers to picom's own window: i3 answers after the work ahead of the
/// message, on the connection that made the moves, so the answer closes the cause right
/// after the last move it explains. A window that moves while a cause is open gets its
/// change renamed to the cause's trigger. A window with no script for that trigger starts
/// its own, so scripts for causes are optional, and what picom suppresses stays
/// suppressed.
///
/// Nothing here waits on the X server for a window that doesn't move: the callback runs
/// on picom's main thread. What we ask, we take in the frame callback, or when a window
/// moved and its answer decides the cause.

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <xcb/damage.h>
#include <xcb/xcb.h>
#include <xcb/xcbext.h>

#include <picom/api.h>

#include "protocol.h"
#include "win_cause.h"

struct wc_state wc;

static void wc_fail(const char *message) {
	fprintf(stderr, "win-cause: %s\n", message);
}

static xcb_atom_t wc_atom(const char *name) {
	auto reply = xcb_intern_atom_reply(
	    wc.c, xcb_intern_atom(wc.c, 0, (uint16_t)strlen(name), name), NULL);
	xcb_atom_t atom = reply != NULL ? reply->atom : XCB_ATOM_NONE;
	free(reply);
	return atom;
}

/// Names of triggers from the command. Kept until picom exits, since a rename can outlive
/// its cause.
static const char *wc_atom_name(xcb_atom_t atom) {
	for (size_t i = 0; i < wc.n_names; i++) {
		if (wc.names[i].atom == atom) {
			return wc.names[i].text;
		}
	}
	auto reply = xcb_get_atom_name_reply(wc.c, xcb_get_atom_name(wc.c, atom), NULL);
	if (reply == NULL) {
		return NULL;
	}
	auto text = strndup(xcb_get_atom_name_name(reply), reply->name_len);
	free(reply);
	PUSH(wc.names, wc.n_names, ((struct name){atom, text}));
	return text;
}

static struct frame *wc_frame_of_client(xcb_window_t client) {
	for (size_t i = 0; i < wc.n_frames; i++) {
		if (wc.frames[i].client == client && wc.frames[i].closed == 0) {
			return &wc.frames[i];
		}
	}
	return NULL;
}

static struct frame *wc_frame(xcb_window_t id) {
	for (size_t i = 0; i < wc.n_frames; i++) {
		if (wc.frames[i].id == id) {
			return &wc.frames[i];
		}
	}
	return NULL;
}

static void wc_drop_frame(size_t i) {
	if (wc.frames[i].reading) {
		xcb_discard_reply(wc.c, wc.frames[i].state.sequence);
	}
	wc.frames[i] = wc.frames[--wc.n_frames];
}

/// Drops what we kept of frames with this id: X gave the id to a new one.
static void wc_forget(xcb_window_t id) {
	for (size_t i = wc.n_frames; i > 0; i--) {
		if (wc.frames[i - 1].id == id) {
			wc_drop_frame(i - 1);
		}
	}
}

/// i3 gives its frames a black background, so when a client goes, the X server paints
/// its frame black where the client was, before i3 drops the frame, and picom would draw
/// that while the window closes. Without a background the X server leaves the client's
/// last picture there. i3 draws all of a frame itself and doesn't need one.
static void wc_keep_picture(xcb_window_t frame) {
	const uint32_t none = XCB_BACK_PIXMAP_NONE;
	auto cookie =
	    xcb_change_window_attributes_checked(wc.c, frame, XCB_CW_BACK_PIXMAP, &none);
	// The frame may be gone already; that is fine.
	xcb_discard_reply(wc.c, cookie.sequence);
	xcb_flush(wc.c);
}

static struct frame *wc_add_frame(xcb_window_t id, xcb_window_t client) {
	wc_forget(id);
	PUSH(wc.frames, wc.n_frames, ((struct frame){.id = id, .client = client}));
	return &wc.frames[wc.n_frames - 1];
}

/// Keeps the last WC_CLOSED_KEPT frames whose client went.
static void wc_trim_closed(void) {
	size_t closed = 0, oldest = 0;
	for (size_t i = 0; i < wc.n_frames; i++) {
		if (wc.frames[i].closed == 0) {
			continue;
		}
		if (closed++ == 0 || wc.frames[i].closed < wc.frames[oldest].closed) {
			oldest = i;
		}
	}
	if (closed > WC_CLOSED_KEPT) {
		wc_drop_frame(oldest);
	}
}

static void wc_close_cause(uint32_t token) {
	size_t kept = 0;
	for (size_t i = 0; i < wc.n_causes; i++) {
		if (wc.causes[i].token != token) {
			wc.causes[kept++] = wc.causes[i];
		}
	}
	wc.n_causes = kept;
}

static void wc_ask_compositor(void) {
	if (!wc.asking) {
		wc.compositor_read = xcb_get_selection_owner(wc.c, wc.a_compositor);
		wc.asking = true;
		xcb_flush(wc.c);
	}
}

static void wc_take_compositor(void) {
	if (!wc.asking) {
		return;
	}
	xcb_get_selection_owner_reply_t *reply = NULL;
	xcb_generic_error_t *error = NULL;
	if (!xcb_poll_for_reply(wc.c, wc.compositor_read.sequence, (void **)&reply, &error)) {
		return;
	}
	wc.asking = false;
	free(error);
	if (reply == NULL) {
		return;
	}
	if (reply->owner != wc.compositor) {
		// picom reloaded: answers to its old window never come.
		wc.n_causes = 0;
		wc.compositor = reply->owner;
	}
	free(reply);
}

/// Returns the cause's token, 0 if none opened.
static uint32_t wc_open_cause(const char *trigger, xcb_window_t only, xcb_window_t except) {
	if (!wc.i3 || wc.compositor == XCB_NONE) {
		return 0;
	}
	wc_ask_compositor();
	uint32_t token = WC_TOKEN_PLUGIN | wc.next_token++;
	PUSH(wc.causes, wc.n_causes, ((struct cause){token, trigger, only, except}));
	xcb_client_message_event_t sync = {
	    .response_type = XCB_CLIENT_MESSAGE,
	    .format = 32,
	    .window = wc.root,
	    .type = wc.a_sync,
	    .data.data32 = {wc.compositor, token},
	};
	xcb_send_event(wc.c, 0, wc.root, XCB_EVENT_MASK_SUBSTRUCTURE_REDIRECT,
	               (const char *)&sync);
	xcb_flush(wc.c);
	return token;
}

static bool wc_cause_open(uint32_t token) {
	for (size_t i = 0; i < wc.n_causes; i++) {
		if (wc.causes[i].token == token) {
			return true;
		}
	}
	return false;
}

/// The cause of a frame's move: the newest wins.
static const struct cause *wc_cause_for(xcb_window_t frame) {
	for (size_t i = wc.n_causes; i > 0; i--) {
		auto cause = &wc.causes[i - 1];
		if (cause->only == frame ||
		    (cause->only == XCB_NONE && cause->except != frame)) {
			return cause;
		}
	}
	return NULL;
}

static bool wc_has_atom(xcb_get_property_reply_t *reply, xcb_atom_t atom) {
	bool has = false;
	if (reply != NULL && reply->format == 32) {
		auto atoms = (xcb_atom_t *)xcb_get_property_value(reply);
		for (uint32_t i = 0; i < reply->value_len; i++) {
			has |= atoms[i] == atom;
		}
	}
	return has;
}

static void wc_read_state(struct frame *frame) {
	if (frame->reading) {
		xcb_discard_reply(wc.c, frame->state.sequence);
	}
	frame->state =
	    xcb_get_property(wc.c, 0, frame->client, wc.a_state, XCB_ATOM_ATOM, 0, 32);
	frame->reading = true;
	xcb_flush(wc.c);
}

static void wc_take_state(struct frame *frame, bool wait) {
	xcb_get_property_reply_t *reply = NULL;
	xcb_generic_error_t *error = NULL;
	if (wait) {
		reply = xcb_get_property_reply(wc.c, frame->state, &error);
	} else if (!xcb_poll_for_reply(wc.c, frame->state.sequence, (void **)&reply, &error)) {
		return;
	}
	frame->reading = false;
	free(error);
	bool fullscreen = wc_has_atom(reply, wc.a_fullscreen);
	free(reply);
	if (fullscreen != frame->fullscreen) {
		frame->fullscreen = fullscreen;
		wc_open_cause("moved-by-fullscreen", frame->id, XCB_NONE);
	}
}

static bool wc_i3_runs(void) {
	auto reply = xcb_get_property_reply(
	    wc.c, xcb_get_property(wc.c, 0, wc.root, wc.a_i3_socket, XCB_ATOM_ANY, 0, 1), NULL);
	bool runs = reply != NULL && reply->type != XCB_ATOM_NONE;
	free(reply);
	return runs;
}

static struct rect
wc_rect(int16_t x, int16_t y, uint16_t width, uint16_t height, uint16_t border) {
	return (struct rect){x, y, width + 2 * border, height + 2 * border};
}

/// Reads what X has of a frame: where it is, whether it shows, and whether its client is
/// fullscreen or floating.
static void wc_read_frame(struct frame *frame) {
	auto geometry_read = xcb_get_geometry(wc.c, frame->id);
	auto attributes_read = xcb_get_window_attributes(wc.c, frame->id);
	auto state_read =
	    xcb_get_property(wc.c, 0, frame->client, wc.a_state, XCB_ATOM_ATOM, 0, 32);
	auto floating_read =
	    xcb_get_property(wc.c, 0, frame->client, wc.a_floating, XCB_ATOM_ANY, 0, 1);
	auto class_read =
	    xcb_get_property(wc.c, 0, frame->id, XCB_ATOM_WM_CLASS, XCB_ATOM_STRING, 0, 4);
	auto geometry = xcb_get_geometry_reply(wc.c, geometry_read, NULL);
	auto attributes = xcb_get_window_attributes_reply(wc.c, attributes_read, NULL);
	auto state = xcb_get_property_reply(wc.c, state_read, NULL);
	auto floating = xcb_get_property_reply(wc.c, floating_read, NULL);
	auto class = xcb_get_property_reply(wc.c, class_read, NULL);
	if (class != NULL && xcb_get_property_value_length(class) >= 9 &&
	    memcmp(xcb_get_property_value(class), "i3-frame", 9) == 0) {
		wc_keep_picture(frame->id);
	}
	if (geometry != NULL) {
		frame->rect = wc_rect(geometry->x, geometry->y, geometry->width,
		                      geometry->height, geometry->border_width);
		frame->before = frame->shown = frame->drawn = frame->rect;
		frame->placed = true;
	}
	frame->mapped = attributes != NULL && attributes->map_state != XCB_MAP_STATE_UNMAPPED;
	frame->fullscreen = wc_has_atom(state, wc.a_fullscreen);
	frame->floating = floating != NULL && floating->type != XCB_ATOM_NONE;
	free(geometry);
	free(attributes);
	free(state);
	free(floating);
	free(class);
}

/// The windows that were there before we came. New ones come from the events.
static void wc_scan(void) {
	auto tree = xcb_query_tree_reply(wc.c, xcb_query_tree(wc.c, wc.root), NULL);
	if (tree == NULL) {
		return;
	}
	auto windows = xcb_query_tree_children(tree);
	for (int i = 0; i < xcb_query_tree_children_length(tree); i++) {
		auto inside =
		    xcb_query_tree_reply(wc.c, xcb_query_tree(wc.c, windows[i]), NULL);
		if (inside != NULL && xcb_query_tree_children_length(inside) > 0) {
			wc_read_frame(
			    wc_add_frame(windows[i], xcb_query_tree_children(inside)[0]));
		}
		free(inside);
	}
	free(tree);
}

static void wc_on_reparent(const xcb_reparent_notify_event_t *e) {
	// A frame that shows already when it gets its client gets no MapNotify, and maybe
	// no ConfigureNotify, for it: it is read as one that was there when we came. When
	// i3 restores its layout after a restart, it puts each window in the frame of its
	// placeholder.
	auto known = wc_frame(e->parent);
	bool shows = known != NULL && known->mapped;
	auto frame = wc_frame_of_client(e->window);
	if (frame != NULL) {
		// i3 gave the client a new frame.
		if (e->parent != wc.root && e->parent != frame->id) {
			auto client = frame->client;
			wc_forget(e->parent);
			frame = wc_frame_of_client(client);
			frame->id = e->parent;
			frame->placed = frame->mapped = false;
			wc_keep_picture(frame->id);
			if (shows) {
				wc_read_frame(frame);
			}
		}
		return;
	}
	if (e->event == wc.root && e->parent != wc.root) {
		wc_keep_picture(e->parent);
		auto token = wc_open_cause("moved-by-open", XCB_NONE, e->parent);
		frame = wc_add_frame(e->parent, e->window);
		frame->opened = token;
		if (shows) {
			wc_read_frame(frame);
		}
		if (token != 0) {
			PUSH(wc.awaited, wc.n_awaited,
			     ((struct awaited){e->parent, token, false}));
			wc.hold = true;
		}
	}
}

static void wc_on_configure(const xcb_configure_notify_event_t *e) {
	auto frame = e->event == wc.root ? wc_frame(e->window) : NULL;
	if (frame == NULL) {
		return;
	}
	if (frame->reading) {
		wc_take_state(frame, true);
	}
	auto rect = wc_rect(e->x, e->y, e->width, e->height, e->border_width);
	auto cause = wc_cause_for(frame->id);
	uint32_t token = cause != NULL ? cause->token : 0;
	if (!frame->placed) {
		frame->shown = frame->drawn = rect;
	}
	if (!frame->placed || frame->cause != token) {
		frame->before = frame->placed ? frame->rect : rect;
		frame->cause = token;
	}
	frame->rect = rect;
	frame->placed = true;
	if (cause != NULL) {
		PUSH(wc.renames, wc.n_renames, ((struct rename){frame->id, cause->trigger}));
	}
}

static void wc_on_map(xcb_window_t window, bool mapped) {
	auto frame = wc_frame(window);
	if (frame != NULL) {
		frame->mapped = mapped;
	}
}

/// The client goes first, i3 moves the others, and its frame goes last.
static void wc_on_client_gone(xcb_window_t client) {
	auto frame = wc_frame_of_client(client);
	if (frame != NULL) {
		auto id = frame->id;
		if (frame->reading) {
			xcb_discard_reply(wc.c, frame->state.sequence);
			frame->reading = false;
		}
		frame->closed = ++wc.closes;
		wc_trim_closed();
		frame->closing = wc_open_cause("moved-by-close", XCB_NONE, id);
	}
}

static void wc_on_property(const xcb_property_notify_event_t *e) {
	if (e->window == wc.root) {
		if (e->atom == wc.a_i3_socket) {
			// A restarted i3 won't answer what the old one was asked.
			wc.n_causes = 0;
			wc.i3 = wc_i3_runs();
		}
		return;
	}
	auto frame = wc_frame_of_client(e->window);
	if (frame == NULL) {
		return;
	}
	if (e->atom == wc.a_state) {
		wc_read_state(frame);
	} else if (e->atom == wc.a_floating) {
		frame->floating = e->state == XCB_PROPERTY_NEW_VALUE;
		if (frame->floating) {
			// The window that floats, and the windows that take its place.
			wc_open_cause("moved-by-float", XCB_NONE, XCB_NONE);
		}
	}
}

static void wc_on_message(const xcb_client_message_event_t *e) {
	if (e->type == wc.a_sync) {
		wc_close_cause(e->data.data32[1]);
	} else if (e->type == wc.a_arm) {
		auto trigger = wc_atom_name(e->data.data32[0]);
		auto window = e->data.data32[2];
		auto frame = window != XCB_NONE ? wc_frame_of_client(window) : NULL;
		if (trigger != NULL && (frame != NULL || window == XCB_NONE)) {
			auto only = frame != NULL ? frame->id : XCB_NONE;
			PUSH(wc.causes, wc.n_causes,
			     ((struct cause){e->data.data32[1], trigger, only, XCB_NONE}));
		}
	}
}

/// picom has a frame's first picture, or the frame went: the hold no longer waits for
/// it to draw.
static void wc_drawn(xcb_window_t id) {
	for (size_t i = 0; i < wc.n_awaited; i++) {
		if (wc.awaited[i].frame == id) {
			wc.awaited[i].drawn = true;
		}
	}
}

static void wc_x_event(const void *event, void *user_data) {
	(void)user_data;
	const xcb_generic_event_t *e = event;
	uint8_t type = e->response_type & 0x7f;
	if (wc.damage_notify != 0 && type == wc.damage_notify) {
		// picom's damage objects are on the frames: picom manages this one, and
		// it has drawn.
		wc_drawn(((const xcb_damage_notify_event_t *)event)->drawable);
		return;
	}
	if (type == XCB_CLIENT_MESSAGE) {
		wc_on_message(event);
		return;
	}
	if ((e->response_type & 0x80) != 0) {
		return;
	}
	switch (type) {
	case XCB_REPARENT_NOTIFY: wc_on_reparent(event); break;
	case XCB_CONFIGURE_NOTIFY: wc_on_configure(event); break;
	case XCB_MAP_NOTIFY:
		wc_on_map(((const xcb_map_notify_event_t *)event)->window, true);
		break;
	case XCB_UNMAP_NOTIFY: {
		auto window = ((const xcb_unmap_notify_event_t *)event)->window;
		wc_on_map(window, false);
		wc_on_client_gone(window);
		break;
	}
	case XCB_DESTROY_NOTIFY: {
		auto window = ((const xcb_destroy_notify_event_t *)event)->window;
		wc_drawn(window);
		wc_on_client_gone(window);
		wc_close_cause(window);
		break;
	}
	case XCB_PROPERTY_NOTIFY: wc_on_property(event); break;
	default: break;
	}
}

static bool wc_overlap(struct rect a, struct rect b) {
	return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height &&
	       b.y < a.y + a.height;
}

static bool wc_near(int32_t a, int32_t b) {
	return a - b <= 1 && b - a <= 1;
}

/// Where a rect starts and ends along axis 0 (x) or 1 (y).
static int32_t wc_lo(struct rect r, int axis) {
	return axis == 0 ? r.x : r.y;
}

static int32_t wc_hi(struct rect r, int axis) {
	return axis == 0 ? r.x + r.width : r.y + r.height;
}

/// Whether `n` shares a split along `axis` with `r`: the same span across it, within a
/// pixel, where `n` is `beside` `r`, and `r` is part of `n`'s span along it where `n`
/// is `at`, short of at least one end.
static bool wc_splits(struct rect r, struct rect at, struct rect beside, int axis) {
	int across = 1 - axis;
	return wc_near(wc_lo(beside, across), wc_lo(r, across)) &&
	       wc_near(wc_hi(beside, across), wc_hi(r, across)) &&
	       (wc_lo(r, axis) > wc_lo(at, axis) || wc_hi(r, axis) < wc_hi(at, axis));
}

/// Whether `n` is a tiled neighbour of `self` that overlaps it, and where. `at` is
/// where it is when the window opens or closes: at an open where it was before the open
/// moved it; at a close where i3 moved it (i3 moves the others before the frame goes).
/// `beside` is where it is next to the window's rect, in the same layout: at an open
/// where it is now, at a close where it was before. With smart_gaps, the two have other
/// gaps when the window is the second on its workspace.
static bool wc_neighbour(const struct frame *self, struct rect r, const struct frame *n,
                         bool open, struct rect *at, struct rect *beside) {
	if (n == self || !n->placed || !n->mapped || n->floating || n->closed != 0) {
		return false;
	}
	bool moved = open ? self->opened != 0 && n->cause == self->opened
	                  : self->closing != 0 && n->cause == self->closing;
	*at = open && moved ? n->before : n->rect;
	*beside = !open && moved ? n->before : n->rect;
	return wc_overlap(r, *at);
}

/// Rect `r` collapsed onto the line at `at` along `axis`.
static struct rect wc_line_at(struct rect r, int axis, int64_t at) {
	return axis == 0 ? (struct rect){(int32_t)at, r.y, 0, r.height}
	                 : (struct rect){r.x, (int32_t)at, r.width, 0};
}

/// The line where a window split from its neighbours, see wc_split_line. The axis is the
/// one along which a neighbour shares a split with it (both or neither: no line). The
/// line is the edge of such a neighbour that lies within the window's span, the one
/// nearest its middle if there are more. For a plain i3 split there is exactly one: where
/// the window went in, or its far edge if it took half of one window. Where gaps come or
/// go with the change (smart_gaps), the neighbour's edge at the side of the workspace
/// lies outside the window's span by as much as its span across changed, and the line is
/// at the window's edge there.
static bool
wc_shared_line(const struct frame *self, struct rect r, bool open, struct rect *line) {
	bool splits[2] = {false, false};
	for (size_t i = 0; i < wc.n_frames; i++) {
		struct rect at, beside;
		if (wc_neighbour(self, r, &wc.frames[i], open, &at, &beside)) {
			splits[0] |= wc_splits(r, at, beside, 0);
			splits[1] |= wc_splits(r, at, beside, 1);
		}
	}
	if (splits[0] == splits[1]) {
		return false;
	}
	int axis = splits[0] ? 0 : 1, across = 1 - axis;
	int64_t lo = wc_lo(r, axis), hi = wc_hi(r, axis);
	bool found = false;
	int64_t best = 0;
	for (size_t i = 0; i < wc.n_frames; i++) {
		struct rect at, beside;
		if (!wc_neighbour(self, r, &wc.frames[i], open, &at, &beside) ||
		    !wc_splits(r, at, beside, axis)) {
			continue;
		}
		int64_t gaps = llabs((int64_t)wc_lo(at, across) - wc_lo(beside, across));
		int64_t far = llabs((int64_t)wc_hi(at, across) - wc_hi(beside, across));
		gaps = far > gaps ? far : gaps;
		for (int end = 0; end < 2; end++) {
			int64_t edge = end == 0 ? wc_lo(at, axis) : wc_hi(at, axis);
			edge = edge < lo && edge >= lo - gaps   ? lo
			       : edge > hi && edge <= hi + gaps ? hi
			                                        : edge;
			if (edge >= lo && edge <= hi &&
			    (!found || llabs(2 * edge - lo - hi) < llabs(2 * best - lo - hi))) {
				best = edge;
				found = true;
			}
		}
	}
	if (!found) {
		return false;
	}
	*line = wc_line_at(r, axis, best);
	return true;
}

/// How far two rects overlap along `axis`, less than 0 if they don't.
static int64_t wc_common(struct rect a, struct rect b, int axis) {
	int64_t lo = wc_lo(a, axis) > wc_lo(b, axis) ? wc_lo(a, axis) : wc_lo(b, axis);
	int64_t hi = wc_hi(a, axis) < wc_hi(b, axis) ? wc_hi(a, axis) : wc_hi(b, axis);
	return hi - lo;
}

/// The far side of a window that shares no line with its neighbours, see wc_split_line:
/// such as one that closes with the window next to it, when one neighbour takes both
/// places, or one next to a split of the other axis. Each neighbour that takes its place
/// (at a close) or makes room for it (at an open) pushes it away from the side it lies
/// on, by the area of its place that it takes or gave. The side is where the neighbour
/// lies wholly beyond the window's edge, within a pixel, and the two overlap across:
/// where the screen showed it last at a close, where i3 put it at an open. The line is
/// the window's edge that the pushes point to, along the axis they push most, the
/// vertical edge on a tie. A neighbour that lies on no side (it overlaps the window, as
/// the next tab or a fullscreen window does) pushes nothing, and with no push, or pushes
/// that cancel out, there is no line.
static bool
wc_far_side(const struct frame *self, struct rect r, bool open, struct rect *line) {
	int64_t push[2] = {0, 0};
	for (size_t i = 0; i < wc.n_frames; i++) {
		auto n = &wc.frames[i];
		struct rect at, beside;
		if (!wc_neighbour(self, r, n, open, &at, &beside)) {
			continue;
		}
		auto lies = open ? n->rect : n->drawn;
		int64_t place = wc_common(r, at, 0) * wc_common(r, at, 1);
		for (int axis = 0; axis < 2; axis++) {
			if (wc_common(lies, r, 1 - axis) <= 0) {
				continue;
			}
			if (wc_hi(lies, axis) <= wc_lo(r, axis) + 1) {
				push[axis] += place;
			} else if (wc_lo(lies, axis) >= wc_hi(r, axis) - 1) {
				push[axis] -= place;
			}
		}
	}
	if (push[0] == 0 && push[1] == 0) {
		return false;
	}
	int axis = llabs(push[0]) >= llabs(push[1]) ? 0 : 1;
	*line = wc_line_at(r, axis, push[axis] > 0 ? wc_hi(r, axis) : wc_lo(r, axis));
	return true;
}

/// The rect a window that opens grows from, or that one that closes shrinks into: its
/// own, collapsed onto a line. The line where it split from its neighbours, if it shares
/// one with them (wc_shared_line), and if not, its far side from those that take its
/// place or make room for it (wc_far_side). A window with neither, as the first on its
/// workspace, has no line. `r` is the window's rect.
static bool
wc_split_line(const struct frame *self, struct rect r, bool open, struct rect *line) {
	if (self->floating || r.width <= 0 || r.height <= 0) {
		return false;
	}
	return wc_shared_line(self, r, open, line) || wc_far_side(self, r, open, line);
}

/// The place that a window that closes leaves, as the windows that take it reach into
/// it: a rect that shrinks into a line on the change's curve, of which the window shows
/// the part inside its own rect `r` (where the screen showed it last). The takers are
/// its tiled neighbours where i3 put them (wc_neighbour). Each one that lies on a side
/// of the window, where the screen showed it last (as in wc_far_side), counts for the
/// axis it lies along, by the area of the place it takes; the axis is the one with
/// more, the one across on a tie. Along it, the rect starts at the edges of the takers
/// on either side, where the screen showed them, and at the window's own edge on a side
/// with none; the line is where the takers on the side before it end, or with none
/// there, those after it. As the takers move on the same curve, the rect's edges are
/// theirs in every frame: the window is cut where they reach, also when windows next to
/// it close with it, and its line then lies beyond its own rect, where the takers end.
/// False if no taker lies on a side of it.
static bool wc_close_region(const struct frame *self, struct rect r, struct rect *from,
                            struct rect *line) {
	int64_t weight[2] = {0, 0};
	bool seen[2][2] = {{false, false}, {false, false}};
	int32_t starts[2][2] = {{0, 0}, {0, 0}}, ends[2][2] = {{0, 0}, {0, 0}};
	for (size_t i = 0; i < wc.n_frames; i++) {
		auto n = &wc.frames[i];
		struct rect at, beside;
		if (!wc_neighbour(self, r, n, false, &at, &beside)) {
			continue;
		}
		int64_t place = wc_common(r, at, 0) * wc_common(r, at, 1);
		for (int axis = 0; axis < 2; axis++) {
			if (wc_common(n->drawn, r, 1 - axis) <= 0) {
				continue;
			}
			int side = wc_hi(n->drawn, axis) <= wc_lo(r, axis) + 1   ? 0
			           : wc_lo(n->drawn, axis) >= wc_hi(r, axis) - 1 ? 1
			                                                         : -1;
			if (side < 0) {
				continue;
			}
			weight[axis] += place;
			int32_t start =
			    side == 0 ? wc_hi(n->drawn, axis) : wc_lo(n->drawn, axis);
			int32_t end = side == 0 ? wc_hi(n->rect, axis) : wc_lo(n->rect, axis);
			// Where takers on one side differ, the one that reaches furthest.
			bool further =
			    !seen[axis][side] ||
			    (side == 0 ? end > ends[axis][side] : end < ends[axis][side]);
			if (further) {
				starts[axis][side] = start;
				ends[axis][side] = end;
			}
			seen[axis][side] = true;
		}
	}
	if (weight[0] == 0 && weight[1] == 0) {
		return false;
	}
	int axis = weight[0] >= weight[1] ? 0 : 1;
	int32_t at = seen[axis][0] ? ends[axis][0] : ends[axis][1];
	int32_t lo = seen[axis][0] ? starts[axis][0] : wc_lo(r, axis);
	int32_t hi = seen[axis][1] ? starts[axis][1] : wc_hi(r, axis);
	lo = at < lo ? at : lo;
	hi = at > hi ? at : hi;
	*from = axis == 0 ? (struct rect){lo, r.y, hi - lo, r.height}
	                  : (struct rect){r.x, lo, r.width, hi - lo};
	*line = wc_line_at(r, axis, at);
	return true;
}

enum {
	WC_LINE,
	WC_LINE_X,
	WC_LINE_Y,
	WC_LINE_WIDTH,
	WC_LINE_HEIGHT,
	WC_LINE_FAR,
	WC_FROM_X,
	WC_FROM_Y,
	WC_FROM_WIDTH,
	WC_FROM_HEIGHT,
};

/// The variables plugin-win-cause-line*: for an open or a close, whether the window has
/// a line (1 or 0), whether that line is its far side rather than a split line it
/// shares with its neighbours (1 or 0), and the rect it grows from or shrinks into; for
/// a close, plugin-win-cause-from* is the rect that shrinks into it (wc_close_region),
/// for anything else the window's own rect. With no line, or for any other trigger, the
/// window's own rect, or 0s if it is not a window i3 manages. picom asks for each
/// variable on its own, so the line is worked out once per animation start.
static double wc_line_variable(const struct picom_animation_start *start, void *user_data) {
	int kind = strcmp(start->trigger, "open") == 0    ? 1
	           : strcmp(start->trigger, "close") == 0 ? 2
	                                                  : 0;
	if (!wc.line.valid || wc.line.pass != start->frame ||
	    wc.line.window != start->window || wc.line.kind != kind) {
		wc.line.valid = true;
		wc.line.pass = start->frame;
		wc.line.window = start->window;
		wc.line.kind = kind;
		wc.line.found = wc.line.far = false;
		wc.line.rect = wc.line.from = (struct rect){0, 0, 0, 0};
		auto frame = wc_frame(start->window);
		// A window closes from where the screen showed it: i3 may have moved it
		// as it closes, as when its neighbour closed with it.
		auto r = frame == NULL ? (struct rect){0, 0, 0, 0}
		         : kind == 2   ? frame->drawn
		                       : frame->rect;
		if (frame != NULL && frame->placed) {
			wc.line.rect = wc.line.from = r;
		}
		if (frame != NULL && frame->placed && kind == 1) {
			wc.line.found = wc_split_line(frame, r, true, &wc.line.rect);
		} else if (frame != NULL && frame->placed && kind == 2) {
			wc.line.found =
			    !frame->floating && r.width > 0 && r.height > 0 &&
			    wc_close_region(frame, r, &wc.line.from, &wc.line.rect);
		}
		struct rect shared;
		wc.line.far = wc.line.found && !wc_shared_line(frame, r, kind == 1, &shared);
	}
	switch ((intptr_t)user_data) {
	case WC_LINE: return wc.line.found ? 1 : 0;
	case WC_LINE_X: return wc.line.rect.x;
	case WC_LINE_Y: return wc.line.rect.y;
	case WC_LINE_WIDTH: return wc.line.rect.width;
	case WC_LINE_HEIGHT: return wc.line.rect.height;
	case WC_LINE_FAR: return wc.line.far ? 1 : 0;
	case WC_FROM_X: return wc.line.from.x;
	case WC_FROM_Y: return wc.line.from.y;
	case WC_FROM_WIDTH: return wc.line.from.width;
	default: return wc.line.from.height;
	}
}

static bool wc_awaiting(void) {
	for (size_t i = 0; i < wc.n_awaited; i++) {
		if (!wc.awaited[i].drawn || wc_cause_open(wc.awaited[i].token)) {
			return true;
		}
	}
	return false;
}

static double wc_now(void) {
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	return (double)now.tv_sec + (double)now.tv_nsec / 1e9;
}

/// Holds the screen from the arrival of a window until the change is complete: i3 has
/// moved the others, and picom has the new window's first picture. picom then starts
/// the animations of the whole change in one pass, on one clock, and the new window
/// shows from the first frame. Without it, it starts a pass or more after its
/// neighbours: picom first asks the X server about it, and draws it once it has drawn.
/// At most WC_HOLD from the first arrival; another one in that time joins the change.
/// Other causes don't hold: i3 sends their moves in one go anyway.
static void wc_hold(double now) {
	bool waiting = wc_awaiting();
	if (wc.hold && waiting && !wc.holding) {
		wc.hold_until = now + WC_HOLD;
		wc.holding = true;
	}
	wc.hold = false;
	if (wc.holding && (!waiting || now >= wc.hold_until)) {
		wc.holding = false;
	}
	if (!wc.holding) {
		wc.n_awaited = 0;
	}
}

static const char *const wc_crop[] = {"crop-x", "crop-y", "crop-width", "crop-height"};

/// A crop box in whole pixels, edge by edge.
static struct rect wc_whole(const double box[4]) {
	long x = lround(box[0]), y = lround(box[1]);
	return (struct rect){(int32_t)x, (int32_t)y, (int32_t)(lround(box[0] + box[2]) - x),
	                     (int32_t)(lround(box[1] + box[3]) - y)};
}

/// Where a frame's animation draws its rectangle, in whole pixels, edge by edge, so edges
/// that windows share stay shared: as the last pass that ran it drew it. False if no
/// animation crops it.
static bool wc_drawn_rect(xcb_window_t frame, struct rect *r) {
	double box[4];
	for (int k = 0; k < 4; k++) {
		if (!wc.api->window_animation_output(frame, wc_crop[k], &box[k]) ||
		    !isfinite(box[k])) {
			return false;
		}
	}
	*r = wc_whole(box);
	return true;
}

static void wc_frame_callback(uint64_t frame, void *user_data) {
	(void)frame;
	(void)user_data;
	if (wc.compositor == XCB_NONE) {
		wc_ask_compositor();
	}
	wc_take_compositor();
	for (size_t i = 0; i < wc.n_frames; i++) {
		if (wc.frames[i].reading) {
			wc_take_state(&wc.frames[i], false);
		}
	}
	// Where the screen showed each frame last: its animation's rectangle, as the
	// last pass that ran it drew it, or where picom had it then.
	for (size_t i = 0; i < wc.n_frames; i++) {
		if (!wc_drawn_rect(wc.frames[i].id, &wc.frames[i].drawn)) {
			wc.frames[i].drawn = wc.frames[i].shown;
		}
	}
	double now = wc_now();
	wc_hold(now);
	bool runs = !(wc.holding && wc.hold_until > now);
	if (!runs) {
		wc.paused = wc.api->pause_screen("plugin-win-cause", wc.hold_until - now, 0);
	} else {
		if (wc.paused) {
			wc.api->pause_screen("plugin-win-cause", 0, 0);
			wc.paused = false;
		}
		// picom runs the animations in this pass: what it has now is what it
		// shows.
		for (size_t i = 0; i < wc.n_frames; i++) {
			auto f = &wc.frames[i];
			if (memcmp(&f->shown, &f->rect, sizeof(f->rect)) != 0) {
				f->shown = f->rect;
			}
		}
	}
	for (size_t i = 0; i < wc.n_renames; i++) {
		wc.api->rename_trigger(wc.renames[i].frame, "geometry", wc.renames[i].trigger);
	}
	wc.n_renames = 0;
}

__attribute__((constructor)) static void wc_init(void) {
	wc.api = picom_api_get_interfaces(0, WC_API_MINOR, "win-cause");
	if (wc.api == NULL) {
		wc_fail("picom's plugin API is not compatible: win-cause needs plugin "
		        "API " WC_API_VERSION " of the picom fork");
		return;
	}
	int screen_num = 0;
	wc.c = xcb_connect(NULL, &screen_num);
	if (xcb_connection_has_error(wc.c)) {
		wc_fail("cannot connect to the X server");
		xcb_disconnect(wc.c);
		wc.c = NULL;
		return;
	}
	auto it = xcb_setup_roots_iterator(xcb_get_setup(wc.c));
	for (int i = screen_num; i > 0 && it.rem; i--) {
		xcb_screen_next(&it);
	}
	wc.root = it.data->root;
	char compositor[32];
	snprintf(compositor, sizeof(compositor), "_NET_WM_CM_S%d", screen_num);
	wc.a_compositor = wc_atom(compositor);
	wc.a_arm = wc_atom(WC_ARM_MESSAGE);
	wc.a_sync = wc_atom("I3_SYNC");
	wc.a_i3_socket = wc_atom("I3_SOCKET_PATH");
	wc.a_state = wc_atom("_NET_WM_STATE");
	wc.a_fullscreen = wc_atom("_NET_WM_STATE_FULLSCREEN");
	wc.a_floating = wc_atom("I3_FLOATING_WINDOW");
	wc.i3 = wc_i3_runs();
	if (!wc.i3) {
		wc_fail("i3 is not running, so no window gets a cause");
	}
	auto damage = xcb_query_extension_reply(
	    wc.c, xcb_query_extension(wc.c, strlen("DAMAGE"), "DAMAGE"), NULL);
	if (damage != NULL && damage->present) {
		wc.damage_notify = damage->first_event + XCB_DAMAGE_NOTIFY;
	}
	free(damage);
	wc_scan();
	static const char *const triggers[] = {"moved-by-open", "moved-by-close",
	                                       "moved-by-fullscreen", "moved-by-float"};
	for (size_t i = 0; i < sizeof(triggers) / sizeof(triggers[0]); i++) {
		wc.api->add_animation_trigger(triggers[i]);
	}
	static const char *const line[] = {
	    "line",     "line-x", "line-y", "line-width", "line-height",
	    "line-far", "from-x", "from-y", "from-width", "from-height"};
	for (intptr_t i = 0; i < (intptr_t)(sizeof(line) / sizeof(line[0])); i++) {
		wc.api->add_animation_variable("plugin-win-cause", line[i],
		                               wc_line_variable, (void *)i);
	}
	wc.api->add_x_event_callback(wc_x_event, NULL);
	wc.api->add_frame_callback(wc_frame_callback, NULL);
}
