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

> **Non-normative.** Goals state intent. Requirements live in §57 and the design
> sections they cite; where a goal and a requirement differ, the requirement
> governs.

The system is intended to:

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
12. Keep the host user's own credentials out of project containers, and give each container only a dedicated key scoped to its repository — provisioned so that key material cannot be extracted by project code.
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

**Trust model.** The repositories this server operates on are the operator's own,
and the operator trusts them. The development container is part of the developer's
trust domain — it is a workstation the operator has handed to an AI client, not a
sandbox for code the operator would hesitate to run directly. Every default in
this specification follows from that: `.git` is writable, the container holds a
key that can push, and hooks run. Where a hardened mode exists (`git_dir_mode:
read-only`, §18.2), it is an option for the uncommon case, not a recommendation.

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

> **Rationale, not requirements.** These principles explain why the design is
> shaped as it is. The requirements they motivate are in §57 and the sections
> each principle cites; where a principle and a requirement differ, the
> requirement governs.

### 5.1 Local Checkout Is the Source of Truth

The host repository SHALL remain the canonical working tree.

The development container SHALL normally bind-mount the repository:

```text
HOST:      /home/user/work/project-a
CONTAINER: /workspace
```

The repository working tree SHALL be mounted read/write.

Repository Git metadata (`.git`) SHALL be mounted according to the workspace
`git_dir_mode` (§18.2), which defaults to read-write so that the full Git workflow —
commit and push included — works from inside the container. A read-only mode is
available for workspaces that run code the developer does not trust (§18.3).

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

### 5.3 Root Is Available Inside Containers; Host Files Are Never Root-Owned

Three identities can touch a repository: the human developer who owns the checkout,
the daemon that reads and writes it on the client's behalf (§13, §20.1), and processes inside the development
container that write to the bind mount. If those identities differ, the system
breaks in ways that are not security failures but are worse for being silent — a
container running as root leaves root-owned files in the human's checkout, the
human's editor cannot save over them, and the daemon's host-side Git reads and
filesystem writes trip over them.

The specification therefore fixes one identity for all three:

> Processes inside a development container SHALL run, by default, as the host
> uid/gid that owns the repository checkout. The daemon SHALL run as that same
> uid. Every file that appears in the host checkout — written by the human, the
> container, or the daemon — is owned by the same user.

Root privileges inside the container remain available. The client SHOULD be able to
perform operations such as:

```bash
sudo apt-get update
sudo apt-get install -y clang cmake protobuf-compiler
rustup component add clippy
cargo install cargo-nextest
npm install
uv sync
```

The first two need root and obtain it through passwordless `sudo` inside the
container. The rest install into the container user's home directory and need
nothing. Container-root privileges are acceptable because the stronger boundary is
between the container and the host; they are simply not the *default identity* of
processes that write into the bind mount.

Configuration:

```yaml
container:
  user: host          # host | root
  sudo: true
```

- `host` (default): the container user is created at container start with the
  checkout owner's uid/gid and a persistent home directory. `sudo` is available
  when enabled.
- `root`: processes run as uid 0. This mode SHALL be refused unless Docker
  user-namespace remapping is active, so that container uid 0 maps to an
  unprivileged host uid. Without remapping it would reintroduce root-owned host
  files, and the daemon SHALL fail workspace creation with
  `CONTAINER_CREATE_FAILED` rather than proceed.

Under `host`, the `safe.directory` entry in §18.2 is unnecessary for the container
user, but SHALL still be written because `sudo git ...` runs as root and Git's
ownership check applies to it.

### 5.4 Explicit Host Mounts Only

The daemon SHALL mount only:

- the selected repository;
- explicitly configured dependency caches;
- explicitly configured project resources;
- the workspace's `ssh-agent` socket, under `ssh.mode: agent` (§19.2);
- explicitly approved devices or other sockets, if supported later.

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

### 5.7 The Container Holds a Dedicated, Scoped Key — Never the Host's

The development container is a trusted development environment. It holds a
write-capable SSH key for its repository (§19), so `git push` works from inside the
container exactly as it would on a workstation.

What the architecture guarantees is not that the container lacks credentials, but
that the credential it has is the right one:

> The key in a container SHALL be dedicated to this system, scoped to the
> repositories the workspace is authorized for, revocable independently, and never
> the human user's own. Host credentials — `~/.ssh`, `gh` sessions, credential
> stores — SHALL NOT be mounted into any container (§19.4).

Because the container can push, destructive-operation policy on the structured Git
tools is a guardrail rather than a boundary (§18.1). Enforcement that binds every
path, including `exec`, lives in deploy-key scope (§18.4) and in rulesets on the
remote (§18.5).

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

### 6.6 SSH Key Provisioning / Remote Validation

Responsibilities:

- provision the workspace's dedicated SSH key into the container — via a
  per-workspace `ssh-agent` socket by default, or a read-only file (§19.2);
- pin GitHub host keys and write the container-local SSH client configuration
  (§19.3);
- refuse to provision any host-user credential (§19.4);
- validate remote repository identity for structured operations (§20.2);
- verify and report remote-side branch protection, using an optional read-only
  API credential (§18.5, §20.4);
- audit SSH authentication events under `agent` mode (§20.7).

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
  "git_dir_mode": "read-write",
  "container_user": "1000:1000",
  "ssh_key_mode": "agent",
  "ssh_key_scope": "repository",
  "created_at": "...",
  "last_used_at": "..."
}
```

### 8.1 Workspace Modes

#### Persistent

Persistent workspaces are reused across sessions.

Installed packages and toolchains remain available until the workspace is rebuilt or deleted.

**Idle stop.** A persistent container SHALL be *stopped* — not deleted — after it
has been idle for `workspace.idle_stop_after`, defaulting to 4 hours. Idle means
no MCP request touched the workspace and no job is running or queued in it. The
container filesystem is kept, so installed tooling survives; the next
`workspace_open` starts it again, which takes seconds rather than a rebuild.
A running job SHALL always hold the container up. `idle_stop_after: never`
disables the behaviour. The stop SHALL be audited and SHALL release the
workspace's `ssh-agent` per §19.2.

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
- `sudo`, configured passwordless for the container user (§5.3)
- `openssh-client` (§19.3)

The image SHOULD NOT bake in a specific uid. The container user is created at
container start to match the checkout owner (§5.3), so the image needs only the
machinery to do that — `useradd`, `sudo`, and a writable home-directory parent.

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
  - path: $HOME/.cargo
    name: cargo
  - path: $HOME/.rustup
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

Examples, expressed relative to the container user's home directory (§5.3):

```text
$HOME/.cargo
$HOME/.rustup
$HOME/.cache/uv
$HOME/.cache/pip
$HOME/.npm
$HOME/.pnpm-store
$HOME/go/pkg/mod
$HOME/.gradle
```

Cache volumes SHALL be owned by the container user's uid so that toolchains
installed without `sudo` can write to them.

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

### 12.1 Git Metadata Exclusion

Repository Git metadata lies inside the repository root, so the boundary check above
does not protect it. It SHALL be protected separately, because a client-controlled
write into `.git` is the host code-execution path described in §18.3 — the same
vector the read-only mount closes for the container, reached through a different
door. A prompt-injected client is in the threat model (§39), so the filesystem tools
SHALL enforce this regardless of whether they execute host-side or in-container.

Filesystem tools SHALL reject, with `PATH_GIT_METADATA`, any request whose
canonicalized path:

1. is `.git` at the repository root, or lies beneath it;
2. contains a path component named `.git` at any depth — this covers submodule
   Git directories and nested repositories;
3. resolves, through a gitfile (`.git` as a file containing `gitdir: <path>`), to
   the referenced Git directory or anything beneath it, wherever that directory
   lives.

This applies to every tool that writes, renames, deletes, or patches. Read tools
MAY expose Git metadata read-only where a client has a legitimate need, but the
default SHOULD be to exclude it from `list_files` and `search_files` output as
well, since it is never useful as editable content.

Rule 3 matters for linked worktrees and submodules, whose Git directories live
outside the checkout (§76). The daemon SHALL resolve the gitfile on workspace open,
record the resolved Git directory in the workspace record, and treat it as
protected for the workspace's lifetime.

`exec` is not covered by this rule. Under the default `git_dir_mode: read-write`
(§18.2) `exec` can write `.git`, and this exclusion is therefore hygiene rather than
a security boundary: it stops the structured tools from being used — by accident or
by a prompt-injected client — to damage metadata that they have no legitimate reason
to touch, and it keeps every structured write auditable as a working-tree write.
Under `git_dir_mode: read-only` the two together close every client-reachable write
path to Git metadata.

### 12.2 File Size Limits

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

### 13.6 Renames and Metadata

There is no `move_file` or `file_stat` tool (§34.2). Renames are
`exec("git mv a b")`. Metadata and content hash are returned by `list_files` and
`read_file`; `read_file` SHALL include the file's `sha256` in its response so the
value can be passed back as `expected_sha256` on a later write.

### 13.7 `search_files`

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

### 15.6 Job Log Storage

§15.5 bounds what a *response* carries. This section bounds what is kept.

**Location.** The daemon SHALL capture each job's stdout and stderr from the
container exec stream and write them to host-side files under its own state
directory:

```text
<state_dir>/jobs/<job_id>/stdout.log
<state_dir>/jobs/<job_id>/stderr.log
```

Logs SHALL NOT be written inside the repository (they would show up as untracked
files and in searches) and SHALL NOT be written inside the container (they would
be lost on rebuild, and a container process could tamper with them). The files are
owned by the daemon uid and live outside all repository roots.

**Per-job cap.** `execution.max_output_bytes` is the on-disk cap **per stream per
job**. When a stream reaches it the daemon SHALL:

- stop appending to the file;
- keep counting, so `stdout_total_bytes` reports the true size;
- keep the final `execution.tail_keep_bytes` of the stream in a ring buffer and
  append it to the file when the job ends, so the *end* of a huge build log — where
  the failure is — is always available;
- set `output_truncated: true` on the job, and mark the gap in the file with a
  single line: `--- <N> bytes omitted ---`.

**Retention.** Completed jobs' logs are pruned on a schedule and on demand:

```yaml
execution:
  max_output_bytes:      10485760   # 10 MiB per stream per job, on disk
  tail_keep_bytes:       262144     # 256 KiB ring buffer kept past the cap
  response_tail_lines:   200        # per response, §15.5
  job_retention_days:    7          # completed jobs older than this are pruned
  job_log_max_total_bytes: 2147483648   # 2 GiB across all jobs; oldest completed pruned first
