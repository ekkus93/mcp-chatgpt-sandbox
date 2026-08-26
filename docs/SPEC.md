# Development Workspace MCP Server — Specification

**Status:** Draft  
**Version:** 0.1  
**Date:** 2026-08-26

## 1. Purpose

This document specifies a private Model Context Protocol (MCP) server that provides ChatGPT and other MCP-capable clients with a substantially more capable software-development environment than a generic restricted sandbox.

The server provides controlled access to local source repositories through project-specific Docker development containers. An MCP client can inspect and modify repository files, run arbitrary development commands, install toolchains and packages, execute tests and linters, perform Git operations, and synchronize authorized changes with GitHub.

The system is intended to support real software-development workflows across heterogeneous technology stacks such as Rust, Python, Node.js/TypeScript, Java, Android, Go, C/C++, and mixed-language repositories.

The core design goal is:

> Give an AI coding client the practical capabilities of a normal development workstation while preserving explicit isolation, repository scoping, credential boundaries, auditability, and control over destructive operations.

The server is language-agnostic. It does not attempt to encode special knowledge of individual compilers, package managers, linters, or test frameworks. Instead, it provides reliable workspace, filesystem, process, Git, and GitHub primitives that allow the client to operate the project's native tools.

---

## 2. Goals

The system SHALL:

1. Allow an MCP client to select an authorized local repository.
2. Create or reuse a Docker development container associated with that repository.
3. Mount the local repository read/write into the development container.
4. Allow the client to read, create, modify, patch, rename, and delete files within the repository.
5. Allow the client to run arbitrary shell commands inside the development container.
6. Allow software, packages, compilers, libraries, linters, SDKs, and other development tools to be installed inside the container.
7. Support persistent development containers so installed tooling survives across MCP sessions.
8. Support clean ephemeral containers for reproducibility and CI-like validation.
9. Support long-running commands through asynchronous jobs that can be polled, inspected, and canceled.
10. Support structured Git operations including status, diff, history, staging, commits, branches, fetch, pull, push, tags, and remote inspection.
11. Permit authorized synchronization with GitHub.
12. Protect GitHub credentials from arbitrary project code where practical.
13. Restrict host filesystem exposure to configured repository roots and explicitly configured cache paths.
14. Prevent project containers from gaining Docker-host control by default.
15. Expose enough diagnostic state for the client to understand the current repository, branch, container, jobs, Git state, and environment.
16. Preserve repository changes directly in the host checkout so they remain immediately visible to local editors and other development tools.
17. Coexist safely with human developers and other AI coding systems such as Claude Code.
18. Maintain auditable records of meaningful write, execution, and Git operations.
19. Fail closed for unauthorized repositories, paths, remotes, destructive Git operations, and invalid workspace state.
20. Remain independent from any specific AI client.

---

## 3. Non-Goals

The initial system SHALL NOT attempt to:

1. Replace GitHub Actions or other CI/CD systems.
2. Implement a full cloud IDE.
3. Provide graphical desktop applications inside containers.
4. Provide unrestricted access to the entire host filesystem.
5. Give development containers direct access to the Docker daemon by default.
6. Automatically grant access to arbitrary GitHub repositories.
7. Automatically expose host SSH keys, GitHub CLI configuration, cloud credentials, browser cookies, or other ambient host secrets.
8. Replace project-specific build systems, package managers, test runners, or linters.
9. Infer or repair every possible development environment automatically.
10. Guarantee that arbitrary untrusted repository code is safe to execute.
11. Provide a general-purpose multi-tenant hostile-user sandbox in the first release.
12. Duplicate high-level GitHub/CI orchestration already provided by dedicated systems such as Ralph Bridge.

---

## 4. Intended Deployment Model

The primary deployment model is a private server controlled by a single user or trusted small team.

Typical host:

- Linux workstation or server.
- Docker Engine installed.
- Local Git repositories under one or more configured repository roots.
- Network access to package registries and GitHub.
- MCP transport exposed only through a private or authenticated channel.
- Development containers created and managed by the MCP daemon.

Example:

```text
                     MCP Client
                     (ChatGPT)
                         |
                         | MCP
                         v
              +----------------------+
              | Dev Workspace MCP    |
              | Server / Daemon      |
              +----------+-----------+
                         |
                         | Docker API
                         v
              +----------------------+
              | Docker Engine        |
              +----------+-----------+
                         |
            +------------+-------------+
            |                          |
            v                          v
 +----------------------+   +----------------------+
 | Project A Container  |   | Project B Container  |
 | /workspace            |   | /workspace           |
 +----------+-----------+   +----------+-----------+
            | bind mount                 | bind mount
            v                            v
 ~/work/project-a              ~/work/project-b
```

The MCP daemon is the privileged control plane. Development containers are execution environments.

---

## 5. Architectural Principles

### 5.1 Local Checkout Is the Source of Truth

The host repository SHALL remain the canonical working tree.

The development container SHALL normally bind-mount the repository:

```text
HOST:      /home/user/work/project-a
CONTAINER: /workspace
```

The repository working tree SHALL be mounted read/write.

Repository Git metadata (`.git`) SHALL be mounted according to the workspace
`git_dir_mode` (§18.2), which defaults to read-only. A writable working tree with
read-only Git metadata is the normal configuration: it permits the client to edit
project files freely while preventing container code from reaching the host through
Git hooks and configuration (§18.3).

Changes made from MCP SHALL immediately appear in the host checkout.

This permits seamless interoperability with:

- VS Code
- local terminals
- Claude Code
- Git clients
- IDEs
- file watchers
- host-side review tools

No explicit file synchronization step should be required.

### 5.2 MCP Daemon Owns Docker

The MCP daemon SHALL control Docker lifecycle operations.

Project containers SHALL NOT receive `/var/run/docker.sock` by default.

Mounting the Docker socket into a container is effectively equivalent to granting host-level control and is outside the normal security model.

Optional nested-container or Docker-in-Docker workflows MAY be added later behind an explicit elevated capability.

### 5.3 Development Containers May Be Privileged Internally

The default development container MAY run as root internally.

The client SHOULD be able to perform operations such as:

```bash
apt-get update
apt-get install -y clang cmake protobuf-compiler
rustup component add clippy
cargo install cargo-nextest
npm install
uv sync
```

Container-root privileges are acceptable because the stronger boundary is between the container and the host.

### 5.4 Explicit Host Mounts Only

The daemon SHALL mount only:

- the selected repository;
- explicitly configured dependency caches;
- explicitly configured project resources;
- explicitly approved devices or sockets, if supported later.

The server SHALL NOT automatically mount the user's home directory.

### 5.5 Structured Tools Plus General Shell

The server SHALL provide both:

1. structured MCP tools for high-value operations; and
2. a general command execution primitive.

Structured operations improve reliability, validation, auditability, and token efficiency.

The general shell preserves language/toolchain independence.

### 5.6 Long Operations Are Jobs

Long-running commands SHALL NOT require a single MCP request to remain open.

Operations such as:

- `cargo build`
- `cargo test`
- Gradle builds
- large test suites
- integration environments
- static-analysis scans

SHALL be capable of running as persistent asynchronous jobs.

### 5.7 GitHub Credentials Never Enter Project Containers

Credentials for authorized remotes SHALL remain outside all project process execution.

> No credential capable of writing to an authorized remote SHALL be present inside a
> development container, in its environment, in its filesystem, in its process
> arguments, or in any file under a repository root.

This is the §19.1 invariant. It is a structural property of the architecture rather
than a policy setting, and SHALL NOT be relaxed by configuration.

All authenticated remote operations SHALL therefore be performed by the MCP daemon
on the host, through the Git broker (§20). No mode in which a token is injected into
a development container is specified.

### 5.8 Language Agnostic

The MCP server SHALL NOT require project-specific logic for Rust, Python, Node.js, Go, Java, C++, or other languages.

Projects declare or install their own tooling.

---

## 6. Major Components

The implementation SHOULD be divided into the following logical components.

### 6.1 MCP Interface Layer

Responsibilities:

- expose tool definitions;
- validate arguments;
- serialize structured responses;
- map internal errors to stable MCP errors;
- enforce request-level authorization;
- attach request identifiers;
- avoid leaking secrets.

### 6.2 Workspace Manager

Responsibilities:

- discover configured repositories;
- resolve repository IDs;
- validate allowed paths;
- create workspace records;
- start, stop, rebuild, and delete development containers;
- mount repository and cache volumes;
- maintain workspace metadata;
- detect stale or missing containers;
- perform recovery.

### 6.3 Filesystem Service

Responsibilities:

- validate workspace-relative paths;
- read files;
- list directories;
- write files;
- patch files;
- create directories;
- rename files;
- delete files;
- provide metadata;
- enforce repository boundary restrictions.

### 6.4 Execution Service

Responsibilities:

- run commands in containers;
- support environment variables and working directories;
- provide synchronous execution for short operations;
- provide asynchronous execution for long operations;
- capture stdout/stderr;
- enforce output limits;
- support cancellation;
- retain job records;
- report exit status and timing.

### 6.5 Git Service

Responsibilities:

- status;
- diff;
- branch state;
- staging;
- commit creation;
- fetch/pull/push;
- log/history;
- tags;
- remote validation;
- conflict detection;
- destructive-operation policy.

### 6.6 GitHub Authentication / Remote Broker

Responsibilities:

- maintain repository-scoped authentication;
- issue short-lived tokens where possible;
- validate remote repository identity;
- perform GitHub operations that should not expose credentials to project code;
- optionally integrate with GitHub App authentication.

### 6.7 Policy Engine

Responsibilities:

- allowed repositories;
- allowed mounts;
- allowed remotes;
- default-branch policy;
- destructive operation policy;
- elevated-capability policy;
- network policy;
- approval requirements.

### 6.8 Job Store

Responsibilities:

- persist job metadata;
- preserve command status across MCP reconnects;
- retain bounded output;
- expire old completed jobs;
- recover orphaned jobs after daemon restart where possible.

### 6.9 Audit Logger

Responsibilities:

- record important operations;
- avoid logging secrets;
- provide traceability for writes, command execution, and Git changes.

---

## 7. Repository Model

Each repository SHALL have a stable configured identifier.

Example configuration:

```yaml
repositories:
  - id: chatgpt-ralph-loop-tools
    path: /home/phil/work/chatgpt_ralph_loop_tools
    github: ekkus93/chatgpt_ralph_loop_tools

  - id: project-gatsby
    path: /home/phil/work/project-gatsby
    github: ekkus93/project-gatsby
```

The client SHOULD use repository IDs rather than arbitrary absolute paths.

### 7.1 Repository Discovery

The server MAY support both:

- explicitly registered repositories;
- discovery beneath configured repository roots.

Example:

