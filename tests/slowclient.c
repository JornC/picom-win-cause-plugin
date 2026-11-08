// SPDX-License-Identifier: MPL-2.0
// Copyright (c) JornC

// A window that takes its time to draw, for the tests. It starts a frame when its
// size changes, at the size and the sync request it knows then, and DELAY milliseconds
// later it fills all of the window at that size and writes the request to its frame sync
// counter. A size that came meanwhile starts the next frame then. Without "sync" it
// doesn't have the frame sync protocol, and only draws. With "never" as the delay it
// draws its first frame, and nothing after that. With "partial" it has no frame sync
// and leaves a strip at the bottom undrawn, as a terminal does below its last row. A
// background of None: the X server paints nothing, so all damage is the client's own.
//
// A delay of A/B takes A ms and B ms in turn, frame by frame: a program whose draw time
// falls and rises.
//
// With "argb" it has a visual with alpha and a transparent background, and draws its first
// frame DELAY ms after it is mapped too: its first picture is empty for that long, as
// wezterm's and Chrome's are. It draws at an alpha of $SLOWCLIENT_ALPHA (0 to 255), or
// opaque. The class in WM_CLASS is $SLOWCLIENT_CLASS, or slowclient.
//
// Usage: slowclient TITLE DELAY|A/B|never [sync|partial|argb]

#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <xcb/sync.h>
#include <xcb/xcb.h>

static xcb_atom_t atom(xcb_connection_t *c, const char *name) {
	auto reply = xcb_intern_atom_reply(
	    c, xcb_intern_atom(c, 0, (uint16_t)strlen(name), name), NULL);
	xcb_atom_t value = reply != NULL ? reply->atom : XCB_ATOM_NONE;
	free(reply);
	return value;
}

static double now_ms(void) {
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (double)t.tv_sec * 1e3 + (double)t.tv_nsec / 1e6;
}

