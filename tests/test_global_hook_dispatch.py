"""G3AT-010/G3AT-011: global-hook dispatch and same-name composition."""

import os
import shutil
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DISPATCH_HOOK = _REPO_ROOT / ".githooks" / "pre-push"


def _isolated_git_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg"
    home.mkdir()
    xdg_config_home.mkdir()
    global_config = tmp_path / "global.gitconfig"

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg_config_home),
            "GIT_CONFIG_GLOBAL": str(global_config),
        }
    )
    return env, global_config


def _run_dispatch(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_DISPATCH_HOOK)],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _git(
    repo: Path,
    env: dict[str, str],
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_g3at_011_repo(
    tmp_path: Path,
    *,
    global_exit_code: int = 0,
    uv_fail_command: str = "",
) -> tuple[Path, dict[str, str], Path, str]:
    env, global_config = _isolated_git_env(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    assert _git(repo, env, "init", "-q", "-b", "main").returncode == 0
    assert _git(repo, env, "config", "user.email", "test@example.com").returncode == 0
    assert _git(repo, env, "config", "user.name", "Test").returncode == 0

    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    assert _git(repo, env, "add", "README.md").returncode == 0
    assert _git(repo, env, "commit", "-q", "--no-verify", "-m", "seed").returncode == 0
    baseline_head = _git(repo, env, "rev-parse", "HEAD").stdout.strip()

    hooks_dir = repo / ".githooks"
    hooks_dir.mkdir()
    for name in ("pre-commit", "_global-hook-dispatch"):
        source = _REPO_ROOT / ".githooks" / name
        target = hooks_dir / name
        shutil.copyfile(source, target)
        target.chmod(0o755)
    assert _git(repo, env, "config", "core.hooksPath", ".githooks").returncode == 0

    order_log = tmp_path / "hook-order.log"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "printf 'local:%s\\n' \"$*\" >> \"$G3AT011_LOG\"\n"
        "if [ -n \"${G3AT011_UV_FAIL-}\" ] && [ \"$*\" = \"$G3AT011_UV_FAIL\" ]; then\n"
        "    exit 31\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    global_hooks = tmp_path / "global-hooks"
    global_hooks.mkdir()
    global_pre_commit = global_hooks / "pre-commit"
    global_pre_commit.write_text(
        "#!/bin/sh\n"
        "printf 'global\\n' >> \"$G3AT011_LOG\"\n"
        "exit \"${G3AT011_GLOBAL_EXIT:-0}\"\n",
        encoding="utf-8",
    )
    global_pre_commit.chmod(0o755)
    global_config.write_text(
        f"[core]\n\thooksPath = {global_hooks}\n",
        encoding="utf-8",
    )

    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["G3AT011_LOG"] = str(order_log)
    env["G3AT011_GLOBAL_EXIT"] = str(global_exit_code)
    env["G3AT011_UV_FAIL"] = uv_fail_command

    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    assert _git(repo, env, "add", "README.md").returncode == 0
    return repo, env, order_log, baseline_head


def test_g3at_010_missing_global_hooks_path_is_a_benign_noop(tmp_path: Path) -> None:
    env, global_config = _isolated_git_env(tmp_path)
    assert not global_config.exists()

    result = _run_dispatch(env)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_g3at_010_malformed_global_config_fails_loudly_without_echoing_value(
    tmp_path: Path,
) -> None:
    env, global_config = _isolated_git_env(tmp_path)
    secret_value = "/tmp/g3at010-secret-hooks-value"
    global_config.write_text(
        f"[core]\n\thooksPath = {secret_value}\n[broken\n",
        encoding="utf-8",
    )

    direct_git = subprocess.run(
        ["git", "config", "--global", "--get", "core.hooksPath"],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert direct_git.returncode not in (0, 1)
    assert direct_git.stderr

    result = _run_dispatch(env)

    assert result.returncode == direct_git.returncode
    assert direct_git.stderr in result.stderr
    assert "global hook dispatch: failed to read global core.hooksPath" in result.stderr
    assert secret_value not in result.stderr
    assert result.stdout == ""


def test_g3at_010_tilde_global_hooks_path_still_dispatches(tmp_path: Path) -> None:
    env, global_config = _isolated_git_env(tmp_path)
    global_hooks = Path(env["HOME"]) / "global-hooks"
    global_hooks.mkdir()
    global_pre_push = global_hooks / "pre-push"
    global_pre_push.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'g3at010-tilde-ran'\nexit 23\n",
        encoding="utf-8",
    )
    global_pre_push.chmod(0o755)
    global_config.write_text(
        "[core]\n\thooksPath = ~/global-hooks\n",
        encoding="utf-8",
    )

    result = _run_dispatch(env)

    assert result.returncode == 23
    assert result.stdout == "g3at010-tilde-ran\n"
    assert result.stderr == ""


def test_g3at_011_real_commit_runs_local_then_global_pre_commit(tmp_path: Path) -> None:
    repo, env, order_log, baseline_head = _prepare_g3at_011_repo(tmp_path)

    result = _git(repo, env, "commit", "-q", "-m", "exercise hook composition")

    assert result.returncode == 0, result.stderr
    assert order_log.read_text(encoding="utf-8").splitlines() == [
        "local:run ruff format --check .",
        "local:run ruff check .",
        "local:run mypy src",
        "global",
    ]
    assert _git(repo, env, "rev-parse", "HEAD").stdout.strip() != baseline_head


def test_g3at_011_local_failure_short_circuits_global_pre_commit(tmp_path: Path) -> None:
    repo, env, order_log, baseline_head = _prepare_g3at_011_repo(
        tmp_path,
        uv_fail_command="run ruff format --check .",
    )

    result = _git(repo, env, "commit", "-q", "-m", "local failure")

    assert result.returncode != 0
    assert order_log.read_text(encoding="utf-8").splitlines() == [
        "local:run ruff format --check .",
        "local:run ruff check .",
        "local:run mypy src",
    ]
    assert _git(repo, env, "rev-parse", "HEAD").stdout.strip() == baseline_head


def test_g3at_011_global_failure_blocks_commit_after_local_checks(tmp_path: Path) -> None:
    repo, env, order_log, baseline_head = _prepare_g3at_011_repo(
        tmp_path,
        global_exit_code=23,
    )

    result = _git(repo, env, "commit", "-q", "-m", "global failure")

    assert result.returncode != 0
    assert order_log.read_text(encoding="utf-8").splitlines() == [
        "local:run ruff format --check .",
        "local:run ruff check .",
        "local:run mypy src",
        "global",
    ]
    assert _git(repo, env, "rev-parse", "HEAD").stdout.strip() == baseline_head
