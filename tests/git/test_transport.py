"""H32: shared Git transport-failure classification (`git/transport.py`).
Marker-string coverage mirrors `git/pull.py`'s/`git/push.py`'s own
`is_non_fast_forward_failure`/`is_push_non_fast_forward_failure` tests;
the last two tests reproduce a real DNS failure and a real refused
connection (not hand-typed stderr) — this classification only means
anything if the wording it matches is wording Git actually produces."""

from __future__ import annotations

import subprocess

import pytest

from dev_workspace_mcp.git.transport import is_auth_failure, is_network_failure

# --- marker-string classification -----------------------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        "Permission denied (publickey).",
        "fatal: Authentication failed for 'https://github.com/x/y.git/'",
    ],
)
def test_is_auth_failure_recognizes_every_documented_marker(stderr: str) -> None:
    assert is_auth_failure(stderr) is True


def test_is_auth_failure_false_for_an_unrelated_failure() -> None:
    assert is_auth_failure("fatal: not a git repository\n") is False


def test_is_auth_failure_false_for_a_network_failure() -> None:
    assert is_auth_failure("ssh: connect to host 192.0.2.1 port 22: Connection timed out") is False


@pytest.mark.parametrize(
    "stderr",
    [
        "fatal: unable to access 'https://x.invalid/y.git/': Could not resolve host: x.invalid",
        "ssh: Could not resolve hostname x.invalid: Name or service not known",
        "ssh: connect to host 127.0.0.1 port 1: Connection refused",
        "ssh: connect to host 192.0.2.1 port 22: Connection timed out",
    ],
)
def test_is_network_failure_recognizes_every_documented_marker(stderr: str) -> None:
    assert is_network_failure(stderr) is True


def test_is_network_failure_false_for_an_unrelated_failure() -> None:
    assert is_network_failure("fatal: not a git repository\n") is False


def test_is_network_failure_false_for_an_auth_failure() -> None:
    assert is_network_failure("Permission denied (publickey).") is False


# --- real transport failures, produced for real, not hand-typed -----------------


def test_a_real_dns_failure_against_an_unresolvable_host_is_classified_as_network() -> None:
    # ".invalid" is reserved by RFC 2606 to never resolve — a stable,
    # real DNS failure, not a flaky live host.
    result = subprocess.run(  # noqa: S603, S607
        ["git", "ls-remote", "https://nonexistent-host-xyz-h32.invalid/foo.git"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert is_network_failure(result.stderr) is True
    assert is_auth_failure(result.stderr) is False


def test_a_real_refused_connection_is_classified_as_network() -> None:
    # Port 1 on loopback: a real port, real "nobody is listening here"
    # refusal from the kernel itself, not a mock.
    result = subprocess.run(  # noqa: S603, S607
        ["git", "ls-remote", "ssh://git@127.0.0.1:1/foo.git"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert is_network_failure(result.stderr) is True