int main(int argc, char **argv) {
	if (argc < 3) {
		fputs("usage: slowclient TITLE DELAY|A/B|never [sync|partial|argb]\n", stderr);
		return 2;
	}
	bool never = strcmp(argv[2], "never") == 0;
	char *rest = NULL;
	double delays[2];
	delays[0] = never ? 0 : strtod(argv[2], &rest);
	delays[1] = rest != NULL && *rest == '/' ? strtod(rest + 1, NULL) : delays[0];
	unsigned frames = 0;
	bool sync = argc > 3 && strcmp(argv[3], "sync") == 0;
	bool partial = argc > 3 && strcmp(argv[3], "partial") == 0;
	bool argb = argc > 3 && strcmp(argv[3], "argb") == 0;

	xcb_connection_t *c = xcb_connect(NULL, NULL);
	if (xcb_connection_has_error(c)) {
		fputs("slowclient: cannot connect to the X server\n", stderr);
		return 1;
	}
	xcb_screen_t *screen = xcb_setup_roots_iterator(xcb_get_setup(c)).data;
	xcb_window_t w = xcb_generate_id(c);
	const uint32_t mask = XCB_EVENT_MASK_EXPOSURE | XCB_EVENT_MASK_STRUCTURE_NOTIFY;
	xcb_visualid_t visual = XCB_NONE;
	for (auto d = xcb_screen_allowed_depths_iterator(screen); argb && d.rem && !visual;
	     xcb_depth_next(&d)) {
		for (auto v = xcb_depth_visuals_iterator(d.data); d.data->depth == 32 && v.rem;
		     xcb_visualtype_next(&v)) {
			if (v.data->_class == XCB_VISUAL_CLASS_TRUE_COLOR) {
				visual = v.data->visual_id;
				break;
			}
		}
	}
	if (argb && visual == XCB_NONE) {
		fputs("slowclient: no visual with alpha\n", stderr);
		return 1;
	}
	if (argb) {
		xcb_colormap_t colormap = xcb_generate_id(c);
		xcb_create_colormap(c, XCB_COLORMAP_ALLOC_NONE, colormap, screen->root, visual);
		const uint32_t values[] = {0, 0, mask, colormap};
		xcb_create_window(c, 32, w, screen->root, 0, 0, 400, 300, 0,
		                  XCB_WINDOW_CLASS_INPUT_OUTPUT, visual,
		                  XCB_CW_BACK_PIXEL | XCB_CW_BORDER_PIXEL | XCB_CW_EVENT_MASK |
		                      XCB_CW_COLORMAP,
		                  values);
	} else {
		const uint32_t values[] = {XCB_BACK_PIXMAP_NONE, mask};
		xcb_create_window(c, XCB_COPY_FROM_PARENT, w, screen->root, 0, 0, 400, 300, 0,
		                  XCB_WINDOW_CLASS_INPUT_OUTPUT, screen->root_visual,
		                  XCB_CW_BACK_PIXMAP | XCB_CW_EVENT_MASK, values);
	}
	const char *alpha_env = getenv("SLOWCLIENT_ALPHA");
	const uint32_t alpha = argb && alpha_env != NULL ? (uint32_t)atoi(alpha_env) & 0xff : 0xff;
	const char *klass = getenv("SLOWCLIENT_CLASS");
	char class[256];
	int n = snprintf(class, sizeof(class), "%s%c%s", argv[1], 0,
	                 klass != NULL ? klass : "slowclient");
	xcb_change_property(c, XCB_PROP_MODE_REPLACE, w, XCB_ATOM_WM_CLASS,
	                    XCB_ATOM_STRING, 8, (uint32_t)n + 1, class);
	xcb_change_property(c, XCB_PROP_MODE_REPLACE, w, XCB_ATOM_WM_NAME,
	                    XCB_ATOM_STRING, 8, (uint32_t)strlen(argv[1]), argv[1]);

	xcb_atom_t a_protocols = atom(c, "WM_PROTOCOLS");
	xcb_atom_t a_delete = atom(c, "WM_DELETE_WINDOW");
	xcb_atom_t a_request = atom(c, "_NET_WM_SYNC_REQUEST");
	xcb_sync_counter_t counter = XCB_NONE;
	xcb_atom_t protocols[2] = {a_delete, a_request};
	xcb_change_property(c, XCB_PROP_MODE_REPLACE, w, a_protocols, XCB_ATOM_ATOM, 32,
	                    sync ? 2 : 1, protocols);
	if (sync) {
		free(xcb_sync_initialize_reply(c, xcb_sync_initialize(c, 3, 1), NULL));
		counter = xcb_generate_id(c);
		xcb_sync_create_counter(c, counter, (xcb_sync_int64_t){0, 0});
		xcb_change_property(c, XCB_PROP_MODE_REPLACE, w,
		                    atom(c, "_NET_WM_SYNC_REQUEST_COUNTER"),
		                    XCB_ATOM_CARDINAL, 32, 1, &counter);
	}

	xcb_gcontext_t gc = xcb_generate_id(c);
	xcb_create_gc(c, gc, w, 0, NULL);
	xcb_map_window(c, w);
	xcb_flush(c);

	uint16_t width = 400, height = 300, frame_width = 0, frame_height = 0;
	int64_t request = 0, frame_request = 0;
	bool dirty = false, drawing = false, first = true;
	double due = 0;
	struct pollfd fd = {.fd = xcb_get_file_descriptor(c), .events = POLLIN};
	for (;;) {
		xcb_generic_event_t *e;
		while ((e = xcb_poll_for_event(c)) != NULL) {
			switch (e->response_type & 0x7f) {
			case XCB_CONFIGURE_NOTIFY: {
				auto ce = (xcb_configure_notify_event_t *)e;
				if (ce->window == w &&
				    (ce->width != width || ce->height != height)) {
					width = ce->width;
					height = ce->height;
					dirty = true;
				}
				break;
			}
			case XCB_EXPOSE: dirty = true; break;
			case XCB_CLIENT_MESSAGE: {
				auto m = (xcb_client_message_event_t *)e;
				if (m->data.data32[0] == a_delete) {
					xcb_destroy_window(c, w);
					xcb_flush(c);
					usleep(20000);
					return 0;
				}
				if (m->data.data32[0] == a_request) {
					request = (int64_t)m->data.data32[2] |
					          ((int64_t)m->data.data32[3] << 32);
				}
				break;
			}
			default: break;
			}
			free(e);
		}
		if (xcb_connection_has_error(c)) {
			return 0;
		}
		if (drawing && now_ms() >= due) {
			drawing = false;
			first = false;
			const uint32_t rgb = 0x206080 + ((uint32_t)frame_width & 0xff);
			uint32_t colour = rgb;
			if (argb) {
				colour = alpha << 24;
				for (int shift = 0; shift < 24; shift += 8) {
					colour |= ((rgb >> shift & 0xff) * alpha / 0xff) << shift;
				}
			}
			xcb_change_gc(c, gc, XCB_GC_FOREGROUND, &colour);
			uint16_t strip = partial && frame_height > 8 ? 8 : 0;
			xcb_rectangle_t all = {0, 0, frame_width,
			                       (uint16_t)(frame_height - strip)};
			xcb_poly_fill_rectangle(c, w, gc, 1, &all);
			if (sync) {
				xcb_sync_set_counter(
				    c, counter,
				    (xcb_sync_int64_t){(int32_t)(frame_request >> 32),
				                       (uint32_t)frame_request});
			}
		}
		if (dirty && !drawing && (!never || first)) {
			dirty = false;
			drawing = true;
			due = now_ms() + (first && !argb ? 0 : delays[frames++ % 2]);
			frame_width = width;
			frame_height = height;
			frame_request = request;
		}
		xcb_flush(c);
		double left = drawing ? due - now_ms() : -1;
		poll(&fd, 1, drawing ? (left > 0 ? (int)left + 1 : 0) : -1);
	}
}
