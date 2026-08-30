# Connecting a repository

This walks through registering one of your own local Git repositories
with a running server, so an MCP client can open a workspace for it —
the piece [`docs/CONFIGURATION.md`](CONFIGURATION.md) documents as a
field reference (`repositories:`, `ssh:`) but doesn't walk through
end-to-end. For connecting a *client* (ChatGPT) to the server itself,
see [`docs/CONNECTING_CHATGPT.md`](CONNECTING_CHATGPT.md) instead — that's
a separate, unrelated step from this one.

## Prerequisites

- The server installed and a config file that already validates (see
  [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)) — `ssh.keys_dir`,
  `ssh.known_hosts`, and `audit.path` must already be set to real,
  writable paths, since the steps below write into two of them.
- A local clone of the repository, under a path the daemon can read —
  typically one of your configured `repository_roots`, or just the path
  you're about to add explicitly.
- Push access to the GitHub repository yourself, to install a deploy
  key on it.

Everything here follows `docs/SPEC.md` §7 (Repository Model) and §19
(Credential Model); this page is the practical sequence, not a
restatement of the reasoning behind it.

## 1. Pin GitHub's SSH host keys

Per §19.3, the daemon "SHALL NOT rely on trust-on-first-use" for the
container's SSH connections to GitHub — `ssh.known_hosts` has to already
contain GitHub's real host key, not one accepted on faith the first time
a container connects. Use GitHub's own published key rather than
`ssh-keyscan`, which fetches whatever a network path hands back:

```bash
echo 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' \
  >> /etc/dev-workspace-mcp/github_known_hosts   # match your ssh.known_hosts path
```

That's GitHub's current Ed25519 host key (SHA256 fingerprint
`+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`), from
[GitHub's SSH key fingerprints page](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
— reconfirm against that page directly rather than trusting a copy in
this file indefinitely; GitHub has rotated host keys before. This step
is **shared across every repository** — do it once per server, not once
per repository.

## 2. Generate a dedicated deploy key for this repository

Per §19.1, the key SHALL be dedicated to this one repository and never
your own personal key. Generate a fresh key pair directly into
`ssh.keys_dir`:

```bash
ssh-keygen -t ed25519 -f /etc/dev-workspace-mcp/keys/my-repo.ed25519 \
  -C "dev-workspace-mcp: my-repo" -N ""
chmod 600 /etc/dev-workspace-mcp/keys/my-repo.ed25519
```

`-N ""` gives it no passphrase — required, since the daemon reads it
non-interactively. The daemon refuses to provision any key that resolves
under *your own* `~/.ssh` (§19.1), so it must live under `ssh.keys_dir`,
not there.

## 3. Install the public key on GitHub as a deploy key

Deploy keys are inherently per-repository — one key can't be reused
across repositories — which is what gives `scope: repository` its
structural guarantee (§19.1): a compromised workspace for this repo
still can't push anywhere else.

1. On the repository's GitHub page: **Settings** → **Deploy keys** →
   **Add deploy key**.
2. Title it something identifying (e.g. `dev-workspace-mcp: my-repo`).
3. Paste the contents of `my-repo.ed25519.pub` (the `.pub` file, never
   the private key) into **Key**.
4. Check **Allow write access** — without it, `git_push` and any push
   from `exec` will authenticate but be rejected by GitHub.
5. **Add key**.

## 4. Register the repository in the config file

Add an entry under `repositories:`, with `ssh_key` naming the file from
step 2 relative to `ssh.keys_dir`:

```yaml
repositories:
  - id: my-repo
    path: /home/phil/work/my-repo
    github: your-org/my-repo
    ssh_key: my-repo.ed25519
```

- `id` is what an MCP client passes to `workspace_open` — pick something
  stable and readable, not the raw path.
- `path` must be an absolute path to the local clone.
- `github` must match the repository's `owner/repo` — the daemon
  normalizes `https://github.com/...`, `git@github.com:...`, and
  `ssh://git@github.com/...` remote URLs to this form and validates
  against it before any remote write (§7.3).

If you'd rather not register repositories individually, `repository_roots`
auto-discovers anything under a configured root instead — see
[`docs/CONFIGURATION.md`](CONFIGURATION.md#block-by-block-summary); the
deploy-key steps above are the same either way, since discovery only
replaces the `repositories:` entry, not the SSH setup.

## 5. Restart the server

Nothing here is hot-reloaded — the config is only read at startup:

```bash
uv run dev-workspace-mcp serve --config <path>
```

`--config` re-validates the whole file, so a typo'd `github:` value or a
`ssh_key` filename that doesn't exist under `keys_dir` fails here with a
clear `error: ...`, not later inside a workspace.

## 6. Confirm it

From an MCP client (or by hand, if you're testing), call
`workspace_open` with the `id` from step 4:

```json
{ "repository_id": "my-repo", "mode": "persistent" }
```

A working setup returns `container_state: "running"` and, per §19.7,
`ssh_key_mode: "agent"` / `ssh_key_scope: "repository"` confirming the
key just installed is the one in use — not `file` mode or `shared`
scope, unless you deliberately configured those (see
[`docs/SECURITY.md`](SECURITY.md) for that trade-off). `remote_protection.status`
reports whether GitHub branch protection was verified on the default
branch, if `github.api_auth` is configured.

From inside the workspace, a real push (`git_push`, or `git push` under
`exec`) is the actual end-to-end test — it exercises the deploy key
just installed, not just the daemon's own bookkeeping.

This file exists so a reader wanting to add their own repository is
pointed somewhere real, not left piecing it together from
`docs/SPEC.md` §7 and §19 by hand — it is not a second copy of either;
for the full reasoning behind any step above, those sections are
authoritative.
