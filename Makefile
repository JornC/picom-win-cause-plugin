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
PLUGIN_LDLIBS = -lm

all: build/win-cause.so build/win-cause build/slowclient

build/win-cause.so: src/plugin.c src/protocol.h src/win_cause.h
	@mkdir -p build
	$(CC) $(CFLAGS) $(LDFLAGS) -shared -fPIC -o $@ src/plugin.c \
		$(LDLIBS) $(PLUGIN_LDLIBS)

build/win-cause: src/cmd.c src/protocol.h
	@mkdir -p build
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ src/cmd.c $(LDLIBS)

build/slowclient: tests/slowclient.c
	@mkdir -p build
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ tests/slowclient.c $(LDLIBS) \
		$(shell $(PKG_CONFIG) --libs xcb-sync)

# The tests that need no X server: what a package's check step runs.
check: all

# Runs real i3, xterm and picom on its own Xvfb. PICOM is a picom with the API above.
check-harness: all
	tests/run.py

check-all: check check-harness

# As ws-direction installs: the plugin where picom finds it by name, and the command under
# PREFIX. A package sets DESTDIR, PREFIX=/usr, and PLUGINDIR to picom's plugin directory.
PICOM_CONFIG_DIR ?= $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/picom
PLUGINDIR ?= $(PICOM_CONFIG_DIR)/plugins
PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin

install: all
	install -Dm755 build/win-cause.so $(DESTDIR)$(PLUGINDIR)/win-cause.so
	install -Dm755 build/win-cause $(DESTDIR)$(BINDIR)/win-cause

uninstall:
	rm -f $(DESTDIR)$(PLUGINDIR)/win-cause.so $(DESTDIR)$(BINDIR)/win-cause

clean:
	rm -rf build

.PHONY: all check check-harness check-all clean install uninstall
