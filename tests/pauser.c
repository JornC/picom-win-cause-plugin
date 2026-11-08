// SPDX-License-Identifier: MPL-2.0
// Copyright (c) JornC

// A picom plugin for tests/opens.py: it pauses the screen for $PAUSER_SECONDS (0.2 by
// default) from the map of each top-level window after the first, as another plugin may
// while it reads what changed (ws-direction does, while the X server is busy). picom then
// starts the animations of the change only once the pause ends.

#include <stdlib.h>
#include <time.h>

#include <xcb/xcb.h>

#include <picom/api.h>

static const struct picom_api *api;
static double seconds = 0.2, until;
static unsigned maps;
static bool paused;
static xcb_window_t root;

static double now(void) {
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static void x_event(const void *event, void *user_data) {
	(void)user_data;
	const xcb_map_notify_event_t *e = event;
	if ((e->response_type & 0x7f) == XCB_MAP_NOTIFY && e->event == root && ++maps > 1) {
		until = now() + seconds;
	}
}

static void frame_callback(uint64_t frame, void *user_data) {
	(void)frame;
	(void)user_data;
	double t = now();
	if (until > t) {
		paused = api->pause_screen("plugin-pauser", until - t, 0);
	} else if (paused) {
		api->pause_screen("plugin-pauser", 0, 0);
		paused = false;
	}
}

__attribute__((constructor)) static void init(void) {
	api = picom_api_get_interfaces(0, PICOM_API_MINOR, "pauser");
	if (api == NULL) {
		return;
	}
	xcb_connection_t *c = xcb_connect(NULL, NULL);
	if (xcb_connection_has_error(c)) {
		return;
	}
	root = xcb_setup_roots_iterator(xcb_get_setup(c)).data->root;
	xcb_disconnect(c);
	const char *s = getenv("PAUSER_SECONDS");
	if (s != NULL) {
		seconds = atof(s);
	}
	api->add_x_event_callback(x_event, NULL);
	api->add_frame_callback(frame_callback, NULL);
}