```

Running jobs SHALL never be pruned. A job whose logs have been pruned SHALL still
be queryable by `exec_status`, with `logs_pruned: true` and its final exit code
and byte counts intact.

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

Structured Git tools execute inside the development container, as the container
user, with the container's SSH key (§20.1). They are a validated, policy-checked,
audited front-end over the same Git that `exec` reaches; they do not hold a
credential `exec` lacks, and they are not a boundary (§18.1). Read-only queries MAY
run host-side for speed. Clients SHOULD prefer the structured tools because their
responses are bounded and structured, their mutations carry state guards, and their
use is auditable — not because `exec` is prevented from doing the same things.

### 17.1 Required Git Operations

The server SHALL expose six structured Git tools (§34.1):

- `git_status` — structured state, conflict detection, ahead/behind;
- `git_diff` — bounded, path-filtered, stat mode;
- `git_log` — bounded history;
- `git_commit` — explicit paths, HEAD and branch guards, strict mode;
- `git_pull` — remote validation, `ff_only` by default;
- `git_push` — remote validation, refspec validation, policy, protection report.

Every other Git operation — branch creation, checkout, restore, fetch, show, tag,
merge, rebase — is performed through `exec("git …")`. Under the trusted-container
model (§18.1) a structured wrapper would add no guard, policy, or credential that
`exec` lacks; §34.2 records the mapping.

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

### 18.1 The Trusted-Container Model

The development container holds a write-capable SSH key for its repository (§19).
This is a deliberate design decision, and its consequence is stated plainly rather
than papered over:

> The container can push. `exec("git push")` works, and so does
> `exec("git push --force")`. No daemon sits between the container and the remote.

Structured Git tools therefore cannot enforce anything that `exec` can bypass, and
§14 guarantees that `exec` can run any command. Policy applied at the structured
tools (§18.9) is a guardrail that keeps a well-behaved client on the rails. It is
not a boundary.

Real enforcement lives in two places that `exec` cannot route around:

1. **what the key can reach** — a deploy key is installed on specific repositories,
   so the container can write only to those (§19.1); and
2. **what the remote will accept** — GitHub rulesets and branch protection reject
   force pushes, deletions, and unreviewed default-branch pushes regardless of
   which client or path issued them (§18.5).

Operations divide into three tiers by how they are actually enforced:

```text
Tier 1 — Structurally prevented
  The container lacks the capability. No policy check is required.
  Enforced by: mount boundaries, absent host credentials, deploy-key scope.

Tier 2 — Remote-enforced
  The container can attempt it; GitHub refuses it.
  Enforced by: rulesets / branch protection on the remote. Applies to every
  client and every path, including exec.

Tier 3 — Advisory
  Reachable from exec by design. The structured tools apply policy; exec
  does not. Documented, audited where observable, recoverable where possible.
```

### 18.2 Container Git Access

The repository working tree SHALL be bind-mounted read/write as specified in §5.1.

The repository Git directory SHALL be mounted according to `git_dir_mode`:

```yaml
container:
  git_dir_mode: read-write    # read-write | read-only | none
```

`read-write` SHALL be the default. Committing and pushing from `exec` require it.

| Mode | `.git` | Use |
|---|---|---|
| `read-write` | bind-mounted `rw` | Default. Full Git workflow inside the container. |
| `read-only` | bind-mounted `ro` over the parent mount | Hardened fetch-only workspace. Commit and push from `exec` fail; structured tools (§20.1) also run in-container and so also fail — this mode is for validation and CI-like runs, not development. |
| `none` | masked with an empty read-only tmpfs | Git is not visible inside the container. Because `.git` is a subpath of the working-tree mount, it cannot be "not mounted"; it SHALL be masked. |

The container user (§5.3) SHALL own `.git` on the host, so no ownership mismatch
arises. The daemon SHALL nonetheless write a container-local `safe.directory`
entry for `/workspace`, because `sudo git ...` runs as uid 0 and Git's ownership
check applies to it:

```text
/etc/gitconfig  (container-local, never in the bind mount)
  [safe]
      directory = /workspace
