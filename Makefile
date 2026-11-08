# picom plugin: tells animation scripts why a window moved, and the win-cause command.
# Needs the picom fork's plugin API, of the version src/win_cause.h names (WC_API_MINOR).
# Point PICOM_API_CFLAGS at a picom tree if the installed picom doesn't have it, e.g.
#   make PICOM_API_CFLAGS=-I/path/to/picom/include
PKG_CONFIG ?= pkg-config
PICOM_API_CFLAGS ?= $(strip $(shell $(PKG_CONFIG) --cflags picom-api 2>/dev/null) \
	$(addprefix -I,$(shell $(PKG_CONFIG) --variable=includedir picom-api 2>/dev/null)))
CFLAGS ?= -O2 -g
override CFLAGS += -std=gnu23 -Wall -Wextra -Wfatal-errors $(PICOM_API_CFLAGS) \
	$(shell $(PKG_CONFIG) --cflags xcb)
LDLIBS = $(shell $(PKG_CONFIG) --libs xcb)
PLUGIN_CFLAGS = $(shell $(PKG_CONFIG) --cflags xcb-damage)
PLUGIN_LDLIBS = -lm

all: build/win-cause.so build/win-cause build/slowclient

build/win-cause.so: src/plugin.c src/protocol.h src/win_cause.h
	@mkdir -p build
	$(CC) $(CFLAGS) $(PLUGIN_CFLAGS) $(LDFLAGS) -shared -fPIC -o $@ src/plugin.c \
		$(LDLIBS) $(PLUGIN_LDLIBS)

build/win-cause: src/cmd.c src/protocol.h
	@mkdir -p build
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ src/cmd.c $(LDLIBS)

build/slowclient: tests/slowclient.c
	@mkdir -p build
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ tests/slowclient.c $(LDLIBS) \
		$(shell $(PKG_CONFIG) --libs xcb-sync)

# The tests that need no X server: what a package's check step runs. WS_DIRECTION is the
# root of a ws-direction tree, whose tests/check-frames.py the tile checker builds on.
check: all
	$(if $(WS_DIRECTION),,$(error make check needs WS_DIRECTION))
	tests/check_tile_test.py
	tests/screen_test.py

# Runs real i3, xterm and picom on its own Xvfb. PICOM is a picom with the API above,
# WS_DIRECTION the root of a built ws-direction tree.
check-harness: all
	$(if $(WS_DIRECTION),,$(error make check-harness needs WS_DIRECTION))
	tests/run.py
	tests/tile.py
	tests/with_ws_direction.py
	tests/drawn.py
	tests/covered.py

check-all: check check-harness

# As ws-direction installs: the plugin and the styles where picom finds them by name (with
# the parts they include in styles/parts), and the command under PREFIX. A package sets DESTDIR, PREFIX=/usr, STYLEDIR, and PLUGINDIR to
# picom's plugin directory.
PICOM_CONFIG_DIR ?= $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/picom
PLUGINDIR ?= $(PICOM_CONFIG_DIR)/plugins
STYLEDIR ?= $(PICOM_CONFIG_DIR)/include/win-cause
PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
STYLES = $(notdir $(wildcard styles/*.conf))
PARTS = $(notdir $(wildcard styles/parts/*.conf))

install: all
	install -Dm755 build/win-cause.so $(DESTDIR)$(PLUGINDIR)/win-cause.so
	install -Dm644 -t $(DESTDIR)$(STYLEDIR) $(addprefix styles/,$(STYLES))
	install -Dm644 -t $(DESTDIR)$(STYLEDIR)/parts $(addprefix styles/parts/,$(PARTS))
	install -Dm755 build/win-cause $(DESTDIR)$(BINDIR)/win-cause

uninstall:
	rm -f $(DESTDIR)$(PLUGINDIR)/win-cause.so $(DESTDIR)$(BINDIR)/win-cause \
		$(addprefix $(DESTDIR)$(STYLEDIR)/,$(STYLES)) \
		$(addprefix $(DESTDIR)$(STYLEDIR)/parts/,$(PARTS))
	rmdir --ignore-fail-on-non-empty $(DESTDIR)$(STYLEDIR)/parts $(DESTDIR)$(STYLEDIR)

clean:
	rm -rf build

.PHONY: all check check-harness check-all clean install uninstall
