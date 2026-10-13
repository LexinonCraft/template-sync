"""
Core functionality for template-sync, including loading templates, applying them to target directories, and managing state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import StrictUndefined, Template

from .git_utils import AbstractRepo, get_repo
from .model import (
    FileDefinition,
    ParameterDefinition,
    StateFileRecord,
    StateTemplateRepository,
    TemplateDefinition,
    TemplateRepositoryConfig,
    TemplateSyncState,
    parse_state_file,
    parse_template_repo_config,
    serialize_state_file,
)

STATE_DIR_NAME = ".template-sync"
STATE_FILE_NAME = "state.json"

TEMPLATE_REPO_CONFIG_FILE_NAME = "templates.yaml"
DEFAULTS_CONFIG_FILE_NAME = "template-sync.yaml"


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

    written_files: list[StateFileRecord] = []
    for file_spec in template.files:
        destination_path = (target / file_spec.target).resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if file_spec.jinja:
            rendered = render_template(repository.repo.read_file(file_spec.source), final_parameters)
            destination_path.write_text(rendered, encoding="utf-8")
        else:
            repository.repo.copy_file(Path(file_spec.source), destination_path)

        written_files.append(
            StateFileRecord(
                source=file_spec.source,
                target=str(destination_path.relative_to(target)),
                mode=file_spec.mode,
                jinja=file_spec.jinja,
                source_sha256=sha256_str(repository.repo.read_file(file_spec.source)),
            )
        )

    state_file = create_state_file(
        repository=repository,
        template_name=template_name,
        target_dir=target,
        parameters=final_parameters,
        file_records=written_files,
    )
    return state_file


def render_template(source: str, parameters: dict[str, str]) -> str:
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


def create_state_file(
    repository: TemplateRepository,
    template_name: str,
    target_dir: Path,
    parameters: dict[str, str],
    file_records: list[StateFileRecord],
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
        files={file_record.target: file_record for file_record in file_records},
    )
    return write_state_file(state, target_dir)


def write_state_file(state: TemplateSyncState, target_dir: Path) -> Path:
    """Write the current state to the state file.

    Args:
        state: The current template sync state.
        target_dir: The directory where the state file should be written.

    Returns:
        Path to the written state.json file.
    """
    state_dir = target_dir / STATE_DIR_NAME
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / STATE_FILE_NAME
    state_file.write_text(serialize_state_file(state), encoding="utf-8")
    return state_file


def sha256_str(content: str) -> str:
    """Compute SHA-256 checksum for a string.

    Args:
        content: String to hash.

    Returns:
        Lowercase hex digest of the string.
    """
    digest = hashlib.sha256()
    digest.update(content.encode("utf-8"))
    return digest.hexdigest()


def sha256_file(file_path: Path) -> str:
    """Compute SHA-256 checksum for a file.

    Args:
        file_path: Path to the file to hash.

    Returns:
        Lowercase hex digest of the file contents.
    """
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_file_definition_for_target_file(repository: TemplateRepository, template: str, target_file: Path) -> FileDefinition | None:
    """Get the template file details corresponding to the target file.

    Args:
        repository: The template repository.
        template: The name of the template within the repository.
        target_file: The target file path within the template output. This should be relative to the template root.

    Returns:
        The FileDefinition corresponding to the target file, or None if it doesn't exist.
    """
    for file in repository.config.templates[template].files:
        if Path(file.target) == target_file:
            return file
    return None


def render_file(repository: TemplateRepository, file_definition: FileDefinition, parameters: dict[str, str]) -> str:
    """Render a template file with the given parameters.

    Args:
        repository: The template repository.
        file_definition: The file definition of the template file to render.
        parameters: The parameters to use for rendering.

    Returns:
        The rendered file content as a string.
    """
    if file_definition.jinja:
        return render_template(repository.repo.read_file(file_definition.source), parameters)
    else:
        return repository.repo.read_file(file_definition.source)


def write_rendered_file(
    repository: TemplateRepository, file_definition: FileDefinition, parameters: dict[str, str], output_path: Path
) -> str:
    """Render a template file and write it to the specified output path.

    Args:
        repository: The template repository.
        file_definition: The file definition of the template file to render.
        parameters: The parameters to use for rendering.
        output_path: The path to write the rendered file to.
    """
    content = render_file(repository, file_definition, parameters)
    output_path.write_text(content, encoding="utf-8")
    return sha256_str(content)


def load_state_and_repository(target_dir: Path) -> tuple[TemplateSyncState, TemplateRepository]:
    """Load the state file and the corresponding template repository.

    Args:
        target_dir: The target directory containing the state file.

    Returns:
        A tuple containing the loaded TemplateSyncState and TemplateRepository.
    """
    state_file_path = target_dir / STATE_DIR_NAME / STATE_FILE_NAME
    with open(state_file_path, "r") as state_file:
        state_file_content = state_file.read()
    state = parse_state_file(state_file_content)
    repository = load_template_repository(
        repo_path=Path(state.template_repository.path),
        repo_rev=state.template_repository.commit,
        config_file=state.template_repository.config_file,
    )
    return state, repository


def regenerate_files(state: TemplateSyncState, repository: TemplateRepository, target_dir: Path, files: Generator[FileDefinition]) -> None:
    """Regenerate the specified files in the target directory (also updates the state file).

    Args:
        state: The current TemplateSyncState.
        repository: The template repository corresponding to the state.
        target_dir: The target directory containing the files.
        files: A list of FileDefinition objects representing the files to regenerate.
    """
    for file in files:
        output_path = target_dir / file.target
        file_hash = write_rendered_file(repository, file, state.parameters, output_path)
        state.files[file.target] = StateFileRecord(
            source=file.source,
            target=file.target,
            mode=file.mode,
            jinja=file.jinja,
            source_sha256=file_hash,
        )
    write_state_file(state, target_dir)
