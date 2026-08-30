# Quickstart

The condensed, copy-pasteable path from a fresh clone to one of your own
repositories open in a workspace. Each step links to the doc with the
actual reasoning — read those before you diverge from the commands
below in a real deployment; this page skips the "why" on purpose.

## 0. Prerequisites

- Linux, with Docker installed and running.
- Python 3.12 and [`uv`](https://docs.astral.sh/uv/).
- A GitHub repository you have push access to, cloned locally.

## 1. Install and build

```bash
uv sync --group dev
git config core.hooksPath .githooks

docker build -t mcp-dev-ubuntu:24.04 docker/default/
```

Details: [`docs/DEVELOPMENT.md`](DEVELOPMENT.md).

## 2. Write a config file

Every field below is required — there's no default for any of them
(`docs/CONFIGURATION.md`). Create the directories first; nothing here
creates them for you:

```bash
sudo mkdir -p /etc/dev-workspace-mcp/keys /var/lib/dev-workspace-mcp /var/log/dev-workspace-mcp
sudo chown "$USER" /etc/dev-workspace-mcp/keys /var/lib/dev-workspace-mcp /var/log/dev-workspace-mcp
```

```yaml
# config.yml
version: 1
state_db_path: /var/lib/dev-workspace-mcp/state.db

docker:
  default_image: mcp-dev-ubuntu:24.04   # matches the tag built in step 1

ssh:
  keys_dir: /etc/dev-workspace-mcp/keys
  known_hosts: /etc/dev-workspace-mcp/github_known_hosts

audit:
  path: /var/log/dev-workspace-mcp/audit.jsonl

repositories: []   # step 3 adds an entry here
```

Details, including every optional block: [`docs/CONFIGURATION.md`](CONFIGURATION.md).

## 3. Connect your repository

```bash
# 3a. Pin GitHub's SSH host key (once per server, not per repo)
echo 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' \
  >> /etc/dev-workspace-mcp/github_known_hosts

# 3b. Generate a dedicated deploy key for this repository
ssh-keygen -t ed25519 -f /etc/dev-workspace-mcp/keys/my-repo.ed25519 \
  -C "dev-workspace-mcp: my-repo" -N ""
chmod 600 /etc/dev-workspace-mcp/keys/my-repo.ed25519
```

Then, on GitHub: repository → **Settings** → **Deploy keys** → **Add
deploy key** → paste `my-repo.ed25519.pub` → check **Allow write
access** → **Add key**.

Add the repository to `config.yml`:

```yaml
repositories:
  - id: my-repo
    path: /home/you/work/my-repo   # absolute path to your local clone
    github: your-org/my-repo
    ssh_key: my-repo.ed25519
```

Full walkthrough and the reasoning behind each part:
[`docs/CONNECTING_A_REPOSITORY.md`](CONNECTING_A_REPOSITORY.md).

## 4. Run the server

```bash
uv run dev-workspace-mcp serve --config config.yml
```

Validates the whole config before serving — a bad path or typo fails
here with `error: ...`, not later. This runs on `stdio`, talking to
whatever spawns it (a local MCP client, or `tunnel-client` for ChatGPT).

## 5. Confirm it

From an MCP client, call `workspace_open`:

```json
{ "repository_id": "my-repo", "mode": "persistent" }
```

Expect `container_state: "running"` and `ssh_key_mode: "agent"` /
`ssh_key_scope: "repository"` in the response. A real `git_push` from
inside the workspace is the actual end-to-end proof.

## 6. Optional: connect ChatGPT

ChatGPT can't spawn a local `stdio` process itself, so it needs OpenAI's
Secure MCP Tunnel in front of the command from step 4. Full setup:
[`docs/CONNECTING_CHATGPT.md`](CONNECTING_CHATGPT.md).

---

This page exists for the fast path; `docs/SPEC.md` remains the
authoritative specification, and the docs linked above cover what each
step is actually for and what else is configurable.
