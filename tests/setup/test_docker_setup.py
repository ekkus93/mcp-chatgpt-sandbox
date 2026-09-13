"""Docker setup planning tests that do not mutate Docker state."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_workspace_mcp.setup.docker_setup import DockerSetupError, default_image_build_plan


def test_default_image_build_plan_is_exact_argv(tmp_path: Path) -> None:
    context = tmp_path / "docker" / "default"
    context.mkdir(parents=True)
    (context / "Dockerfile").write_text("FROM scratch\n")
    plan = default_image_build_plan("example:latest", source_root=tmp_path)
    assert plan.argv == ("docker", "build", "-t", "example:latest", str(context))
    assert plan.display_command.endswith(str(context))


def test_default_image_build_plan_requires_real_source_context(tmp_path: Path) -> None:
    with pytest.raises(DockerSetupError, match="build context is unavailable"):
        default_image_build_plan("example:latest", source_root=tmp_path)
