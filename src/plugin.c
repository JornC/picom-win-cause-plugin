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

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
		if (wc.frames[i].client == client) {
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

static struct frame *wc_add_frame(xcb_window_t id, xcb_window_t client) {
	PUSH(wc.frames, wc.n_frames, ((struct frame){.id = id, .client = client}));
	return &wc.frames[wc.n_frames - 1];
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

static void wc_open_cause(const char *trigger, xcb_window_t only, xcb_window_t except) {
	if (!wc.i3 || wc.compositor == XCB_NONE) {
		return;
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

/// Reads what X has of a frame: whether its client is fullscreen.
static void wc_read_frame(struct frame *frame) {
	auto state_read =
	    xcb_get_property(wc.c, 0, frame->client, wc.a_state, XCB_ATOM_ATOM, 0, 32);
	auto state = xcb_get_property_reply(wc.c, state_read, NULL);
	frame->fullscreen = wc_has_atom(state, wc.a_fullscreen);
	free(state);
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
	auto frame = wc_frame_of_client(e->window);
	if (frame != NULL) {
		// i3 gave the client a new frame.
		if (e->parent != wc.root) {
			frame->id = e->parent;
		}
		return;
	}
	if (e->event == wc.root && e->parent != wc.root) {
		wc_open_cause("moved-by-open", XCB_NONE, e->parent);
		wc_add_frame(e->parent, e->window);
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
	auto cause = wc_cause_for(frame->id);
	if (cause != NULL) {
		PUSH(wc.renames, wc.n_renames, ((struct rename){frame->id, cause->trigger}));
	}
}

/// The client goes first, i3 moves the others, and its frame goes last.
static void wc_on_client_gone(xcb_window_t client) {
	auto frame = wc_frame_of_client(client);
	if (frame != NULL) {
		auto id = frame->id;
		if (frame->reading) {
			xcb_discard_reply(wc.c, frame->state.sequence);
		}
		*frame = wc.frames[--wc.n_frames];
		wc_open_cause("moved-by-close", XCB_NONE, id);
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
	} else if (e->atom == wc.a_floating && e->state == XCB_PROPERTY_NEW_VALUE) {
		// The window that floats, and the windows that take its place.
		wc_open_cause("moved-by-float", XCB_NONE, XCB_NONE);
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

static void wc_x_event(const void *event, void *user_data) {
	(void)user_data;
	const xcb_generic_event_t *e = event;
	uint8_t type = e->response_type & 0x7f;
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
	case XCB_UNMAP_NOTIFY:
		wc_on_client_gone(((const xcb_unmap_notify_event_t *)event)->window);
		break;
	case XCB_DESTROY_NOTIFY: {
		auto window = ((const xcb_destroy_notify_event_t *)event)->window;
		wc_on_client_gone(window);
		wc_close_cause(window);
		break;
	}
	case XCB_PROPERTY_NOTIFY: wc_on_property(event); break;
	default: break;
	}
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
	wc_scan();
	static const char *const triggers[] = {"moved-by-open", "moved-by-close",
	                                       "moved-by-fullscreen", "moved-by-float"};
	for (size_t i = 0; i < sizeof(triggers) / sizeof(triggers[0]); i++) {
		wc.api->add_animation_trigger(triggers[i]);
	}
	wc.api->add_x_event_callback(wc_x_event, NULL);
	wc.api->add_frame_callback(wc_frame_callback, NULL);
}
