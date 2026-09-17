# Configuration

The production configuration is YAML and is enforced by the Pydantic models in
`src/mcp_chatgpt_sandbox/config/models.py`. `docs/SPEC.md` remains the complete
server configuration reference: §52 contains the worked example and the feature
sections explain the semantics behind each block.

For normal first-time setup and interactive edits, use the wizard:

```bash
uv run mcp-chatgpt-sandbox setup
# or choose an explicit target
uv run mcp-chatgpt-sandbox setup --config /absolute/path/config.yml
```

Manual YAML authoring is still fully supported for automation, recovery, and
operators who deliberately prefer config-as-code.

## Wizard ownership and preservation

The setup wizard does **not** maintain a second configuration schema.
`ServerConfig`, its nested production models, and the final production
`load_config()` reload remain authoritative.

The wizard classifies every current top-level `ServerConfig` field so adding a
new production field forces an explicit setup-support decision. It may guide or
edit:

- `version` when creating a new config;
- `state_db_path`;
- `repository_roots` and `repositories`;
- `docker`, `workspace`, and `container`;
- `ssh` and `github`;
- `git_policy`;
- `resources`, `audit`, and `logging`.

`filesystem` and `execution` are advanced controls and stay untouched in the
normal path unless the operator explicitly opens/reviews advanced runtime
settings. More generally, a field being wizard-supported does not mean opening
the wizard overwrites it: only fields actually reviewed in the current session
are included in the candidate update.

For existing production-valid configs, untouched top-level fields are preserved.
When the wizard updates a mapping such as `github:` or `audit:`, reviewed
subfields are deep-merged so valid unedited keys are preserved as well.

Before a config write, the wizard:

1. safely reads and parses the current source, rejecting duplicate YAML keys;
2. validates it with production semantics;
3. materializes only reviewed values;
4. validates and serializes the exact candidate;
5. shows a structured review summary;
6. binds that review to the digest/existence of the source file;
7. acquires the bounded sibling setup-writer lock and rechecks source identity while the lock is held;
8. refuses if the source changed after review or another setup writer cannot be serialized safely;
9. creates a timestamped backup when replacing a file;
10. writes atomically with restrictive permissions;
11. byte-verifies the persisted candidate;
12. reloads it through production `load_config()` before reporting success.

The serialization lock is an empty owner-only sibling named
`.CONFIG_NAME.setup.lock` (for example, `.config.yml.setup.lock`). It contains no
configuration or credential material and remains on disk between runs. Kernel
advisory-lock ownership is tied to the open descriptor, so process exit/crash
releases ownership without a stale-PID or stale-age recovery protocol. Lock
acquisition is bounded; failure is actionable and does not modify the reviewed
config.

Raw private keys, GitHub tokens, passwords, and tunnel runtime credentials are
not fields in resumable setup state. Credential **paths** and non-secret tunnel
identity may be persisted; secret contents are not.

## What's actually required

Everything has a sensible default except five operator-supplied fields:

| Field | Why it has no default |
| --- | --- |
| `state_db_path` | Where operational state (workspace/job/audit-index records, §68) is written is an operator decision. |
| `docker.default_image` | The image workspaces build from. |
| `ssh.keys_dir` | Where repository deploy keys live on the host (§19.1). |
| `ssh.known_hosts` | The pinned host-key file SSH connections from containers verify against (§19.3). |
| `audit.path` | Where the structured audit log is written (§24). |

`repositories`/`repository_roots` default to empty. A config with neither is
valid but exposes no repository to `workspace_open`.

The wizard supplies documented operator defaults for host paths but does not
turn those defaults into new `ServerConfig` defaults. Its explicit storage
creation action can create reviewed parent directories and, when the selected
`known_hosts` file is absent or empty, populate it with the same production-
pinned GitHub host-key set used by the loader. It does not use trust-on-first-use.

Setup directory creation uses **leaf-private** semantics. When a selected
setup-owned directory is missing, that selected leaf is explicitly secured to
`0700`. Existing arbitrary ancestors are never chmodded as a side effect of a
setup write. If `parents=True` must create intermediate ancestors, those
intermediate components follow normal filesystem/umask semantics and are outside
the private-leaf guarantee. Failure to secure a leaf that setup promises to keep
private is a hard setup error.

## Block-by-block summary

- **`repositories`** (§7) — explicit repository registrations: stable `id`,
  absolute local `path`, canonical `github: owner/repo`, and optional `ssh_key`
  filename relative to `ssh.keys_dir`. The wizard provides add/edit/remove and
  read-only local Git/remote verification.
- **`repository_roots`** (§7.1) — absolute directories under which repositories
  may be auto-discovered. Discovery does not replace remote-identity or SSH
  policy.
