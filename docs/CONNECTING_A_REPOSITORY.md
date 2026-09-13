# Connecting a repository

The recommended way to register a local Git repository is the interactive setup
wizard's **Repository access** step. It replaces hand-editing the `repositories:`
YAML list with guided add/edit/remove and read-only Git/remote verification.

The deploy key itself is still an explicit external credential: the wizard will
never silently generate/install a GitHub credential or persist private-key
contents in setup state.

For connecting ChatGPT to the server rather than connecting a repository to the
server, see [`docs/CONNECTING_CHATGPT.md`](CONNECTING_CHATGPT.md).

## Prerequisites

- Install dependencies and start the wizard with `uv run dev-workspace-mcp setup`.
- A local Git clone at an absolute path readable by the daemon.
- Push access to the GitHub repository if you want to install a write-capable
  deploy key.

## 1. Configure storage and pinned host keys

In the wizard's **Server storage** step choose:

- the SSH keys directory;
- the pinned `known_hosts` file.

The validation action is read-only. The separate reviewed creation action may
create missing directories and, if the selected `known_hosts` file is absent or
empty, populate it with the production-pinned GitHub host-key set. This uses the
same key material as the production loader and does not use trust-on-first-use.

If you configure manually instead, verify GitHub's currently published SSH
fingerprints at GitHub's official documentation before creating your pinned file.
The production source-of-truth copy used by this project is
`GITHUB_SSH_HOST_KEYS` in `src/dev_workspace_mcp/config/loader.py`.

## 2. Generate a dedicated deploy key

For the recommended repository-scoped SSH mode, generate one key pair per
repository directly under the configured `ssh.keys_dir`:

```bash
ssh-keygen -t ed25519 \
  -f ~/.config/dev-workspace-mcp/keys/my-repo.ed25519 \
  -C "dev-workspace-mcp: my-repo" -N ""
chmod 600 ~/.config/dev-workspace-mcp/keys/my-repo.ed25519
```

Do not use your personal key. The daemon refuses key material resolving under
your own `~/.ssh` path.

## 3. Install the public key on GitHub

On the GitHub repository:

1. Open **Settings → Deploy keys → Add deploy key**.
2. Give it a descriptive title such as `dev-workspace-mcp: my-repo`.
3. Paste the `.pub` file contents — never the private key.
4. Enable **Allow write access** if this MCP server should push.
5. Add the key.

Deploy keys are repository-specific, which is why the recommended
`ssh.scope: repository` choice provides stronger isolation than a shared key.

## 4. Register the repository in the wizard

In **Repository access**, enter:

- a stable repository ID;
- the absolute local path;
- canonical GitHub `owner/repo`;
- optional repository SSH-key filename, e.g. `my-repo.ed25519`.

Use **Verify entered repository read-only** before saving. Verification does not
modify repository content; it checks that the path is a Git working tree and
normalizes the local `origin` URL using the same production GitHub remote rules.
A mismatch between the configured `owner/repo` and local origin is explicit.

The manager rejects duplicate IDs and duplicate local paths. Removing a record
requires an explicit confirmation checkbox.

If you prefer discovery, the same screen can configure absolute
`repository_roots`; the UI explains that roots are discovery authorization, not
a replacement for SSH or remote-identity checks.

### Manual YAML fallback

The equivalent manual record is:

```yaml
repositories:
  - id: my-repo
    path: /home/you/work/my-repo
    github: your-org/my-repo
    ssh_key: my-repo.ed25519
```

`id` is what clients pass to `workspace_open`. `github` must match the normalized
GitHub remote identity.

## 5. Validate SSH/GitHub choices

The next wizard step recommends:

- `ssh.mode: agent` — private key bytes do not enter the workspace container;
- `ssh.scope: repository` — each repository uses its own key.

Shared scope and file mode remain supported but the wizard displays their
isolation/exposure trade-offs before allowing the choice.

The validation checks referenced key files and the pinned `known_hosts` file
without printing their contents. Optional GitHub API verification remains
explicitly `unverified` when credentials/capability are unavailable; uncertainty
is never turned into a PASS.

## 6. Review, write, and diagnose

The wizard's **Configuration review** step shows repository additions/removals or
changes before the config write. Existing configs receive a timestamped backup,
and source changes after review force a fresh review instead of being overwritten.

After writing, use final diagnostics or run them later with:

```bash
uv run dev-workspace-mcp doctor \
  --config ~/.config/dev-workspace-mcp/config.yml
```

Repository path, remote identity, SSH references, pinned hosts, Docker, and
server construction are all included in the final matrix.

## 7. Start and confirm

```bash
uv run dev-workspace-mcp serve \
  --config ~/.config/dev-workspace-mcp/config.yml
```

Then call:

```json
{ "repository_id": "my-repo", "mode": "persistent" }
```

A working setup returns a running container. For a write-enabled deploy key, a
real structured `git_push` on a disposable/test branch is the strongest
end-to-end credential and remote-validation proof.

`docs/SPEC.md` §7 and §19 remain authoritative for the repository and credential
models; this page is the practical wizard/manual sequence.
