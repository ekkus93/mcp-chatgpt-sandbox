# Configuration

The production configuration is YAML and is enforced by the Pydantic models in
`src/dev_workspace_mcp/config/models.py`. `docs/SPEC.md` remains the complete
server configuration reference: §52 contains the worked example and the feature
sections explain the semantics behind each block.

For normal first-time setup and interactive edits, use the wizard:

```bash
uv run dev-workspace-mcp setup
# or choose an explicit target
uv run dev-workspace-mcp setup --config /absolute/path/config.yml
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
7. refuses if the source changes before commit;
8. creates a timestamped backup when replacing a file;
9. writes atomically with restrictive permissions;
10. byte-verifies the persisted candidate;
11. reloads it through production `load_config()` before reporting success.

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

## Block-by-block summary

- **`repositories`** (§7) — explicit repository registrations: stable `id`,
  absolute local `path`, canonical `github: owner/repo`, and optional `ssh_key`
  filename relative to `ssh.keys_dir`. The wizard provides add/edit/remove and
  read-only local Git/remote verification.
- **`repository_roots`** (§7.1) — absolute directories under which repositories
  may be auto-discovered. Discovery does not replace remote-identity or SSH
  policy.
- **`docker`** (§9) — `default_image` and `workspace_path` (`/workspace` by
  default). The wizard can inspect the selected image and explicitly run the
  project's reviewed default-image build action.
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
  not a parallel runtime policy layer.
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
uv run dev-workspace-mcp doctor --config /path/to/config.yml
```

or:

```bash
uv run dev-workspace-mcp setup --check --config /path/to/config.yml
```

Both call the same diagnostic engine used by the wizard's final screen.

For the full field list, types, and validation rules, read
`src/dev_workspace_mcp/config/models.py` and `docs/SPEC.md`.
