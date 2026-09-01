"""G3AT-010: global-hook dispatch distinguishes absence from Git config failure."""

import os
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
