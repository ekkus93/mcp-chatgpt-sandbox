"""Helpers shared by the `exec`-family tool bodies: the TERM→grace→KILL
escalation, the two §24 audit side effects, and job-id minting.

JPO-005 (`docs/JOB_PROMOTION_SPEC_2026-09-18.md` §2.6): the escalation and
audit helpers were moved verbatim out of `tools/execution.py`, which stood
at 797 lines against the `< 800` budget
`tests/test_refactored_module_size_guard.py` enforces and had no room for
§14.4's promotion path. A pure move — no behaviour changed.

Who uses what (PJPO-004, `docs/POST_JPO_CODE_REVIEW_SPEC_2026-09-18.md`
§2.4, corrects an earlier version of this docstring that said the move let
the promotion module "use these" in general):

- `kill_after_timeout` — `tools/execution.py`, `tools/workspace.py`, and
  `tools/execution_promotion.py`. The last is the reason it lives outside
  `tools/execution.py` at all: that module imports the promotion module,
  so the reverse import would be circular. `tools/workspace.py` imports it
  from here rather than through `tools/execution.py` because strict mypy
  rejects an implicit re-export.
- `new_job_id` — `tools/execution.py` (`exec_start`) and
  `tools/execution_promotion.py`; added here by JPO-006, for the same
  circular-import reason.
- `audit_command_started` / `audit_ssh_auth_if_applicable` — only
  `tools/execution.py`. The promotion path audits through a callback that
  module builds, not by importing these. They are public (they lost their
  leading underscore in the move) because they are imported from another
  module, not because a second module uses them; PD3-A kept it that way
  rather than importing private names across a module boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets

import docker

from mcp_chatgpt_sandbox.audit import write_audit_event
from mcp_chatgpt_sandbox.config.models import AuditConfig, SSHConfig
from mcp_chatgpt_sandbox.errors import ContainerRuntimeFailedError, RepositoryNotAllowedError
from mcp_chatgpt_sandbox.execution.service import ExecutionService
from mcp_chatgpt_sandbox.repositories import RepositoryRegistry
from mcp_chatgpt_sandbox.workspace.manager import WorkspaceRecord
from mcp_chatgpt_sandbox.workspace.ssh_agent import resolve_ssh_key_path, ssh_key_fingerprint

#: How long to wait after TERM before escalating to KILL on a timed-out
#: command. Shared by `tools/workspace.py`'s `run_setup_steps` too
#: (G2R-016, `docs/GAP2_SPEC.md`) — a real cross-import, not a duplicated
#: constant kept in sync by hand: the two used to be separate constants
#: (`_SETUP_KILL_GRACE_PERIOD_SECONDS` there), which is exactly the shape
#: that let the OSError-handling `kill_after_timeout` below closes also
#: exist in only one of the two call sites for as long as it did.
KILL_GRACE_PERIOD_SECONDS = 2.0


async def kill_after_timeout(
    execution_service: ExecutionService,
    exec_id: str,
    *,
    workspace_id: str,
    timeout_seconds: float,
    grace_period_seconds: float = KILL_GRACE_PERIOD_SECONDS,
) -> None:
    """G2R-016 (`docs/GAP2_SPEC.md`): TERM, a grace period, then KILL —
    shared between `run_exec` below and `tools/workspace.py`'s
    `run_setup_steps`, extracted rather than left independently
    hand-copied so the two cannot diverge again the way they already
    had (`run_exec` handled a failed kill; the setup-step copy did not,
    letting `PermissionError`/`DockerException` escape past
    `@translate_workspace_errors` as an unstructured crash).

    `ExecutionService.kill`'s own docstring is explicit that both
    `PermissionError` (HR-020 — the daemon's own uid lacks permission to
    signal this pid) and a `docker.errors.DockerException` from
    `exec_inspect` (RR-013 — the container was removed out from under a
    still-running exec) are deliberately allowed to propagate, not
    swallowed as if the signal had succeeded. Caught here as
    `(OSError, docker.errors.DockerException)`, not `OSError` alone:
    `docker.errors.APIError` — the common case — chains through
    `HTTPError`/`RequestException` to `OSError`, so a narrower
    `except OSError` already covered it, but `kill`'s own docstring
    promises *any* `DockerException` from `exec_inspect`, and a
    non-`APIError` one (confirmed by checking `docker.errors`'
    hierarchy directly, not assumed) would still escape a
    plain `except OSError`."""
    try:
        await asyncio.to_thread(execution_service.kill, exec_id)
        await asyncio.sleep(grace_period_seconds)
        await asyncio.to_thread(execution_service.kill, exec_id, grace_period_reached=True)
    except (OSError, docker.errors.DockerException) as exc:
        raise ContainerRuntimeFailedError(
            f"command exceeded {timeout_seconds}s and could not be terminated: {exc}",
            details={"workspace_id": workspace_id, "timeout_seconds": timeout_seconds},
            recoverable=False,
        ) from exc


def audit_command_started(
    audit_config: AuditConfig,
    *,
    workspace_id: str,
    repository_id: str,
    argv: list[str] | None,
    shell: str | None,
    cwd: str,
) -> None:
    """§24's "command started" event (HR-028/AT-021): without this,
    nothing about a command's own invocation ever reached the audit log
    at all — AT-021 ("run a command containing a configured secret and
    verify audit output does not expose the secret value") had no audit
    output to inspect in the first place. `metadata` carries exactly the
    request shape a client gave (`argv`/`shell`/`cwd`), passed through
    `write_audit_event`'s existing `redact_secrets` (§24.1) — a
    GitHub-token-shaped value or `scheme://user:pass@host` embedded
    anywhere in `argv`/`shell` is caught by the same pattern matching
    `to_tool_error` already applies to a client-facing error message."""
    write_audit_event(
        audit_config,
        operation="exec",
        result="success",
        workspace_id=workspace_id,
        repository_id=repository_id,
        metadata={"argv": argv, "shell": shell, "cwd": cwd},
    )


def audit_ssh_auth_if_applicable(
    audit_config: AuditConfig,
    ssh_config: SSHConfig,
    registry: RepositoryRegistry,
    record: WorkspaceRecord,
) -> None:
    """AT-037: under `ssh.mode: agent`, an `exec` against a workspace
    with a real ssh-agent mount (§19.2: persistent-mode workspaces whose
    repository resolves a key — the same condition `workspace_open`'s
    own mount-building code gates on; an ephemeral workspace or one with
    no key configured never gets an agent socket at all, so there is
    nothing to audit) produces an `ssh_auth` record carrying the key's
    fingerprint. Every check below is a silent skip, not an error: this
    is an audit *side effect* of a command that has already been
    authorized to run, not a second authorization gate — a repository
    that stopped resolving, or a key that fails to fingerprint, should
    never be what makes `exec` itself fail."""
    if ssh_config.mode != "agent" or record.mode != "persistent":
        return
    with contextlib.suppress(RepositoryNotAllowedError):
        repository = registry.get(record.repository_id)
        key_path = resolve_ssh_key_path(ssh_config, repository)
        if key_path is None:
            return
        fingerprint = ssh_key_fingerprint(key_path)
        if fingerprint is None:
            return
        write_audit_event(
            audit_config,
            operation="ssh_auth",
            result="success",
            workspace_id=record.id,
            repository_id=record.repository_id,
            metadata={"fingerprint": fingerprint},
        )


def new_job_id() -> str:
    #: Same `secrets.token_hex(4)` convention as ephemeral workspace ids
    #: (workspace/manager.py). Here (JPO-006) rather than in
    #: `tools/execution.py` because §14.4's promotion mints job ids too.
    return f"job_{secrets.token_hex(4)}"
