"""S6: deterministic, collision-safe naming and labels (§50, §51)."""

from dev_workspace_mcp.workspace.naming import (
    LABEL_MANAGED,
    LABEL_REPOSITORY_ID,
    LABEL_WORKSPACE_ID,
    container_labels,
    container_name,
    ephemeral_workspace_id,
    persistent_workspace_id,
)


def test_persistent_workspace_id_is_deterministic() -> None:
    assert persistent_workspace_id("example") == persistent_workspace_id("example")
    assert persistent_workspace_id("example") == "ws_example"


def test_ephemeral_workspace_id_incorporates_the_suffix() -> None:
    assert ephemeral_workspace_id("example", suffix="abcd1234") == "ws_example_abcd1234"


def test_ephemeral_workspace_ids_differ_by_suffix() -> None:
    a = ephemeral_workspace_id("example", suffix="aaaa")
    b = ephemeral_workspace_id("example", suffix="bbbb")
    assert a != b


def test_container_name_is_deterministic() -> None:
    assert container_name("ws_example") == container_name("ws_example")


def test_container_name_differs_for_different_workspace_ids() -> None:
    assert container_name("ws_a") != container_name("ws_b")


def test_container_name_has_the_expected_shape() -> None:
    name = container_name("ws_example")
    assert name.startswith("mcp-dev-ws-example-")
    suffix = name.removeprefix("mcp-dev-ws-example-")
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def test_container_labels_shape() -> None:
    labels = container_labels(workspace_id="ws_example", repository_id="example")
    assert labels == {
        LABEL_MANAGED: "true",
        LABEL_WORKSPACE_ID: "ws_example",
        LABEL_REPOSITORY_ID: "example",
    }