```

`GIT_TERMINAL_PROMPT=0` SHALL be set so that a misconfigured remote fails rather
than hanging on a credential prompt.

**Gitfiles.** In a linked worktree, `.git` is not a directory but a file
containing `gitdir: /abs/path/to/main/.git/worktrees/<name>`, and that directory
in turn holds a `commondir` file pointing back to the main repository's `.git`.
Both live outside the checkout, so a bind mount of the checkout alone leaves Git
inside the container unable to find its repository.

On `workspace_open` the daemon SHALL detect a gitfile and, when present:

1. resolve `gitdir:` and, through it, `commondir`, to the main repository's `.git`;
2. verify the resolved path is under an authorized repository root;
3. bind-mount the main repository's `.git` directory into the container **at the
   same absolute path it has on the host**, honouring `git_dir_mode`, so that the
   absolute path in the gitfile and the relative path in `commondir` both resolve
   unchanged;
4. record the resolved Git directory in the workspace record and protect it under
   §12.1.

Mounting at the host path rather than rewriting the gitfile is deliberate: the
gitfile is shared with the host and rewriting it would break the human's checkout.
Submodule gitfiles (`gitdir: ../.git/modules/<name>`) are relative and inside the
checkout, and need no special handling.

### 18.3 Git Metadata as a Host Code-Execution Path

A writable `.git` directory is a host code-execution path, independent of any
container escape. Repository Git metadata is interpreted by Git processes running on
the host, under the human user's identity, whenever a developer, IDE, file watcher,
or other AI client touches the repository.

Container-writable `.git` permits:

```text
.git/hooks/pre-commit          executes on the host on the next human commit
.git/config core.fsmonitor     executes on the host on nearly every git invocation
.git/config core.sshCommand    executes on the host on the next remote operation
.git/config alias.*            executes on the host when the alias is invoked
.git/config filter.*.clean     executes on the host on checkout/add
.git/config diff.*.textconv    executes on the host on diff
.git/config credential.helper  substitutes a credential source on the host
```

Under the default `git_dir_mode`, this is an **accepted risk** (§39.2). The
trusted-container model treats the container as part of the developer's trust
domain: it operates on the developer's own repositories with a key the developer
issued for it. Code that can poison `.git` from the container could equally have
been run by the developer on the host.

The risk is bounded by three things this specification does require:

- the key in the container is never the human's own key (§19.4), so poisoned
  metadata cannot obtain a credential the container did not already have;
- the structured filesystem tools refuse `.git` (§12.1), so a confused or
  prompt-injected *client* cannot reach it except by deliberately using `exec`;
- `git_dir_mode: read-only` remains available for workspaces that run code the
  developer does not trust (§18.2).

### 18.4 Tier 1 — Structurally Prevented

The following SHALL be unreachable from the development container and SHALL
require no policy check:

- access to host paths outside the repository mount and configured caches (§5.4);
- the Docker socket (§5.2);
- the host user's own credentials — `~/.ssh`, `gh` configuration, git credential
  stores (§19.4);
- writing to any repository the workspace's deploy key is not installed on
  (§19.1, `ssh.scope: repository`);
- extraction of private key material, under `ssh.mode: agent` (§19.2) — the
  container can *use* the key while the agent socket is mounted, but cannot read
  it.

`workspace_info` SHALL report `ssh_key_mode` and `ssh_key_scope` so that a client
can determine which of these hold for the current workspace.

### 18.5 Tier 2 — Remote-Enforced

The following SHALL be enforced by GitHub rulesets or branch protection on the
remote, because that is the only enforcement point a container holding a
write-capable key cannot bypass:

- force push to protected branches;
- branch deletion;
- tag deletion and non-fast-forward tag updates;
- direct push to the default branch, where the developer wants a PR workflow;
- required status checks and reviews.

The daemon SHALL treat remote protection as part of the workspace's security
posture, not as someone else's problem:

- With a read-only API credential configured (§19.5), the daemon SHALL query the
  repository's rulesets and branch protection for the default branch on
  `workspace_open`, and SHALL report the result as `remote_protection` in
  `workspace_info` (§20.4).
- `git_policy.require_remote_protection` governs what happens when protection is
  absent:

  ```yaml
  git_policy:
    require_remote_protection: warn    # off | warn | deny
    remote_protection_checks: [force_push, deletion]
  ```

  - `off`: not checked.
  - `warn` (default): `workspace_open` succeeds; `remote_protection.status` is
    `missing` and the response carries a warning.
  - `deny`: the daemon SHALL NOT provision a write-capable key for a repository
    whose default branch lacks the listed protections. `workspace_open` fails
    with `REMOTE_PROTECTION_MISSING`. This is the strongest guarantee available
    in this model: the container never receives a key that could force-push
    somewhere a force push would be accepted.

Without an API credential, `remote_protection.status` SHALL be `unverified`, and
`deny` SHALL be treated as `warn` with the reason reported.

### 18.6 Tier 3 — Advisory

The following are reachable from `exec` by design:

- every operation that §18.9 configures — force push, hard reset, clean, rebase,
  remote URL change — when issued through `exec` rather than a structured tool;
- deletion or corruption of working-tree files, including bulk deletion;
- deletion of untracked and ignored files, which are not recoverable from Git;
- writing to `.git` under the default `git_dir_mode` (§18.3);
- outbound network use, including transmission of repository contents to an
  attacker-controlled destination.

Structured-tool policy (§18.9) applies to the structured tools and not to `exec`.
The acceptance tests in §61 exercise the structured path and the remote-enforced
path separately, and none of them asserts that `exec` is prevented from anything in
this list.

### 18.7 Hooks

Repository hooks are a normal part of a development workflow — `pre-commit`
formatters and linters are a feature, not a threat — and the trusted-container
model runs them.

Structured Git tools execute inside the container (§20.1), so hooks run there,
under the container user, with the same reach as any other `exec`. The daemon
SHALL NOT disable them by default.

`git_commit` and `git_push` MAY accept a `no_verify: true` parameter to skip hooks
for a single operation, matching `--no-verify`.

Hooks are never executed by the daemon on the host. The daemon runs no Git
commands against the checkout that could trigger them; the structured `git_status`
and `git_diff` reads it performs host-side (§20.1) SHALL be invoked with
`-c core.hooksPath=<empty-dir> -c core.fsmonitor=` so that a read cannot execute
repository-supplied code under the daemon's identity.

### 18.8 Submodules

Submodule URLs originate in `.gitmodules`, a tracked and therefore
container-writable file.

Under `ssh.scope: repository` (default), the workspace key is installed only on
the primary repository, so submodules pointing at other private repositories
cannot be fetched — a structural consequence of key scope, not a policy. Public
submodules fetch normally over HTTPS.

The fetch step of structured `git_pull` SHALL pass `--no-recurse-submodules`, and
structured `git_push` SHALL pass `--recurse-submodules=no`. Structured submodule initialization and
update SHALL validate each submodule URL under §20.2 and SHALL be governed by
`git_policy.submodules`.

### 18.9 Structured-Tool Policy Configuration

This configuration applies to the structured Git tools only (§18.6).

```yaml
git_policy:
  # remote write — structured tools
  push:                       allow
  default_branch_commit:      allow                # allow | deny
  default_branch_push:        allow_fast_forward   # allow | allow_fast_forward | require_approval | deny
  force_push:                 deny
  force_with_lease:           deny
  delete_remote_branch:       require_approval
  delete_remote_tag:          deny
  change_remote_url:          deny

  # local destructive — structured tools
  hard_reset:                 require_approval
  clean_untracked:            require_approval
  rebase:                     require_approval
  amend_published:            deny

  # sync strategy
  pull_strategy:              ff_only              # ff_only | merge | rebase
  submodules:                 deny                 # deny | require_approval | allow

  # remote-side enforcement (§18.5) — applies to every path, including exec
  require_remote_protection:  warn                 # off | warn | deny
  remote_protection_checks:   [force_push, deletion]

  # validation
  validate_remote:            true
  allowed_url_schemes:        [ssh, https]
```

`require_approval` MAY be implemented as a hard denial in the first release (§42).

The default-branch question SHALL remain configurable rather than imposed, per §43.
Where a developer wants the default branch genuinely protected against every
client, the place to express that is a GitHub ruleset, and
`require_remote_protection: deny` makes the daemon insist on it.

---

## 19. Credential Model

GitHub synchronization is required. The development container SHALL hold a
write-capable SSH key for its repository, so that the full Git workflow — including
push — works from `exec` exactly as it would on a developer workstation.

The credential model is therefore not about keeping credentials out of the
container. It is about ensuring the credential in the container is **dedicated,
scoped, revocable, and never the human's own**.

### 19.1 The Key

The key provisioned into a workspace SHALL be:

- **dedicated** — generated for this system, used for nothing else;
- **scoped** — installed on the repositories this workspace is authorized for, and
  no others;
- **revocable** — removable from GitHub without affecting any other access;
- **never the human's personal key** — the host user's `~/.ssh` is never mounted
  (§19.4), and the daemon SHALL refuse to provision a key file that resolves to a
  path under the host user's home `.ssh` directory.

```yaml
ssh:
  scope: repository      # repository | shared
