// SPDX-License-Identifier: MPL-2.0
// Copyright (c) JornC

/// win-cause run <trigger> [<window>...] -- <command> [argument]...
///
/// Runs a command, and the windows it moves start the trigger instead of their own.
/// With a list, only those windows do. The trigger holds from before the command to i3's
/// answer after it, so a command that moves nothing leaves nothing behind, and it ends
/// with this process's window, so neither does one that is killed. Needs i3: without it
/// the command just runs.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include <xcb/xcb.h>

#include "protocol.h"

static xcb_atom_t atom(xcb_connection_t *c, const char *name) {
	xcb_intern_atom_reply_t *reply = xcb_intern_atom_reply(
	    c, xcb_intern_atom(c, 0, (uint16_t)strlen(name), name), NULL);
	xcb_atom_t result = reply != NULL ? reply->atom : XCB_ATOM_NONE;
	free(reply);
	return result;
}

static int usage(void) {
	fputs("usage: win-cause run <trigger> [<window>...] -- <command> [argument]...\n",
	      stderr);
	return 2;
}

static int run(char **command) {
	pid_t pid = fork();
	if (pid < 0) {
		perror("win-cause: fork");
		return 1;
	}
	if (pid == 0) {
		execvp(command[0], command);
		perror(command[0]);
		_exit(127);
	}
	int status = 0;
	if (waitpid(pid, &status, 0) < 0) {
		perror("win-cause: waitpid");
		return 1;
	}
	return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}

static bool i3_runs(xcb_connection_t *c, xcb_window_t root) {
	xcb_get_property_reply_t *reply = xcb_get_property_reply(
	    c, xcb_get_property(c, 0, root, atom(c, "I3_SOCKET_PATH"), XCB_ATOM_ANY, 0, 1),
	    NULL);
	bool runs = reply != NULL && reply->type != XCB_ATOM_NONE;
	free(reply);
	return runs;
}

int main(int argc, char **argv) {
	int separator = 3;
	while (separator < argc && strcmp(argv[separator], "--") != 0) {
		separator++;
	}
	if (argc < 5 || strcmp(argv[1], "run") != 0 || separator + 1 >= argc) {
		return usage();
	}
	const char *trigger = argv[2];
	char **command = argv + separator + 1;

	int screen_num = 0;
	xcb_connection_t *c = xcb_connect(NULL, &screen_num);
	if (xcb_connection_has_error(c)) {
		fputs("win-cause: cannot connect to the X server\n", stderr);
		return 1;
	}
	xcb_screen_iterator_t it = xcb_setup_roots_iterator(xcb_get_setup(c));
	for (int i = screen_num; i > 0 && it.rem; i--) {
		xcb_screen_next(&it);
	}
	xcb_window_t root = it.data->root;
	char compositor_name[32];
	snprintf(compositor_name, sizeof(compositor_name), "_NET_WM_CM_S%d", screen_num);
	xcb_get_selection_owner_reply_t *owner_reply = xcb_get_selection_owner_reply(
	    c, xcb_get_selection_owner(c, atom(c, compositor_name)), NULL);
	xcb_window_t compositor = owner_reply != NULL ? owner_reply->owner : XCB_NONE;
	free(owner_reply);
	if (compositor == XCB_NONE || !i3_runs(c, root)) {
		// No animation to name, so only the command is left to do.
		xcb_disconnect(c);
		return run(command);
	}

	xcb_atom_t arm = atom(c, WC_ARM_MESSAGE), name = atom(c, trigger),
	           sync = atom(c, "I3_SYNC");
	// Created first: it is the token, and what ends the cause if we die.
	xcb_window_t token = xcb_generate_id(c);
	xcb_create_window(c, XCB_COPY_FROM_PARENT, token, root, -1, -1, 1, 1, 0,
	                  XCB_WINDOW_CLASS_INPUT_ONLY, XCB_COPY_FROM_PARENT, 0, NULL);
	for (int i = 3; i < separator || i == 3; i++) {
		xcb_client_message_event_t message = {
		    .response_type = XCB_CLIENT_MESSAGE,
		    .format = 32,
		    .window = root,
		    .type = arm,
		    .data.data32 = {name, token,
		                    i < separator ? (uint32_t)strtoul(argv[i], NULL, 0) : 0},
		};
		xcb_send_event(c, 0, root, XCB_EVENT_MASK_SUBSTRUCTURE_NOTIFY,
		               (const char *)&message);
	}
	// The round trip puts the messages ahead of everything the command makes i3 do.
	free(xcb_get_input_focus_reply(c, xcb_get_input_focus(c), NULL));

	int status = run(command);

	// i3 answers after all it did for the command, on the connection that did it.
	xcb_client_message_event_t ask = {
	    .response_type = XCB_CLIENT_MESSAGE,
	    .format = 32,
	    .window = root,
	    .type = sync,
	    .data.data32 = {token, 0},
	};
	xcb_send_event(c, 0, root, XCB_EVENT_MASK_SUBSTRUCTURE_REDIRECT, (const char *)&ask);
	xcb_flush(c);
	xcb_generic_event_t *event;
	while ((event = xcb_wait_for_event(c)) != NULL) {
		bool answered = (event->response_type & 0x7f) == XCB_CLIENT_MESSAGE;
		free(event);
		if (answered) {
			break;
		}
	}
	// Destroyed here, and not by the disconnect, so that what runs after us can't
	// count as part of this: the disconnect happens when the X server gets to it.
	xcb_destroy_window(c, token);
	free(xcb_get_input_focus_reply(c, xcb_get_input_focus(c), NULL));
	xcb_disconnect(c);
	return status;
}
