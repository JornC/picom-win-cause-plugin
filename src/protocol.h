// SPDX-License-Identifier: MPL-2.0
// Copyright (c) JornC

/// How the win-cause command talks to the plugin. It sends client messages to the root
/// window with the SubstructureNotify mask, as EWMH clients do, so they reach picom's
/// connection and keep their order against every window change picom sees. i3 doesn't
/// select that mask.

#pragma once

/// data32[0] is the atom of a trigger name, data32[1] the command's token, data32[2] a
/// window: moves of that window start that trigger until the token's window is destroyed.
/// With window 0, moves of every window do. X destroys the window when the command dies,
/// so a command that is killed leaves nothing behind.
#define WC_ARM_MESSAGE "_WIN_CAUSE_ARM"

/// Tokens with this bit are the plugin's own. The command's are window ids, which X keeps
/// below this bit.
#define WC_TOKEN_PLUGIN 0x80000000U
