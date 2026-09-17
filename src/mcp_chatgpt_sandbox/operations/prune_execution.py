"""Exact, plan-scoped destructive reclamation for O8/O9 / PMOR-008..010.

Execution never discovers deletion targets. The caller supplies an immutable
``PrunePlan`` created by O6/O7, and every candidate is independently inspected
by its exact recorded identity immediately before its exact action. O9 keeps
post-primary SSH-agent socket cleanup durable and retryable instead of losing
that obligation after the primary process/row is gone. There is deliberately
no label-wide, prefix-wide, path-wide, or system-wide fallback.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, NotFound

from mcp_chatgpt_sandbox.jobs.retention import (
    JobLogCleanupDebt,
    attempt_job_log_cleanup,
    get_job_log_cleanup_debt,
)
from mcp_chatgpt_sandbox.operations.inventory import (
    ByteObservation,
    InventoryResource,
    ResourceInventoryResult,
)
from mcp_chatgpt_sandbox.operations.prune_candidates import PruneCandidate
from mcp_chatgpt_sandbox.operations.prune_plans import (
    PrunePlan,
    RevalidationStatus,
    revalidate_prune_plan,
)
from mcp_chatgpt_sandbox.operations.resources import (
    OwnershipProof,
    OwnershipStatus,
    ResourceKind,
    ResourceLifecycleState,
    classify_cache_volume,
    classify_workspace_container,
)
from mcp_chatgpt_sandbox.operations.ssh_agent_cleanup import (
    SSHAgentCleanupDebt,
    attempt_ssh_agent_cleanup,
    get_ssh_agent_cleanup_debt,
    observe_socket_identity,
    transition_ssh_agent_to_cleanup_debt,
)
from mcp_chatgpt_sandbox.workspace.naming import LABEL_REPOSITORY_ID, LABEL_WORKSPACE_ID
from mcp_chatgpt_sandbox.workspace.ssh_agent import (
    SSHAgentRecord,
    _pid_exists,
    _record_matches_running_agent,
    _terminate_pid,
    agent_socket_path,
)


class PruneExecutionStatus(StrEnum):
    NOT_CONFIRMED = "not_confirmed"
    RECLAIMED = "reclaimed"
    ALREADY_ABSENT = "already_absent"
    STALE = "stale"
    FAILED = "failed"


class PruneExecutionAggregateStatus(StrEnum):
    NOT_CONFIRMED = "not_confirmed"
    SUCCESS = "success"
    MIXED = "mixed"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class PruneExecutionOutcome:
    candidate: PruneCandidate
    status: PruneExecutionStatus
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PruneExecutionResult:
    confirmed: bool
    status: PruneExecutionAggregateStatus
    outcomes: tuple[PruneExecutionOutcome, ...]


class _ProbeStatus(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class _Probe:
    status: _ProbeStatus
    resource: InventoryResource | None = None
    target: object | None = None
    reason: str | None = None


def _owned_state_proof(*evidence: str) -> OwnershipProof:
    return OwnershipProof(
        status=OwnershipStatus.OWNED,
        evidence=tuple(evidence),
        reason="positive durable-state ownership proof",
    )


def _single_inventory(resource: InventoryResource) -> ResourceInventoryResult:
    return ResourceInventoryResult(
        resources=(resource,),
        diagnostics=(),
        inspected_count=1,
        result_limit=1,
        inspection_limit=1,
        truncated=False,
        incomplete=False,
        diagnostics_truncated=False,
    )


def _volume_bytes(attrs: Mapping[str, Any]) -> ByteObservation:
    usage = attrs.get("UsageData")
    if isinstance(usage, Mapping):
        size = usage.get("Size")
        if isinstance(size, int) and not isinstance(size, bool):
            return ByteObservation.measured(size)
    return ByteObservation.unavailable(
        "bounded Docker volume metadata did not provide a size measurement"
    )


def _aggregate_status(
    outcomes: tuple[PruneExecutionOutcome, ...],
) -> PruneExecutionAggregateStatus:
    has_success = any(
        outcome.status in {PruneExecutionStatus.RECLAIMED, PruneExecutionStatus.ALREADY_ABSENT}
        for outcome in outcomes
    )
    has_problem = any(
        outcome.status in {PruneExecutionStatus.STALE, PruneExecutionStatus.FAILED}
        for outcome in outcomes
    )
    if has_problem and has_success:
        return PruneExecutionAggregateStatus.MIXED
    if has_problem:
        return PruneExecutionAggregateStatus.DEGRADED
    return PruneExecutionAggregateStatus.SUCCESS


class PruneExecutionService:
    """Execute only exact candidates frozen in a previously stored plan."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        docker_client: docker.DockerClient | None,
        state_dir: Path,
    ) -> None:
        self._connection = connection
        self._docker = docker_client
        self._state_dir = state_dir
        self._ssh_runtime_dir = state_dir / "ssh-agents"

    def execute(self, plan: PrunePlan, *, confirmed: bool) -> PruneExecutionResult:
        if not confirmed:
            return PruneExecutionResult(
                confirmed=False,
                status=PruneExecutionAggregateStatus.NOT_CONFIRMED,
                outcomes=tuple(
                    PruneExecutionOutcome(
                        candidate=candidate,
                        status=PruneExecutionStatus.NOT_CONFIRMED,
                        reason="confirmation_required",
                    )
                    for candidate in plan.candidates
                ),
            )

        outcomes = tuple(
            self._execute_candidate(plan.token, candidate) for candidate in plan.candidates
        )
        return PruneExecutionResult(
            confirmed=True,
            status=_aggregate_status(outcomes),
            outcomes=outcomes,
        )

    def _execute_candidate(
        self,
        plan_token: str,
        candidate: PruneCandidate,
    ) -> PruneExecutionOutcome:
        probe = self._probe(candidate)
        if probe.status is _ProbeStatus.ABSENT:
            if candidate.kind is ResourceKind.SSH_AGENT:
                linked = self._retry_linked_ssh_cleanup(candidate)
                if linked is not None:
                    return linked
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.ALREADY_ABSENT,
                reason="exact_target_absent",
            )
        if probe.status is _ProbeStatus.FAILED or probe.resource is None:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason=probe.reason or "identity_probe_failed",
            )

        validation = revalidate_prune_plan(
            PrunePlan(token=plan_token, candidates=(candidate,)),
            _single_inventory(probe.resource),
        )
        decision = validation.decisions[0]
        if decision.status is RevalidationStatus.STALE:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.STALE,
                reason=decision.reason,
            )

        action = self._act(candidate, probe.target)
        if action is not None:
            return action

        post = self._probe(candidate)
        if post.status is _ProbeStatus.ABSENT:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.RECLAIMED,
                reason=None,
            )
        if post.status is _ProbeStatus.FAILED:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="deletion_probe_failed",
            )
        return PruneExecutionOutcome(
            candidate=candidate,
            status=PruneExecutionStatus.FAILED,
            reason="deletion_unproven",
        )

    def _retry_linked_ssh_cleanup(
        self,
        candidate: PruneCandidate,
    ) -> PruneExecutionOutcome | None:
        try:
            debt = get_ssh_agent_cleanup_debt(self._connection, candidate.resource_id)
        except sqlite3.Error:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="identity_probe_failed",
            )
        if debt is None:
            return None
        reason = attempt_ssh_agent_cleanup(
            self._connection,
            state_dir=self._state_dir,
            debt=debt,
        )
        if reason is not None:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason=reason,
            )
        return PruneExecutionOutcome(
            candidate=candidate,
            status=PruneExecutionStatus.RECLAIMED,
            reason=None,
        )

    def _probe(self, candidate: PruneCandidate) -> _Probe:
        if candidate.kind is ResourceKind.WORKSPACE_CONTAINER:
            return self._probe_container(candidate)
        if candidate.kind is ResourceKind.CACHE_VOLUME:
            return self._probe_volume(candidate)
        if candidate.kind is ResourceKind.JOB_LOG_CLEANUP:
            return self._probe_job_log_cleanup(candidate)
        if candidate.kind is ResourceKind.SSH_AGENT:
            return self._probe_ssh_agent(candidate)
        if candidate.kind is ResourceKind.SSH_AGENT_CLEANUP:
            return self._probe_ssh_agent_cleanup(candidate)
        return _Probe(_ProbeStatus.FAILED, reason="unsupported_candidate_kind")

    def _probe_container(self, candidate: PruneCandidate) -> _Probe:
        if self._docker is None:
            return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")
        try:
            container = self._docker.containers.get(candidate.resource_id)
            container.reload()
            labels = dict(container.labels or {})
            status = str(container.status or "unknown")
        except NotFound:
            return _Probe(_ProbeStatus.ABSENT)
        except DockerException:
            return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")

        proof = classify_workspace_container(labels)
        workspace_id = labels.get(LABEL_WORKSPACE_ID)
        repository_id = labels.get(LABEL_REPOSITORY_ID)
        lifecycle = ResourceLifecycleState.UNKNOWN
        if proof.status is OwnershipStatus.FOREIGN:
            lifecycle = ResourceLifecycleState.FOREIGN
        elif proof.status is OwnershipStatus.OWNED and workspace_id and repository_id:
            try:
                row = self._connection.execute(
                    "SELECT repository_id, container_id, state FROM workspace WHERE id = ?",
                    (workspace_id,),
                ).fetchone()
            except sqlite3.Error:
                return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")
            if row is None:
                lifecycle = ResourceLifecycleState.ORPHANED_OWNED
            else:
                proof = classify_workspace_container(
                    labels,
                    expected_workspace_id=workspace_id,
                    expected_repository_id=row["repository_id"],
                )
                if not proof.positively_owned:
                    lifecycle = ResourceLifecycleState.FOREIGN
                elif row["container_id"] != candidate.resource_id:
                    lifecycle = ResourceLifecycleState.ORPHANED_OWNED
                elif row["state"] in {"running", "starting", "creating", "rebuilding"}:
                    lifecycle = ResourceLifecycleState.ACTIVE
                else:
                    lifecycle = ResourceLifecycleState.RETAINED

        resource = InventoryResource(
            kind=ResourceKind.WORKSPACE_CONTAINER,
            resource_id=candidate.resource_id,
            lifecycle_state=lifecycle,
            ownership=proof,
            bytes=ByteObservation.unavailable("container size not measured by O2"),
            repository_id=repository_id,
            workspace_id=workspace_id,
            details={"status": status},
        )
        return _Probe(_ProbeStatus.PRESENT, resource=resource, target=container)

    def _probe_volume(self, candidate: PruneCandidate) -> _Probe:
        if self._docker is None:
            return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")
        try:
            volume = self._docker.volumes.get(candidate.resource_id)
            volume.reload()
            attrs = dict(volume.attrs or {})
            labels = dict(attrs.get("Labels") or {})
        except NotFound:
            return _Probe(_ProbeStatus.ABSENT)
        except DockerException:
            return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")

        proof = classify_cache_volume(labels)
        repository_id = labels.get(LABEL_REPOSITORY_ID)
        if proof.status is OwnershipStatus.FOREIGN:
            lifecycle = ResourceLifecycleState.FOREIGN
        elif proof.status is OwnershipStatus.UNKNOWN or repository_id is None:
            lifecycle = ResourceLifecycleState.UNKNOWN
        else:
            try:
                row = self._connection.execute(
                    "SELECT 1 FROM repository WHERE id = ?",
                    (repository_id,),
                ).fetchone()
            except sqlite3.Error:
                return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")
            lifecycle = (
                ResourceLifecycleState.ORPHANED_OWNED
                if row is None
                else ResourceLifecycleState.RETAINED
            )

        resource = InventoryResource(
            kind=ResourceKind.CACHE_VOLUME,
            resource_id=candidate.resource_id,
            lifecycle_state=lifecycle,
            ownership=proof,
            bytes=_volume_bytes(attrs),
            repository_id=repository_id,
        )
        return _Probe(_ProbeStatus.PRESENT, resource=resource, target=volume)

    def _probe_job_log_cleanup(self, candidate: PruneCandidate) -> _Probe:
        try:
            debt = get_job_log_cleanup_debt(self._connection, candidate.resource_id)
        except sqlite3.Error:
            return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")
        if debt is None:
            return _Probe(_ProbeStatus.ABSENT)
        resource = InventoryResource(
            kind=ResourceKind.JOB_LOG_CLEANUP,
            resource_id=debt.job_id,
            lifecycle_state=ResourceLifecycleState.CLEANUP_PENDING,
            ownership=_owned_state_proof("durable job_log_cleanup tombstone row"),
            bytes=ByteObservation.accounted(debt.bytes_hint),
            workspace_id=debt.workspace_id,
            details={"last_error_recorded": debt.last_error is not None},
        )
        return _Probe(_ProbeStatus.PRESENT, resource=resource, target=debt)

    def _probe_ssh_agent(self, candidate: PruneCandidate) -> _Probe:
        try:
            row = self._connection.execute(
                """
                SELECT ssh_agent.workspace_id, ssh_agent.pid, ssh_agent.socket_path,
                       ssh_agent.boot_id, ssh_agent.process_start_time,
                       CASE WHEN workspace.id IS NULL THEN 0 ELSE 1 END AS workspace_present
                FROM ssh_agent
                LEFT JOIN workspace ON workspace.id = ssh_agent.workspace_id
                WHERE ssh_agent.workspace_id = ?
                """,
                (candidate.resource_id,),
            ).fetchone()
        except sqlite3.Error:
            return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")
        if row is None:
            return _Probe(_ProbeStatus.ABSENT)

        record = SSHAgentRecord(
            workspace_id=row["workspace_id"],
            pid=int(row["pid"]),
            socket_path=Path(row["socket_path"]),
            boot_id=row["boot_id"],
            process_start_time=row["process_start_time"],
        )
        lifecycle = (
            ResourceLifecycleState.ACTIVE
            if row["workspace_present"]
            else ResourceLifecycleState.ORPHANED_OWNED
        )
        resource = InventoryResource(
            kind=ResourceKind.SSH_AGENT,
            resource_id=record.workspace_id,
            lifecycle_state=lifecycle,
            ownership=_owned_state_proof("durable ssh_agent row"),
            bytes=ByteObservation.unavailable("ssh-agent byte size is not meaningful"),
            workspace_id=record.workspace_id,
            details={
                "pid": record.pid,
                "boot_id": record.boot_id,
                "process_start_time": record.process_start_time,
            },
        )
        return _Probe(_ProbeStatus.PRESENT, resource=resource, target=record)

    def _probe_ssh_agent_cleanup(self, candidate: PruneCandidate) -> _Probe:
        try:
            debt = get_ssh_agent_cleanup_debt(self._connection, candidate.resource_id)
        except sqlite3.Error:
            return _Probe(_ProbeStatus.FAILED, reason="identity_probe_failed")
        if debt is None:
            return _Probe(_ProbeStatus.ABSENT)
        bytes_observation = (
            ByteObservation.accounted(debt.bytes_hint)
            if debt.bytes_hint is not None
            else ByteObservation.unavailable("SSH-agent cleanup byte accounting is unavailable")
        )
        resource = InventoryResource(
            kind=ResourceKind.SSH_AGENT_CLEANUP,
            resource_id=debt.workspace_id,
            lifecycle_state=ResourceLifecycleState.CLEANUP_PENDING,
            ownership=_owned_state_proof(
                "durable ssh_agent_cleanup tombstone row",
                "socket target is reconstructed beneath configured state_dir",
            ),
            bytes=bytes_observation,
            workspace_id=debt.workspace_id,
            details={
                "last_error_recorded": debt.last_error is not None,
                "socket_device": debt.socket_device,
                "socket_inode": debt.socket_inode,
            },
        )
        return _Probe(_ProbeStatus.PRESENT, resource=resource, target=debt)

    def _act(
        self,
        candidate: PruneCandidate,
        target: object | None,
    ) -> PruneExecutionOutcome | None:
        if candidate.action == "remove_exact_container":
            return self._remove_docker_target(candidate, target)
        if candidate.action == "remove_exact_cache_volume":
            return self._remove_docker_target(candidate, target)
        if candidate.action == "retry_job_log_cleanup":
            return self._retry_job_log_cleanup(candidate, target)
        if candidate.action == "terminate_exact_ssh_agent":
            return self._terminate_ssh_agent(candidate, target)
        if candidate.action == "retry_ssh_agent_cleanup":
            return self._retry_ssh_agent_cleanup(candidate, target)
        return PruneExecutionOutcome(
            candidate=candidate,
            status=PruneExecutionStatus.FAILED,
            reason="unsupported_action",
        )

    @staticmethod
    def _remove_docker_target(
        candidate: PruneCandidate,
        target: object | None,
    ) -> PruneExecutionOutcome | None:
        remove = getattr(target, "remove", None)
        if not callable(remove):
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="identity_probe_failed",
            )
        try:
            remove()
        except NotFound:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.ALREADY_ABSENT,
                reason="exact_target_absent",
            )
        except DockerException:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="action_failed",
            )
        return None

    def _retry_job_log_cleanup(
        self,
        candidate: PruneCandidate,
        target: object | None,
    ) -> PruneExecutionOutcome | None:
        if not isinstance(target, JobLogCleanupDebt):
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="identity_probe_failed",
            )
        try:
            cleaned = attempt_job_log_cleanup(
                self._connection,
                state_dir=self._state_dir,
                debt=target,
            )
        except sqlite3.Error:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="action_failed",
            )
        if not cleaned:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="action_failed",
            )
        return None

    def _retry_ssh_agent_cleanup(
        self,
        candidate: PruneCandidate,
        target: object | None,
    ) -> PruneExecutionOutcome | None:
        if not isinstance(target, SSHAgentCleanupDebt):
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="identity_probe_failed",
            )
        reason = attempt_ssh_agent_cleanup(
            self._connection,
            state_dir=self._state_dir,
            debt=target,
        )
        if reason is not None:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason=reason,
            )
        return None

    def _terminate_ssh_agent(
        self,
        candidate: PruneCandidate,
        target: object | None,
    ) -> PruneExecutionOutcome | None:
        if not isinstance(target, SSHAgentRecord):
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="identity_probe_failed",
            )
        expected_socket = agent_socket_path(self._ssh_runtime_dir, target.workspace_id)
        if target.socket_path != expected_socket:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="identity_unprovable",
            )
        if not _pid_exists(target.pid):
            return self._clear_exact_ssh_record(candidate, target)
        if not _record_matches_running_agent(target):
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="identity_unprovable",
            )
        try:
            _terminate_pid(target.pid)
        except OSError:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="action_failed",
            )
        if _record_matches_running_agent(target):
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="deletion_unproven",
            )
        return self._clear_exact_ssh_record(candidate, target)

    def _clear_exact_ssh_record(
        self,
        candidate: PruneCandidate,
        record: SSHAgentRecord,
    ) -> PruneExecutionOutcome | None:
        identity = observe_socket_identity(record.socket_path)
        try:
            debt = transition_ssh_agent_to_cleanup_debt(
                self._connection,
                record,
                identity,
            )
        except sqlite3.Error:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason="deletion_unproven",
            )

        reason = attempt_ssh_agent_cleanup(
            self._connection,
            state_dir=self._state_dir,
            debt=debt,
        )
        if reason is not None:
            return PruneExecutionOutcome(
                candidate=candidate,
                status=PruneExecutionStatus.FAILED,
                reason=reason,
            )
        return None
