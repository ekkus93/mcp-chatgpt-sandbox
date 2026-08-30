# Configuration

`docs/SPEC.md` is the only configuration reference this project
maintains: §52 gives the full example file, and each block is documented
alongside the feature it configures — §7 (repositories), §9 (images),
§11 (caches), §19 (SSH keys), §18.9 (Git policy), §26 (resources), §53
(secrets). The Pydantic models in
`src/dev_workspace_mcp/config/models.py` are the actual enforced schema;
`docs/SPEC.md` §52's example is kept consistent with them by hand
(`load_config`, `src/dev_workspace_mcp/config/loader.py`, fails closed on
anything the models reject). Every model sets `extra: forbid` — an
unrecognized key anywhere in the file is a validation error, not a
silently ignored typo.

## What's actually required

Config is YAML. Everything has a sensible default except five fields,
which is why there's no bundled example to copy — a real one would need
values only you can supply (a state directory, an image tag, key
locations):

| Field | Why it has no default |
| --- | --- |
| `state_db_path` | Where operational state (workspace/job/audit-index records, §68) is written is an operator decision, not a convention to assume. |
| `docker.default_image` | The image workspaces build from — there's no safe generic default to fall back to. |
| `ssh.keys_dir` | Where per-repository deploy keys live on the host (§19.1). |
| `ssh.known_hosts` | The pinned host-key file SSH connections from containers verify against (§19.3). |
| `audit.path` | Where the audit log is written (§24). |

`repositories`/`repository_roots` also default to empty, which is worth
calling out even though it's not a hard requirement: a config with
neither registers no repositories at all, so `workspace_open` has
nothing to open.

## Block-by-block summary

Each block below governs one area; see the cited `docs/SPEC.md` section
for the reasoning, not just the field list.

- **`repositories`** (§7) — explicitly registers a repository: a stable
  `id` the client uses instead of a raw path, the local `path` (must be
  absolute), a `github: owner/repo` identity used to validate remotes,
  and an optional `ssh_key` filename (relative to `ssh.keys_dir`; not
  used under `ssh.scope: shared`). For the end-to-end steps — generating
  that key, installing it on GitHub, adding the entry — see
  [`docs/CONNECTING_A_REPOSITORY.md`](CONNECTING_A_REPOSITORY.md).
- **`repository_roots`** (§7.1) — directories the daemon may
  auto-discover repositories under, as an alternative to registering
  each one individually. Entries must be absolute; discovered paths must
  resolve (after symlinks) to stay under an authorized root.
- **`docker`** (§9) — `default_image` names the image workspace
  containers build from (subject to a precedence order: an explicit
  per-call image beats a repo's own `.devcontainer.json` or Dockerfile
  beats this default); `workspace_path` (default `/workspace`) is where
  the repository is mounted inside the container.
- **`workspace`** (§8.1) — `idle_stop_after` (a duration string like
  `4h`, or `never`) controls when an idle *persistent* container is
  stopped (not deleted) to free resources; default 4 hours.
- **`container`** (§5.3, §18.2) — `user` (`host`, default, or `root`)
  controls the in-container process identity; `root` without Docker
  user-namespace remapping is refused rather than silently allowed.
  `git_dir_mode` (`read-write` default, `read-only`, or `none`) controls
  how much of `.git` the container can see, trading workflow
  completeness for isolation from host-executed Git hooks.
- **`ssh`** (§19) — the credential model's core: `mode` (`agent`,
  default — key material never enters the container; or `file` — the
  key is bind-mounted, simpler but extractable by container code) and
  `scope` (`repository`, default — one key per repo; or `shared` — one
  key for every configured repo, which trades away per-repository key
  scoping for simplicity). `keys_dir` and `known_hosts` are required, as
  noted above.
- **`github`** (§19.5, §19.6) — an optional, strictly **read-only** API
  credential (`api_auth: none` default) used to check branch-protection
  status and populate repository metadata; it is never mounted into a
  container and cannot push.
- **`filesystem`** (§12.2) — byte/count ceilings for the filesystem
  tools: read/write/patch size limits, and listing/search result caps,
  each independently configurable with generous defaults (10 MiB
  reads/writes, 1 MiB patches, 1000 listing entries, 500 search
  results).
- **`execution`** (§14.4, §15.5, §15.6) — timeouts (`sync_timeout_seconds`,
  `default_job_timeout_seconds`) and output handling for `exec`/jobs:
  how much output is captured to disk (`max_output_bytes`,
  `tail_keep_bytes`) versus how much is returned directly in an MCP
  response (`default_output_response_bytes`, deliberately much smaller,
  so a noisy command doesn't consume the client's context) — plus job
  log retention (`job_retention_days`, `job_log_max_total_bytes`).
- **`git_policy`** (§18.9) — governs only the *structured* Git tools
  (`git_commit`/`git_push`/`git_pull`); `exec` is a real shell and isn't
  bound by it. Covers force-push, force-with-lease, remote branch/tag
  deletion, amending published commits, default-branch push/commit, pull
  strategy, and whether a missing remote branch-protection ruleset
  blocks a write (`require_remote_protection`). Every key here maps to a
  parameter a structured tool actually accepts — operations with no
  structured tool (hard reset, `clean -fdx`, rebase, changing a remote
  URL) deliberately have no policy key; they're reachable only through
  `exec`, bounded instead by the container and the remote.
- **`resources`** (§26) — optional Docker `cpus`/`memory`/`pids_limit`
  ceilings on workspace containers; unset (the default for each) means
  no limit, matching Docker's own default rather than an opinionated
  daemon ceiling.
- **`audit`** (§24) — `enabled` (default `true`) and the required `path`
  for the structured, append-only audit log.
- **`logging`** — the standard-library `logging` level (`INFO` default);
  separate from the structured audit log above, and not itself in
  `docs/SPEC.md` §52's worked example (it predates that example; see
  `docs/HARDENING_TODO.md` HR-031).

## Secrets

Per §53: secrets belong in protected files outside repository roots,
environment variables, OS secret stores, or restricted-permission
GitHub App credential files — never committed to Git as project
configuration. Consistent with that, no field in the schema holds a raw
secret value directly; `ssh.keys_dir`/`ssh_key`/`shared_key` and
`github.api_token_path` are all path references to files that live
outside repository roots and are never mounted into a container.

This file exists so a reader who lands on it directly is pointed
somewhere real, not left with a 404 — it is not a second copy of the
reference kept in parallel with `docs/SPEC.md`. For the full field list,
types, and validation rules, read the models directly:
`src/dev_workspace_mcp/config/models.py`.
