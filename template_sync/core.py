"""
Core functionality for template-sync, including loading templates, applying them to target directories, and managing state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, Template

from .git_utils import AbstractRepo, get_repo
from .model import (
    ParameterDefinition,
    StateFileRecord,
    StateTemplateRepository,
    TemplateDefinition,
    TemplateRepositoryConfig,
    TemplateSyncState,
    parse_template_repo_config,
    serialize_state_file,
)

STATE_DIR_NAME = ".template-sync"
STATE_FILE_NAME = "state.json"

TEMPLATE_REPO_CONFIG_FILE_NAME = "templates.yaml"


class TemplateSyncError(Exception):
    """Raised when template loading or application fails."""


@dataclass(frozen=True)
class TemplateRepository:
    repo: AbstractRepo
    config: TemplateRepositoryConfig


def load_template_repository(repo_path: Path, repo_rev: str | None = None, config_file: str | None = None) -> TemplateRepository:
    """Load and validate template repository configuration.

    Args:
        repo_path: Path to the root of the template repository.
        repo_rev: Optional git revision to checkout before reading the config.
        config_file: Name of the YAML config file inside repo_path.

    Returns:
        Parsed TemplateRepository with validated template definitions.

    Raises:
        TemplateSyncError: If paths are invalid, YAML is invalid, or schema checks fail.
    """
    repo = get_repo(repo_path, rev=repo_rev)

    config_content = repo.read_file(config_file or TEMPLATE_REPO_CONFIG_FILE_NAME)
    config = parse_template_repo_config(config_content)

    return TemplateRepository(repo=repo, config=config)


def parse_key_value_pairs(values: tuple[str, ...]) -> dict[str, str]:
    """Parse CLI-style KEY=VALUE arguments into a dictionary.

    Args:
        values: Tuple of raw override strings passed via CLI.

    Returns:
        Mapping from parameter names to provided string values.

    Raises:
        TemplateSyncError: If any item is not in KEY=VALUE format.
    """
    result: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise TemplateSyncError(f"Invalid parameter override '{raw}'. Use KEY=VALUE format.")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise TemplateSyncError(f"Invalid parameter override '{raw}': key cannot be empty.")
        result[key] = value
    return result


def find_missing_parameters(
    template: TemplateDefinition, provided_values: dict[str, str], target_dir_name_provided: bool
) -> list[ParameterDefinition]:
    """Find required parameters not satisfied by provided values/defaults.

    Args:
        template: Template definition containing parameter metadata.
        provided_values: Parameter values currently available.

    Returns:
        Required parameters that still need explicit values.
    """
    missing: list[ParameterDefinition] = []
    for parameter in template.parameters:
        if parameter.name in provided_values:
            continue
        if parameter.default is not None:
            continue
        if parameter.dirname_as_default and target_dir_name_provided:
            continue
        if parameter.required:
            missing.append(parameter)
    return missing


def apply_template(
    repository: TemplateRepository,
    template_name: str,
    template: TemplateDefinition,
    target_dir: Path,
    current_dir: Path,
    parameter_values: dict[str, str],
    force: bool,
) -> Path:
    """Apply one template into a target directory and persist generation state.

    Args:
        repository: Template repository metadata and root path.
        template: Template definition to apply.
        target_dir: Destination directory for copied/rendered files.
        current_dir: Current working directory at the time of invocation.
        parameter_values: Parameter values used for Jinja2 rendering.
        force: If True, overwrite existing target files.

    Returns:
        Path to the generated state file in target_dir.

    Raises:
        TemplateSyncError: If validation fails, files are missing, or writes are unsafe.
    """
    unresolved_parameters = find_missing_parameters(template, parameter_values, target_dir.name != "")
    if unresolved_parameters:
        missing_names = ", ".join(param.name for param in unresolved_parameters)
        raise TemplateSyncError(f"Missing required parameters: {missing_names}")

    final_parameters = dict(parameter_values)
    for parameter in template.parameters:
        if parameter.name not in final_parameters:
            if parameter.dirname_as_default and target_dir.name != "":
                final_parameters[parameter.name] = target_dir.name
                continue
            if parameter.default is not None:
                final_parameters[parameter.name] = parameter.default

    unknown_parameter_names = sorted(set(final_parameters.keys()) - {p.name for p in template.parameters})
    if unknown_parameter_names:
        unknown_text = ", ".join(unknown_parameter_names)
        raise TemplateSyncError(f"Unknown parameters for template '{template_name}': {unknown_text}")

    target = current_dir / target_dir
    target.mkdir(parents=True, exist_ok=True)

    for file_spec in template.files:
        destination_path = (target / file_spec.target).resolve()
        if destination_path.exists() and not force:
            raise TemplateSyncError(f"Target file already exists: {destination_path}. Use --force to overwrite.")
    if (target / STATE_DIR_NAME / STATE_FILE_NAME).exists() and not force:
        raise TemplateSyncError(f"State file already exists: {target_dir / STATE_DIR_NAME / STATE_FILE_NAME}. Use --force to overwrite.")

    written_files: list[dict[str, Any]] = []
    for file_spec in template.files:
        destination_path = (target / file_spec.target).resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if file_spec.jinja:
            rendered = _render_template(repository.repo.read_file(file_spec.source), final_parameters)
            destination_path.write_text(rendered, encoding="utf-8")
        else:
            repository.repo.copy_file(Path(file_spec.source), destination_path)

        written_files.append(
            {
                "source": file_spec.source,
                "target": str(destination_path.relative_to(target)),
                "mode": file_spec.mode,
                "jinja": file_spec.jinja,
                "source_sha256": _sha256_file(repository.repo.read_file(file_spec.source)),
            }
        )

    state_file = write_state_file(
        repository=repository,
        template_name=template_name,
        target_dir=target,
        parameters=final_parameters,
        file_records=written_files,
    )
    return state_file


def _render_template(source: str, parameters: dict[str, str]) -> str:
    """Render a Jinja2 template file with strict variable handling.

    Args:
        source: Source content of the template file.
        parameters: Values exposed to the Jinja2 render context.

    Returns:
        Rendered text content.

    Raises:
        TemplateSyncError: If Jinja2 rendering fails.
    """
    template = Template(source, undefined=StrictUndefined, keep_trailing_newline=True)
    try:
        return template.render(**parameters)
    except Exception as exc:
        raise TemplateSyncError(f"Failed to render Jinja2 template: {exc}") from exc


def write_state_file(
    repository: TemplateRepository,
    template_name: str,
    target_dir: Path,
    parameters: dict[str, str],
    file_records: list[dict[str, Any]],
) -> Path:
    """Write generation metadata used for traceability and future sync features.

    Args:
        repository: Source repository metadata.
        template_name: Name of the applied template.
        target_dir: Directory where output files were written.
        parameters: Final parameter values used for rendering.
        file_records: Per-file metadata records for generated files.

    Returns:
        Path to the written state.json file.
    """
    state_dir = target_dir / STATE_DIR_NAME
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / STATE_FILE_NAME

    state = TemplateSyncState(
        generated_at=datetime.now(tz=UTC),
        template=template_name,
        parameters=parameters,
        template_repository=StateTemplateRepository(
            path=str(repository.repo.get_root()),
            config_file="templates.yaml",
            ref=repository.repo.get_ref(),
            commit=repository.repo.get_commit_hash(),
        ),
        files=[StateFileRecord(**file_record) for file_record in file_records],
    )
    state_file.write_text(serialize_state_file(state), encoding="utf-8")
    return state_file


def _sha256_file(content: str) -> str:
    """Compute SHA-256 checksum for a file.

    Args:
        content: File content to hash.

    Returns:
        Lowercase hex digest of the file contents.
    """
    digest = hashlib.sha256()
    digest.update(content.encode("utf-8"))
    return digest.hexdigest()