```yaml
repository_roots:
  - /home/phil/work
```

If discovery is enabled, the daemon SHALL resolve symlinks and ensure the resulting path remains under an authorized repository root.

### 7.2 Repository Identity

The server SHOULD capture:

- repository ID;
- canonical host path;
- Git repository root;
- configured GitHub repository;
- current remote URLs;
- default branch;
- workspace/container ID;
- active branch;
- dirty state.

### 7.3 Remote Validation

Before a remote write, the Git service SHALL verify that the configured remote resolves to an authorized repository.

The server SHOULD normalize common forms:

```text
https://github.com/owner/repo.git
git@github.com:owner/repo.git
ssh://git@github.com/owner/repo.git
```

to:

```text
owner/repo
```

---

## 8. Workspace Model

A workspace is the server-side representation of a repository development environment.

Example fields:

```json
{
  "workspace_id": "ws_project_gatsby",
  "repository_id": "project-gatsby",
  "host_path": "/home/phil/work/project-gatsby",
  "container_id": "0bd...",
  "container_name": "mcp-project-gatsby",
  "container_state": "running",
  "mode": "persistent",
  "workspace_path": "/workspace",
  "image": "mcp-dev-ubuntu:24.04",
  "git_dir_mode": "read-only",
  "created_at": "...",
  "last_used_at": "..."
}
```

### 8.1 Workspace Modes

#### Persistent

Persistent workspaces are reused across sessions.

Installed packages and toolchains remain available until the workspace is rebuilt or deleted.

#### Ephemeral

Ephemeral workspaces are created for a bounded task and deleted afterward.

Use cases include:

- clean-room validation;
- reproducing CI;
- verifying installation instructions;
- ensuring hidden state is not required.

### 8.2 Workspace Lifecycle States

Recommended states:

```text
absent
creating
stopped
starting
running
stopping
rebuilding
failed
deleting
```

Transitions SHALL be explicit.

Unexpected Docker state SHALL be reconciled on the next workspace operation.

### 8.3 Workspace Open

`workspace_open` SHOULD:

1. validate repository authorization;
2. inspect repository state;
3. locate an existing workspace;
4. create one if needed;
5. ensure its container is running;
6. return current repository and environment status.

### 8.4 Workspace Reset/Rebuild

The system SHOULD provide separate semantics:

- `workspace_restart`: restart the existing container;
- `workspace_rebuild`: rebuild the container while preserving the repository;
- `workspace_reset`: discard environment state and recreate from configuration;
- `workspace_delete`: remove workspace/container metadata.

A reset SHALL NOT delete the host repository.

---

## 9. Container Image Strategy

The server SHOULD support several environment sources.

Recommended precedence:

1. explicit MCP workspace configuration;
2. `.devcontainer/devcontainer.json`;
3. project-specific Dockerfile;
4. configured default development image.

Potential files:

```text
.mcp-workspace.yml
.mcp-workspace.yaml
.devcontainer/devcontainer.json
Dockerfile.dev
Dockerfile
```

### 9.1 Default Image

A practical default Linux image SHOULD include:

- Bash
- coreutils
- Git
- curl
- wget
- ca-certificates
- build-essential
- pkg-config
- unzip
- zip
- jq
- Python
- common archive utilities

The image SHOULD NOT attempt to contain every language toolchain.

### 9.2 Development Image Persistence

The container filesystem MAY be preserved for persistent workspaces.

For reproducibility, the system SHOULD record:

- base image reference;
- image digest;
- configuration version;
- workspace creation time.

---

## 10. Project Workspace Configuration

The server SHOULD support an optional project file such as `.mcp-workspace.yml`.

Example:

```yaml
version: 1

image: ubuntu:24.04
workspace: /workspace

setup:
  - apt-get update
  - apt-get install -y build-essential curl git pkg-config
  - curl https://sh.rustup.rs -sSf | sh -s -- -y

environment:
  RUST_BACKTRACE: "1"

caches:
  - path: /root/.cargo
    name: cargo
  - path: /root/.rustup
    name: rustup

github:
  repository: ekkus93/example-project

validation:
  format:
    - cargo fmt --check
  lint:
    - cargo clippy --all-targets --all-features -- -D warnings
  test:
    - cargo test --all-features
```

The configuration format SHALL be versioned.

Unknown mandatory fields SHOULD cause a validation error rather than being silently ignored.

---

## 11. Dependency and Toolchain Caches

The server SHOULD support persistent named volumes for dependency caches.

Examples:

```text
/root/.cargo
/root/.rustup
/root/.cache/uv
/root/.cache/pip
/root/.npm
/root/.pnpm-store
/root/go/pkg/mod
/root/.gradle
```

Cache behavior SHALL be configurable as:

- per workspace;
- per repository;
- shared globally.

The default SHOULD favor isolation where cross-project contamination may affect reproducibility.

The server SHALL NOT treat a cache as trusted executable input merely because it is persistent.

---

## 12. Filesystem Security

All filesystem tools SHALL operate relative to the repository root unless explicitly documented otherwise.

The server SHALL reject:

```text
../../etc/passwd
/workspace/../../host-secret
```

Path checks SHALL:

1. normalize separators;
2. resolve `.` and `..`;
3. consider symlink traversal;
4. resolve canonical paths before authorization decisions;
5. ensure the resolved path remains within the allowed repository root.

Writes through symlinks that escape the repository SHALL be rejected.

### 12.1 File Size Limits

The server SHOULD define configurable limits for:

- direct file reads;
- direct writes;
- patch payloads;
- directory listings.

Large files SHOULD be accessed through chunked reads or artifact mechanisms.

---

## 13. Filesystem MCP Tools

The exact names are implementation-defined, but the following capability set is required.

### 13.1 `list_files`

Purpose:

- list files and directories under a repository-relative path.

Inputs:

```json
{
  "workspace_id": "ws_example",
  "path": "src",
  "recursive": false,
  "max_entries": 500
}
```

Output SHOULD include:

- relative path;
- type;
- size;
- modification time where useful;
- symlink status.

### 13.2 `read_file`

Inputs SHOULD support:

- workspace;
- path;
- optional byte range or line range;
- maximum response size.

Output SHOULD identify truncation.

### 13.3 `write_file`

Writes complete file contents.

The server SHOULD optionally support optimistic concurrency:

```json
{
  "expected_sha256": "..."
}
```

If the file changed since the client read it, the write can fail safely.

### 13.4 `patch_file`

Preferred for precise edits.

Supported patch formats MAY include:

- unified diff;
- explicit range replacement;
- structured search-and-replace.

The response SHOULD report:

- whether the patch applied;
- changed paths;
- old/new hashes;
- rejected hunks.

### 13.5 `delete_file`

Deletion SHOULD be repository-scoped.

Recursive directory deletion SHOULD be explicit.

### 13.6 `move_file`

Move or rename a file within the repository.

### 13.7 `file_stat`

Returns metadata and content hash.

### 13.8 `search_files`

Optional but strongly recommended.

Capabilities MAY include:

- filename globbing;
- text search;
- regex search;
- ripgrep-backed search.

---

## 14. Command Execution

Command execution is a primary capability.

### 14.1 Command Form

The API SHOULD support both:

- argv form;
- shell form.

Preferred argv example:

```json
{
  "workspace_id": "ws_example",
  "argv": ["cargo", "test", "--all-features"],
  "cwd": "/workspace",
  "env": {},
  "timeout_seconds": 120
}
```

Shell example:

```json
{
  "workspace_id": "ws_example",
  "shell": "set -euo pipefail\ncargo fmt --check\ncargo test",
  "cwd": "/workspace"
}
```

Argv form avoids quoting ambiguity and SHOULD be preferred where practical.

### 14.2 Working Directory

The working directory SHALL default to `/workspace`.

A requested working directory SHALL remain inside allowed container paths.

### 14.3 Environment

The command MAY receive explicit environment overrides.

Sensitive environment variables SHALL be redacted from logs.

### 14.4 Synchronous Execution

Short commands MAY use a synchronous endpoint.

Example use cases:

- `git status --short`;
- `rustc --version`;
- `python --version`;
- `cargo fmt --check` for small repositories.

The synchronous execution path SHOULD have a strict maximum duration.

If the command exceeds the synchronous threshold, the server MAY convert it to a background job and return a job ID.

---

## 15. Asynchronous Jobs

Long-running execution SHALL be represented as jobs.

### 15.1 Job State

Recommended states:

```text
queued
starting
running
succeeded
failed
canceled
timed_out
lost
```

### 15.2 Job Fields

A job SHOULD record:

```json
{
  "job_id": "job_8712",
  "workspace_id": "ws_example",
  "command": "...",
  "cwd": "/workspace",
  "state": "running",
  "pid": 1234,
  "started_at": "...",
  "finished_at": null,
  "exit_code": null,
  "stdout_bytes": 28123,
  "stderr_bytes": 441,
  "output_truncated": false
}
```

### 15.3 Required Job Operations

#### `exec_start`

Starts an asynchronous command.

#### `exec_status`

Returns current status and recent output.

#### `exec_output`

Retrieves output by offset, range, stream, or line window.

#### `exec_cancel`

Requests cancellation.

The implementation SHOULD attempt graceful termination first, then force termination after a configured grace period.

### 15.4 Job Persistence

Job metadata SHOULD survive MCP reconnects.

If the MCP daemon restarts, it SHOULD reconcile jobs against running container processes where feasible.

Unrecoverable jobs SHALL transition to `lost`.

### 15.5 Output Limits

The server SHALL bound command output returned in normal responses.

Recommended behavior:

```json
{
  "stdout_tail": "...",
  "stderr_tail": "...",
  "stdout_truncated": true,
  "stderr_truncated": false,
  "stdout_total_bytes": 8134482
}
```

Full logs SHOULD be retrievable incrementally.

This prevents multi-megabyte compiler output from consuming MCP context unnecessarily.

---

## 16. Package and Software Installation

The system SHALL intentionally allow the client to install development dependencies inside the container.

Examples:

```bash
apt-get update
apt-get install -y clang lld protobuf-compiler cmake
rustup toolchain install stable
rustup component add rustfmt clippy
cargo install cargo-nextest
uv sync
npm ci
go install ...
```

Installation is performed through normal command execution unless a future structured package-install tool is added.

### 16.1 Network Requirement

Development containers SHOULD have outbound network access by default for:

- OS packages;
- language package registries;
- GitHub;
- documentation;
- dependency downloads.

Network restrictions MAY be configured per deployment or repository.

### 16.2 Reproducibility

The system SHOULD encourage projects to capture required setup in:

- Dev Containers;
- Dockerfiles;
- `.mcp-workspace.yml`;
- project dependency manifests;
- bootstrap scripts.

Ad hoc installation remains permitted in persistent environments.

