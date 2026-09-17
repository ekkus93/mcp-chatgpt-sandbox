"""S24: SSH client configuration and host-key pinning (§19.3), against a
real Docker daemon and the real S7 default image."""

import subprocess
from pathlib import Path

import docker

from mcp_chatgpt_sandbox.config.models import RepositoryConfig, ServerConfig
from mcp_chatgpt_sandbox.server import create_server


def _configured(
    minimal_config: ServerConfig,
    default_image_tag: str,
    repo_path: Path,
    *,
    known_hosts_content: str = "",
) -> ServerConfig:
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_path, check=True)
    minimal_config.ssh.known_hosts.parent.mkdir(parents=True, exist_ok=True)
    minimal_config.ssh.known_hosts.write_text(known_hosts_content)
    return minimal_config.model_copy(
        update={
            "docker": minimal_config.docker.model_copy(update={"default_image": default_image_tag}),
            "repositories": [
                RepositoryConfig(id="example", path=repo_path, github="owner/example")
            ],
        }
    )


async def _open(server, repository_id: str = "example") -> str:
    result = await server.call_tool("workspace_open", {"repository_id": repository_id})
    return result.structured_content["workspace_id"]


async def test_ssh_config_file_is_written_in_the_container(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open(server)

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "cat ~/.ssh/config"}
    )
    content = result.structured_content["stdout_tail"]
    assert "Host github.com" in content
    assert "User git" in content
    assert "IdentitiesOnly yes" in content
    assert "StrictHostKeyChecking yes" in content
    assert "UserKnownHostsFile /etc/mcp/ssh/known_hosts" in content
    # Default ssh.mode is "agent" — no static IdentityFile to point at.
    assert "IdentityFile" not in content


async def test_ssh_config_permissions_are_private(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open(server)

    result = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "stat -c '%a' ~/.ssh && stat -c '%a' ~/.ssh/config",
        },
    )
    modes = result.structured_content["stdout_tail"].split()
    assert modes[0] == "700"
    assert modes[1] == "600"


async def test_ssh_config_owned_by_the_checkout_user(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open(server)

    # The exec's own default identity is the checkout owner, not root
    # (§5.3) — so ~/.ssh being writable by that same exec, with no sudo,
    # confirms it's owned correctly.
    check = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "test -w ~/.ssh/config && echo WRITABLE"}
    )
    assert "WRITABLE" in check.structured_content["stdout_tail"]


async def test_known_hosts_is_mounted_read_only_with_pinned_content(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    pinned = (
        "github.com ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
    )
    config = _configured(
        minimal_config,
        default_image_tag,
        tmp_path / "work" / "example",
        known_hosts_content=pinned,
    )
    server = create_server(config)
    workspace_id = await _open(server)

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "argv": ["cat", "/etc/mcp/ssh/known_hosts"]}
    )
    assert result.structured_content["stdout_tail"] == pinned

    write_attempt = await server.call_tool(
        "exec",
        {"workspace_id": workspace_id, "shell": "echo tampered >> /etc/mcp/ssh/known_hosts"},
    )
    assert write_attempt.structured_content["exit_code"] != 0


async def test_known_hosts_mount_is_never_inside_the_working_tree(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open(server)

    result = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "find /workspace -name known_hosts 2>/dev/null; true",
        },
    )
    assert result.structured_content["stdout_tail"].strip() == ""