```

- `repository` (default): one **deploy key** per repository, with write access
  enabled on GitHub. GitHub deploy keys are inherently per-repository, so the
  workspace for repository A structurally cannot write to repository B. This is
  the Tier-1 scoping guarantee in §18.4.
- `shared`: one key — typically a machine-user's key — installed on every
  configured repository. Simpler to manage; loses structural per-repository
  scoping. `workspace_info` SHALL report `ssh_key_scope: shared` so a client knows
  the guarantee is absent.

Key files live host-side, outside all repository roots, mode `0600`, owned by the
daemon uid:

```yaml
ssh:
  keys_dir: /etc/dev-workspace-mcp/keys
repositories:
  - id: project-gatsby
    path: /home/phil/work/project-gatsby
    github: ekkus93/project-gatsby
    ssh_key: project-gatsby.ed25519     # relative to keys_dir; omit under scope: shared
```

### 19.2 Provisioning

```yaml
ssh:
  mode: agent            # agent | file
```

#### `agent` (default)

The daemon runs a dedicated `ssh-agent` per workspace, loads the workspace key into
it, and bind-mounts the agent's socket into the container:

```text
HOST:      <daemon runtime dir>/ws_project_gatsby/agent.sock
CONTAINER: /run/mcp/ssh/agent.sock
ENV:       SSH_AUTH_SOCK=/run/mcp/ssh/agent.sock
```

Properties:

- private key material never enters the container filesystem, environment, or
  process arguments — the container can request signatures, not read the key;
- the key is usable only while the workspace is running; the daemon SHALL remove
  it from the agent on `workspace_stop` and terminate the agent on
  `workspace_delete`;
- every signature request is observable by the daemon, which yields an audit
  record per authenticated SSH connection, including those initiated from `exec`
  (§20.7);
- a later version MAY require daemon-side confirmation per signature, which would
  provide a coarse approval gate over every authenticated remote operation
  regardless of path (§75).

#### `file`

The private key is bind-mounted read-only into the container:

```text
HOST:      /etc/dev-workspace-mcp/keys/project-gatsby.ed25519
CONTAINER: $HOME/.ssh/id_mcp     (mode 0600, owned by the container user)
```

Properties:

- simplest possible setup; no agent lifecycle;
- key material is readable by any process in the container, including repository
  build scripts — a compromised dependency can copy it;
- `exec`-path remote operations are not observable by the daemon.

`file` mode is acceptable in the single-user private deployment (§4). `workspace_info`
SHALL report `ssh_key_mode: file` so a client knows extraction is possible.

### 19.3 SSH Client Configuration

The daemon SHALL write a container-local SSH client configuration for the container
user — into the container filesystem, never into the bind mount:

```text
$HOME/.ssh/config
  Host github.com
      User git
      IdentitiesOnly yes
      IdentityFile ~/.ssh/id_mcp          # file mode only
      StrictHostKeyChecking yes
      UserKnownHostsFile ~/.ssh/known_hosts_mcp
```

`known_hosts_mcp` SHALL contain GitHub's published SSH host keys, pinned. The
daemon SHALL NOT rely on trust-on-first-use, and SHALL NOT honour a `known_hosts`
or SSH configuration file from inside the repository.

`git config --system url.<base>.insteadOf` MAY be written container-locally to
route HTTPS GitHub URLs over SSH, so that a repository whose `origin` is HTTPS still
authenticates with the workspace key.

### 19.4 Host Credentials

The daemon SHALL NOT mount into any development container:

```text
~/.ssh
~/.config/gh
~/.git-credentials
~/.netrc
~/.docker/config.json
```

This is an invariant, not a default: the container receives the key the developer
issued for it (§19.1) and nothing that belongs to the developer personally.

`gh` MAY be installed inside a container and MAY be authenticated there with a
dedicated fine-grained token scoped to the configured repositories, under the same
rules as the SSH key. It SHALL NOT be authenticated with the host user's `gh`
session. GitHub operations beyond ordinary Git — pull requests, CI inspection,
merges — are the responsibility of Ralph Bridge (§21).

### 19.5 Optional Daemon API Credential

The daemon MAY hold a **read-only** GitHub credential — a fine-grained token or a
GitHub App installation — for:

- verifying rulesets and branch protection (§18.5, §20.4);
- populating `workspace_info` with default branch and remote metadata;
- confirming that a configured deploy key is installed on the repository it is
  meant for.

This credential SHALL NOT have `contents: write` or any permission that could push.
It lives host-side, outside all repository roots, and SHALL NOT be mounted into or
otherwise exposed to any container. If it is absent, the features above degrade to
`unverified` rather than failing.

### 19.6 Configuration

```yaml
ssh:
  mode: agent
  scope: repository
  keys_dir: /etc/dev-workspace-mcp/keys
  known_hosts: /etc/dev-workspace-mcp/github_known_hosts

github:
  api_auth: token                 # none | token | app
  api_token_path: /etc/dev-workspace-mcp/github-readonly.token
  repositories:
    - ekkus93/chatgpt_ralph_loop_tools
    - ekkus93/project-gatsby
```

### 19.7 What the Key Can and Cannot Do

Stated once, so that no other section overclaims:

| | |
|---|---|
| Push to the workspace's repository | **Yes**, from any path including `exec` |
| Force-push to the workspace's repository | **Yes**, unless a ruleset on the remote refuses it (§18.5) |
| Push to any other repository | **No**, under `scope: repository` — the key is not installed there |
| Be extracted by repository code | **No** under `agent` mode; **yes** under `file` mode |
| Be the developer's personal key | **No**, ever |
| Survive workspace stop | **No** under `agent` mode; the file persists host-side under `file` mode |

---

## 20. Git Execution and Remote Validation

### 20.1 Execution Location

Structured Git tools SHALL execute inside the development container, through the
execution service (§14), as the container user, using the container's SSH key.
They are a structured, validated, audited front-end over the same Git the client
could reach through `exec` — not a second execution path with a second credential.

Rationale: once the container holds a write-capable key, a host-side broker with
its own credential adds a second credential to protect and no enforcement that the
first path does not already lack. Keeping one execution identity (§5.3) and one
credential (§19.1) keeps the model honest and the implementation small.

Exceptions — read-only queries the daemon MAY perform host-side for speed, without
starting a container:

- `git_status`, `git_diff`, `git_log`.

Host-side reads SHALL be invoked with `-c core.hooksPath=<empty-dir>
-c core.fsmonitor=` (§18.7) and SHALL NOT take the index lock
(`GIT_OPTIONAL_LOCKS=0`). Host-side reads run as the daemon uid, which is the
checkout owner (§40).

### 20.2 Remote Identity Validation

Before any structured operation that contacts a remote, the daemon SHALL:

1. resolve the remote name to its configured URL via `git remote get-url <name>`;
2. reject the URL unless its scheme appears in `git_policy.allowed_url_schemes`;
3. explicitly reject `ext::`, `file://`, and any transport capable of invoking a
   local command, by both scheme check and `-c protocol.ext.allow=never`;
4. normalize the URL to `owner/repo` form per §7.3;
5. compare the normalized value against the repository's configured GitHub identity
   and against `github.repositories`;
6. fail with `GIT_REMOTE_NOT_ALLOWED` on any mismatch.

The structured operation then runs against the configured remote, over SSH, with
the workspace key. Where the client supplies `expected_remote_repository`, a
mismatch SHALL fail the operation regardless of server-side configuration.

This validation catches mistakes and confused clients. It does not bind `exec`. The
structural backstop for `exec` is key scope (§18.4): a substituted remote pointing
at a repository the key is not installed on fails at GitHub.

The daemon SHALL reject any client-supplied `--upload-pack`, `--receive-pack`, or
`--exec` value on structured operations.

### 20.3 Refspec Validation

Client-supplied refspecs on structured operations SHALL be validated rather than
passed through.

The daemon SHALL reject:

- a leading `+` (forced update) unless force policy permits it;
- an empty source (branch or tag deletion) unless deletion policy permits it;
- `--mirror`, `--all`, and `--tags` in combination with force;
- any refspec whose destination is outside `refs/heads/` or `refs/tags/`.

Structured push SHALL use an explicit refspec rather than relying on
`push.default`:

```text
git -c protocol.ext.allow=never \
    push --atomic --recurse-submodules=no \
    origin refs/heads/<branch>:refs/heads/<branch>
```

