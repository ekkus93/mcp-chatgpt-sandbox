"""`ExecutionService.pid_of` (§14, §15.5, RR-022) — pure logic against a
mocked `docker.DockerClient`, no real Docker daemon needed. The
Docker-backed race reproduction lives in
`tests/workspace/test_async_runner.py`'s
`test_rr022_pid_of_reliably_reports_a_pid_immediately_after_exec_start`;
this file is for `pid_of`'s own branches, not the race itself."""

from unittest.mock import MagicMock

import docker
import pytest

from mcp_chatgpt_sandbox.execution.service import ExecutionService


def _service_with_exec_inspect(side_effect_or_return: object) -> ExecutionService:
    client = MagicMock()
    if isinstance(side_effect_or_return, Exception):
        client.api.exec_inspect.side_effect = side_effect_or_return
    else:
        client.api.exec_inspect.return_value = side_effect_or_return
    return ExecutionService(client)


def test_pid_of_returns_the_pid_when_exec_inspect_reports_one() -> None:
    service = _service_with_exec_inspect({"Pid": 4321})
    assert service.pid_of("exec789") == 4321


def test_at_vat006_a_docker_exception_from_exec_inspect_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """VR-006 (`docs/REVIEW_SPEC.md` §4): `pid_of` warns when the retry
    budget is exhausted (see the test below) but its
    `except docker.errors.DockerException: return None` branch returned
    silently. Both produce the same `None`, and R16's requirement is
    that an absence be *surfaced* — the `DockerException` branch was the
    one path where it wasn't."""
    service = _service_with_exec_inspect(docker.errors.DockerException("daemon unreachable"))
    with caplog.at_level("WARNING", logger="mcp_chatgpt_sandbox.execution.service"):
        pid = service.pid_of("exec123")
    assert pid is None
    matching = [r for r in caplog.records if "exec123" in r.message]
    assert matching, f"no matching log record; got: {[r.message for r in caplog.records]}"
    assert matching[0].levelname == "WARNING"


def test_retry_exhaustion_is_logged_baseline_unchanged_by_this_fix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pins the pre-existing behavior VR-006 compares the `DockerException`
    branch against: `Pid: 0` on every retry (RR-022) already logs once
    the retry budget is exhausted. Not a reproduction — this already
    passed before this task."""
    service = _service_with_exec_inspect({"Pid": 0})
    with caplog.at_level("WARNING", logger="mcp_chatgpt_sandbox.execution.service"):
        pid = service.pid_of("exec456")
    assert pid is None
    matching = [r for r in caplog.records if "exec456" in r.message]
    assert matching, f"no matching log record; got: {[r.message for r in caplog.records]}"
    assert matching[0].levelname == "WARNING"