- **`docker`** (§9) — `default_image` and `workspace_path` (`/workspace` by
  default). The wizard resolves the packaged/repository-bundled default image
  build context independently of the caller's current working directory, shows a
  shell-safe preview of the exact build argv, and builds only after explicit
  approval. If that bundled context cannot be resolved or validated, the Docker
  step remains **ACTION REQUIRED** and no Docker build/client mutation is
  attempted.
- **`workspace`** (§8.1) — `idle_stop_after`, e.g. `4h` or `never`, controlling
  idle stop for persistent workspaces.
- **`container`** (§5.3, §18.2) — `user` (`host` default or `root`), `sudo`, and
  `git_dir_mode` (`read-write`, `read-only`, or `none`). Root operation remains
  subject to the production user-namespace safety checks.
- **`ssh`** (§19) — `mode` (`agent` recommended/default or `file`) and `scope`
  (`repository` recommended/default or `shared`), plus required `keys_dir` and
  `known_hosts`. The wizard explains the isolation/exposure consequences before
  validation.
- **`github`** (§19.5, §19.6) — optional read-only API integration for branch-
  protection metadata. `api_auth: token` stores only an absolute token-file
  path in config. Current runtime verification does not implement GitHub App
  auth, and the wizard reports that state as unverified rather than success.
- **`filesystem`** (§12.2) — byte/count ceilings for read/write/patch and
  listing/search tools. It is an advanced wizard section.
- **`execution`** (§14.4, §15.5, §15.6) — command/job timeouts, captured output,
  MCP response limits, and job retention. It is an advanced wizard section.
- **`git_policy`** (§18.9) — structured Git-tool policy. The wizard offers a
  Recommended preset equal to current production defaults, a stricter
  Conservative preset, and Custom mode that covers every current production
  `GitPolicyConfig` field. Presets materialize to the production model; they are
  not a parallel runtime policy layer. If resumable Custom state is no longer
  compatible with the current production schema, activation remains interactive,
  the Git-policy step becomes action-required, the stored payload is not silently
  rewritten to defaults, and the operator can recover with a preset or validated
  Custom values.
- **`resources`** (§26) — optional Docker CPU/memory/PID limits. Unset means no
  daemon-imposed limit.
- **`audit`** (§24) — `enabled` (default `true`) and required output `path`.
- **`logging`** — standard-library logging level (`INFO` default), separate from
  the structured audit log.

## Per-repository `.mcp-workspace.yml`

A repository may commit `.mcp-workspace.yml` (or `.mcp-workspace.yaml`) for the
project-level settings defined by `docs/SPEC.md` §10. The setup wizard does not
modify this file.

| Field | Current status | Runtime consumer / required action |
| --- | --- | --- |
| `version` | **Supported** | Validated by `load_project_config`; recorded in reproducibility metadata. |
| `image` | **Supported** | `resolve_effective_image` and CLI reset/rebuild choose it ahead of `docker.default_image`. |
| `setup` | **Supported** | `run_setup_steps` runs commands after genuine container creation/reset/rebuild. |
| `environment` | **Supported** | Added only after project-environment security validation. |
| `caches` | **Supported** | `resolve_caches` / `build_cache_mounts` create repository-scoped cache mounts. |
| `workspace` | **Rejected** | No project-level workspace-path override is implemented. |
| `github` | **Rejected** | Configure daemon `repositories[].github` instead. |
| `validation` | **Rejected** | No lifecycle hook consumes these fields; they are rejected rather than ignored. |

Rejected fields fail project-config loading explicitly, including an explicitly
written `null`. `SUPPORTED_PROJECT_CONFIG_FIELDS` and
`UNSUPPORTED_PROJECT_CONFIG_FIELDS` keep the inventory exhaustive.

## Secrets

Per §53, secrets belong in protected files outside repository roots,
environment variables, OS secret stores, or other restricted external stores —
never committed to project configuration. Config fields such as `ssh_key`,
`shared_key`, and `github.api_token_path` are references, not raw secret values.

The setup state follows the same rule. Resume data may contain selected paths,
repository identities, policy choices, and tunnel IDs/profile names, but it
rejects secret-shaped state fields and deliberately has no raw token/private-key
field.

## Manual validation and diagnostics

After either wizard or manual edits:

```bash
uv run mcp-chatgpt-sandbox doctor --config /path/to/config.yml
```

or:

```bash
uv run mcp-chatgpt-sandbox setup --check --config /path/to/config.yml
```

Both call the same diagnostic engine used by the wizard's final screen.

For the full field list, types, and validation rules, read
`src/mcp_chatgpt_sandbox/config/models.py` and `docs/SPEC.md`.
