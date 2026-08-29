#!/bin/bash
# Creates the container user at start, matching the host checkout owner's
# uid/gid (§5.3), then drops from root to that user before exec'ing the
# real command. Root privileges remain available inside the container via
# passwordless sudo (§5.3) — this script is what makes that user exist.
#
# Env vars, set by the daemon per workspace (never baked into the image):
#   MCP_UID, MCP_GID     target uid/gid. Unset means container.user: root
#                        (already refused by the daemon unless userns
#                        remapping is active, §5.3) — run as uid 0 directly.
#   MCP_USER             desired username, default "mcpuser".
#   MCP_SUDO             "true"/"false", default "true" — configure
#                        passwordless sudo for the created user.
#   MCP_WORKSPACE_PATH   container path of the repository mount, default
#                        "/workspace" — recorded in git's safe.directory.
#   MCP_SSH_MODE         "agent"/"file" (§19), default "agent" — whether
#                        the container-local SSH config's Host github.com
#                        block includes an IdentityFile line (file mode
#                        only; agent mode authenticates via SSH_AUTH_SOCK,
#                        no static identity file to point at).
#   MCP_CACHE_PATHS      colon-separated, $HOME-relative dependency-cache
#                        paths (§11, S30) — e.g. ".cargo:.rustup". Each is
#                        a separate Docker named-volume mount, created
#                        root-owned by default; listed here so this
#                        script can chown them to the container user,
#                        the same way it already does for $HOME itself.

set -euo pipefail

MCP_USER="${MCP_USER:-mcpuser}"
MCP_SUDO="${MCP_SUDO:-true}"
MCP_WORKSPACE_PATH="${MCP_WORKSPACE_PATH:-/workspace}"
MCP_SSH_MODE="${MCP_SSH_MODE:-agent}"
MCP_CACHE_PATHS="${MCP_CACHE_PATHS:-}"

if [ "$(id -u)" = "0" ]; then
    # §18.2: git run from inside the container SHALL trust the mounted
    # repository despite it having a different owner than whichever uid
    # ends up running git, and SHALL NOT prompt for remote credentials.
    # Container-local: /etc/gitconfig is never inside the bind mount.
    mkdir -p /etc
    cat > /etc/gitconfig <<EOF
[safe]
	directory = $MCP_WORKSPACE_PATH
EOF
    # §19.3: an HTTPS-configured origin still authenticates with the
    # workspace's SSH key rather than prompting for a password/token.
    git config --system url."git@github.com:".insteadOf "https://github.com/"
fi
export GIT_TERMINAL_PROMPT=0

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