async def test_https_origin_routed_over_ssh_via_insteadof(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """§19.3: `git config --system url.<base>.insteadOf` — verified by
    asking git itself what URL it would actually push to."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open(server)

    await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": "git remote add origin https://github.com/owner/example.git",
        },
    )
    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "git ls-remote --get-url origin"}
    )
    assert result.structured_content["stdout_tail"].strip() == "git@github.com:owner/example.git"


async def test_at_034_substituted_host_key_fails_closed_no_prompt(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    """AT-034: the same mechanism our SSH config relies on —
    `StrictHostKeyChecking yes` against a pinned `UserKnownHostsFile` —
    verified generically with a real local `sshd`, a real substituted
    host key, and a real connection attempt. `BatchMode=yes` is what
    prevents an interactive "trust this key?" prompt (there is nothing to
    prompt on a non-interactive `exec` anyway); with it, a real client
    hitting a mismatch fails outright rather than hanging."""
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    server = create_server(config)
    workspace_id = await _open(server)

    # `openssh-server` is baked into the real S7 default image. Keep this
    # acceptance test network-independent during pytest: generate any missing
    # host keys locally, create sshd's runtime directory, and start the daemon.
    start_sshd = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": (
                "sudo ssh-keygen -A "
                "&& sudo mkdir -p /run/sshd "
                "&& sudo /usr/sbin/sshd -p 2222 "
                "&& sleep 1"
            ),
        },
    )
    assert start_sshd.structured_content["exit_code"] == 0, start_sshd.structured_content

    # A separate exec call keeps ssh-keyscan's real output isolated and makes
    # the host-key line we parse unambiguous.
    keyscan = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "argv": ["ssh-keyscan", "-p", "2222", "-t", "ed25519", "localhost"],
        },
    )
    real_known_hosts_line = keyscan.structured_content["stdout_tail"].strip()
    assert real_known_hosts_line, "ssh-keyscan returned nothing — sshd isn't actually up"

    # A substituted host key: a real, freshly generated key that is
    # *not* the one sshd is actually presenting, in the same
    # "[host]:port keytype key" line format ssh-keyscan itself produced.
    wrong_key_setup = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": (
                "ssh-keygen -t ed25519 -f /tmp/substituted_key -N '' -q "
                "&& cat /tmp/substituted_key.pub"
            ),
        },
    )
    substituted_pubkey = wrong_key_setup.structured_content["stdout_tail"].strip()
    host_port_field = real_known_hosts_line.split(" ", 1)[0]
    substituted_key_field = " ".join(substituted_pubkey.split(" ")[:2])
    wrong_known_hosts_line = f"{host_port_field} {substituted_key_field}"

    write_wrong = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": f"echo '{wrong_known_hosts_line}' > /tmp/wrong_known_hosts",
        },
    )
    assert write_wrong.structured_content["exit_code"] == 0

    # Sanity check first: the *correct* pinned key reaches the auth stage
    # (fails on "Permission denied" — no valid credentials — not on host
    # verification) — proving ssh-keyscan's line is genuinely usable and
    # the harness itself works, before trusting the mismatch case below.
    write_correct = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "shell": f"echo '{real_known_hosts_line}' > /tmp/correct_known_hosts",
        },
    )
    assert write_correct.structured_content["exit_code"] == 0

    correct_attempt = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "argv": [
                "ssh",
                "-o",
                "UserKnownHostsFile=/tmp/correct_known_hosts",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "BatchMode=yes",
                "-p",
                "2222",
                "nonexistent-user@localhost",
                "true",
            ],
        },
    )
    assert correct_attempt.structured_content["exit_code"] != 0
    assert "Permission denied" in correct_attempt.structured_content["stderr_tail"]
    assert "REMOTE HOST IDENTIFICATION" not in correct_attempt.structured_content["stderr_tail"]

    # The actual AT-034 case: a substituted key fails on host
    # verification specifically, not merely "connection failed" for some
    # other reason, and does not hang waiting for interactive input.
    mismatched_attempt = await server.call_tool(
        "exec",
        {
            "workspace_id": workspace_id,
            "argv": [
                "ssh",
                "-o",
                "UserKnownHostsFile=/tmp/wrong_known_hosts",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "BatchMode=yes",
                "-p",
                "2222",
                "nonexistent-user@localhost",
                "true",
            ],
            "timeout_seconds": 10,
        },
    )
    assert mismatched_attempt.structured_content["exit_code"] != 0
    stderr = mismatched_attempt.structured_content["stderr_tail"]
    assert "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr
    assert "Permission denied" not in stderr  # never reached the auth stage


async def test_ssh_config_includes_identity_file_under_file_mode(
    minimal_config: ServerConfig,
    default_image_tag: str,
    docker_client: docker.DockerClient,
    tmp_path: Path,
) -> None:
    config = _configured(minimal_config, default_image_tag, tmp_path / "work" / "example")
    config = config.model_copy(update={"ssh": config.ssh.model_copy(update={"mode": "file"})})
    server = create_server(config)
    workspace_id = await _open(server)

    result = await server.call_tool(
        "exec", {"workspace_id": workspace_id, "shell": "cat ~/.ssh/config"}
    )
    assert "IdentityFile ~/.ssh/id_mcp" in result.structured_content["stdout_tail"]
