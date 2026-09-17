"""H32: shared Git transport-failure classification (`git/transport.py`).
Marker-string coverage mirrors `git/pull.py`'s/`git/push.py`'s own
`is_non_fast_forward_failure`/`is_push_non_fast_forward_failure` tests;
the last two tests reproduce a real DNS failure and a real refused
connection (not hand-typed stderr) — this classification only means
anything if the wording it matches is wording Git actually produces."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mcp_chatgpt_sandbox.git.transport import is_auth_failure, is_network_failure

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


@pytest.mark.parametrize(
    "stderr",
    [
        "error: cannot open '.git/FETCH_HEAD': Permission denied",
        "fatal: could not create leading directories: Permission denied",
        "warning: unable to unlink .git/x: Permission denied",
    ],
)
def test_is_auth_failure_false_for_a_local_permission_failure(stderr: str) -> None:
    """RR-023 (`docs/REMEDIATION_SPEC.md` §16): the exact three lines the
    defect was reported against — none of these are a rejected
    credential, and the bare `"Permission denied"` substring used to
    match all three. See `test_a_real_local_permission_failure_during_
    fetch_is_not_classified_as_auth` below for the same defect
    reproduced from a real `git fetch`, not hand-typed."""
    assert is_auth_failure(stderr) is False


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


def test_a_real_local_permission_failure_during_fetch_is_not_classified_as_auth(
    tmp_path: Path,
) -> None:
    """RR-023: the `git_dir_mode: read-only` scenario (§18.2) — `.git`
    itself unwritable — reproduced for real: a real bare remote, a real
    clone, `.git` made unwritable, then a real `git fetch` against it.
    Produces the exact real stderr the defect was reported against
    ("error: cannot open '.git/FETCH_HEAD': Permission denied"), not a
    hand-typed string, the same way the DNS/refused-connection cases
    above do for network failures."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)  # noqa: S603, S607

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)  # noqa: S603, S607
    subprocess.run(["git", "-C", str(work), "config", "user.email", "a@b.c"], check=True)  # noqa: S603, S607
    subprocess.run(["git", "-C", str(work), "config", "user.name", "a"], check=True)  # noqa: S603, S607
    (work / "f.txt").write_text("hi\n")
    subprocess.run(["git", "-C", str(work), "add", "f.txt"], check=True)  # noqa: S603, S607
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "init"], check=True)  # noqa: S603, S607
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)  # noqa: S603, S607

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)  # noqa: S603, S607
    git_dir = clone / ".git"
    git_dir.chmod(0o555)  # read + execute only: FETCH_HEAD can't be opened for writing
    try:
        result = subprocess.run(  # noqa: S603, S607
            ["git", "-C", str(clone), "fetch", "origin"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        git_dir.chmod(0o755)  # restore before pytest's tmp_path cleanup removes the tree

    assert result.returncode != 0
    assert "Permission denied" in result.stderr
    assert "(" not in result.stderr  # confirms this is genuinely the no-parenthetical local case
    assert is_auth_failure(result.stderr) is False