# §11: cache volumes SHALL be owned by the container user's uid. A fresh
# Docker named volume is root-owned regardless of what mounts on top of
# it; not recursive, matching $HOME's own chown above — this is about
# making the mount point itself writable, not repairing ownership of
# whatever a previous container run already left inside it.
#
# HR-008 (docs/HARDENING_SPEC.md §6): this daemon-side validation is
# necessary but not sufficient on its own — it is deliberately kept as
# an independent, defense-in-depth control alongside H6's own
# `environment:`-key allowlist-reject at project-config load time
# (`project_config.py`'s `_environment_cannot_shadow_daemon_variables`,
# HR-006), not because that control is bypassable: H6 already rejects
# any project `environment:` entry starting with `MCP_` outright, so
# `MCP_CACHE_PATHS` cannot reach this script through that path today.
# The point of keeping this validation anyway is the same one RR-031
# makes for merge ordering — a regression in one control should not
# silently become a bypass of both. Since this runs as root, before the
# identity drop below, an unsafe entry here means an arbitrary
# `mkdir`/`chown` as root: reproduced with
# `MCP_CACHE_PATHS='../../etc:../../usr/bin'` leaving `/etc` and
# `/usr/bin` owned by the container user.
if [ -n "$MCP_CACHE_PATHS" ]; then
    # RR-029 (docs/REMEDIATION_SPEC.md §26): `IFS=':' read -ra ... <<<
    # "$MCP_CACHE_PATHS"` used to do this splitting in one `read` call,
    # which has two failure modes verified empirically. First, `read`
    # stops at the first newline in its input — a value containing one
    # (`$'.cargo\n../../etc'`) silently dropped everything after it,
    # with no rejection and no mount, contradicting this section's own
    # claim (corrected above) of exiting non-zero on a bad entry.
    # Rejected outright here, before any splitting, rather than folded
    # into the loop below.
    case "$MCP_CACHE_PATHS" in
        *$'\n'*)
            echo "entrypoint: MCP_CACHE_PATHS may not contain a newline" >&2
            exit 1
            ;;
    esac
    # Second, `read`'s field splitting silently drops a *trailing*
    # empty field (`.cargo:` produced one field, not the two a leading
    # or middle empty correctly produced) — `read` treats a trailing
    # delimiter the way it treats trailing whitespace with the default
    # IFS, absorbing it rather than producing an empty final field.
    # Split manually instead: pure string slicing, position-independent,
    # so a leading, middle, or trailing empty field all land in
    # `CACHE_PATHS` identically and the `-z "$cache_path"` check below
    # catches all three the same way.
    CACHE_PATHS=()
    _remaining="$MCP_CACHE_PATHS"
    while [[ "$_remaining" == *:* ]]; do
        CACHE_PATHS+=("${_remaining%%:*}")
        _remaining="${_remaining#*:}"
    done
    CACHE_PATHS+=("$_remaining")
    for cache_path in "${CACHE_PATHS[@]}"; do
        if [ -z "$cache_path" ]; then
            echo "entrypoint: MCP_CACHE_PATHS contains an empty entry" >&2
            exit 1
        fi
        case "$cache_path" in
            /*)
                echo "entrypoint: MCP_CACHE_PATHS entry must be \$HOME-relative, not absolute: $cache_path" >&2
                exit 1
                ;;
        esac
        IFS='/' read -ra _cache_path_parts <<< "$cache_path"
        for _part in "${_cache_path_parts[@]}"; do
            if [ "$_part" = ".." ]; then
                echo "entrypoint: MCP_CACHE_PATHS entry may not contain '..': $cache_path" >&2
                exit 1
            fi
        done
        # RR-030: `mkdir -p "$HOME_DIR/$cache_path"` then a plain
        # `chown` (no `-h`) both follow symlinks, as root, before the
        # identity drop below. A cache volume persists across runs and
        # is container-writable, so a symlink planted inside one
        # cache's own tree in an earlier run — nothing above rejects a
        # symlink, only `..` — can redirect a later, `..`-free cache
        # path outside `$HOME` entirely, silently `mkdir`/`chown`ing
        # whatever it points at instead. Walked per-component instead
        # of `mkdir -p`: an existing component that is a symlink is
        # refused outright rather than followed; a component that does
        # not exist yet is created fresh with a plain `mkdir` (not
        # `-p`), so it can never itself be a symlink. `chown -h` on the
        # final component is defense in depth on top of that, not a
        # substitute for it (`-h` alone still lets `mkdir -p` create
        # the too-deep, symlink-crossed directory it would chown).
        _current="$HOME_DIR"
        IFS='/' read -ra _cache_path_components <<< "$cache_path"
        for _component in "${_cache_path_components[@]}"; do
            [ -z "$_component" ] && continue
            _current="$_current/$_component"
            if [ -L "$_current" ]; then
                echo "entrypoint: MCP_CACHE_PATHS entry '$cache_path' passes through a symlink at '$_current'" >&2
                exit 1
            fi
            if [ ! -e "$_current" ]; then
                mkdir "$_current"
            elif [ ! -d "$_current" ]; then
                echo "entrypoint: MCP_CACHE_PATHS entry '$cache_path' passes through a non-directory at '$_current'" >&2
                exit 1
            fi
        done
        chown -h "$MCP_UID:$MCP_GID" "$_current"
    done
fi

# §19.3: container-local SSH client config — never into the bind mount,
# so a repository cannot substitute its own. StrictHostKeyChecking yes
# plus a pinned UserKnownHostsFile (mounted read-only at a fixed path by
# the daemon, never generated here) means no trust-on-first-use: an
# unrecognized or substituted host key fails the connection outright
# rather than prompting (there is nothing to prompt on a non-interactive
# exec anyway) or silently trusting it.
#
# VR-004 (docs/REVIEW_SPEC.md §4): the same symlink refusal the cache
# loop above already applies (RR-030) is needed here too — `caches[].path`
# can resolve to `.ssh` or `.` ($HOME itself; verified: both normalize to
# themselves), so a project can mount a persistent, container-writable
# cache volume over this exact path, plant `config -> <target>` in an
# unprivileged run, and have this block's own `mkdir -p`/redirect/`chmod`
# follow it in the *next* run's root phase — before the identity drop.
# `.ssh` itself is refused if it is already a symlink (mirroring the
# cache loop's own mount-point check); `config` is refused the same way
# before it is ever opened for writing, since `mkdir -p` alone would not
# have caught a symlinked *file* one level down.
if [ -L "$HOME_DIR/.ssh" ]; then
    echo "entrypoint: $HOME_DIR/.ssh is a symlink, refusing to provision SSH config through it" >&2
    exit 1
fi
mkdir -p "$HOME_DIR/.ssh"
if [ -L "$HOME_DIR/.ssh/config" ]; then
    echo "entrypoint: $HOME_DIR/.ssh/config is a symlink, refusing to write through it" >&2
    exit 1
fi
{
    echo "Host github.com"
    echo "    User git"
    echo "    IdentitiesOnly yes"
    if [ "$MCP_SSH_MODE" = "file" ]; then
        echo "    IdentityFile ~/.ssh/id_mcp"
    fi
    echo "    StrictHostKeyChecking yes"
    echo "    UserKnownHostsFile /etc/mcp/ssh/known_hosts"
} > "$HOME_DIR/.ssh/config"
chmod 700 "$HOME_DIR/.ssh"
chmod 600 "$HOME_DIR/.ssh/config"
chown -R "$MCP_UID:$MCP_GID" "$HOME_DIR/.ssh"

# HOME/USER/LOGNAME are set explicitly for the target user; everything
# else the daemon put in the environment (§14.3's overrides included)
# passes through unchanged — setpriv's --reset-env would wipe those too,
# which is why it is not used here.
export HOME="$HOME_DIR" USER="$USER_NAME" LOGNAME="$USER_NAME"
exec setpriv --reuid="$MCP_UID" --regid="$MCP_GID" --clear-groups "$@"
