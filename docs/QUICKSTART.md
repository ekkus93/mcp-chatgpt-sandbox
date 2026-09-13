# Quickstart

This is the shortest path from a fresh clone to a validated server configuration
and one repository available to an MCP client. The recommended path uses the
interactive Textual setup wizard; manual YAML remains supported as a fallback.

## 0. Prerequisites

- Linux, with Docker installed (running is recommended; the wizard can still
  configure the server while Docker is temporarily unavailable).
- Python 3.12 and [`uv`](https://docs.astral.sh/uv/).
- A local Git clone you want the server to expose.
- Push access to its GitHub repository if you want write-capable Git workflows.

## 1. Install the project

```bash
uv sync --group dev
git config core.hooksPath .githooks
```

Details: [`docs/DEVELOPMENT.md`](DEVELOPMENT.md).

## 2. Run the setup wizard

```bash
uv run dev-workspace-mcp setup
```

The default target is:

```text
~/.config/dev-workspace-mcp/config.yml
```

Use another location with `--config /absolute/path/config.yml`.

The wizard guides these areas in order:

1. Python/Docker prerequisites.
2. State DB, audit, SSH-key, and pinned `known_hosts` paths.
3. Docker image/workspace settings, including an explicit image-build action.
4. Repository registration and read-only Git remote verification.
5. SSH isolation and optional GitHub API credential-file references.
6. Git safety policy presets or a complete custom policy.
7. Optional advanced filesystem/execution/resource/logging limits.
8. Optional OpenAI Secure MCP Tunnel / ChatGPT guidance.
9. Exact configuration review and explicit write authorization.
10. Final machine-verifiable diagnostics.

The wizard never stores raw private-key/API/tunnel credential material in its
resumable state. Existing valid configs are edited by preserving settings that
were not reviewed, and replacements receive timestamped backups.

Useful setup modes:

```bash
uv run dev-workspace-mcp setup --resume
uv run dev-workspace-mcp setup --no-browser
uv run dev-workspace-mcp setup --advanced
```

## 3. Install a repository deploy key when push access is needed

The wizard registers/verifies repository metadata, but it does not invent or
silently install a GitHub deploy key. Generate a dedicated key under the
`ssh.keys_dir` you selected:

```bash
ssh-keygen -t ed25519 -f ~/.config/dev-workspace-mcp/keys/my-repo.ed25519 \
  -C "dev-workspace-mcp: my-repo" -N ""
chmod 600 ~/.config/dev-workspace-mcp/keys/my-repo.ed25519
```

On GitHub, open the repository's **Settings → Deploy keys → Add deploy key**,
paste the `.pub` file, and enable write access if this MCP server should push.
Return to the wizard, set the repository's SSH-key filename, and rerun the SSH
validation step.

The wizard's reviewed storage action creates/populates an absent or empty
`known_hosts` file with the production-pinned GitHub host-key set; it does not
use trust-on-first-use.

Full repository credential walkthrough:
[`docs/CONNECTING_A_REPOSITORY.md`](CONNECTING_A_REPOSITORY.md).

## 4. Run final diagnostics headlessly

The final wizard screen and these commands use the same diagnostics engine:

```bash
uv run dev-workspace-mcp doctor \
  --config ~/.config/dev-workspace-mcp/config.yml

# equivalent
uv run dev-workspace-mcp setup --check \
  --config ~/.config/dev-workspace-mcp/config.yml
```

Exit codes are:

- `0` — all mandatory machine checks pass;
- `1` — mandatory remediation/action remains;
- `2` — a mandatory hard failure occurred.

Optional ChatGPT setup may be skipped without making an otherwise valid local
server fail diagnostics.

## 5. Run the server

```bash
uv run dev-workspace-mcp serve \
  --config ~/.config/dev-workspace-mcp/config.yml
```

This starts MCP over `stdio`, suitable for a local MCP client or for OpenAI
Secure MCP Tunnel.

## 6. Confirm a repository workspace

From an MCP client, call:

```json
{ "repository_id": "my-repo", "mode": "persistent" }
```

Expect a running container state. For a repository with a write-capable deploy
key, a real structured `git_push` on a disposable/test branch is the strongest
end-to-end proof of the container, mount, remote validation, and credential
path.

## 7. Optional: connect ChatGPT

The wizard's ChatGPT step is optional and deliberately keeps OpenAI plan/UI
eligibility out of local configuration. It can guide creation/repair of the
`tunnel-client` profile and machine-check that profile; ChatGPT UI completion is
recorded separately as **USER-ATTESTED** because the local wizard cannot inspect
that hosted UI independently.

See [`docs/CONNECTING_CHATGPT.md`](CONNECTING_CHATGPT.md) for the complete flow
and current OpenAI documentation links.

## Manual configuration fallback

Manual YAML is still fully supported. It is useful for automated provisioning,
recovery, or operators who deliberately prefer config-as-code. See
[`docs/CONFIGURATION.md`](CONFIGURATION.md) for field semantics and
[`docs/SPEC.md`](SPEC.md) §52 for the worked configuration example.

A minimal manual config still needs `state_db_path`, `docker.default_image`,
`ssh.keys_dir`, `ssh.known_hosts`, and `audit.path`; repositories may be added
with either explicit `repositories:` records or `repository_roots:` discovery.

---

`docs/SPEC.md` remains the authoritative server specification;
[`docs/SETUP_WIZARD_SPEC.md`](SETUP_WIZARD_SPEC.md) is the authoritative safety
and UX contract for the interactive setup feature.
