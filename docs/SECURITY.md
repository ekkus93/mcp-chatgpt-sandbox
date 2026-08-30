# Security

`docs/SPEC.md` is the only security reference this project maintains:

- Trust model — §4
- Threat model — §39
- Credential model — §19
- Security defaults — §74

`docs/HARDENING_SPEC.md` supplements it with the specific hardening
requirements (HR-001..HR-033) a later review found and this project's
`docs/HARDENING_TODO.md` closed against.

## Trust model

Per §4, this is a private server for a single user or trusted small
team, operating on **your own** repositories. The development container
is treated as part of your own trust domain — "a workstation the
operator has handed to an AI client, not a sandbox for code the operator
would hesitate to run directly." Every default follows from that: `.git`
is writable in the container, the container holds a key that can push,
and Git hooks run. Where a hardened mode exists (`container.git_dir_mode:
read-only`, §18.2), it's an option for the uncommon case, not the
recommendation.

That means the actual adversarial assumption (§39) is not "the
repository is hostile" — it's:

> Things the repository *pulls in* — dependencies, tooling, and the AI
> client's own behaviour under prompt injection — may be untrusted, and
> mistakes happen.

A compromised dependency's install script, a misbehaving build tool,
prompt-injected content steering the client toward a destructive
command, or an accidental one from the client itself — the container
boundary is what keeps those away from your host filesystem, the Docker
daemon, and your personal credentials (never mounted in, §19.4). It does
**not** keep them away from the repository or its remote, because the
container is meant to work on both.

## What's accepted, not prevented

§39.2 is explicit about a short list of Tier-3 risks this project treats
as inherent to a real development workstation rather than something to
police:

- **Remote writes from `exec`.** The container holds a write-capable
  key; a build script running under `exec` can push, including
  force-push wherever the remote allows it. Mitigated by key scope
  (§19.1) and remote-side branch protection (§18.5) — not by the daemon.
- **Git metadata poisoning.** Under the default `git_dir_mode:
  read-write`, container code can write hooks/config that later run
  under *your* identity when host-side Git touches the repo (§18.3).
- **Working-tree destruction.** `exec` can delete or corrupt any file in
  the working tree. Tracked content is recoverable from the object
  store; untracked/ignored content is not.
- **Network exfiltration.** Outbound network access is allowed by
  default; nothing stops container code from sending repository content
  somewhere it shouldn't.

These are bounded by the container boundary, key scope, the remote, and
Git's own recoverability — not by policy — and accepted in exchange for
an environment that can actually build, test, and ship real software.

## Credential model (§19)

- **Dedicated, scoped, revocable, never your own.** No GitHub write
  credential the operator personally holds is ever handed to a
  container.
- **`ssh.mode: agent`** (default) — a per-workspace `ssh-agent` holds the
  key; the socket is bind-mounted, key material never enters the
  container, and every signature is audited. **`ssh.mode: file`** — the
  private key itself is bind-mounted read-only: simpler, but extractable
  by any in-container process, and not observable by the daemon.
  "Acceptable in the single-user private deployment" per §4, not a
  general recommendation.
- **`ssh.scope: repository`** (default) — one deploy key per repository,
  a structural guarantee that a compromised container can only push to
  the one repo it was opened for. **`ssh.scope: shared`** — one key
  valid for every configured repository; simpler, but loses that
  structural scoping.
- **GitHub API credential** (`github.api_auth`) is strictly **read-only**
  — used to check branch-protection status and populate metadata, never
  to push — and lives host-side only, never mounted into a container.
- **Invariant, not configurable:** the host user's own credentials, `~/.ssh`, and `~/.gh` config are never mounted into a container, under any configuration (§19.4).

## Security defaults

The full table lives in §74; the highlights, condensed:

```text
Host user's credentials/keys in container:  DENIED (invariant)
Docker socket in project container:         DENIED
Privileged project container:               DENIED
Dedicated SSH key in container:             PROVIDED (agent socket, per-repository)
Container .git write access:                ALLOWED (git_dir_mode: read-write)
GitHub host keys:                           PINNED
Force push (git_push):                      DENIED
Remote branch deletion (git_push):          APPROVAL-REQUIRED
Remote tag deletion (git_push):             DENIED
Amend a published commit (git_commit):      DENIED
Container process identity:                 host checkout owner's uid/gid
Container uid 0 without userns remapping:   DENIED
Outbound network:                           ALLOWED
```

These are the *default* posture, not hard limits — `docs/CONFIGURATION.md`
covers what's actually adjustable (`git_policy`, `ssh`, `container`) and
what's an invariant that isn't.

## Host requirements (§40)

The daemon reads and writes your working tree directly and runs
host-side Git reads, so it SHALL run as the uid that owns your
repository roots — your own account in the standard single-user
deployment. Production deployments SHOULD additionally: restrict the
daemon's readable repository roots, avoid running the daemon itself as
host root unless Docker access requires it, use Docker group or
rootless Docker where feasible, protect MCP transport authentication,
firewall the service, and avoid public unauthenticated exposure — see
[`docs/CONNECTING_CHATGPT.md`](CONNECTING_CHATGPT.md) for how this
project satisfies the last two without ever opening a public listener.

## Hardening review

`docs/HARDENING_SPEC.md` documents a dedicated review pass across
container escape, credential handling, Git metadata poisoning, remote
validation, and audit/logging gaps, producing HR-001 through HR-033.
`docs/HARDENING_TODO.md` tracks each one to closure against this
codebase — some as code changes, some (like §39.2's accepted risks
above) as an explicit, documented decision not to add a control that
would undermine the trust model itself.

## Reporting a vulnerability

This project has no public issue tracker or disclosure address yet —
contact the repository owner directly.

This file exists so a reader who lands on it directly is pointed
somewhere real, not left with a 404 — it is not a second copy of the
reference kept in parallel with `docs/SPEC.md`/`docs/HARDENING_SPEC.md`.