---

## 17. Git Tooling

Git SHOULD be available both through command execution and structured MCP operations.

Structured operations SHALL be preferred for repository state and mutating Git operations.

Structured Git tools SHALL be executed host-side by the Git broker (§20) against the
host checkout, not inside the development container. Git usage inside the container
is read-only and is bounded by `git_dir_mode` (§18.2); it is available for build
tooling that shells out to Git, and is not the path by which repository state is
mutated. Clients requiring authoritative repository state SHALL use `git_status`
rather than `exec`.

### 17.1 Required Git Operations

The server SHOULD expose:

- `git_status`
- `git_diff`
- `git_log`
- `git_show`
- `git_branch_list`
- `git_branch_create`
- `git_checkout`
- `git_add`
- `git_restore`
- `git_commit`
- `git_fetch`
- `git_pull`
- `git_push`
- `git_remote`
- `git_tag`
- `git_merge`
- `git_rebase` where policy permits

### 17.2 Git Status

Structured output SHOULD include:

```json
{
  "branch": "master",
  "head_sha": "...",
  "upstream": "origin/master",
  "ahead": 1,
  "behind": 0,
  "detached_head": false,
  "clean": false,
  "staged": [],
  "unstaged": [],
  "untracked": []
}
```

### 17.3 Git Diff

The API SHOULD support:

- working tree diff;
- staged diff;
- commit-to-commit diff;
- path filtering;
- stat-only mode;
- bounded output.

### 17.4 Commit Creation

A structured commit operation SHOULD:

1. confirm current branch;
2. validate staged paths;
3. optionally stage explicit paths;
4. reject unexpected changes when strict mode is requested;
5. create the commit;
6. return new commit SHA.

The API SHOULD support an `expected_head_sha` guard.

Example:

```json
{
  "workspace_id": "ws_example",
  "expected_head_sha": "abc123...",
  "paths": [
    "src/lib.rs",
    "tests/test_lib.rs"
  ],
  "message": "fix: validate workspace state"
}
```

If HEAD changed, the operation SHALL fail instead of committing against an unexpected base.

---

## 18. Git Operation Model and Destructive-Operation Policy

### 18.1 Enforcement Boundary

Structured Git tools cannot by themselves carry destructive-operation policy,
because §14 grants arbitrary shell access to the same repository. If a development
container held credentials for an authorized remote, `exec("git push --force")`
would bypass the policy engine entirely and AT-011 could not hold.

This specification therefore establishes a single enforcement boundary:

> All authenticated remote Git operations SHALL be performed by the MCP daemon on
> the host. The development container SHALL NOT hold credentials for any authorized
> remote, and SHALL NOT hold write access to repository Git metadata.

Policy is enforced at the only component that can reach the remote. The shell is no
longer a bypass because the shell has nothing to authenticate with.

Operations divide into three tiers by how they are actually enforced. The
specification SHALL be explicit about which tier each operation occupies, and SHALL
NOT describe a tier-3 operation as prevented.

```text
Tier 1 — Structurally prevented
  The container lacks the capability. No policy check is required.
  Enforced by: absent credentials, read-only .git mount.

Tier 2 — Policy-gated at the broker
  The daemon can perform it; policy decides whether it does.
  Enforced by: the policy engine, at the git broker (§20).

Tier 3 — Advisory only
  Reachable from exec inside the container. Bounded by the container
  boundary and by recoverability, not by policy.
  Enforced by: nothing. Documented, audited after the fact, recoverable.
```

### 18.2 Container Git Access

The repository working tree SHALL be bind-mounted read/write as specified in §5.1.

The repository Git directory SHALL be mounted according to a configured
`git_dir_mode`:

```yaml
container:
  git_dir_mode: read-only    # none | read-only | read-write
```

`read-only` SHALL be the default.

| Mode | `.git` mount | Consequence |
|---|---|---|
| `none` | not mounted | No Git operations inside the container. Strongest isolation. Breaks build tooling that shells out to Git. |
| `read-only` | bind-mounted `ro` | Read-only Git operations work. Metadata cannot be modified. Default. |
| `read-write` | bind-mounted `rw` | Elevated capability (§42). SHALL be refused unless explicitly enabled, and SHALL be reported by `workspace_info` as a weakened security mode. |

Under `read-only`, the daemon SHALL configure the container environment so that
read-only Git usage behaves correctly:

```text
GIT_OPTIONAL_LOCKS=0        # git status et al. do not attempt to take the index lock
GIT_TERMINAL_PROMPT=0       # no interactive credential prompting
```

The daemon SHALL provide a container-local system Git configuration — written into
the container filesystem, never into the bind mount — marking the workspace path as
a safe directory, so that container-root operating on host-owned files does not
fail ownership validation:

```text
/etc/gitconfig  (container-local)
  [safe]
      directory = /workspace
```

Commands that work under `read-only`: `git log`, `git show`, `git rev-parse`,
`git describe`, `git cat-file`, `git ls-files`, `git diff` against committed state.

Commands that fail under `read-only`: anything creating a ref, an object, an index,
or a lock — `git commit`, `git add`, `git checkout`, `git merge`, `git reset`,
`git fetch`, `git push`, `git stash`.

The specification SHALL NOT promise that `git status` is fully functional inside the
container. Clients requiring repository state SHALL use the structured `git_status`
tool (§17.2), which is executed host-side by the daemon.

### 18.3 Rationale for Read-Only Git Metadata

A writable `.git` directory is a host code-execution path, independent of any
container escape. Repository Git metadata is interpreted by Git processes running on
the host, under the human user's identity, whenever a developer, IDE, file watcher,
or other AI client touches the repository.

Container-writable `.git` would permit:

```text
.git/hooks/pre-commit          executes on the host on the next human commit
.git/config core.fsmonitor     executes on the host on nearly every git invocation
.git/config core.sshCommand    executes on the host on the next remote operation
.git/config alias.*            executes on the host when the alias is invoked
.git/config filter.*.clean     executes on the host on checkout/add
.git/config diff.*.textconv    executes on the host on diff
.git/config credential.helper  substitutes a credential source on the host
```

This is precisely the adversary named in §39 — a repository whose build scripts or
dependencies are untrusted — reaching the host without escaping the container. A
read-only Git directory closes the entire class.

Note the pairing that makes this sufficient: `.gitattributes` and `.gitmodules` are
tracked files and therefore remain container-writable, but they are inert data. The
executable half of each mechanism — `filter.<name>.clean`, `diff.<name>.textconv`,
`merge.<name>.driver`, submodule URLs after initialization — lives in `.git/config`.
Making the configuration unwritable neutralizes the tracked-file half.

### 18.4 Tier 1 — Structurally Prevented

The following SHALL be unreachable from the development container and SHALL require
no policy check:

- push, force push, and push with any refspec;
- deletion of remote branches or tags;
- authenticated fetch or pull from an authorized remote;
- creation, deletion, or modification of local refs;
- commit creation;
- history rewriting (`rebase`, `commit --amend`, `filter-branch`);
- modification of `.git/config`, `.git/hooks`, or any Git metadata;
- installation of a credential helper visible to host Git.

`workspace_info` SHALL report `git_dir_mode` so that a client can determine whether
these guarantees hold for the current workspace.

### 18.5 Tier 2 — Policy-Gated at the Broker

The following are performed by the daemon and SHALL be subject to the policy engine:

- push to an authorized remote;
- push to the repository default branch;
- force push and `--force-with-lease`;
- remote branch deletion;
- remote tag deletion or replacement;
- non-fast-forward pull strategies (merge, rebase);
- `git reset --hard`, `git clean -fdx`, and other destructive local operations
  invoked through structured tools;
- changing a configured remote URL;
- submodule initialization or update.

### 18.6 Tier 3 — Advisory Only

The following remain reachable from `exec` and SHALL be documented as such:

- deletion or corruption of working-tree files, including bulk deletion;
- deletion of untracked and ignored files, including build artifacts and any
  untracked file not represented in Git;
- writing arbitrary content into tracked files that a subsequent brokered commit
  may then record;
- outbound network use, including transmission of repository contents to an
  attacker-controlled destination.

The specification SHALL be explicit that brokered Git prevents unauthorized writes
to authorized remotes. It does not prevent network exfiltration in general, which
remains an accepted risk under §39.2. Tracked content destroyed from within the
container is recoverable from the read-only object store; untracked content is not.

Acceptance tests SHALL reflect these boundaries. AT-011 SHALL assert that a force
push is denied at the broker *and* that `exec("git push --force")` fails for lack of
credentials, rather than asserting only the former.

### 18.7 Hook Execution Policy for Brokered Operations

The daemon performs Git operations on the host, against a working tree that
container code has written. Repository-supplied hooks are therefore a host
code-execution path for the daemon itself.

Two vectors exist and both SHALL be addressed:

1. `.git/hooks/*` — not container-writable under §18.2, but may contain hooks
   installed by the human or by earlier tooling.
2. `core.hooksPath` — a configuration key that may point at a directory inside the
   tracked working tree, which *is* container-writable.

The daemon SHALL execute brokered Git operations with hooks disabled by default:

```yaml
git_policy:
  broker_hooks: disabled     # disabled | host_only | enabled
```

- `disabled` (default): the daemon SHALL pass `-c core.hooksPath=<empty-dir>`,
  where `<empty-dir>` is a daemon-owned empty directory outside all repository
  roots. `--no-verify` SHALL NOT be relied upon, as it covers only a subset of
  hooks.
- `host_only`: hooks SHALL run only if the effective `core.hooksPath` resolves
  outside the repository working tree. If it resolves inside, the operation SHALL
  fail with `POLICY_DENIED`.
- `enabled`: an elevated capability (§42). SHALL be reported as a weakened mode.

The daemon SHALL likewise neutralize configuration keys that invoke external
commands during its own operations:

```text
-c core.fsmonitor=
-c core.sshCommand=          (unless supplied by the broker itself)
-c credential.helper=        (cleared, then supplied per §19.4)
```

### 18.8 Submodules

Submodule URLs originate in `.gitmodules`, a tracked and therefore
container-writable file.

Brokered fetch and push SHALL pass `--no-recurse-submodules` and
`--recurse-submodules=no` respectively.

Submodule initialization and update SHALL be a Tier-2 operation. Each submodule URL
SHALL be validated under §20.3 exactly as a primary remote is, at the time of the
operation.

### 18.9 Policy Configuration

