"""Operational resource ownership and lifecycle vocabulary.

Post-MVP Operations O1 / PMOR-001 establishes the one ownership model that
later inventory and prune code must consume. Names are identifiers used to
address resources; they are never ownership evidence. Docker resources require
positive managed labels plus the current GAP3 Y15 physical test namespace.
Host job-log cleanup remains rooted in durable state and the trusted
``<state_dir>/jobs/<validated-id>`` reconstruction from GAP3 Y7.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from dev_workspace_mcp.workspace.naming import (
    LABEL_MANAGED,
    LABEL_REPOSITORY_ID,
    LABEL_TEST_NAMESPACE,
    LABEL_WORKSPACE_ID,
    current_test_resource_namespace,
)

LABEL_RESOURCE_KIND = "dev-workspace-mcp.resource-kind"
RESOURCE_KIND_CACHE_VOLUME = "cache-volume"


class ResourceKind(StrEnum):
    WORKSPACE_CONTAINER = "workspace_container"
    CACHE_VOLUME = "cache_volume"
    JOB_LOG_DIRECTORY = "job_log_directory"
    JOB_LOG_CLEANUP = "job_log_cleanup"
    SSH_AGENT = "ssh_agent"
    SSH_AGENT_CLEANUP = "ssh_agent_cleanup"
    STATE_STORAGE = "state_storage"
    DOCKER_IMAGE = "docker_image"


class ResourceLifecycleState(StrEnum):
    ACTIVE = "active"
    RETAINED = "retained"
    CLEANUP_PENDING = "cleanup_pending"
    ORPHANED_OWNED = "orphaned_owned"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"
    DEGRADED = "degraded"


class OwnershipStatus(StrEnum):
    OWNED = "owned"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OwnershipProof:
    status: OwnershipStatus
    evidence: tuple[str, ...]
    reason: str

    @property
    def positively_owned(self) -> bool:
        return self.status is OwnershipStatus.OWNED


@dataclass(frozen=True, slots=True)
class ResourceOwnershipRule:
    kind: ResourceKind
    positive_evidence: tuple[str, ...]
    inventory_only: bool = False


OWNERSHIP_RULES: Mapping[ResourceKind, ResourceOwnershipRule] = {
    ResourceKind.WORKSPACE_CONTAINER: ResourceOwnershipRule(
        kind=ResourceKind.WORKSPACE_CONTAINER,
        positive_evidence=(
            f"Docker label {LABEL_MANAGED}=true",
            f"Docker label {LABEL_WORKSPACE_ID}=<durable workspace id>",
            f"Docker label {LABEL_REPOSITORY_ID}=<durable repository id>",
            "GAP3 Y15 test namespace matches the current process",
        ),
    ),
    ResourceKind.CACHE_VOLUME: ResourceOwnershipRule(
        kind=ResourceKind.CACHE_VOLUME,
        positive_evidence=(
            f"Docker label {LABEL_MANAGED}=true",
            f"Docker label {LABEL_RESOURCE_KIND}={RESOURCE_KIND_CACHE_VOLUME}",
            f"Docker label {LABEL_REPOSITORY_ID}=<repository id>",
            "GAP3 Y15 test namespace matches the current process",
        ),
    ),
    ResourceKind.JOB_LOG_DIRECTORY: ResourceOwnershipRule(
        kind=ResourceKind.JOB_LOG_DIRECTORY,
        positive_evidence=(
            "durable job row or job_log_cleanup tombstone identifies the job id",
            "path is reconstructed as <state_dir>/jobs/<validated job id>",
        ),
    ),
    ResourceKind.JOB_LOG_CLEANUP: ResourceOwnershipRule(
        kind=ResourceKind.JOB_LOG_CLEANUP,
        positive_evidence=("durable job_log_cleanup tombstone row",),
    ),
    ResourceKind.SSH_AGENT: ResourceOwnershipRule(
        kind=ResourceKind.SSH_AGENT,
        positive_evidence=(
            "durable ssh_agent row",
            "GAP3 Y2 boot-id/process-start-time identity proof when signaling",
        ),
    ),
    ResourceKind.SSH_AGENT_CLEANUP: ResourceOwnershipRule(
        kind=ResourceKind.SSH_AGENT_CLEANUP,
        positive_evidence=(
            "durable ssh_agent_cleanup tombstone row",
            "socket target reconstructed beneath configured state directory",
            "stored device/inode identity binds an existing retry target",
        ),
    ),
    ResourceKind.STATE_STORAGE: ResourceOwnershipRule(
        kind=ResourceKind.STATE_STORAGE,
        positive_evidence=("configured state_db_path/state directory, never a discovered prefix",),
    ),
    ResourceKind.DOCKER_IMAGE: ResourceOwnershipRule(
        kind=ResourceKind.DOCKER_IMAGE,
        positive_evidence=("Docker image identity referenced by positively owned workspace state",),
        inventory_only=True,
    ),
}

INVENTORY_ONLY_RESOURCE_KINDS = frozenset(
    kind for kind, rule in OWNERSHIP_RULES.items() if rule.inventory_only
)


def _namespace_proof(labels: Mapping[str, str]) -> OwnershipProof | None:
    expected = current_test_resource_namespace()
    actual = labels.get(LABEL_TEST_NAMESPACE)
    if actual != expected:
        return OwnershipProof(
            OwnershipStatus.FOREIGN,
            (),
            "test resource namespace does not match this process",
        )
    evidence = "production namespace" if expected is None else f"{LABEL_TEST_NAMESPACE}={expected}"
    return OwnershipProof(OwnershipStatus.OWNED, (evidence,), "namespace matches")


def cache_volume_labels(*, repository_id: str) -> dict[str, str]:
    labels = {
        LABEL_MANAGED: "true",
        LABEL_RESOURCE_KIND: RESOURCE_KIND_CACHE_VOLUME,
        LABEL_REPOSITORY_ID: repository_id,
    }
    namespace = current_test_resource_namespace()
    if namespace is not None:
        labels[LABEL_TEST_NAMESPACE] = namespace
    return labels


def classify_workspace_container(
    labels: Mapping[str, str],
    *,
    expected_workspace_id: str | None = None,
    expected_repository_id: str | None = None,
) -> OwnershipProof:
    if labels.get(LABEL_MANAGED) != "true":
        return OwnershipProof(
            OwnershipStatus.FOREIGN,
            (),
            f"missing {LABEL_MANAGED}=true ownership label",
        )
    namespace = _namespace_proof(labels)
    assert namespace is not None
    if not namespace.positively_owned:
        return namespace

    workspace_id = labels.get(LABEL_WORKSPACE_ID)
    repository_id = labels.get(LABEL_REPOSITORY_ID)
    if not workspace_id or not repository_id:
        return OwnershipProof(
            OwnershipStatus.UNKNOWN,
            namespace.evidence,
            "managed container is missing workspace/repository identity labels",
        )
    if expected_workspace_id is not None and workspace_id != expected_workspace_id:
        return OwnershipProof(
            OwnershipStatus.FOREIGN,
            namespace.evidence,
            "workspace identity label does not match expected durable state",
        )
    if expected_repository_id is not None and repository_id != expected_repository_id:
        return OwnershipProof(
            OwnershipStatus.FOREIGN,
            namespace.evidence,
            "repository identity label does not match expected durable state",
        )
    return OwnershipProof(
        OwnershipStatus.OWNED,
        namespace.evidence
        + (
            f"{LABEL_MANAGED}=true",
            f"{LABEL_WORKSPACE_ID}={workspace_id}",
            f"{LABEL_REPOSITORY_ID}={repository_id}",
        ),
        "positive workspace-container ownership proof",
    )


def classify_cache_volume(
    labels: Mapping[str, str],
    *,
    expected_repository_id: str | None = None,
) -> OwnershipProof:
    if labels.get(LABEL_MANAGED) != "true":
        return OwnershipProof(
            OwnershipStatus.FOREIGN,
            (),
            f"missing {LABEL_MANAGED}=true ownership label",
        )
    namespace = _namespace_proof(labels)
    assert namespace is not None
    if not namespace.positively_owned:
        return namespace

    resource_kind = labels.get(LABEL_RESOURCE_KIND)
    if resource_kind is None:
        return OwnershipProof(
            OwnershipStatus.UNKNOWN,
            namespace.evidence,
            "managed volume is missing a resource-kind label",
        )
    if resource_kind != RESOURCE_KIND_CACHE_VOLUME:
        return OwnershipProof(
            OwnershipStatus.FOREIGN,
            namespace.evidence,
            "Docker volume has a different managed resource kind",
        )
    repository_id = labels.get(LABEL_REPOSITORY_ID)
    if not repository_id:
        return OwnershipProof(
            OwnershipStatus.UNKNOWN,
            namespace.evidence,
            "managed cache volume is missing repository identity",
        )
    if expected_repository_id is not None and repository_id != expected_repository_id:
        return OwnershipProof(
            OwnershipStatus.FOREIGN,
            namespace.evidence,
            "cache-volume repository label does not match expected durable state",
        )
    return OwnershipProof(
        OwnershipStatus.OWNED,
        namespace.evidence
        + (
            f"{LABEL_MANAGED}=true",
            f"{LABEL_RESOURCE_KIND}={RESOURCE_KIND_CACHE_VOLUME}",
            f"{LABEL_REPOSITORY_ID}={repository_id}",
        ),
        "positive cache-volume ownership proof",
    )
