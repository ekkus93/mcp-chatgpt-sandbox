"""R6 acceptance coverage for obsolete SSH cleanup helper removal."""

from mcp_chatgpt_sandbox.operations import ssh_agent_cleanup


# Covers: PMORRAT-006
def test_r6_obsolete_inventory_augmentation_helper_is_removed() -> None:
    """The superseded inventory augmentation entry point must not remain callable."""
    assert not hasattr(
        ssh_agent_cleanup,
        "augment_inventory_with_ssh_agent_cleanup_debt",
    )


def test_r6_live_ssh_cleanup_api_remains_available() -> None:
    """Dead-code removal must not erase the active durable cleanup API."""
    expected = {
        "SSHAgentCleanupDebt",
        "SocketIdentity",
        "attempt_ssh_agent_cleanup",
        "cleanup_socket_path",
        "get_ssh_agent_cleanup_debt",
        "list_ssh_agent_cleanup_debt",
        "observe_socket_identity",
        "transition_ssh_agent_to_cleanup_debt",
    }
    missing = sorted(name for name in expected if not hasattr(ssh_agent_cleanup, name))
    assert not missing, f"active SSH cleanup API disappeared: {missing}"