### 20.4 Remote Protection Verification

With a read-only API credential (§19.5), the daemon SHALL verify on
`workspace_open`, and MAY re-verify before structured push, that the repository's
default branch is protected against the operations listed in
`git_policy.remote_protection_checks`.

The result SHALL be reported in `workspace_info` (and therefore `workspace_open`)
and in the `git_push` response:

```json
{
  "remote_protection": {
    "status": "verified",          // verified | missing | unverified
    "default_branch": "master",
    "force_push_blocked": true,
    "deletion_blocked": true,
    "requires_pull_request": false,
    "checked_at": "..."
  }
}
```

Behaviour when protection is missing is governed by
`git_policy.require_remote_protection` (§18.5).

### 20.5 Structured Operation Contracts

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
  "expected_local_head_sha": "def456...",
  "no_verify": false
}
```

Response:

```json
{
  "remote_repository": "ekkus93/chatgpt_ralph_loop_tools",
  "branch": "master",
  "pushed_sha": "def456...",
  "fast_forward": true,
  "ssh_key_mode": "agent",
  "ssh_key_scope": "repository",
  "remote_protection": { "status": "verified", "force_push_blocked": true }
}
```

### 20.6 Preconditions

Because the working tree is shared with container processes and with human
developers, structured mutating operations SHALL evaluate preconditions immediately
before acting, and SHALL fail closed:

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

Each structured Git operation SHALL emit an audit record:

```json
{
  "timestamp": "...",
  "request_id": "...",
  "session_id": "...",
  "workspace_id": "...",
  "repository_id": "...",
  "operation": "git_push",
  "remote_repository": "ekkus93/chatgpt_ralph_loop_tools",
  "refspec": "refs/heads/master:refs/heads/master",
  "policy_decision": "allow",
  "remote_protection_status": "verified",
  "result": "success",
  "resulting_sha": "def456..."
}
```

Under `ssh.mode: agent`, the daemon SHALL additionally emit an `ssh_auth` record
for every signature request the agent serves:

```json
{
  "timestamp": "...",
  "workspace_id": "...",
  "operation": "ssh_auth",
  "key_fingerprint": "SHA256:...",
  "requesting_pid": 4121,
  "requesting_command": "git push origin master"
}
```

This is the only visibility the daemon has into `exec`-path remote operations. It
records that an authenticated connection was made, not what was pushed; the
`requesting_command` field is best-effort from `/proc` and MAY be absent. Under
`ssh.mode: file`, `exec`-path remote operations are not observable by the daemon,
and the audit log SHALL NOT be described as complete for that mode.

Denials SHALL be audited with the same fidelity as successes, including the policy
key that produced the denial. Audit records SHALL NOT contain key material.

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
- `ssh_key_mode` and `ssh_key_scope` (§19);
- `remote_protection` (§20.4).

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

Memory, as distinct from disk, is reclaimed by the idle-stop policy in §8.1: a
stopped container holds no RAM, and its filesystem remains until pruned here.

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
PATH_GIT_METADATA
GIT_REMOTE_NOT_ALLOWED
GIT_REFSPEC_NOT_ALLOWED
GIT_AUTH_FAILED
REMOTE_PROTECTION_MISSING
SSH_KEY_PROVISION_FAILED
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

## 33. Tool Contracts and Versioning

### 33.1 The Contract Is `tools/list`

MCP already defines a machine-readable contract: the `tools/list` response, in
which every tool carries a JSON Schema `inputSchema`. This specification does not
duplicate it. The implementation is the source of truth for tool schemas, and the
spec constrains them by behaviour (§13, §14, §15, §17, §20, §62–§66) rather than
by restating every field.

To make that contract reviewable and stable rather than whatever the code happens
to emit, the server SHALL:

1. export its complete `tools/list` response — including an `outputSchema` for
   every tool that returns structured content — to `schemas/tools.json` in the
   repository, via a build or CLI command such as
   `dev-workspace-mcp export-schemas`;
2. commit that file, so that every change to a tool's contract appears as a diff
   in review;
3. fail CI when the exported file differs from the committed one (§71), so that a
   contract cannot change silently.

The examples in §62–§66 are illustrative. `schemas/tools.json` governs.

### 33.2 Versioning

The exported contract SHALL carry a `contract_version` in `server_info`.

A change to `schemas/tools.json` SHALL bump `contract_version`:

- **patch** — a new optional input field, a new output field, a loosened
  constraint, a description change;
- **minor** — a new tool;
- **major** — a removed tool, a removed or renamed field, a tightened constraint,
  a changed meaning.

Breaking changes SHOULD be avoided by adding a new tool or field alongside the old
one and deprecating the old for at least one minor version.

### 33.3 Capabilities

`server_info` SHALL include a `capabilities` map so a client can test for
optional behaviour without parsing schemas:

```json
{
  "contract_version": "1.0.0",
  "capabilities": {
    "filesystem.patch.unified_diff": true,
    "filesystem.write.expected_sha256": true,
    "exec.jobs": true,
    "git.commit.expected_head_sha": true,
    "git.push.remote_protection": true,
    "workspace.devcontainer": false
  }
}
```

Capability keys SHALL be stable once published; a capability that is removed SHALL
be reported as `false` rather than omitted, for at least one major version.

---

## 34. MCP Tool Surface

Every tool definition is sent to the model on every request, before any work
begins, and tool-selection accuracy falls as the count rises. The surface is
therefore deliberately small. A structured tool earns its place only by offering
something `exec` cannot: bounded output, a state guard, a policy check, or an audit
record. Everything else is `exec`.

### 34.1 MVP Surface — 21 tools

```text
Server        server_info

Workspace     repository_list
              workspace_open
              workspace_info

Filesystem    list_files
              read_file
              write_file
              patch_file
              delete_file
              search_files

Execution     exec
              exec_start
              exec_status
              exec_output
              exec_cancel

Git           git_status
              git_diff
              git_log
              git_commit
              git_pull
              git_push
```

The MVP SHALL expose exactly this set. `workspace_open` SHALL return the same
payload as `workspace_info`, so a client needs one call to be productive.

### 34.2 Folded — use `exec` or an existing tool

| Not provided | Use instead | Why |
|---|---|---|
| `file_stat` | `list_files` (size, mtime, hash) or `read_file` (hash in response) | metadata is already returned |
| `move_file` | `exec("git mv a b")` | nothing to guard; Git records the rename |
| `git_add` | `git_commit` with `paths` | separate staging defeats the HEAD guard; `git_commit` stages what it is told |
| `git_show`, `git_remote`, `git_branch_list` | `exec` | read-only, output is small |
| `git_branch_create`, `git_checkout`, `git_restore` | `exec` | no policy, no remote, no guard that `exec` lacks under §18.1 |
| `git_fetch` | `git_pull` (fetch step) or `exec` | remote validation applies via `git_pull` |
| `git_tag`, `git_merge`, `git_rebase` | `exec` | rare; policy on them is advisory anyway (§18.6) |
| `repository_info` | `workspace_info` | one payload carries repository, Git, and protection state |
| `server_capabilities` | `server_info` (`capabilities` field, §33) | one call |

### 34.3 Deferred — later phases

```text
Workspace     workspace_start   workspace_stop   workspace_restart
              workspace_rebuild workspace_reset  workspace_delete
              workspace_lock    workspace_unlock workspace_lock_status
Execution     job_list
Diagnostics   workspace_disk_usage  workspace_environment
              workspace_processes   audit_recent
