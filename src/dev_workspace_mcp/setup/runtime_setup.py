"""Advanced runtime configuration validation with production Pydantic models."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from dev_workspace_mcp.config import ExecutionConfig, FilesystemConfig, LoggingConfig
from dev_workspace_mcp.config.models import ResourcesConfig


class SetupRuntimeError(RuntimeError):
    """Safe advanced-runtime validation failure."""


@dataclass(frozen=True, slots=True)
class RuntimeChoices:
    filesystem: dict[str, object] | None = None
    execution: dict[str, object] | None = None
    resources: dict[str, object] | None = None
    logging: dict[str, object] | None = None

    def reviewed_fragments(self) -> dict[str, object]:
        result: dict[str, object] = {}
        try:
            if self.filesystem is not None:
                result["filesystem"] = FilesystemConfig.model_validate(self.filesystem).model_dump(
                    mode="json"
                )
            if self.execution is not None:
                result["execution"] = ExecutionConfig.model_validate(self.execution).model_dump(
                    mode="json"
                )
            if self.resources is not None:
                result["resources"] = ResourcesConfig.model_validate(self.resources).model_dump(
                    mode="json"
                )
            if self.logging is not None:
                result["logging"] = LoggingConfig.model_validate(self.logging).model_dump(
                    mode="json"
                )
        except ValidationError as exc:
            raise SetupRuntimeError(
                "Advanced runtime choices are rejected by production models"
            ) from exc
        return result


def production_runtime_defaults() -> dict[str, dict[str, object]]:
    """Return current production defaults for the advanced editor.

    Keeping this derived from the production Pydantic models means the setup UI does not own a
    second set of default constants. A future model-default change appears here automatically.
    """

    return {
        "filesystem": FilesystemConfig().model_dump(mode="json"),
        "execution": ExecutionConfig().model_dump(mode="json"),
        "resources": ResourcesConfig().model_dump(mode="json"),
        "logging": LoggingConfig().model_dump(mode="json"),
    }


def runtime_editor_values(
    overrides: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    """Overlay persisted/existing reviewed values onto production defaults for presentation."""

    values = production_runtime_defaults()
    if overrides is None:
        return values
    for section_name, section_override in overrides.items():
        if section_name not in values or not isinstance(section_override, dict):
            continue
        values[section_name].update(
            {key: value for key, value in section_override.items() if isinstance(key, str)}
        )
    return values
