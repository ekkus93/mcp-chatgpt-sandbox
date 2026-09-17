"""PSCR-010 (`docs/POST_SWPH_CODE_REVIEW_SPEC_2026-09-17.md`): prove the shared
ownership helper scopes a label query to the running test context.

G3R-015 already namespaces physical Docker resources per pytest context, but the
ownership labels a query filters on carry the *logical* id, which is identical
across namespaces. Every absence and exact-count assertion in `tests/workspace`
therefore matched a concurrent run's container until this task. These tests pin
the filter itself rather than relying on the converted assertions to notice a
regression, and they use a fake client so they need no Docker daemon.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_chatgpt_sandbox.workspace.naming import (
    LABEL_TEST_NAMESPACE,
    LABEL_WORKSPACE_ID,
    TEST_RESOURCE_NAMESPACE_ENV,
    current_test_resource_namespace,
)
from tests.workspace.conftest import containers_in_this_context


class _FakeContainer:
    def __init__(self, name: str, labels: dict[str, str]) -> None:
        self.name = name
        self.labels = labels

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<_FakeContainer {self.name}>"


class _FakeContainers:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self._containers = containers
        self.list_calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> list[_FakeContainer]:
        self.list_calls.append(kwargs)
        wanted = kwargs["filters"]["label"]
        return [
            c
            for c in self._containers
            if f"{LABEL_WORKSPACE_ID}={c.labels[LABEL_WORKSPACE_ID]}" == wanted
        ]


class _FakeClient:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self.containers = _FakeContainers(containers)


def _client(*specs: tuple[str, str | None]) -> _FakeClient:
    containers = []
    for name, namespace in specs:
        labels = {LABEL_WORKSPACE_ID: "ws_example"}
        if namespace is not None:
            labels[LABEL_TEST_NAMESPACE] = namespace
        containers.append(_FakeContainer(name, labels))
    return _FakeClient(containers)


def test_pscrv_010_helper_excludes_a_foreign_namespace_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TEST_RESOURCE_NAMESPACE_ENV, "this-context")
    mine = current_test_resource_namespace()
    assert mine is not None

    client = _client(("mine", mine), ("theirs", "a-concurrent-run"))

    found = containers_in_this_context(
        client,  # type: ignore[arg-type]
        label=LABEL_WORKSPACE_ID,
        value="ws_example",
    )

    # The unfiltered query returns both; this is exactly the shape that produced
    # `assert [<Container: ...>] == []` across 11 CI failures.
    assert len(client.containers.list(filters={"label": f"{LABEL_WORKSPACE_ID}=ws_example"})) == 2
    assert [c.name for c in found] == ["mine"]


def test_pscrv_010_helper_includes_this_contexts_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TEST_RESOURCE_NAMESPACE_ENV, "this-context")
    mine = current_test_resource_namespace()
    client = _client(("mine", mine))

    found = containers_in_this_context(
        client,  # type: ignore[arg-type]
        label=LABEL_WORKSPACE_ID,
        value="ws_example",
    )

    assert [c.name for c in found] == ["mine"]


def test_pscrv_010_helper_filters_nothing_in_the_production_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Production leaves the variable unset, and unnamespaced containers must
    # still be returned; the helper must not silently hide real resources.
    monkeypatch.delenv(TEST_RESOURCE_NAMESPACE_ENV, raising=False)
    assert current_test_resource_namespace() is None

    client = _client(("production", None), ("namespaced", "some-test-run"))

    found = containers_in_this_context(
        client,  # type: ignore[arg-type]
        label=LABEL_WORKSPACE_ID,
        value="ws_example",
    )

    assert [c.name for c in found] == ["production"]