```

Until these exist, `workspace_open` performs start and create; rebuild, reset,
and delete are operator actions on the host. Each SHALL be added only when a
concrete workflow needs it, and §34.1 SHALL be updated when it is.

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

Per §4, the repositories are the operator's own and are trusted. The adversarial
assumption is therefore not "the repository is hostile" but:

> Things the repository *pulls in* — dependencies, tooling, and the AI client's own
> behaviour under prompt injection — may be untrusted, and mistakes happen.

Examples:

- compromised or typosquatted dependency with an install script;
- a third-party build tool that misbehaves;
- prompt-injected content reaching the AI client and steering it toward a
  destructive command;
- an accidental destructive command from the client;
- credential theft attempted by any of the above.

The container boundary keeps these away from the host's filesystem, Docker daemon,
and the operator's personal credentials — but only if those are not mounted into
the container, which is why §19.4 is an invariant. It does not keep them away from
the repository itself or from the repository's remote, because the container is
meant to work on both (§18.1, §39.2).

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
    the human user's identity, without requiring any container escape. Accepted
    under the default `git_dir_mode`; see §18.3 and §39.2;
16. hook execution under the daemon's identity during host-side reads (§18.7,
    §20.1);
17. local-command Git transports and transport overrides — `ext::`, `file://`,
    `--upload-pack`, `--receive-pack` — on structured operations (§20.2);
18. private key extraction by repository code — prevented under `ssh.mode: agent`,
    accepted under `ssh.mode: file` (§19.2);
19. key scope creep — a key that can write to repositories beyond the workspace's
    own; prevented under `ssh.scope: repository` (§19.1);
20. destructive remote operations from `exec` — force push, branch deletion — which
    no client-side control can prevent once the container holds a write key, and
    which SHALL therefore be enforced at the remote (§18.5);
21. SSH host-key substitution — prevented by pinning (§19.3);
22. refspec abuse on structured operations — forced updates, ref deletion, and
    destinations outside `refs/heads/` and `refs/tags/` (§20.3);
23. submodule URL substitution through the tracked `.gitmodules` file (§18.8).

### 39.2 Accepted Risks

In the private single-user deployment model:

- arbitrary code execution inside the development container is intentional;
- project code may access files inside its own repository;
- project code may use outbound network access unless restricted;
- persistent containers may accumulate mutable state.

The trusted-container model (§18.1) accepts the following explicitly, and they
SHALL NOT be described elsewhere in this specification as prevented:

- **Remote writes from `exec`.** The container holds a write-capable key. Code
  running under `exec` — including repository build scripts — can push, and can
  force-push wherever the remote permits it. The mitigations are key scope (§19.1)
  and remote-side rulesets (§18.5), not anything the daemon does.
- **Git metadata poisoning.** Under the default `git_dir_mode: read-write`, code
  in the container can write hooks and configuration that host Git processes will
  later execute under the developer's identity (§18.3). The container is treated as
  part of the developer's trust domain.
- **Working-tree destruction.** Code running under `exec` may delete or corrupt any
  file in the working tree, including bulk deletion. Tracked content is recoverable
  from the object store; untracked and ignored content is not.
- **Network exfiltration.** Nothing prevents project code from transmitting
  repository contents to an attacker-controlled destination over the container's
  ordinary outbound network access.

All four are Tier-3 under §18.6: bounded by the container boundary, key scope, the
remote, and recoverability — not by policy.

These are accepted in exchange for a practical development environment.

---

## 40. Host Security Requirements

The daemon touches the human's checkout directly: it performs host-side Git reads
(§20.1), and — depending on where the filesystem service executes — writes
working-tree files on the client's behalf. It therefore SHALL run as the uid that
owns the repository roots. In the single-user
deployment model (§4) that is the developer's own account. A dedicated daemon
account is acceptable only when it owns the repository roots — which is a
different deployment, not a hardening of this one.

The production deployment SHOULD:

- run the MCP daemon as the checkout owner, per the above;
- restrict the daemon's readable repository roots;
- avoid running the daemon itself as host root unless Docker access requires it;
- use Docker group or rootless Docker where feasible;
- protect MCP transport authentication;
- firewall the service appropriately;
- avoid public unauthenticated exposure.

The daemon SHALL treat Docker control as a high-privilege capability.

---

## 41. Container Hardening

Even though root is reachable inside project containers through `sudo` (§5.3), the daemon SHOULD consider:

- dropping unnecessary Linux capabilities;
- `no-new-privileges` — **only when `container.sudo` is `false`**. The flag blocks
  setuid escalation, which is how `sudo` works; with it set, `sudo apt-get` fails.
  Under the default `sudo: true` the daemon SHALL NOT set it, and SHALL rely on
  capability dropping and the other controls below instead;
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
- `ssh.scope: shared` — a key that can write to more than the workspace's own
  repository (§19.1);
- submodule initialization or update (§18.8).

An implementation MAY initially hard-deny these instead of implementing interactive approval.

Mounting the host user's own credentials (§19.4) is not an elevated capability. It
SHALL NOT be granted by policy, approval, or configuration.

---

## 43. Git Default Branch Policy

The default branch deserves explicit handling.

The relevant keys are the ones in §18.9, not a separate block:

```yaml
git_policy:
  default_branch_commit: allow               # allow | deny
  default_branch_push:   allow_fast_forward  # allow | allow_fast_forward | require_approval | deny
  force_push:            deny
```

Some users prefer branch/PR workflows; others intentionally allow direct development on `master`.

The server SHALL make this configurable rather than imposing one workflow universally.
These keys govern the structured tools. A developer who wants the default branch
protected against every path, including `exec`, expresses that as a GitHub ruleset
and sets `require_remote_protection: deny` (§18.5).

---

## 44. Recovery Behavior

### 44.1 Daemon Restart

On startup, the daemon SHOULD:

1. load workspace records;
2. inspect Docker containers carrying the server's labels;
3. reconcile missing/extra containers;
4. for each running workspace under `ssh.mode: agent`, start a fresh
   `ssh-agent`, load its key, and re-bind the socket at the path the container
   already has as `SSH_AUTH_SOCK` — the previous agent died with the daemon, and
   until this step the container's key is silently unusable;
5. terminate any orphaned agent processes carrying the daemon's labels whose
   workspace is not running;
6. inspect running jobs if possible;
7. mark unrecoverable jobs as `lost`;
8. preserve repository state.

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
    ssh_key: chatgpt-ralph-loop-tools.ed25519   # relative to ssh.keys_dir

docker:
  default_image: mcp-dev-ubuntu:24.04
  workspace_path: /workspace

workspace:
  idle_stop_after: 4h              # §8.1 — stop (not delete) idle persistent containers; "never" to disable

container:
  user: host                       # §5.3 — checkout owner's uid/gid
  sudo: true
  git_dir_mode: read-write         # §18.2

ssh:                               # §19
  mode: agent                      # agent | file
  scope: repository                # one deploy key per repository
  keys_dir: /etc/dev-workspace-mcp/keys
  known_hosts: /etc/dev-workspace-mcp/github_known_hosts

github:
  api_auth: token                  # none | token | app — read-only, §19.5
  api_token_path: /etc/dev-workspace-mcp/github-readonly.token
  repositories:
    - ekkus93/chatgpt_ralph_loop_tools

execution:                         # §15.6
  sync_timeout_seconds: 30
  default_job_timeout_seconds: 3600
  max_output_bytes: 10485760       # per stream per job, on disk
  tail_keep_bytes: 262144
  response_tail_lines: 200
  job_retention_days: 7
  job_log_max_total_bytes: 2147483648

git_policy:                        # structured tools only, §18.9
  force_push: deny
  hard_reset: require_approval
  delete_remote_branch: require_approval
  default_branch_push: allow_fast_forward
  pull_strategy: ff_only
  validate_remote: true
  require_remote_protection: warn  # §18.5 — binds every path, including exec
  remote_protection_checks: [force_push, deletion]

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

## 54. Implementation Language of the Server (undecided)

This section concerns the language the MCP server *itself* is written in. It has
no bearing on which languages the server supports for project development — that
is unconstrained by design (§5.8, §36–§38), and a Rust, Python, or TypeScript
project is served identically whatever the daemon is written in.

The decision is deliberately left open until implementation begins. The server
can be implemented in any language with robust MCP and Docker support.

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

The host user's own credentials — `~/.ssh`, `gh` configuration, git credential
stores — SHALL NOT be mounted into or otherwise exposed to any development
container (§19.4). The credential a container holds SHALL be a dedicated key
issued for this system and scoped to the workspace's authorized repositories
(§19.1).