```yaml
git_policy:
  # Tier 2 — remote write
  push:                     allow
  default_branch_push:      allow_fast_forward   # allow | allow_fast_forward | require_approval | deny
  force_push:               deny
  force_with_lease:         deny
  delete_remote_branch:     require_approval
  delete_remote_tag:        deny
  change_remote_url:        deny

  # Tier 2 — local destructive
  hard_reset:               require_approval
  clean_untracked:          require_approval
  rebase:                   require_approval
  amend_published:          deny

  # Tier 2 — sync strategy
  pull_strategy:            ff_only              # ff_only | merge | rebase
  submodules:               deny                 # deny | require_approval | allow

  # Enforcement environment
  broker_hooks:             disabled
  validate_remote:          true
  allowed_url_schemes:      [https, ssh]
```

`require_approval` MAY be implemented as a hard denial in the first release (§42).

The default-branch question SHALL remain configurable rather than imposed, per §43.

---

## 19. Credential Model

GitHub synchronization is required. Credential exposure to project code SHALL be
eliminated rather than minimized.

### 19.1 Invariant

> No credential capable of writing to an authorized remote SHALL be present inside
> a development container, in its environment, in its filesystem, in its process
> arguments, or in any file under a repository root.

This is the mechanism by which FR-025 is satisfied. It is a structural property, not
a policy setting, and SHALL NOT be relaxed by configuration.

### 19.2 Preferred: GitHub App

The daemon SHALL support GitHub App installation authentication:

1. App private key and identifiers are stored on the host, outside all repository
   roots, readable only by the daemon account.
2. Repository authorization is configured server-side (§7).
3. Immediately before a remote operation, the daemon mints a short-lived
   installation token scoped to the target repository.
4. The token is used for exactly one Git invocation.
5. The token is discarded. It SHALL NOT be cached beyond its operation, and SHALL
   NOT be written to disk.

This yields repository-scoped access, short-lived credentials, revocability, and
per-operation auditability.

### 19.3 Acceptable Alternative: Daemon-Held Fine-Grained Token

An MVP MAY use a fine-grained personal access token in place of a GitHub App,
provided the token is held by the daemon under §19.1 and §19.4.

The token SHALL be scoped to the configured repositories and SHALL grant no
organization-wide or account-wide permissions.

This is a weaker credential model than §19.2 — the token is long-lived and its
compromise is not self-limiting — but it preserves the §19.1 invariant and therefore
satisfies FR-025.

### 19.4 Credential Handling Requirements

The daemon SHALL supply credentials to Git without exposing them to the process
table, the filesystem, or repository configuration.

A credential SHALL NOT appear in:

- process arguments, including embedded in a URL passed on the command line;
- `.git/config`, or any file beneath a repository root;
- any persistent environment;
- audit records, error messages, or tool responses;
- command output returned to the client.

The daemon SHOULD supply the credential through one of:

- an ephemeral `GIT_ASKPASS` helper that reads the secret from an inherited file
  descriptor or a daemon-private unix socket;
- a per-invocation credential helper configured through `GIT_CONFIG_COUNT` /
  `GIT_CONFIG_KEY_n` / `GIT_CONFIG_VALUE_n`, cleared after the invocation;
- an `http.extraHeader` authorization value supplied through the same per-invocation
  configuration mechanism.

Where a temporary file is unavoidable, it SHALL be created outside all repository
roots with mode `0600` and removed before the operation returns, including on the
failure path.

The secret-redaction layer (§24.1) SHALL be applied to brokered Git stdout and
stderr before any of it reaches an audit record or a tool response.

### 19.5 Host Credentials

The daemon SHALL NOT mount into any development container:

```text
~/.ssh
~/.config/gh
~/.git-credentials
~/.netrc
~/.docker/config.json
```

The prohibition on host SSH keys is normative, and is stated as SHALL NOT rather
than SHOULD NOT so that it matches the corresponding DENIED default in §74.

`gh` MAY be installed inside a container. It SHALL NOT be authenticated there.
GitHub API operations requiring authentication SHALL be brokered, or delegated to
Ralph Bridge (§21).

### 19.6 Credential Scope and Lifetime

```yaml
github:
  auth: app                  # app | token
  app:
    app_id: ...
    private_key_path: /etc/dev-workspace-mcp/github-app.pem
    installation_id: ...
  token_ttl_seconds: 300
  repositories:
    - ekkus93/chatgpt_ralph_loop_tools
    - ekkus93/project-gatsby
```

A minted token SHALL be scoped to the single repository targeted by the operation
where the authentication mechanism permits it.

---

## 20. The Git Broker

### 20.1 Position

The Git broker is the component that owns the host checkout's Git metadata and is
the sole holder of remote credentials. Injection of credentials into a development
container is not an accepted strategy under §19.1, and no such mode is specified.

```text
   MCP client
       |
       | git_* tools
       v
  +---------------------------+
  |  MCP daemon               |
  |    policy engine  (§18.9) |
  |    git broker             |----> credential source (§19)
  +------------+--------------+
               |
               | git, host-side, hooks disabled
               v
      host checkout /home/phil/work/project
        .git   ------------------------------> container: read-only
        worktree ----------------------------> container: read-write
```

### 20.2 Responsibilities

The broker SHALL:

- execute all Git operations that create or modify refs, objects, or Git
  configuration;
- execute all authenticated remote operations;
- evaluate policy (§18.9) before each operation;
- validate remote identity (§20.3) immediately before each authenticated operation;
- enforce working-tree and state preconditions (§20.6);
- produce structured results and structured errors (§31);
- emit audit records (§24).

The broker SHALL be the only component in the system that holds a remote credential.

### 20.3 Remote Identity Validation

Before any authenticated operation, the broker SHALL:

1. resolve the remote name to a URL via `git remote get-url <name>`;
2. reject the URL unless its scheme appears in `git_policy.allowed_url_schemes`;
3. explicitly reject `ext::`, `file://`, and any transport capable of invoking a
   local command, by both scheme check and `-c protocol.ext.allow=never`;
4. normalize the URL to `owner/repo` form per §7.3;
5. compare the normalized value against the repository's configured GitHub identity
   and against `github.repositories`;
6. fail with `GIT_REMOTE_NOT_ALLOWED` on any mismatch.

The broker SHALL then invoke Git against the **validated URL literal**, not the
remote name. Passing the remote name would leave a window in which the URL could
change between validation and use — the working tree is shared with a human, and
`.git/config` remains host-writable even though it is container-read-only.

The broker SHALL reject any client-supplied `--upload-pack`, `--receive-pack`, or
`--exec` value.

Where the client supplies `expected_remote_repository`, a mismatch SHALL fail the
operation regardless of server-side configuration.

### 20.4 Refspec Validation

Client-supplied refspecs SHALL be validated rather than passed through.

The broker SHALL reject:

- a leading `+` (forced update) unless force policy permits it;
- an empty source (branch or tag deletion) unless deletion policy permits it;
- `--mirror`, `--all`, and `--tags` in combination with force;
- any refspec whose destination is outside `refs/heads/` or `refs/tags/`.

Push SHALL be invoked with an explicit refspec rather than relying on
`push.default`:

```text
git -c protocol.ext.allow=never \
    -c core.hooksPath=<empty-dir> \
    -c core.fsmonitor= \
    push --atomic --recurse-submodules=no \
    <validated-url> refs/heads/<branch>:refs/heads/<branch>
```

### 20.5 Brokered Operation Contracts

#### `git_fetch`

Fetches from a validated remote. SHALL pass `--no-recurse-submodules`. Does not
modify the working tree.

#### `git_pull`

SHALL be implemented as fetch followed by an integration step governed by
`git_policy.pull_strategy`, defaulting to `ff_only`.

Merge and rebase strategies modify a working tree that container processes may be
concurrently reading or writing, and SHALL require explicit configuration. A pull
that cannot fast-forward under `ff_only` SHALL fail with `GIT_NON_FAST_FORWARD`
rather than silently escalating.

#### `git_push`

Request:

```json
{
  "workspace_id": "ws_chatgpt_ralph_loop_tools",
  "remote": "origin",
  "branch": "master",
  "expected_remote_repository": "ekkus93/chatgpt_ralph_loop_tools",
  "expected_local_head_sha": "def456..."
}
```

Response:

```json
{
  "remote_repository": "ekkus93/chatgpt_ralph_loop_tools",
  "branch": "master",
  "pushed_sha": "def456...",
  "fast_forward": true,
  "credential_mode": "github_app",
  "token_ttl_seconds": 300
}
```

The response SHALL identify the credential mode so that a client can distinguish
§19.2 from §19.3 without inspecting server configuration.

### 20.6 Preconditions

Because the working tree is shared with container processes and with human
developers, the broker SHALL evaluate preconditions immediately before acting, and
SHALL fail closed:

- the workspace advisory lock (§22.1), where the deployment requires one;
- `expected_branch` and `expected_head_sha` guards (§45);
- interrupted-operation detection — merge, rebase, cherry-pick, bisect in progress
  (§44.4) — which SHALL abort the operation rather than attempt repair;
- dirty-working-tree evaluation for operations that would overwrite uncommitted
  changes (§22.3);
- for commit creation, that no path outside the explicitly supplied `paths` list is
  staged when strict mode is requested.

A precondition failure SHALL return the corresponding structured error from §31 and
SHALL leave repository state unmodified.

### 20.7 Audit

Each brokered operation SHALL emit an audit record containing:

```json
{
  "timestamp": "...",
  "request_id": "...",
  "session_id": "...",
  "workspace_id": "...",
  "repository_id": "...",
  "operation": "git_push",
  "remote_repository": "ekkus93/chatgpt_ralph_loop_tools",
  "validated_url_scheme": "https",
  "refspec": "refs/heads/master:refs/heads/master",
  "policy_decision": "allow",
  "credential_mode": "github_app",
  "result": "success",
  "resulting_sha": "def456..."
}
```

The record SHALL NOT contain the credential, the credentialed URL, or any
`Authorization` header value. Redaction SHALL occur before serialization, not as a
post-processing pass over an already-written record.

Denials SHALL be audited with the same fidelity as successes, including the policy
key that produced the denial.

---

## 21. Interaction With Ralph Bridge

The Development Workspace MCP server and Ralph Bridge SHOULD have distinct responsibilities.

### Development Workspace MCP

Primary responsibility:

> local development execution.

Capabilities:

- workspace files;
- compiler/toolchain access;
- package installation;
- tests;
- linters;
- local Git;
- ordinary push/pull.

### Ralph Bridge

Primary responsibility:

> GitHub and CI orchestration.

Capabilities may include:

- GitHub branches;
- commits;
- pull requests;
- protected operations;
- CI inspection;
- CI waiting;
- controlled merges;
- GitHub-specific validation.

The systems MAY overlap in basic GitHub functionality, but SHOULD avoid tightly coupling their internal implementations.

Example workflow:

```text
ChatGPT
   |
   +--> Development Workspace MCP
   |      edit
   |      cargo test
   |      cargo clippy
   |      git commit
   |      git push
   |
   +--> Ralph Bridge
          inspect CI
          diagnose workflow
          create PR
          merge
```

