from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from jinja2 import StrictUndefined, Template


STATE_DIR_NAME = ".template-sync"
STATE_FILE_NAME = "state.json"


class TemplateSyncError(Exception):
    """Raised when template loading or application fails."""


@dataclass(frozen=True)
class TemplateParameter:
    name: str
    prompt: str | None = None
    default: str | None = None
    required: bool = True


@dataclass(frozen=True)
class TemplateFile:
    source: str
    target: str
    mode: str
    jinja: bool


@dataclass(frozen=True)
class TemplateDefinition:
    name: str
    description: str
    parameters: tuple[TemplateParameter, ...]
    files: tuple[TemplateFile, ...]


@dataclass(frozen=True)
class TemplateRepository:
    root: Path
    config_path: Path
    templates: dict[str, TemplateDefinition]


def load_template_repository(repo_path: Path, config_file: str = "templates.json") -> TemplateRepository:
    root = repo_path.expanduser().resolve()
    config_path = root / config_file

    if not root.is_dir():
        raise TemplateSyncError(f"Template repository path does not exist: {root}")
    if not config_path.is_file():
        raise TemplateSyncError(f"Template config file not found: {config_path}")

    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateSyncError(f"Invalid JSON in config file {config_path}: {exc}") from exc

    raw_templates = config_data.get("templates")
    if not isinstance(raw_templates, dict):
        raise TemplateSyncError("Config file must contain a top-level 'templates' object")

    templates: dict[str, TemplateDefinition] = {}
    for template_name, template_data in raw_templates.items():
        templates[template_name] = _parse_template_definition(template_name, template_data)

    if not templates:
        raise TemplateSyncError("No templates defined in config file")

    return TemplateRepository(root=root, config_path=config_path, templates=templates)


def _parse_template_definition(template_name: str, template_data: Any) -> TemplateDefinition:
    if not isinstance(template_data, dict):
        raise TemplateSyncError(f"Template '{template_name}' must be an object")

    description = str(template_data.get("description", ""))

    parameters = _parse_parameters(template_name, template_data.get("parameters", []))
    files = _parse_files(template_name, template_data.get("files"))

    return TemplateDefinition(
        name=template_name,
        description=description,
        parameters=tuple(parameters),
        files=tuple(files),
    )


def _parse_parameters(template_name: str, raw_parameters: Any) -> list[TemplateParameter]:
    if not isinstance(raw_parameters, list):
        raise TemplateSyncError(f"Template '{template_name}': 'parameters' must be a list")

    parameters: list[TemplateParameter] = []
    seen_names: set[str] = set()

    for idx, item in enumerate(raw_parameters):
        if isinstance(item, str):
            parameter = TemplateParameter(name=item)
        elif isinstance(item, dict):
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise TemplateSyncError(
                    f"Template '{template_name}': parameters[{idx}] requires non-empty string 'name'"
                )
            prompt = item.get("prompt")
            default = item.get("default")
            required = item.get("required", True)

            if prompt is not None and not isinstance(prompt, str):
                raise TemplateSyncError(
                    f"Template '{template_name}': parameters[{idx}] field 'prompt' must be a string"
                )
            if default is not None and not isinstance(default, str):
                default = str(default)
            if not isinstance(required, bool):
                raise TemplateSyncError(
                    f"Template '{template_name}': parameters[{idx}] field 'required' must be a boolean"
                )

            parameter = TemplateParameter(name=name, prompt=prompt, default=default, required=required)
        else:
            raise TemplateSyncError(
                f"Template '{template_name}': parameters[{idx}] must be a string or object"
            )

        if parameter.name in seen_names:
            raise TemplateSyncError(
                f"Template '{template_name}': duplicate parameter '{parameter.name}'"
            )

        seen_names.add(parameter.name)
        parameters.append(parameter)

    return parameters


def _parse_files(template_name: str, raw_files: Any) -> list[TemplateFile]:
    if not isinstance(raw_files, list) or not raw_files:
        raise TemplateSyncError(f"Template '{template_name}': 'files' must be a non-empty list")

    files: list[TemplateFile] = []
    for idx, item in enumerate(raw_files):
        if not isinstance(item, dict):
            raise TemplateSyncError(f"Template '{template_name}': files[{idx}] must be an object")

        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            raise TemplateSyncError(
                f"Template '{template_name}': files[{idx}] requires non-empty string 'source'"
            )

        mode = item.get("mode", "static")
        if mode not in {"static", "dynamic"}:
            raise TemplateSyncError(
                f"Template '{template_name}': files[{idx}] field 'mode' must be 'static' or 'dynamic'"
            )

        jinja = bool(item.get("jinja", False))
        target = item.get("target")
        if target is None:
            target = _default_target_name(source, jinja)
        if not isinstance(target, str) or not target.strip():
            raise TemplateSyncError(
                f"Template '{template_name}': files[{idx}] requires non-empty string 'target'"
            )

        files.append(TemplateFile(source=source, target=target, mode=mode, jinja=jinja))

    return files