The first sentence is unconditional and SHALL NOT be relaxed by policy or
configuration.

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

### FR-031 Git Metadata Exclusion from Structured Tools

The structured filesystem tools SHALL NOT write to repository Git metadata,
regardless of where they execute, including Git directories reached through a
gitfile (§12.1). Under `git_dir_mode: read-only`, `.git` SHALL additionally be
unwritable from the container (§18.2).

### FR-032 Remote Protection Verification

With a read-only API credential configured, the daemon SHALL verify and report the
default branch's protection against force push and deletion on `workspace_open`,
and SHALL honour `git_policy.require_remote_protection` (§18.5, §20.4).

### FR-033 Remote Transport Restriction

Structured Git operations SHALL reject configured remote URLs whose transport is
capable of invoking a local command (§20.2). SSH host keys for GitHub SHALL be
pinned (§19.3).

### FR-034 Refspec Validation

Client-supplied refspecs SHALL be validated against policy rather than passed
through to Git (§20.4).

### FR-035 Mode Disclosure

`workspace_info` SHALL report `git_dir_mode`, `ssh_key_mode`, `ssh_key_scope`, and
`remote_protection` (§25).

### FR-036 File Ownership Identity

Files written into the host checkout from a development container SHALL be owned
by the checkout owner's uid/gid. The daemon SHALL run as that uid. Container uid 0
SHALL NOT be the default process identity, and SHALL be refused entirely unless
user-namespace remapping is active (§5.3).

### FR-037 Dedicated Key Provisioning

The daemon SHALL provision a dedicated SSH key into each workspace, via an agent
socket by default, and SHALL refuse to provision any key that resolves to the host
user's `.ssh` directory (§19.1, §19.2).

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
3. read/write working-tree bind mount, `.git` read-write (§18.2);
3a. container processes running as the checkout owner's uid, with root via `sudo`
    (§5.3);
3b. Git metadata excluded from every filesystem tool (§12.1);
4. `workspace_open`;
5. `workspace_info`, reporting `git_dir_mode`, `ssh_key_mode`, `ssh_key_scope`,
   `remote_protection`;
6. filesystem list/read/write/patch/delete;
7. synchronous command execution;
8. asynchronous job start/status/output/cancel;
9. Git status/diff/log;
10. commit with explicit paths and HEAD guard;
11. structured pull/push, executed in-container (§20);
11a. the tool surface limited to the 21 tools in §34.1;
12. dedicated per-repository SSH deploy key provisioned via agent socket, GitHub
    host keys pinned, host credentials never mounted (§19);
13. remote identity and refspec validation on structured operations (§20.2,
    §20.3);
14. remote protection verification with `require_remote_protection: warn`
    (§18.5, §20.4);
15. path traversal protection;
16. Docker socket isolation;
17. bounded command output;
18. audit log;
19. container rebuild/reset.

Agent-mode key provisioning is in the MVP rather than `file` mode alone, because
it is what makes the model's two strongest claims true — the key cannot be
extracted, and `exec`-path remote operations are audited. It is a per-workspace
`ssh-agent` and a socket mount; the lifecycle is small.

Remote protection verification is in the MVP because it is the only enforcement of
force-push and deletion policy that binds `exec`, and because without the report a
client cannot tell whether those protections exist.

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
- `ssh.scope: shared`;
- per-signature approval through the agent (§19.2, §75);
- interactive approval for `require_approval` policies, which MAY be implemented as
  hard denial in the first release.

---

## 60. Recommended Implementation Phases

### Phase 1 — Core Workspace

Implement:

- MCP server;
- repository configuration;
- Docker lifecycle;
- writable working-tree mount, `.git` read-write by default (§18.2);
- container user matching the checkout owner's uid/gid, `sudo` available (§5.3);
- Git metadata exclusion in the filesystem service (§12.1);
- container-local Git environment — `safe.directory`, `GIT_TERMINAL_PROMPT=0`;
- `workspace_open`;
- `workspace_info`, including `git_dir_mode`;
- filesystem tools;
- basic synchronous `exec`.

Acceptance:

- client can modify a real local repository and run a command in its container;
- AT-030 passes — the filesystem tools refuse `.git`;
- AT-014a passes — files written from the container are owned by the checkout
  owner on the host.

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

- structured Git tools as an in-container front-end over the execution service
  (§20.1), with host-side read-only queries;
- `git_status`, `git_diff`, `git_log`;
- `git_commit` with explicit paths;
- state guards;
- hook neutralization on host-side reads (§18.7).

Acceptance:

- client can safely implement, validate, and commit a change;
- AT-023 passes — a `core.hooksPath` hook inside the tracked working tree does not
  execute during a host-side `git_status`.

### Phase 4 — GitHub Synchronization

Implement:

- per-workspace `ssh-agent` provisioning and socket mount (§19.2);
- pinned GitHub host keys and container-local SSH configuration (§19.3);
- structured `git_pull` (`ff_only` by default) and `git_push` (§20.5);
- remote identity validation with transport restriction (§20.2);
- refspec validation (§20.3);
- structured-tool destructive-operation policy (§18.9);
- remote protection verification with an optional read-only API credential
  (§20.4).

Acceptance:

- client can publish a normal fast-forward commit to an authorized repository from
  both the structured tool and `exec("git push")`;
- AT-011, AT-013, AT-024, AT-025, AT-025a, AT-026, AT-031, AT-032, and AT-033
  pass.

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

Given an authorized repository, `read_file("../../etc/passwd")` and equivalent
paths on every filesystem tool SHALL fail with `PATH_OUTSIDE_REPOSITORY`.

This tests the filesystem tools' path validation and the mount boundary. It does
not, and cannot, constrain `exec`: `exec("cat /etc/passwd")` succeeds inside the
container and returns the *container's* `/etc/passwd`, which is not the host's.
What `exec` can see is bounded by §5.4 — only the mounts the daemon made are
present.

### AT-002 Symlink Escape

A repository symlink pointing outside the repository SHALL not permit reads or
writes through the filesystem tools. Inside the container the same symlink
dangles, because its target is not mounted; this is the mount boundary doing the
work, and the test SHALL confirm both.

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

A structured `git_push` with force SHALL be denied under the default policy.

Separately, against a repository whose default branch has a ruleset blocking force
pushes, `exec("git push --force origin master")` SHALL be rejected by the remote.
Against a repository with no such ruleset it SHALL succeed, and the test SHALL
record that this is the expected, documented behaviour (§18.6) — not a failure.

### AT-012 Docker Socket

Inside the project container, `/var/run/docker.sock` SHALL not be present by default.

### AT-013 Host Credential Isolation

The host user's `~/.ssh`, `~/.config/gh`, `~/.git-credentials`, and `~/.netrc`
SHALL NOT be present inside any container, and no key fingerprint belonging to the
host user's personal keys SHALL be offered by the container's SSH client.

Under `ssh.mode: agent`, no private key material SHALL be discoverable inside the
container by filesystem search, environment inspection, or reading `/proc` —
including during an in-flight `git push`.

### AT-014 Container Root via sudo

The project container SHALL be capable of installing normal OS packages through
`sudo apt-get` while its default process identity is the host checkout owner.

### AT-014a File Ownership

A file written from inside the container SHALL appear in the host checkout owned by
the checkout owner's uid/gid. The human SHALL be able to overwrite and delete it
without elevated privileges, and a subsequent `git add` from inside the container
and a structured `git_pull` across it SHALL both succeed.

### AT-014b Root Mode Refusal

With `container.user: root` and no user-namespace remapping active, workspace
creation SHALL fail with `CONTAINER_CREATE_FAILED` rather than start a container
that would write root-owned files.

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

### AT-021 Git Hook Write (read-only mode)

Under `git_dir_mode: read-only`, writing `.git/hooks/pre-commit` from inside the
development container SHALL fail. Under the default `read-write` it SHALL succeed,
and the test SHALL record that this is documented behaviour (§18.3).

### AT-022 Git Config Write (read-only mode)

Under `git_dir_mode: read-only`, setting `core.fsmonitor` in `.git/config` from
inside the development container SHALL fail.

