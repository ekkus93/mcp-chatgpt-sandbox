#!/bin/bash
# Creates the container user at start, matching the host checkout owner's
# uid/gid (§5.3), then drops from root to that user before exec'ing the
# real command. Root privileges remain available inside the container via
# passwordless sudo (§5.3) — this script is what makes that user exist.
#
# Env vars, set by the daemon per workspace (never baked into the image):
#   MCP_UID, MCP_GID  target uid/gid. Unset means container.user: root
#                     (already refused by the daemon unless userns
#                     remapping is active, §5.3) — run as uid 0 directly.
#   MCP_USER          desired username, default "mcpuser".
#   MCP_SUDO          "true"/"false", default "true" — configure
#                     passwordless sudo for the created user.

set -euo pipefail

MCP_USER="${MCP_USER:-mcpuser}"
MCP_SUDO="${MCP_SUDO:-true}"

if [ "$(id -u)" != "0" ]; then
    # Not root at entrypoint: nothing to create, just run the command.
    exec "$@"
fi

if [ -z "${MCP_UID:-}" ] || [ -z "${MCP_GID:-}" ]; then
    # container.user: root — uid 0 in the container is meant to map to an
    # unprivileged host uid via user-namespace remapping.
    exec "$@"
fi

if ! getent group "$MCP_GID" >/dev/null 2>&1; then
    groupadd -g "$MCP_GID" "$MCP_USER"
fi
GROUP_NAME="$(getent group "$MCP_GID" | cut -d: -f1)"

if ! getent passwd "$MCP_UID" >/dev/null 2>&1; then
    useradd -u "$MCP_UID" -g "$MCP_GID" -m -s /bin/bash "$MCP_USER"
fi
USER_NAME="$(getent passwd "$MCP_UID" | cut -d: -f1)"

if [ "$MCP_SUDO" = "true" ]; then
    echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/mcp-$USER_NAME"
    chmod 0440 "/etc/sudoers.d/mcp-$USER_NAME"
fi

HOME_DIR="$(getent passwd "$MCP_UID" | cut -d: -f6)"

# Ensure the user's own home is usable even though the container started
# as root.
chown "$MCP_UID:$MCP_GID" "$HOME_DIR" 2>/dev/null || true

# HOME/USER/LOGNAME are set explicitly for the target user; everything
# else the daemon put in the environment (§14.3's overrides included)
# passes through unchanged — setpriv's --reset-env would wipe those too,
# which is why it is not used here.
export HOME="$HOME_DIR" USER="$USER_NAME" LOGNAME="$USER_NAME"
exec setpriv --reuid="$MCP_UID" --regid="$MCP_GID" --clear-groups "$@"
