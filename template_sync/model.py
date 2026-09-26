import json
from datetime import datetime
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator

CURRENT_TEMPLATE_REPO_CONFIG_VERSION = 1
CURRENT_STATE_SCHEMA_VERSION = 1


def coerce_to_str(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    else:
        raise TypeError(f"Cannot coerce type {type(value)} to string")


# Model definitions for template repository configuration


class ParameterDefinition(BaseModel):
    name: Annotated[str, BeforeValidator(coerce_to_str)]
    prompt: Annotated[str | None, BeforeValidator(coerce_to_str)] = None
    default: Annotated[str | None, BeforeValidator(coerce_to_str)] = None
    dirname_as_default: bool = False
    required: bool = False


class FileDefinition(BaseModel):
    source: str
    target: str
    mode: Literal["static", "dynamic"]
    jinja: bool = False


class TemplateDefinition(BaseModel):
    description: Annotated[str, BeforeValidator(coerce_to_str)]
    parameters: list[ParameterDefinition]
    files: list[FileDefinition]


class TemplateRepositoryConfig(BaseModel):
    version: int | Literal["snapshot"]
    templates: dict[Annotated[str, BeforeValidator(coerce_to_str)], TemplateDefinition]


def parse_template_repo_config(config: str) -> TemplateRepositoryConfig:
    """
    Parse a YAML string into a TemplateRepositoryConfig object.

    Args:
        config (str): YAML string of the configuration.

    Returns:
        TemplateRepositoryConfig: The parsed template repository configuration.
    """
    config_data = yaml.safe_load(config)

    if not isinstance(config_data, dict):
        raise TypeError("Config data must be a dictionary")

    schema_version = config_data.get("version")
    if schema_version is None:
        raise ValueError("Missing config schema version")
    if schema_version not in [CURRENT_TEMPLATE_REPO_CONFIG_VERSION, "snapshot"]:
        raise ValueError(f"Unsupported config schema version: {schema_version}")

    # here we could apply migration logic

    return TemplateRepositoryConfig.model_validate(config_data)


# Model definitions for state file


class StateFileRecord(BaseModel):
    source: str
    target: str
    mode: Literal["static", "dynamic"]
    jinja: bool
    source_sha256: str


class StateTemplateRepository(BaseModel):
    path: str
    config_file: str
    ref: str | None
    commit: str | None


class TemplateSyncState(BaseModel):
    schema_version: int = CURRENT_STATE_SCHEMA_VERSION
    generated_at: datetime
    template: str
    parameters: dict[str, str]
    template_repository: StateTemplateRepository
    files: list[StateFileRecord]


def parse_state_file(state: str) -> TemplateSyncState:
    """Parse and validate a JSON template-sync state file."""
    try:
        state_data = json.loads(state)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid state JSON: {exc.msg}") from exc

    schema_version = state_data.get("schema_version")
    if schema_version is None:
        raise ValueError("Missing state schema version")
    if schema_version != CURRENT_STATE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported state schema version: {schema_version}")

    return TemplateSyncState.model_validate(state_data)


def serialize_state_file(state: TemplateSyncState) -> str:
    """Convert a validated template-sync state to formatted JSON."""
    return state.model_dump_json(indent=2) + "\n"