---

## 22. Workspace Concurrency

Multiple actors may touch the host working tree:

- human developer;
- ChatGPT;
- Claude Code;
- IDE;
- automated process.

The system SHOULD expose concurrency state.

### 22.1 Advisory Locks

Recommended tools:

- `workspace_lock`
- `workspace_unlock`
- `workspace_lock_status`

An advisory lock record SHOULD include:

```json
{
  "workspace_id": "ws_example",
  "owner": "chatgpt-session-...",
  "acquired_at": "...",
  "expires_at": "...",
  "reason": "implementing S4"
}
```

Locks SHOULD have leases so abandoned sessions do not block the repository permanently.

### 22.2 Optimistic Safety

Even with locks, mutating operations SHOULD use state guards where practical:

- expected HEAD SHA;
- expected file hash;
- expected branch;
- expected dirty-state assumptions.

This protects against concurrent edits without requiring hard locking.

### 22.3 Dirty Working Tree

The server SHALL report dirty state before operations that could overwrite changes.

Potentially destructive operations SHOULD fail when unrelated uncommitted changes exist unless explicitly authorized.

---

## 23. Session Model

MCP client sessions are transient.

Workspace state SHALL not depend on a single chat remaining connected.

The server SHOULD distinguish:

- MCP session;
- workspace;
- execution job;
- repository lock.

A workspace MAY survive many client sessions.

A job MAY outlive the MCP request that created it.

---

## 24. Audit Logging

The daemon SHOULD create append-only structured audit records.

Recommended events:

- workspace opened;
- container created/rebuilt/deleted;
- file written;
- file deleted;
- command started;
- command canceled;
- Git commit created;
- Git push attempted;
- Git push completed;
- elevated operation denied;
- repository authorization failure;
- remote mismatch detected.

Audit records SHOULD include:

```json
{
  "timestamp": "...",
  "request_id": "...",
  "session_id": "...",
  "workspace_id": "...",
  "repository_id": "...",
  "operation": "git_push",
  "result": "success"
}
```

### 24.1 Secret Redaction

Audit logs SHALL NOT intentionally contain:

- access tokens;
- passwords;
- private keys;
- authorization headers;
- secret environment values.

A configurable secret-redaction layer SHOULD inspect command metadata and output.

---

## 25. Observability

The server SHOULD expose a diagnostic endpoint or MCP tool.

Example `server_info` response:

```json
{
  "version": "0.1.0",
  "docker_available": true,
  "docker_version": "...",
  "active_workspaces": 3,
  "running_jobs": 1,
  "configured_repositories": 12
}
```

`workspace_info` SHOULD include:

- container state;
- image;
- OS information;
- CPU architecture;
- repository path;
- Git branch and SHA;
- dirty state;
- active jobs;
- active advisory lock;
- important tool versions if inexpensive to discover.

`workspace_info` SHALL additionally report the security-relevant modes in force, so
that a client can determine which guarantees apply without inspecting server
configuration:

- `git_dir_mode` (§18.2);
- `credential_mode` (§19);
- `broker_hooks` (§18.7).

These SHALL be reported as SHALL rather than SHOULD, because a client cannot
otherwise distinguish a hardened workspace from one running under an elevated
capability (§42).

Toolchain discovery MAY be lazy rather than running many commands on every request.

---

## 26. Resource Controls

The server SHOULD support configurable Docker resource limits.

Example:

```yaml
resources:
  cpus: 8
  memory: 16g
  pids_limit: 4096
```

Persistent development workspaces MAY have generous limits.

Ephemeral validation workspaces MAY use stricter limits.

The server SHOULD detect and report:

- OOM termination;
- disk exhaustion;
- process limit exhaustion.

---

## 27. Disk Management

Persistent workspaces and caches can consume significant disk space.

The server SHOULD expose:

- workspace disk usage;
- cache disk usage;
- Docker image usage;
- cleanup operations.

Potential tools:

- `workspace_disk_usage`
- `cache_list`
- `cache_prune`
- `workspace_prune`

Cleanup SHALL NOT delete repository files.

---

## 28. Network Policy

Default mode SHOULD allow outbound network access.

The policy layer SHOULD permit future modes such as:

```text
full
package-registries-only
github-only
offline
```

Inbound port exposure from development containers SHALL require explicit configuration.

### 28.1 Port Forwarding

A later version MAY support project services:

```json
{
  "workspace_id": "ws_example",
  "container_port": 3000
}
```

The daemon SHOULD prefer loopback-only host bindings by default.

---

## 29. Device Access

Hardware access is outside the MVP but the architecture SHOULD leave room for:

- USB devices;
- serial ports;
- Android devices via ADB;
- GPUs;
- audio devices.

Such capabilities SHALL require explicit configuration because they weaken isolation.

Example future policy:

```yaml
devices:
  - /dev/bus/usb
gpu: nvidia
```

---

## 30. Dev Container Support

Support for `.devcontainer/devcontainer.json` is strongly recommended.

The implementation MAY initially support a subset:

- image;
- Dockerfile;
- build context;
- environment variables;
- mounts;
- post-create commands;
- workspace folder.

Unsupported Dev Container properties SHALL be reported clearly.

The server SHALL not silently approximate security-sensitive settings.

---

## 31. Error Model

All tools SHOULD return stable machine-readable error codes.

Recommended categories:

```text
INVALID_ARGUMENT
WORKSPACE_NOT_FOUND
WORKSPACE_NOT_RUNNING
REPOSITORY_NOT_ALLOWED
PATH_OUTSIDE_REPOSITORY
FILE_CONFLICT
JOB_NOT_FOUND
COMMAND_FAILED
COMMAND_TIMEOUT
GIT_DIRTY
GIT_CONFLICT
GIT_HEAD_CHANGED
GIT_REMOTE_NOT_ALLOWED
GIT_REFSPEC_NOT_ALLOWED
GIT_HOOK_POLICY_DENIED
GIT_AUTH_FAILED
GIT_NON_FAST_FORWARD
POLICY_DENIED
CONTAINER_CREATE_FAILED
CONTAINER_RUNTIME_FAILED
NETWORK_FAILED
INTERNAL_ERROR
```

Responses SHOULD include:

- stable code;
- concise message;
- recoverable/not recoverable;
- relevant structured details.

Secrets SHALL not be included.

---

## 32. Idempotency

Operations that may be retried SHOULD support idempotency where useful.

Examples:

- workspace creation;
- workspace start;
- job cancellation;
- remote fetch.

Commit creation and push operations SHOULD use stronger state guards rather than naïve retry.

The server MAY support a caller-provided idempotency key.

---

## 33. Tool Versioning

MCP tool contracts SHALL be versioned deliberately.

Breaking schema changes SHOULD require:

- new tool version;
- server major version;
- or capability negotiation.

The server SHOULD expose a capability document.

Example:

```json
{
  "filesystem.patch": 1,
  "exec.jobs": 1,
  "git.structured_status": 1,
  "workspace.devcontainer": 1
}
```

---

## 34. Recommended MCP Tool Surface

An initial comprehensive API could expose the following tools.

### Server

```text
server_info
server_capabilities
```

### Repositories

```text
repository_list
repository_info
```

### Workspaces

```text
workspace_open
workspace_info
workspace_start
workspace_stop
workspace_restart
workspace_rebuild
workspace_reset
workspace_delete
workspace_lock
workspace_unlock
workspace_lock_status
```

### Filesystem

```text
list_files
read_file
file_stat
write_file
patch_file
move_file
delete_file
search_files
```

### Execution

```text
exec
exec_start
exec_status
exec_output
exec_cancel
job_list
```

### Git

```text
git_status
git_diff
git_log
git_show
git_remote
git_branch_list
git_branch_create
git_checkout
git_add
git_restore
git_commit
git_fetch
git_pull
git_push
git_tag
git_merge
git_rebase
```

### Diagnostics

```text
workspace_disk_usage
workspace_environment
workspace_processes
audit_recent
```

The MVP does not need every listed tool if equivalent functionality exists through `exec`, but the structured interfaces SHOULD be implemented for operations where correctness or policy matters.

---

## 35. Example Development Workflow

An AI client receives:

> Continue task S4 from `docs/STATELESS_MCP_TODO.md`.

The client performs:

```text
repository_list
workspace_open(repository_id)
git_status
git_pull
read_file(docs/STATELESS_MCP_TODO.md)
search_files(...)
read_file(...)
patch_file(...)
exec("cargo fmt --check")
exec_start("cargo test --all-features")
exec_status(job_id)
exec_output(job_id)
patch_file(...)
exec("cargo clippy ...")
git_diff
git_status
git_commit(expected_head_sha, paths, message)
git_push
```

At each stage, the local host checkout reflects the current state.

---

## 36. Example Rust Workflow

```bash
rustc --version
cargo --version
```

If unavailable:

```bash
apt-get update
apt-get install -y curl build-essential pkg-config
curl https://sh.rustup.rs -sSf | sh -s -- -y
```

Then:

```bash
rustup component add rustfmt clippy
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-features
```

The server itself does not require Rust-specific APIs.

---

## 37. Example Python Workflow

```bash
uv sync
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

If `uv` is unavailable, the client can install it inside the container.

---

## 38. Example Android Workflow

A project-specific environment may install:

- Java/JDK;
- Android SDK command-line tools;
- Gradle dependencies;
- NDK;
- CMake.

The server SHALL support long-running Gradle builds as background jobs.

Physical Android device support MAY be implemented later through explicitly configured device forwarding.

---

## 39. Security Threat Model

The primary adversarial assumption is:

> Repository contents and commands derived from repository contents may be untrusted.

Examples:

- malicious `Makefile`;
- malicious `package.json` install script;
- compromised dependency;
- prompt-injected repository documentation;
- accidental destructive command;
- build script that attempts credential theft.

The container boundary protects the host from much of this risk, but only if sensitive host resources are not mounted into the container.

### 39.1 Threats to Address

The implementation SHALL consider:

1. host filesystem escape;
2. Docker socket abuse;
3. GitHub credential theft;
4. symlink path escape;
5. dangerous Git history rewriting;
6. unauthorized repository access;
7. remote substitution attacks;
8. secret leakage through logs;
9. runaway processes;
10. disk exhaustion;
11. abandoned long-running jobs;
12. concurrent repository modification;
13. container breakout risk;
14. network-based exfiltration;
15. Git metadata poisoning as a host code-execution path — hooks, `core.fsmonitor`,
    `core.sshCommand`, aliases, clean/smudge filters, `textconv`, and credential
    helpers written into `.git` are interpreted by host Git processes running under
    the human user's identity, without requiring any container escape (§18.3);
16. hook execution during brokered operations, including `core.hooksPath` directed
    at a container-writable path inside the tracked working tree (§18.7);
17. local-command Git transports and transport overrides — `ext::`, `file://`,
    `--upload-pack`, `--receive-pack` (§20.3);