def _default_target_name(source: str, jinja: bool) -> str:
    if jinja and source.endswith(".j2"):
        return source[:-3]
    return source


def parse_key_value_pairs(values: tuple[str, ...]) -> dict[str, str]:
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
    template: TemplateDefinition,
    provided_values: dict[str, str],
) -> list[TemplateParameter]:
    missing: list[TemplateParameter] = []
    for parameter in template.parameters:
        if parameter.name in provided_values:
            continue
        if parameter.default is not None:
            continue
        if parameter.required:
            missing.append(parameter)
    return missing


def apply_template(
    repository: TemplateRepository,
    template: TemplateDefinition,
    target_dir: Path,
    parameter_values: dict[str, str],
    force: bool,
) -> Path:
    unresolved_parameters = find_missing_parameters(template, parameter_values)
    if unresolved_parameters:
        missing_names = ", ".join(param.name for param in unresolved_parameters)
        raise TemplateSyncError(f"Missing required parameters: {missing_names}")

    final_parameters = dict(parameter_values)
    for parameter in template.parameters:
        if parameter.name not in final_parameters and parameter.default is not None:
            final_parameters[parameter.name] = parameter.default

    unknown_parameter_names = sorted(set(final_parameters.keys()) - {p.name for p in template.parameters})
    if unknown_parameter_names:
        unknown_text = ", ".join(unknown_parameter_names)
        raise TemplateSyncError(
            f"Unknown parameters for template '{template.name}': {unknown_text}"
        )

    target = target_dir.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    written_files: list[dict[str, Any]] = []
    for file_spec in template.files:
        source_path = (repository.root / file_spec.source).resolve()
        _validate_source_under_repository(repository.root, source_path, file_spec.source)
        if not source_path.is_file():
            raise TemplateSyncError(f"Template source file does not exist: {source_path}")

        destination_path = (target / file_spec.target).resolve()
        _validate_target_under_directory(target, destination_path, file_spec.target)
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if destination_path.exists() and not force:
            raise TemplateSyncError(
                f"Target file already exists: {destination_path}. Use --force to overwrite."
            )

        if file_spec.jinja:
            rendered = _render_template(source_path, final_parameters)
            destination_path.write_text(rendered, encoding="utf-8")
        else:
            shutil.copyfile(source_path, destination_path)

        written_files.append(
            {
                "source": file_spec.source,
                "target": str(destination_path.relative_to(target)),
                "mode": file_spec.mode,
                "jinja": file_spec.jinja,
                "source_sha256": _sha256_file(source_path),
            }
        )

    state_file = write_state_file(
        repository=repository,
        template=template,
        target_dir=target,
        parameters=final_parameters,
        file_records=written_files,
    )
    return state_file


def _render_template(path: Path, parameters: dict[str, str]) -> str:
    template_source = path.read_text(encoding="utf-8")
    template = Template(template_source, undefined=StrictUndefined, keep_trailing_newline=True)
    try:
        return template.render(**parameters)
    except Exception as exc:
        raise TemplateSyncError(f"Failed to render Jinja2 template {path}: {exc}") from exc


def _validate_source_under_repository(repository_root: Path, source_path: Path, source_label: str) -> None:
    try:
        source_path.relative_to(repository_root)
    except ValueError as exc:
        raise TemplateSyncError(
            f"Template source path escapes repository root: {source_label}"
        ) from exc


def _validate_target_under_directory(target_root: Path, target_path: Path, target_label: str) -> None:
    try:
        target_path.relative_to(target_root)
    except ValueError as exc:
        raise TemplateSyncError(
            f"Target path escapes destination directory: {target_label}"
        ) from exc


def write_state_file(
    repository: TemplateRepository,
    template: TemplateDefinition,
    target_dir: Path,
    parameters: dict[str, str],
    file_records: list[dict[str, Any]],
) -> Path:
    state_dir = target_dir / STATE_DIR_NAME
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / STATE_FILE_NAME

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "template": template.name,
        "parameters": parameters,
        "template_repository": {
            "path": str(repository.root),
            "config_file": str(repository.config_path.name),
            "commit": get_repository_commit(repository.root),
        },
        "files": file_records,
    }

    state_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state_file


def get_repository_commit(repository_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    commit = completed.stdout.strip()
    return commit if commit else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while True:
            chunk = file_handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