### AT-023 Hooks Path on Host-Side Reads

With `core.hooksPath` pointed at a directory inside the tracked working tree
containing an executable hook, a host-side `git_status` or `git_diff` SHALL NOT
execute that hook under the daemon's identity.

### AT-024 Local-Command Transport

A remote URL of the form `ext::sh -c ...` SHALL be rejected before any authenticated
operation is attempted.

### AT-025 Key Scope

Under `ssh.scope: repository`, with `origin` pointed at a second repository on
which the workspace's deploy key is not installed, both a structured `git_push`
and `exec("git push")` SHALL fail — the first at validation with
`GIT_REMOTE_NOT_ALLOWED`, the second at GitHub with an authentication error.

### AT-025a SSH-Form Remote

With `origin` configured as `git@github.com:owner/repo.git`, `exec("git push")` and
a structured `git_push` SHALL both succeed using the workspace key, with no
interactive prompt.

### AT-026 Refspec Validation

A push refspec with a leading `+` SHALL be rejected under `force_push: deny`.

### AT-027 Submodule URL Substitution

A `.gitmodules` URL pointing at an unauthorized repository SHALL NOT be fetched.

### AT-028 Container Git Workflow

Under the default `git_dir_mode`, `git add`, `git commit`, `git fetch`, and
`git push` SHALL all succeed from `exec` inside the container, and the resulting
commit SHALL be visible in the host checkout with the correct ownership.

### AT-029 Mode Disclosure

`workspace_info` SHALL report `git_dir_mode`, `ssh_key_mode`, `ssh_key_scope`, and
`remote_protection` values matching the workspace's actual configuration and the
remote's actual state.

### AT-030 Filesystem Tool Git Metadata

`write_file(".git/hooks/pre-commit")`, `patch_file(".git/config")`, and
`delete_file(".git/config")` SHALL each fail with `PATH_GIT_METADATA`. The
same SHALL hold for a path containing a `.git` component at any depth, and for the
gitdir target of a gitfile.

### AT-031 Host Key Pinning

With a substituted SSH host key for `github.com` presented to the container, every
SSH connection SHALL fail with a host-key mismatch and SHALL NOT prompt.

### AT-032 Remote Protection Reporting

Against a repository whose default branch has no ruleset, `workspace_open` under
`require_remote_protection: warn` SHALL succeed with `remote_protection.status:
missing` and a warning; under `deny` it SHALL fail with
`REMOTE_PROTECTION_MISSING` and SHALL NOT have provisioned a key into the
container.

### AT-033 Agent Lifecycle

After `workspace_stop`, the workspace key SHALL no longer be loaded in any agent,
and after `workspace_delete` no agent for the workspace SHALL be running.

### AT-034 SSH Auth Audit

Under `ssh.mode: agent`, an `exec("git fetch")` SHALL produce an `ssh_auth` audit
record for the workspace carrying the key's fingerprint.

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
  "git_dir_mode": "read-write",
  "ssh_key_mode": "agent",
  "ssh_key_scope": "repository",
  "remote_protection": { "status": "verified", "force_push_blocked": true },
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
structured operation executed in-container (§20.1): the request is subject to
remote identity validation (§20.2), refspec validation (§20.3), and preconditions
(§20.6), and the response reports the key mode, key scope, and remote protection
status in force.

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
- stdout_log_path        (host-side, under <state_dir>/jobs/<id>/, §15.6)
- stderr_log_path
- stdout_total_bytes
- stderr_total_bytes
- output_truncated
- logs_pruned
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
- MCP contract check — `export-schemas` output SHALL match the committed
  `schemas/tools.json` byte-for-byte, and `contract_version` SHALL have been
  bumped if it does not (§33);
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
schemas/tools.json
```

`README.md` should focus on setup and first use.

`SPEC.md` remains the authoritative behavioral design.

`schemas/tools.json` is the authoritative tool contract (§33.1), generated from
the implementation and committed. `docs/TOOLS.md` is human-readable commentary on
it and SHOULD be generated from the same source where practical, so that the two
cannot disagree.

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
5. reconciles per-workspace `ssh-agent` processes (§44.1);
6. validates repository roots;
7. starts MCP transport.

A health check SHOULD report degraded state if Docker is unavailable.

---

## 74. Security Defaults

> **Summary.** This table is a one-screen view of defaults whose normative
> definitions live in §5.3, §18.2, §18.9, §19, and §52. If the table and a cited
> section disagree, the section is right and the table is stale.

Three of these are invariants rather than defaults, and SHALL NOT be reconfigurable:

```text
Host user's credentials in container: DENIED  (invariant, §19.4)
Host SSH directory mount:             DENIED  (invariant, §19.4)
Host gh config mount:                 DENIED  (invariant, §19.4)
```

The remaining default configuration SHOULD choose:

```text
Docker socket in project container: DENIED
Privileged project container:       DENIED
Additional host mounts:             DENIED
Dedicated SSH key in container:     PROVIDED (agent socket, per-repository, §19)
Container .git write access:        ALLOWED  (git_dir_mode: read-write, §18.2)
Hooks inside the container:         RUN      (§18.7)
Hooks on host-side daemon reads:    DISABLED (§18.7)
GitHub host keys:                   PINNED   (§19.3)
Submodule init/update:              DENIED   (structured); key scope bounds exec
Configured remote schemes not ssh/https: DENIED (structured)
Force push (structured tools):      DENIED
Force push (exec):                  ENFORCED BY REMOTE RULESET where present (§18.5)
Force-with-lease (structured):      DENIED
Remote branch deletion (structured): DENIED
Remote tag deletion (structured):   DENIED
Remote URL change (structured):     DENIED
Hard reset (structured):            DENIED or approval-required
Pull strategy:                      FF-ONLY
Remote protection check:            WARN if missing (require_remote_protection)
Outbound network:                   ALLOWED
Container process identity:         HOST CHECKOUT OWNER uid/gid (§5.3)
Container root via sudo:            ALLOWED
Container uid 0 without userns:     DENIED
Root-owned files in host checkout:  PREVENTED
Working-tree write:                 ALLOWED
Container Git workflow (exec):      ALLOWED, including push
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
- per-signature approval through the workspace `ssh-agent`, gating every
  authenticated remote operation regardless of path (§19.2);
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

The mount handling it needs is already specified: a linked worktree's `.git` is a
gitfile, and §18.2 defines how the main repository's Git directory is mounted so
that it resolves inside the container. Worktree mode is therefore a matter of
creating the worktree and pointing the workspace at it, not of new mount logic.

---

## 77. Decision Summary

> **Summary.** A reader's digest of the decisions made throughout this document.
> Each is normative only in the section that makes it; this list carries no
> requirements of its own.

The initial architecture follows these decisions:

1. The system is an MCP-controlled Docker development workspace.
2. The host repository is mounted read/write and remains the source of truth.
3. The MCP daemon, not the project container, controls Docker.
4. Container processes run as the host checkout owner's uid; root is reachable inside the container through `sudo`, and is never the identity that writes to the host.
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
15. Force pushes and other destructive operations are denied on the structured tools; because `exec` can bypass that, enforcement that binds every path lives in remote rulesets, which the daemon verifies and can require.
16. The container holds a write-capable SSH key for its repository — a dedicated per-repository deploy key, provisioned through an agent socket so it cannot be extracted, and never the host user's own. The container can push.
17. Structured Git tools execute inside the container with that key; the daemon holds no Git write credential, and at most a read-only API credential for verification.
18. Repository Git metadata is mounted read-write by default, which is an accepted host code-execution risk in the trusted-container model; a read-only mode exists for untrusted code.
19. The server is language-agnostic.
20. Dev Container support is desirable but not required for the earliest MVP.
21. The system complements rather than replaces Ralph Bridge.

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
17. perform all of the above without exposing the host Docker socket, arbitrary host filesystem paths, or any of the host user's own credentials to project code — and with the container's dedicated key unable to reach any repository but its own.

At that point the server provides a practical replacement for the major software-development capabilities missing from a restricted generic AI sandbox.