18. remote URL substitution between validation and use (§20.3);
19. refspec abuse — forced updates, ref deletion, and destinations outside
    `refs/heads/` and `refs/tags/` (§20.4);
20. submodule URL substitution through the tracked `.gitmodules` file (§18.8).

### 39.2 Accepted Risks

In the private single-user deployment model:

- arbitrary code execution inside the development container is intentional;
- project code may access files inside its own repository;
- project code may use outbound network access unless restricted;
- persistent containers may accumulate mutable state.

Two consequences of the above are accepted explicitly, and SHALL NOT be described
elsewhere in this specification as prevented:

- **Working-tree destruction.** Code running under `exec` may delete or corrupt any
  file in the working tree, including bulk deletion. Tracked content is recoverable
  from the read-only object store; untracked and ignored content is not.
- **Network exfiltration.** Brokered Git (§20) prevents unauthorized writes to
  authorized remotes. It does not prevent project code from transmitting repository
  contents to an attacker-controlled destination over the container's ordinary
  outbound network access.

Both are Tier-3 operations under §18.6: bounded by the container boundary and by
recoverability, not by policy.

These are accepted in exchange for a practical development environment.

---

## 40. Host Security Requirements

The production deployment SHOULD:

- run the MCP daemon under a dedicated host account where practical;
- restrict the daemon's readable repository roots;
- avoid running the daemon itself as host root unless Docker access requires it;
- use Docker group or rootless Docker where feasible;
- protect MCP transport authentication;
- firewall the service appropriately;
- avoid public unauthenticated exposure.

The daemon SHALL treat Docker control as a high-privilege capability.

---

## 41. Container Hardening

Even though project containers may run as root internally, the daemon SHOULD consider:

- dropping unnecessary Linux capabilities;
- `no-new-privileges` where compatible;
- seccomp;
- AppArmor/SELinux;
- PID limits;
- memory limits;
- CPU limits;
- read-only mounts for non-project resources;
- disabling privileged mode by default.

The repository bind mount remains writable.

---

## 42. Approval Model

The system SHOULD distinguish:

### Ordinary Operations

May execute without additional human approval after workspace authorization.

Examples:

- read/write repository files;
- install packages in the container;
- run tests;
- run linters;
- normal commit;
- fast-forward push to an authorized branch.

### Elevated Operations

Should require explicit policy authorization or user approval.

Examples:

- force push;
- hard reset;
- remote branch deletion;
- host device mounting;
- Docker socket mounting;
- privileged container;
- mounting additional host paths;
- writing outside authorized repository roots;
- `git_dir_mode: read-write` — a writable `.git` mount, which reopens the host
  code-execution path described in §18.3;
- `broker_hooks: enabled` — permitting repository-supplied hooks to run during
  brokered operations (§18.7);
- submodule initialization or update (§18.8).

An implementation MAY initially hard-deny these instead of implementing interactive approval.

The §19.1 credential invariant is not an elevated capability. It SHALL NOT be
granted by policy, approval, or configuration.

---

## 43. Git Default Branch Policy

The default branch deserves explicit handling.

Possible configuration:

```yaml
git_policy:
  default_branch:
    direct_commit: allow
    direct_push: allow_fast_forward
    force_push: deny
```

Some users prefer branch/PR workflows; others intentionally allow direct development on `master`.

The server SHALL make this configurable rather than imposing one workflow universally.

---

## 44. Recovery Behavior

### 44.1 Daemon Restart

On startup, the daemon SHOULD:

1. load workspace records;
2. inspect Docker containers carrying the server's labels;
3. reconcile missing/extra containers;
4. inspect running jobs if possible;
5. mark unrecoverable jobs as `lost`;
6. preserve repository state.

### 44.2 Container Crash

The workspace SHOULD transition to `stopped` or `failed`.

The repository remains intact on the host.

### 44.3 Host Restart

Persistent container metadata SHOULD permit workspaces to be reopened.

Containers MAY use Docker restart policies, but automatic restart is optional.

### 44.4 Interrupted Git Operation

The Git service SHALL detect states including:

- merge in progress;
- rebase in progress;
- cherry-pick in progress;
- unresolved conflicts.

It SHALL report these rather than proceeding as though the repository is normal.

---

## 45. Repository State Safety

Before high-impact operations, structured Git tools SHOULD capture:

- HEAD SHA;
- branch;
- dirty state;
- upstream;
- ahead/behind;
- remote identity.

Clients SHOULD be able to pass expected values.

Example:

```json
{
  "expected_branch": "master",
  "expected_head_sha": "..."
}
```

Mismatch SHALL fail closed.

This is particularly important when humans or multiple AI clients may edit concurrently.

---

## 46. Output and Context Efficiency

MCP responses SHALL be designed for AI context efficiency.

Bad behavior:

- returning entire 20 MB compiler logs;
- returning every file in `target/`;
- returning huge binary diffs.

Preferred behavior:

- bounded tail;
- structured summaries;
- pagination;
- explicit truncation;
- hash and metadata;
- follow-up retrieval tools.

Example:

```json
{
  "state": "failed",
  "exit_code": 1,
  "stdout_tail": "...",
  "stderr_tail": "...",
  "output_truncated": true,
  "next_offset": 65536
}
```

---

## 47. Binary Files

Direct editing APIs MAY reject arbitrary binary writes in the first release.

Binary file support MAY use:

- file upload artifacts;
- base64 with strict size limits;
- container-generated files.

The server SHALL avoid accidentally interpreting binary content as UTF-8 text.

---

## 48. Large Repository Handling

The server SHOULD avoid full-tree scanning by default.

Directory and search operations SHOULD support:

- path scope;
- glob filters;
- max results;
- pagination;
- hidden-file controls;
- ignore-file awareness.

`search_files` SHOULD preferably use `ripgrep` where available.

---

## 49. Ignore Rules

Search operations SHOULD honor `.gitignore` by default but allow override.

Directories commonly skipped by default MAY include:

```text
.git
target
node_modules
.venv
dist
build
.gradle
```

Explicit requests SHALL permit searching them.

---

## 50. Container Labeling

All MCP-managed containers SHOULD carry labels such as:

```text
com.example.mcp-workspace.managed=true
com.example.mcp-workspace.workspace-id=...
com.example.mcp-workspace.repository-id=...
```

This supports safe reconciliation and cleanup.

The daemon SHALL NOT manage unrelated Docker containers.

---

## 51. Naming

Container names SHOULD be deterministic and collision-safe.

Example:

```text
mcp-dev-project-gatsby-6f3a9c
```

Cache volume names SHOULD likewise include repository/workspace identity.

---

## 52. Configuration

A server configuration file MAY resemble:

```yaml
version: 1

repository_roots:
  - /home/phil/work

repositories:
  - id: chatgpt-ralph-loop-tools
    path: /home/phil/work/chatgpt_ralph_loop_tools
    github: ekkus93/chatgpt_ralph_loop_tools

docker:
  default_image: mcp-dev-ubuntu:24.04
  workspace_path: /workspace

execution:
  sync_timeout_seconds: 30
  default_job_timeout_seconds: 3600
  max_output_bytes: 10485760
  response_tail_lines: 200

git_policy:
  force_push: deny
  hard_reset: require_approval
  delete_remote_branch: require_approval
  validate_remote: true

security:
  allow_docker_socket: false
  allow_privileged_containers: false
  allow_additional_host_mounts: false

audit:
  enabled: true
  path: /var/log/dev-workspace-mcp/audit.jsonl
```

Configuration SHALL be validated at startup.

---

## 53. Secret Configuration

Secrets SHOULD be supplied through:

- protected files outside repository roots;
- environment variables;
- OS secret stores;
- GitHub App credential files with restricted permissions.

Secrets SHALL NOT live in project configuration committed to Git.

---

## 54. Suggested Implementation Language

The server can be implemented in any language with robust MCP and Docker support.

Practical choices include:

### Python

Advantages:

- rapid MCP implementation;
- strong subprocess and Docker ecosystem;
- easy schema validation.

### Rust

Advantages:

- strong type safety;
- efficient daemon;
- robust process control;
- single-binary deployment.

### TypeScript/Node.js

Advantages:

- strong MCP ecosystem;
- mature Docker libraries;
- rapid development.

Language choice is not architecturally significant.

---

## 55. Docker Control Options

The daemon MAY control Docker using:

1. Docker Engine HTTP API;
2. language-specific Docker SDK;
3. Docker CLI subprocesses.

A native API/SDK is preferred for structured lifecycle operations.

Using the CLI is acceptable for an MVP if command construction is safe and outputs are parsed carefully.

---

## 56. MCP Transport

The server MAY use any supported MCP transport appropriate for private deployment.

Security requirements:

- authentication;
- encrypted transport when crossing hosts;
- no public unauthenticated exposure;
- request-size limits;
- server-side timeouts;
- connection-level rate limiting if appropriate.

---

## 57. Functional Requirements

### FR-001 Repository Listing

The server SHALL list repositories available to the MCP client.

### FR-002 Repository Authorization

The server SHALL reject repositories outside configured authorization.

### FR-003 Workspace Creation

The server SHALL create a development workspace for an authorized repository.

### FR-004 Persistent Workspace

The server SHALL support persistent workspaces.

### FR-005 Ephemeral Workspace

The server SHALL support ephemeral workspaces.

### FR-006 Writable Repository Mount

The development container SHALL receive the repository as a writable bind mount.

### FR-007 File Read

The client SHALL be able to read repository files.

### FR-008 File Write

The client SHALL be able to create and replace repository files.

### FR-009 File Patch

The client SHALL be able to apply precise text patches.

### FR-010 File Delete

The client SHALL be able to delete repository files.

### FR-011 Path Isolation

Filesystem APIs SHALL prevent access outside the authorized repository.

### FR-012 Command Execution

The client SHALL be able to execute arbitrary development commands inside the workspace.

### FR-013 Package Installation

The workspace SHALL permit installation of additional development software.

### FR-014 Async Jobs

The system SHALL support commands that outlive individual MCP calls.

### FR-015 Job Polling

The client SHALL be able to retrieve job state.

### FR-016 Incremental Output

The client SHALL be able to retrieve bounded command output incrementally.

### FR-017 Job Cancellation

The client SHALL be able to cancel running jobs.

### FR-018 Git Status

The client SHALL receive structured Git status.

### FR-019 Git Diff

The client SHALL retrieve Git diffs.

### FR-020 Git Commit

The client SHALL be able to create Git commits.

### FR-021 Git Fetch/Pull

The client SHALL be able to synchronize from authorized Git remotes.

