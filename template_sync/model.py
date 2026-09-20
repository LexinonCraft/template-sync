import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import yaml

CURRENT_TEMPLATE_REPO_CONFIG_VERSION = 1
CURRENT_STATE_SCHEMA_VERSION = 1

# Model definitions for template repository configuration

@dataclass
class ParameterDefinition:
    name: str
    prompt: str | None = None
    default: str | None = None
    required: bool = False

@dataclass
class FileDefinition:
    source: str
    target: str
    mode: Literal["static", "dynamic"]
    jinja: bool = False

@dataclass
class TemplateDefinition:
    description: str
    parameters: list[ParameterDefinition]
    files: list[FileDefinition]

@dataclass
class TemplateRepositoryConfig:
    templates: dict[str, TemplateDefinition]

def _validate_template_repo_config(config_data: dict) -> TemplateRepositoryConfig:
    if not "version" in config_data:
        raise ValueError("Missing config version")
    if config_data["version"] not in [CURRENT_TEMPLATE_REPO_CONFIG_VERSION, "snapshot"]:
        raise ValueError(f"Unsupported config version: {config_data["version"]}")

    # TODO: Add further validation for the structure of the config data (e.g., required keys, unexpected keys, types)
    return TemplateRepositoryConfig(
        templates={
            template_name: TemplateDefinition(
                description=template["description"],
                parameters=[
                    ParameterDefinition(
                        name=param if isinstance(param, str) else param["name"],
                        prompt=None if isinstance(param, str) else param.get("prompt"),
                        default=None if isinstance(param, str) else (str(param.get("default")) if param.get("default") is not None else None),
                        required=False if isinstance(param, str) else param.get("required", False),
                    )
                    for param in template.get("parameters", [])
                ],
                files=[
                    FileDefinition(
                        source=file["source"],
                        target=file["target"],
                        mode=file["mode"],
                        jinja=file.get("jinja", False),
                    )
                    for file in template.get("files", [])
                ],
            )
            for template_name, template in config_data.get("templates", {}).items()
        }
    )

def parse_template_repo_config(config: str) -> TemplateRepositoryConfig:
    """
    Parse a YAML string into a TemplateRepositoryConfig object.

    Args:
        config (str): YAML string of the configuration.

    Returns:
        TemplateRepositoryConfig: The parsed template repository configuration.
    """
    config_data = yaml.safe_load(config)

    # here we could apply migration logic

    return _validate_template_repo_config(config_data)

# Model definitions for state file

@dataclass
class StateFileRecord:
    source: str
    target: str
    mode: Literal["static", "dynamic"]
    jinja: bool
    source_sha256: str

@dataclass
class StateTemplateRepository:
    path: str
    config_file: str
    ref: str | None
    commit: str | None

@dataclass
class TemplateSyncState:
    generated_at: datetime
    template: str
    parameters: dict[str, str]
    template_repository: StateTemplateRepository
    files: list[StateFileRecord]

def _required_value(data: dict, key: str) -> object:
    if key not in data:
        raise ValueError(f"Missing state field: {key}")
    return data[key]

def _string_value(data: dict, key: str, *, allow_none: bool = False) -> str | None:
    value = _required_value(data, key)
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"State field '{key}' must be a non-empty string")
    return value

def _validate_state_file_record(data: object) -> StateFileRecord:
    if not isinstance(data, dict):
        raise ValueError("State file entries must be objects")  # noqa: TRY004

    mode = _string_value(data, "mode")
    if mode not in ("static", "dynamic"):
        raise ValueError(f"Unsupported state file mode: {mode}")

    jinja = _required_value(data, "jinja")
    if not isinstance(jinja, bool):
        raise ValueError("State field 'jinja' must be a boolean")  # noqa: TRY004

    source_sha256 = _string_value(data, "source_sha256")
    if source_sha256 is None:
        raise ValueError("State field 'source_sha256' must be a SHA-256 hex digest")
    if re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256) is None:
        raise ValueError("State field 'source_sha256' must be a SHA-256 hex digest")

    source = _string_value(data, "source")
    target = _string_value(data, "target")
    if source is None or target is None or mode is None:
        raise ValueError("State file fields must be non-empty strings")

    return StateFileRecord(
        source=source,
        target=target,
        mode=mode,
        jinja=jinja,
        source_sha256=source_sha256,
    )

def _validate_state(data: object) -> TemplateSyncState:
    if not isinstance(data, dict):
        raise ValueError("State file must contain a JSON object")  # noqa: TRY004

    schema_version = _required_value(data, "schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ValueError("State field 'schema_version' must be an integer")  # noqa: TRY004
    if schema_version != CURRENT_STATE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported state schema version: {schema_version}")

    generated_at_value = _string_value(data, "generated_at")
    if generated_at_value is None:
        raise ValueError("State field 'generated_at' must be an ISO-8601 timestamp")
    try:
        generated_at = datetime.fromisoformat(generated_at_value)
    except ValueError as exc:
        raise ValueError("State field 'generated_at' must be an ISO-8601 timestamp") from exc

    parameters = _required_value(data, "parameters")
    if not isinstance(parameters, dict) or any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in parameters.items()
    ):
        raise ValueError("State field 'parameters' must map strings to strings")

    repository_data = _required_value(data, "template_repository")
    if not isinstance(repository_data, dict):
        raise ValueError("State field 'template_repository' must be an object")  # noqa: TRY004

    files = _required_value(data, "files")
    if not isinstance(files, list):
        raise ValueError("State field 'files' must be a list")  # noqa: TRY004

    template = _string_value(data, "template")
    path = _string_value(repository_data, "path")
    config_file = _string_value(repository_data, "config_file")
    if template is None or path is None or config_file is None:
        raise ValueError("State fields must be non-empty strings")

    return TemplateSyncState(
        generated_at=generated_at,
        template=template,
        parameters=parameters,
        template_repository=StateTemplateRepository(
            path=path,
            config_file=config_file,
            ref=_string_value(repository_data, "ref", allow_none=True),
            commit=_string_value(repository_data, "commit", allow_none=True),
        ),
        files=[_validate_state_file_record(file_data) for file_data in files],
    )

def parse_state_file(state: str) -> TemplateSyncState:
    """Parse and validate a JSON template-sync state file."""
    try:
        state_data = json.loads(state)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid state JSON: {exc.msg}") from exc

    return _validate_state(state_data)

def serialize_state_file(state: TemplateSyncState) -> str:
    """Convert a validated template-sync state to formatted JSON."""
    payload = {
        "schema_version": CURRENT_STATE_SCHEMA_VERSION,
        "generated_at": state.generated_at.isoformat(),
        "template": state.template,
        "parameters": state.parameters,
        "template_repository": {
            "path": state.template_repository.path,
            "config_file": state.template_repository.config_file,
            "ref": state.template_repository.ref,
            "commit": state.template_repository.commit,
        },
        "files": [
            {
                "source": file_record.source,
                "target": file_record.target,
                "mode": file_record.mode,
                "jinja": file_record.jinja,
                "source_sha256": file_record.source_sha256,
            }
            for file_record in state.files
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


