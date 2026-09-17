"""Project workspace configuration — `.mcp-workspace.yml` (§9, §10, §16.2)
— S30.

Optional, per-repository, committed to the repository itself (unlike
`docs/SPEC.md`'s daemon-side `config.yml`): lets a project declare its own
image, one-time setup steps, environment variables, and dependency
caches, rather than the operator hand-configuring each repository's
`docker.default_image`/`environment` at the daemon level.

`.devcontainer/devcontainer.json` support (§9's precedence item 2, §30)
is deliberately **not** implemented here — §30 itself says the server
"MAY initially support a subset," and this task's own scope is already
substantial without a second config format. `.mcp-workspace.yml`, when
present, is therefore the only source of image/setup/environment/cache
configuration this daemon currently reads from the repository itself;
`docker.default_image` remains the fallback (§9's precedence item 4)
whenever no `.mcp-workspace.yml` is present or it omits `image`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from mcp_chatgpt_sandbox.errors import ContainerCreateFailedError

#: §9's list — checked in this order; the first one present wins. Both
#: extensions are accepted since real YAML tooling accepts either.
_CANDIDATE_FILENAMES = (".mcp-workspace.yml", ".mcp-workspace.yaml")

#: §10's schema, as parsed by this module today. `1` is the only value
#: any released version of this daemon has ever implemented — H32
#: (`docs/HARDENING_SPEC.md` §9): widen this set the day a second
#: schema version actually exists, not before.
SUPPORTED_PROJECT_CONFIG_VERSIONS = frozenset({1})

#: G3R-009 (`docs/GAP3_SPEC.md`): every top-level project-config field is
#: deliberately classified. The supported set has a production consumer; the
#: unsupported set remains in the model only to produce a precise failure for
#: §10's future-facing syntax instead of accepting a silent no-op. Tests assert
#: that these two sets cover `ProjectWorkspaceConfig.model_fields` exactly.
SUPPORTED_PROJECT_CONFIG_FIELDS = frozenset({"version", "image", "setup", "environment", "caches"})
UNSUPPORTED_PROJECT_CONFIG_FIELDS = frozenset({"workspace", "github", "validation"})

#: GR-004 (`docs/GAP_SPEC.md`): exact-name keys `environment:` may not
#: set, because `docker/default/entrypoint.sh` reads them (directly, or
#: implicitly via the shell that runs it) before the privilege drop.
#: `LD_*` is matched by prefix instead (checked separately, alongside
#: this set, at the one call site) since the loader family is open-ended
#: (`LD_PRELOAD`, `LD_LIBRARY_PATH`, `LD_AUDIT`, ...).
#:
#: G2R-002 (`docs/GAP2_SPEC.md`) adds the `GIT_CONFIG*` family:
#: `entrypoint.sh`'s root phase runs exactly one git command —
#: `git config --system url."git@github.com:".insteadOf "https://…"` —
#: and each of these redirects it to an attacker-chosen path, creating or
#: appending as root. Reproduced with git 2.43.0:
#: `GIT_CONFIG_SYSTEM=/tmp/x git config --system a.b c` writes `/tmp/x`.
#: Content is fixed (a URL rewrite rule), so this is arbitrary root-owned
#: file creation/corruption, not code execution.
#:
#: The broader `GIT_*` family was assessed and found safe *for this
#: entrypoint, as it stands today* — recorded here rather than
#: re-derived, since the safety depends on the root phase staying this
#: narrow: `GIT_SSH`/`GIT_SSH_COMMAND`/`GIT_EXTERNAL_DIFF` have nothing to
#: act on (no repository, no diff, no transport in the root phase), and
#: `GIT_DIR`/`GIT_ALTERNATE_OBJECT_DIRECTORIES` likewise — `git config
#: --system` never opens a repository. A future entrypoint change that
#: adds a root-phase `git` call against a real repository (a fetch, a
#: diff, anything transport-touching) SHALL re-evaluate this assessment
#: rather than assume it still holds. `GIT_CONFIG_KEY_<n>`/
#: `GIT_CONFIG_VALUE_<n>` need no separate entry: git only reads them when
#: `GIT_CONFIG_COUNT` is also set, so blocking the switch is sufficient —
#: verified against git's own documented behavior, not assumed.
_ENTRYPOINT_CONTROL_ENV_KEYS = frozenset(
    {
        "PATH",
        "BASH_ENV",
        "ENV",
        "SHELLOPTS",
        "IFS",
        "HOME",
        "GIT_CONFIG",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_COUNT",
    }
)

#: G2R-001 (`docs/GAP2_SPEC.md`): a *key* that is not a legal POSIX
#: environment-variable name. Exists specifically to reject bash's
#: exported-function form, `BASH_FUNC_<name>%%` — the `%%` makes the key
#: itself invalid — which the exact-name/prefix check above cannot see
#: since it only ever compares against variables that ARE legally named.
#: Reproduced in a live container: an entry keyed `BASH_FUNC_id%%` with
#: value `() { echo 1234; }` reaches `entrypoint.sh` as an imported bash
#: function that shadows the real `id`, and a shell function takes
#: precedence over `PATH` lookup — so the drop-bypass this task's sibling
#: check exists for survives even a hardcoded `PATH` (G3,
#: `docs/GAP_TODO.md`). Verified this does not over-reject: `PATH`,
#: `RUST_LOG`, and `GIT_CONFIG_SYSTEM` all match; only the `%%`-suffixed
#: function-export form fails.
_POSIX_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: G2R-001: bash's *pre-Shellshock* function-export form used a bare,
#: identifier-shaped key (so the pattern above cannot see it at all) with
#: a value beginning `() {`. Current bash does not import this form —
#: only the modern `BASH_FUNC_<name>%%` key — so this guards a downgraded
#: or patched `/bin/bash` rather than the one this project ships; two
#: lines, worth having so "closes the shape, not one spelling" is true
#: rather than aspirational.
_SHELL_FUNCTION_VALUE = re.compile(r"^\s*\(\s*\)\s*\{")


class ProjectCacheEntry(BaseModel):
    """One `caches[]` entry (§11). `path` is authored `$HOME`-relative
    (`$HOME/.cargo` or `~/.cargo`, per §10's own example) — normalized by
    `workspace/caches.py`, not here, since this model only validates
    shape, not daemon-specific mount semantics."""

    model_config = {"extra": "forbid"}

    path: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")


class ProjectGithubConfig(BaseModel):
    model_config = {"extra": "forbid"}

    repository: str


class ProjectValidationConfig(BaseModel):
    """Shape reserved by §10 for a future validation-command surface.

    G3R-009 keeps this nested model so the intended schema remains explicit,
    but `ProjectWorkspaceConfig` rejects the top-level `validation` field
    before accepting any of these currently unimplemented commands.
    """

    model_config = {"extra": "forbid"}

    format: list[str] = Field(default_factory=list)
    lint: list[str] = Field(default_factory=list)
    test: list[str] = Field(default_factory=list)


class ProjectWorkspaceConfig(BaseModel):
    """The full `.mcp-workspace.yml` schema (§10). `extra: "forbid"`
    is what makes "unknown fields SHOULD cause a validation error" real
    rather than aspirational — matching every other config model in this
    codebase (`config/models.py`)."""

    model_config = {"extra": "forbid"}

    #: §10: "The configuration format SHALL be versioned." — H32
    #: (`docs/HARDENING_SPEC.md` §9): a bare `int` satisfied the letter
    #: of that sentence while accepting *any* integer, including a
    #: `version` no version of this daemon has ever implemented (`999`
    #: parsed and got recorded as this workspace's `config_version` — a
    #: silent, wrong "reproducibility record", §9.2 — exactly as
    #: happily as `version: 1`). `1` is the only schema this module
    #: parses, so it is the only value accepted; a real future schema
    #: change widens this, not a blanket `int`.
    version: int
    image: str | None = None
    workspace: str | None = None
    """Reserved by §10 but rejected while no runtime override exists.

    G3R-009 forbids silently accepting this field until the hard-coded
    workspace path is replaced by a specified, end-to-end override.
    """
    setup: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    caches: list[ProjectCacheEntry] = Field(default_factory=list)
    github: ProjectGithubConfig | None = None
    validation: ProjectValidationConfig | None = None

    @field_validator(*sorted(UNSUPPORTED_PROJECT_CONFIG_FIELDS), mode="before")
    @classmethod
    def _reject_unimplemented_operational_fields(cls, v: Any, info: ValidationInfo) -> Any:
        """G3R-009 / G3AT-009: operational-looking project fields must
        never parse successfully while the daemon does nothing with them.

        These three fields are present in §10's future-facing example but
        have no production consumer today:

        * `workspace` does not override `DEFAULT_WORKSPACE_PATH`;
        * project-level `github.repository` does not feed the daemon's
          repository registry / remote-protection identity; and
        * `validation` has no tool or lifecycle hook that runs its commands.

        A ``mode="before"`` field validator is deliberate: it runs only
        when the author supplied the field, so omitted defaults remain valid,
        and even an explicit ``null`` cannot masquerade as supported syntax.
        Keep this reject list synchronized with the runtime-consumption table
        in ``docs/CONFIGURATION.md``. Implement a field end-to-end before
        removing it from this validator.
        """
        raise ValueError(
            f"project configuration field {info.field_name!r} is not supported by this daemon "
            "and would otherwise be ignored (G3R-009); remove it until the feature is implemented"
        )

    @field_validator("version")
    @classmethod
    def _version_must_be_supported(cls, v: int) -> int:
        if v not in SUPPORTED_PROJECT_CONFIG_VERSIONS:
            raise ValueError(
                f"unsupported version {v!r} (§10) — this daemon only implements "
                f"{sorted(SUPPORTED_PROJECT_CONFIG_VERSIONS)!r}"
            )
        return v

    @field_validator("environment")
    @classmethod
    def _environment_cannot_shadow_daemon_variables(cls, v: dict[str, str]) -> dict[str, str]:
        """HR-006 (`docs/HARDENING_SPEC.md` §6): `environment:` is
        repository-supplied and, via `exec` plus a container-writable
        `.git/`, effectively client-authorable. `MCP_*` and
        `SSH_AUTH_SOCK` are the identity/credential variables
        `build_workspace_run_kwargs` computes for the container (§5.3) —
        a real regression let `MCP_UID: "0"` here run the container as
        root on a daemon with no user-namespace remapping, routing
        straight around the `container.user: root` refusal, which only
        ever inspects `container_config.user`. This is one of two
        independent controls (`container_config.py` also merges the
        daemon's own variables last); this one is deliberately the
        allowlist-reject side, so a bypass of one does not silently
        become a bypass of both."""
        shadowed = sorted(k for k in v if k == "SSH_AUTH_SOCK" or k.startswith("MCP_"))
        if shadowed:
            raise ValueError(
                f"environment may not set daemon-controlled variable(s) {shadowed} (§5.3/HR-006)"
            )
        return v

    @field_validator("environment")
    @classmethod
    def _environment_cannot_control_interpreter_or_resolution(
        cls, v: dict[str, str]
    ) -> dict[str, str]:
        """GR-004 (`docs/GAP_SPEC.md`): the check above closes the
        daemon-identity gap HR-006 was written for, but `environment:`
        reaches further than that — `docker/default/entrypoint.sh` runs
        as root, PID 1, *before* the privilege drop, as `#!/bin/bash`,
        with no `PATH=` assignment anywhere and unqualified calls to
        `id`/`git`/`getent`/`useradd`/`mkdir`/`chown`/`chmod`/`setpriv`.
        Every key other than the daemon-identity ones above used to
        reach that phase unfiltered — verified in a live container that
        a hostile `BASH_ENV` or `PATH` there is root code execution, and
        that a `PATH` shadowing `id` to print anything but `0` skips the
        privilege-drop check (`entrypoint.sh`'s
        `[ "$(id -u)" != "0" ]`) entirely, running the workspace command
        itself as root even under `container.sudo: false`.

        A denylist, not an allowlist: `environment:` exists precisely so
        a project can set arbitrary, project-specific variables (build
        flags, log levels, service URLs) — an allowlist of "safe" names
        would defeat that purpose. The residual risk of a denylist is
        the ordinary one: a future interpreter-affecting variable this
        set does not yet name.

        `docker/default/entrypoint.sh` gaining an explicit, hardcoded
        `PATH` (`docs/GAP_TODO.md` G3) closes the sharpest consequence of
        a missed *named* variable — the drop-bypass via a hostile `PATH`
        — but it is not a backstop for every consequence of a missed
        input, and SHALL NOT be relied on as one: an exported shell
        function reaches the entrypoint under a key that was never going
        to appear in this set at all (it is not naming a dangerous
        variable, it is not a legal variable name), and a shell function
        takes precedence over `PATH` lookup regardless of what `PATH` is
        set to — G3's fix does not defend against it. Found live
        (G2R-001, `docs/GAP2_SPEC.md`) precisely because this paragraph
        used to claim G3 covered it. Closed directly, not by leaning on
        G3, by `_environment_cannot_supply_an_exported_shell_function`
        below."""
        blocked = sorted(k for k in v if k in _ENTRYPOINT_CONTROL_ENV_KEYS or k.startswith("LD_"))
        if blocked:
            raise ValueError(
                f"environment may not set variable(s) {blocked} that control the "
                "container entrypoint's interpreter or command resolution (§5.3/GR-004)"
            )
        return v

    @field_validator("environment")
    @classmethod
    def _environment_cannot_supply_an_exported_shell_function(
        cls, v: dict[str, str]
    ) -> dict[str, str]:
        """G2R-001 (`docs/GAP2_SPEC.md`): the check above closes every
        *named* interpreter-control variable, but bash imports **exported
        functions** from the environment too, under a key
        (`BASH_FUNC_<name>%%`) that check never considers because it is
        not one of the names it compares against. A function takes
        precedence over `PATH` lookup, so `BASH_FUNC_id%%` shadowing the
        real `id` defeats both the check above and `entrypoint.sh`'s own
        hardcoded `PATH` (G3, `docs/GAP_TODO.md`) at once — reproduced in
        a live container: `entrypoint.sh`'s `[ "$(id -u)" != "0" ]` takes
        the imported function's answer, and the workspace command runs as
        uid 0 with the privilege drop never performed.

        Two checks, not one, because bash has changed which half of a
        function export carries the signature: the key for the current
        `BASH_FUNC_<name>%%` form, the value for the pre-Shellshock bare-
        name form neither this daemon's shipped image nor most current
        systems import — kept anyway so this rejects the *shape*, not one
        historical spelling of it. Deliberately its own validator, not
        merged into the daemon-controlled-variable check above or the
        HR-006 one two methods up: each rejects a genuinely different
        thing (a key that is not a legal variable name at all, versus a
        legal name whose meaning happens to be dangerous), and a bypass of
        one SHALL NOT silently become a bypass of the others — the same
        reasoning that keeps HR-006's own check separate."""
        not_a_name = sorted(k for k in v if not _POSIX_ENV_VAR_NAME.match(k))
        looks_like_a_function = sorted(
            k for k, value in v.items() if _SHELL_FUNCTION_VALUE.match(value)
        )
        offending = sorted(set(not_a_name) | set(looks_like_a_function))
        if offending:
            raise ValueError(
                f"environment may not set variable(s) {offending} that define an "
                "exported shell function (§5.3/G2R-001)"
            )
        return v


def _candidate_path(repo_path: Path) -> Path | None:
    for filename in _CANDIDATE_FILENAMES:
        candidate = repo_path / filename
        if candidate.is_file():
            return candidate
    return None


def load_project_config(repo_path: Path) -> ProjectWorkspaceConfig | None:
    """`None` when neither `.mcp-workspace.yml` nor `.mcp-workspace.yaml`
    exists — an ordinary, unconfigured repository, not an error. Raises
    `ContainerCreateFailedError` for a present-but-invalid file: workspace
    creation genuinely cannot proceed correctly against a project
    configuration the daemon cannot make sense of, so this fails closed
    the same way an invalid `container.user: root` configuration already
    does (§10: "Unknown mandatory fields SHOULD cause a validation
    error"), rather than silently falling back to defaults."""
    path = _candidate_path(repo_path)
    if path is None:
        return None

    try:
        raw: Any = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ContainerCreateFailedError(
            f"{path.name} is not readable or not valid YAML: {exc}",
            details={"path": str(path)},
        ) from exc

    if not isinstance(raw, dict):
        raise ContainerCreateFailedError(
            f"{path.name}: top level must be a mapping, got {type(raw).__name__}",
            details={"path": str(path)},
        )

    try:
        return ProjectWorkspaceConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError, deliberately caught broadly
        raise ContainerCreateFailedError(
            f"{path.name}: invalid project configuration: {exc}",
            details={"path": str(path)},
        ) from exc


def devcontainer_json_is_ignored(repo_path: Path) -> bool:
    """H32 (`docs/HARDENING_SPEC.md` §9's §30 row): `.devcontainer/
    devcontainer.json` support is not implemented (this module's own
    docstring above) — §30 requires that fact be "reported clearly,"
    which the server-wide `capabilities.workspace.devcontainer: False`
    flag (`server_info`) only does for a client that already knows to
    check it against a repository it already knows has the file. This
    is the per-repository half: `True` when *this* repository actually
    has a `devcontainer.json` the daemon is silently not reading,
    surfaced on `workspace_info`/`workspace_open` where an operator
    working with this one repository will actually see it.

    Checked independently of whether `.mcp-workspace.yml` is also
    present — §9's precedence list ranks `.mcp-workspace.yml` above
    `devcontainer.json`, but ranking is not the same as "so the second
    file's existence no longer matters"; a project with *both* still
    has a devcontainer.json being ignored."""
    return (repo_path / ".devcontainer" / "devcontainer.json").is_file()