### FR-022 Git Push

The client SHALL be able to push authorized changes.

### FR-023 Git Remote Validation

The server SHALL validate remote repository identity before authenticated writes.

### FR-024 Git State Guards

Structured Git mutations SHOULD support expected branch and expected HEAD guards.

### FR-025 Credential Isolation

No credential capable of writing to an authorized remote SHALL be present inside a
development container, in its environment, in its filesystem, in its process
arguments, or in any file under a repository root (§19.1).

This requirement is unconditional. It is not qualified by "by default", and SHALL
NOT be relaxed by policy or configuration.

### FR-026 Docker Socket Isolation

Project containers SHALL NOT receive Docker daemon access by default.

### FR-027 Audit Logging

Important mutating operations SHALL be auditable.

### FR-028 Crash Recovery

Repository contents SHALL survive daemon/container failure.

### FR-029 Concurrent Modification Detection

The system SHOULD detect stale file or Git assumptions.

### FR-030 Diagnostic State

The client SHALL be able to query workspace/container/repository state.

### FR-031 Git Metadata Integrity

Repository Git metadata SHALL NOT be writable from a development container under
the default `git_dir_mode` (§18.2).

### FR-032 Hook Execution Control

Repository-supplied hooks SHALL NOT execute during brokered Git operations under
the default `broker_hooks` policy, including hooks reached through a
`core.hooksPath` that resolves inside the tracked working tree (§18.7).

### FR-033 Remote Transport Restriction

The Git broker SHALL reject remote URLs whose transport is capable of invoking a
local command, and SHALL invoke Git against a validated URL literal rather than a
remote name (§20.3).

### FR-034 Refspec Validation

Client-supplied refspecs SHALL be validated against policy rather than passed
through to Git (§20.4).

### FR-035 Mode Disclosure

`workspace_info` SHALL report `git_dir_mode`, `credential_mode`, and `broker_hooks`
(§25).

---

## 58. Non-Functional Requirements

### NFR-001 Reliability

Ordinary workspace operations SHOULD be deterministic and recoverable.

### NFR-002 Security

Unauthorized host paths and repositories SHALL fail closed.

### NFR-003 Performance

Common metadata operations SHOULD complete quickly without starting unnecessary processes.

### NFR-004 Context Efficiency

Tool responses SHALL use bounded, structured output.

### NFR-005 Portability

The architecture SHOULD support common Linux Docker hosts.

### NFR-006 Extensibility

New workspace providers and elevated capabilities SHOULD be addable without breaking the core tool contract.

### NFR-007 Observability

Failures SHOULD contain enough structured context for diagnosis.

### NFR-008 Auditability

Mutating and security-sensitive operations SHOULD be traceable.

---

## 59. MVP Scope

The first usable release SHOULD prioritize practical development value.

### Required MVP Capabilities

1. repository allowlist;
2. one persistent Docker container per active repository;
3. read/write working-tree bind mount with read-only `.git` (§18.2);
4. `workspace_open`;
5. `workspace_info`, reporting `git_dir_mode`, `credential_mode`, `broker_hooks`;
6. filesystem list/read/write/patch/delete;
7. synchronous command execution;
8. asynchronous job start/status/output/cancel;
9. Git status/diff/log;
10. stage/commit;
11. brokered fetch/pull/push (§20);
12. daemon-held repository-scoped GitHub authentication, with no credential present
    in any container (§19.1);
13. remote identity and refspec validation (§20.3, §20.4);
14. hooks disabled during brokered operations (§18.7);
15. path traversal protection;
16. Docker socket isolation;
17. bounded command output;
18. audit log;
19. container rebuild/reset.

Brokered Git is in the MVP rather than deferred. It is simpler to implement than
credential injection — the daemon already runs on the host and already owns the
checkout — and it is the mechanism by which FR-025 holds. Retrofitting it after a
release that injects credentials would require rebuilding the credential model, the
policy enforcement point, and the acceptance tests together.

Read-only `.git` is likewise in the MVP because it is a mount flag, not a
subsystem.

### Explicitly Defer From MVP

- physical device forwarding;
- GPU support;
- service port forwarding;
- multi-user authorization;
- Windows containers;
- Kubernetes;
- browser/desktop UI;
- full Dev Container specification compatibility;
- interactive shell terminal streaming;
- secrets management integrations;
- Docker-in-Docker;
- arbitrary extra host mounts;
- submodule support (§18.8, denied by default);
- `git_dir_mode: read-write` and `broker_hooks: enabled` elevated modes;
- interactive approval for `require_approval` policies, which MAY be implemented as
  hard denial in the first release.

---

## 60. Recommended Implementation Phases

### Phase 1 — Core Workspace

Implement:

- MCP server;
- repository configuration;
- Docker lifecycle;
- writable working-tree mount with read-only `.git` (§18.2);
- container-local Git environment — `GIT_OPTIONAL_LOCKS=0`, `safe.directory`;
- `workspace_open`;
- `workspace_info`, including `git_dir_mode`;
- filesystem tools;
- basic synchronous `exec`.

Acceptance:

- client can modify a real local repository and run a command in its container;
- AT-021 and AT-022 pass — `.git/hooks` and `.git/config` are not writable from
  inside the container.

### Phase 2 — Persistent Execution

Implement:

- persistent workspaces;
- async jobs;
- output paging;
- cancellation;
- command timeouts;
- job persistence.

Acceptance:

- long `cargo test` or Gradle builds survive individual MCP request timeouts.

### Phase 3 — Structured Git

Implement:

- the Git broker as the host-side execution path for all structured Git tools (§20);
- status;
- diff;
- history;
- staging;
- commit;
- branch operations;
- state guards;
- hook neutralization during brokered operations (§18.7).

Acceptance:

- client can safely implement, validate, and commit a change;
- AT-023 passes — a `core.hooksPath` hook inside the tracked working tree does not
  execute during a brokered commit.

### Phase 4 — GitHub Synchronization

Implement:

- brokered fetch;
- brokered pull, `ff_only` by default;
- brokered push;
- remote identity validation, including transport restriction and validated-URL
  invocation (§20.3);
- refspec validation (§20.4);
- daemon-held scoped authentication and credential handling (§19.4);
- protected destructive operations (§18.9).

Acceptance:

- client can publish a normal fast-forward commit to an authorized repository while
  no credential is present in any container;
- AT-011, AT-013, AT-024, AT-025, and AT-026 pass.

### Phase 5 — Environment Configuration

Implement:

- persistent caches;
- `.mcp-workspace.yml`;
- configurable images;
- setup hooks;
- initial Dev Container support.

Acceptance:

- Rust/Python/Node projects can define reusable development environments.

### Phase 6 — Hardening

Implement:

- audit improvements;
- resource controls;
- lock leases;
- recovery;
- security tests;
- symlink escape testing;
- secret redaction;
- disk management.

### Phase 7 — Advanced Development Features

Potentially add:

- port forwarding;
- GPU access;
- USB/ADB;
- service orchestration;
- workspace snapshots;
- ephemeral validation environments;
- artifact export.

---

## 61. Acceptance Test Scenarios

### AT-001 Repository Boundary

Given an authorized repository, attempts to read `../../etc/passwd` SHALL fail.

### AT-002 Symlink Escape

A repository symlink pointing outside the repository SHALL not permit unauthorized reads or writes.

### AT-003 File Modification Visibility

A file written through MCP SHALL immediately appear changed in the host checkout.

### AT-004 Persistent Package Install

Install a tool such as `cargo-nextest`, restart the MCP session, reopen the same persistent workspace, and verify the tool remains available.

### AT-005 Workspace Reset

Reset the workspace and verify repository contents remain intact while container-local installed state is removed.

### AT-006 Async Build

Start a long-running build, disconnect/reconnect the MCP client, and verify the job remains queryable.

### AT-007 Output Bounding

Run a command producing multi-megabyte output and verify normal responses remain bounded while incremental retrieval remains possible.

### AT-008 Git State Guard

Create a Git commit externally after the client reads HEAD, then attempt a guarded commit with the old expected HEAD. The operation SHALL fail.

### AT-009 Unauthorized Remote

Change `origin` to an unauthorized GitHub repository. A structured push SHALL fail.

### AT-010 Non-Fast-Forward Push

A normal push against a diverged remote SHALL fail without automatically forcing.

### AT-011 Force Push Policy

A structured force push SHALL be denied at the broker under the default policy.

Additionally, `exec("git push --force")` inside the development container SHALL fail
for lack of credentials, demonstrating that the denial does not depend on the client
choosing the structured path.

### AT-012 Docker Socket

Inside the project container, `/var/run/docker.sock` SHALL not be present by default.

### AT-013 Credential Isolation

Host SSH keys and GitHub CLI credentials SHALL NOT be mounted into any container.

No credential capable of writing to an authorized remote SHALL be discoverable from
inside the container by environment inspection, filesystem search under repository
roots, or process inspection — including during an in-flight brokered operation.

### AT-014 Container Root

The project container SHALL be capable of installing normal OS packages when configured for root operation.

### AT-015 Crash Preservation

Kill the development container while repository changes exist. Restart the workspace and verify repository changes remain.

### AT-016 Concurrent Modification

Read a file hash, modify the file externally, then submit a guarded write using the old hash. The server SHALL report a conflict.

### AT-017 Dirty Tree Protection

Attempt an operation configured to require a clean tree while unrelated uncommitted changes exist. The operation SHALL fail.

### AT-018 Git Conflict Reporting

Create a merge conflict and verify `git_status` reports conflict state without attempting automatic destructive repair.

### AT-019 Secret Redaction

Run a command containing a configured secret and verify audit output does not expose the secret value.

### AT-020 Repository Discovery Escape

A symlink beneath an allowed repository root pointing elsewhere SHALL not be treated as an authorized repository.

### AT-021 Git Hook Write

Writing `.git/hooks/pre-commit` from inside the development container SHALL fail
under the default `git_dir_mode`.

### AT-022 Git Config Write

Setting `core.fsmonitor` in `.git/config` from inside the development container
SHALL fail under the default `git_dir_mode`.

### AT-023 Hooks Path Escape

With `core.hooksPath` pointed at a directory inside the tracked working tree
containing an executable hook, a brokered commit SHALL NOT execute that hook under
`broker_hooks: disabled`.

### AT-024 Local-Command Transport

A remote URL of the form `ext::sh -c ...` SHALL be rejected before any authenticated
operation is attempted.

### AT-025 Remote Substitution Race

Changing `origin` between remote validation and push SHALL NOT affect the push
destination, which SHALL be the validated URL literal.

### AT-026 Refspec Validation

A push refspec with a leading `+` SHALL be rejected under `force_push: deny`.

### AT-027 Submodule URL Substitution

A `.gitmodules` URL pointing at an unauthorized repository SHALL NOT be fetched.

### AT-028 Read-Only Container Git

Under the default `git_dir_mode`, `git log` and `git rev-parse` SHALL succeed inside
the container while `git commit` and `git fetch` SHALL fail.

### AT-029 Mode Disclosure

`workspace_info` SHALL report `git_dir_mode`, `credential_mode`, and `broker_hooks`
values matching the workspace's actual configuration.

---

## 62. Example `workspace_open` Contract

Request:

```json
{
  "repository_id": "chatgpt-ralph-loop-tools",
  "mode": "persistent"
}
```

Response:

```json
{
  "workspace_id": "ws_chatgpt_ralph_loop_tools",
  "repository_id": "chatgpt-ralph-loop-tools",
  "container_state": "running",
  "workspace_path": "/workspace",
  "git_dir_mode": "read-only",
  "credential_mode": "github_app",
  "broker_hooks": "disabled",
  "git": {
    "branch": "master",
    "head_sha": "abc123...",
    "clean": true,
    "upstream": "origin/master"
  }
}
```

The security-mode fields are required by FR-035. A client SHALL be able to determine
from this response whether the workspace is running under any elevated capability
(§42).

---

## 63. Example `exec_start` Contract

Request:

```json
{
  "workspace_id": "ws_chatgpt_ralph_loop_tools",
  "argv": [
    "uv",
    "run",
    "--frozen",
    "pytest"
  ],
  "cwd": "/workspace",
  "timeout_seconds": 3600
}
```

Response:

```json
{
  "job_id": "job_b4dbdd",
  "state": "running",
  "started_at": "2026-08-26T..."
}
```

---

## 64. Example `exec_status` Contract

Response:

```json
{
  "job_id": "job_b4dbdd",
  "state": "running",
  "exit_code": null,
  "elapsed_seconds": 38.5,
  "stdout_tail": "tests/...\n",
  "stderr_tail": "",
  "stdout_total_bytes": 18428,
  "stderr_total_bytes": 0,
  "output_truncated": false
}
```

---

## 65. Example `git_commit` Contract

Request:

```json
{
  "workspace_id": "ws_chatgpt_ralph_loop_tools",
  "expected_branch": "master",
  "expected_head_sha": "abc123...",
  "paths": [
    "src/example.py",
    "tests/test_example.py"
  ],
  "message": "fix: validate workspace execution state"
}
```

Response:

```json
{
  "commit_sha": "def456...",
  "branch": "master",
  "files_committed": [
    "src/example.py",
    "tests/test_example.py"
  ]
}
```

---

## 66. Example `git_push` Contract

The `git_push` contract is normative in §20.5 and is not restated here. Push is a
brokered operation: the request is subject to remote identity validation (§20.3),
refspec validation (§20.4), and preconditions (§20.6), and the response reports the
credential mode in force.

---

## 67. Suggested Internal Data Model

### Repository

```text
Repository
- id
- canonical_path
- configured_remote
- default_branch
- policy_id
```

### Workspace

```text
Workspace
- id
- repository_id
- mode
- container_id
- image
- workspace_path
- state
- created_at
- last_used_at
```

### Job

```text
Job
- id
- workspace_id
- command
- cwd
- state
- pid
- started_at
- finished_at
- exit_code
- stdout_log_path
- stderr_log_path
```

### Lock

```text
WorkspaceLock
- workspace_id
- owner
- lease_id
- acquired_at
- expires_at
```

### AuditEvent

```text
AuditEvent
- id
- timestamp
- request_id
- session_id
- workspace_id
- operation
- result
- metadata
```

---

## 68. Database

A lightweight embedded database such as SQLite is sufficient for the initial release.

Suitable persisted state:

- workspace records;
- jobs;
- locks;
- audit indexes;
- configuration metadata.

Repository file contents SHALL remain in the filesystem rather than being copied into the database.

---

## 69. Docker Metadata Recovery

Container identity SHALL not depend solely on SQLite.

Docker labels SHALL permit reconstruction when metadata and Docker state diverge.

On reconciliation:

- unknown managed container -> inspect labels and recover or quarantine;
- recorded container missing -> mark workspace absent/stopped;
- multiple containers for one workspace -> fail closed and require reconciliation.

---

## 70. Testing Strategy

The project SHOULD maintain:

### Unit Tests

- path authorization;
- Git remote normalization;
- policy decisions;
- command argument validation;
- state transitions;
- secret redaction;
- configuration validation.

### Integration Tests

Using disposable Git repositories and Docker containers:

- filesystem mutation;
- package install;
- job lifecycle;
- container recovery;
- Git commit;
- push to disposable remote;
- conflicts;
- symlink escape;
- cache persistence.

### Security Tests

- path traversal;
- symlink escape;
- malicious remote URL;
- secret leakage;
- forbidden Docker socket;
- forbidden host mounts;
- destructive Git policy.

### End-to-End MCP Tests

A real MCP client harness SHOULD:

1. open repository;
2. modify source;
3. run tests;
4. create commit;
5. push to disposable remote;
6. verify result.

---

## 71. CI for the MCP Server

The MCP server's own CI SHOULD include:

- formatting;
- linting;
- static analysis;
- unit tests;
- Docker-backed integration tests;
- security acceptance tests;
- MCP schema validation;
- container image build;
- dependency vulnerability scanning where practical.

Docker integration tests SHOULD use disposable repositories and must never write to production repositories.

---

## 72. Documentation Requirements

The project SHOULD include:

```text
README.md
docs/SPEC.md
docs/SECURITY.md
docs/CONFIGURATION.md
docs/TOOLS.md
docs/DEVELOPMENT.md
docs/THREAT_MODEL.md
```

`README.md` should focus on setup and first use.

`SPEC.md` remains the authoritative behavioral design.

---

## 73. Operational Workflow

Typical startup:

```bash
dev-workspace-mcp serve --config /etc/dev-workspace-mcp/config.yml
```

The server:

1. validates configuration;
2. verifies Docker connectivity;
3. opens state database;
4. reconciles managed containers;
5. validates repository roots;
6. starts MCP transport.

A health check SHOULD report degraded state if Docker is unavailable.

---

## 74. Security Defaults

Two of these are invariants rather than defaults, and SHALL NOT be reconfigurable:

```text
Credentials in project container:   DENIED   (invariant, §19.1)
Host SSH directory mount:           DENIED   (invariant, §19.5)
Host gh config mount:               DENIED   (invariant, §19.5)
```

The remaining default configuration SHOULD choose:

```text
Docker socket in project container: DENIED
Privileged project container:       DENIED
Additional host mounts:             DENIED
Container .git write access:        DENIED   (git_dir_mode: read-only, §18.2)
Hooks during brokered operations:   DISABLED (broker_hooks: disabled, §18.7)
Submodule init/update:              DENIED
Non-https/ssh remote transports:    DENIED
Force push:                         DENIED
Force-with-lease push:              DENIED
Remote branch deletion:             DENIED
Remote tag deletion:                DENIED
Remote URL change:                  DENIED
Hard reset:                         DENIED or approval-required
Pull strategy:                      FF-ONLY
Outbound network:                   ALLOWED
Container root:                     ALLOWED
Working-tree write:                 ALLOWED
Container read-only Git access:     ALLOWED
Normal commit:                      ALLOWED
Normal authorized push:             ALLOWED
```

These defaults provide a powerful development environment without casually exposing host-level control.

---

## 75. Future Enhancements

Potential future capabilities include:

- Git worktree-based isolated concurrent agents;
- snapshot/restore;
- overlay-based disposable working trees;
- Nix/Guix environment support;
- GPU allocation;
- USB/ADB forwarding;
- browser test integration;
- containerized databases and sidecars;
- Docker Compose orchestration;
- Kubernetes-backed workspaces;
- remote workspace hosts;
- multi-user RBAC;
- per-tool interactive approvals;
- secrets vault integration;
- VS Code Dev Container parity;
- project environment fingerprints;
- automatic CI command discovery;
- patch checkpoints;
- reproducible workspace export;
- build artifact publication.

---

## 76. Future Worktree Isolation

A particularly useful later enhancement is a mode where the MCP server creates a dedicated Git worktree for an AI session instead of sharing the human's exact working tree.

Example:

```text
/home/phil/work/project              human checkout
/home/phil/.mcp/worktrees/project/1  ChatGPT worktree
```

This can reduce concurrent edit conflicts.

However, the initial design intentionally favors mounting the user's real repository because immediate interoperability and simplicity are core goals.

Worktree mode SHOULD therefore be optional rather than mandatory.

---

## 77. Decision Summary

The initial architecture SHALL follow these decisions:

1. The system is an MCP-controlled Docker development workspace.
2. The host repository is mounted read/write and remains the source of truth.
3. The MCP daemon, not the project container, controls Docker.
4. Project containers may run as root internally.
5. Project containers may install arbitrary development dependencies.
6. Outbound network access is normally available.
7. Persistent workspaces are the default.
8. Ephemeral workspaces are supported for reproducibility.
9. Long-running commands use persistent asynchronous jobs.
10. Command output is bounded and incrementally retrievable.
11. Filesystem operations are repository-scoped and symlink-safe.
12. Structured Git tools are provided in addition to general shell access.
13. Git mutations use state guards where practical.
14. Normal authorized Git pushes are supported.
15. Force pushes and other destructive operations are denied by default.
16. No credential capable of writing to an authorized remote is present in a project container, under any configuration.
17. All authenticated remote operations are brokered by the daemon on the host; GitHub authentication uses short-lived repository-scoped credentials, preferably through a GitHub App.
17a. Repository Git metadata is mounted read-only into project containers, because a writable `.git` is a host code-execution path independent of container escape.
18. The server is language-agnostic.
19. Dev Container support is desirable but not required for the earliest MVP.
20. The system complements rather than replaces Ralph Bridge.

---

## 78. Definition of Done for Initial Production-Usable Release

The first production-usable release is complete when ChatGPT can reliably perform the following workflow against an authorized real repository:

1. connect to the MCP server;
2. list/select the repository;
3. open its persistent development workspace;
4. inspect Git state;
5. pull current remote changes;
6. inspect and search project files;
7. modify repository files;
8. install missing development tools;
9. run project linters;
10. run project unit/integration tests;
11. start and monitor long-running builds;
12. inspect failures without receiving unbounded logs;
13. review Git diff;
14. create a commit with state guards;
15. push the commit to the configured GitHub repository;
16. reconnect later and reuse the same configured development environment;
17. perform all of the above without exposing the host Docker socket, arbitrary host filesystem paths, writable repository Git metadata, or any remote-write credential to project code.

At that point the server provides a practical replacement for the major software-development capabilities missing from a restricted generic AI sandbox.
