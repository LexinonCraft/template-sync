"""
Command-line interface for template-sync.
"""

from __future__ import annotations

from pathlib import Path

import click
from more_termcolor import colored

from template_sync.core import (
    DEFAULTS_CONFIG_FILE_NAME,
    TemplateDefinition,
    TemplateSyncError,
    apply_template,
    load_template_repository,
    parse_key_value_pairs,
)
from template_sync.model import DefaultsConfig, parse_defaults_config


@click.group(help="Manage and apply template bundles from a repository.")
def entrypoint() -> None:
    pass


def _style(message: str, color: str) -> str:
    """Apply terminal color to a message.

    Args:
        message: Text to colorize.
        color: Foreground color name.

    Returns:
        Colorized text suitable for terminal output.
    """
    return colored(message, color)


def _echo_info(message: str) -> None:
    """Print an informational message in a consistent style.

    Args:
        message: Informational text.

    Returns:
        None.
    """
    click.echo(_style(message, "cyan"))


def _echo_success(message: str) -> None:
    """Print a success message in a consistent style.

    Args:
        message: Success text.

    Returns:
        None.
    """
    click.echo(_style(message, "green"))


def _echo_note(message: str) -> None:
    """Print a neutral note message in a consistent style.

    Args:
        message: Note text.

    Returns:
        None.
    """
    click.echo(_style(message, "white"))


@entrypoint.command("list")
@click.option("--repo", "repo_path", required=True, type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--config", "config_file", show_default=True)
def list_templates(repo_path: Path, config_file: str | None = None) -> None:
    try:
        resolved_repo_path = repo_path.expanduser().resolve()
        repository = load_template_repository(repo_path=resolved_repo_path, config_file=config_file)
    except TemplateSyncError as exc:
        raise click.ClickException(str(exc)) from exc

    _echo_info(f"Templates in {resolved_repo_path}:")
    for name in sorted(repository.config.templates):
        template = repository.config.templates[name]
        description = f" - {template.description}" if template.description else ""
        _echo_note(f"- {name}{description} (parameters: {len(template.parameters)}, files: {len(template.files)})")


@entrypoint.command("apply", help="Apply a template from a repository to a target directory.")
@click.argument("target_dir", type=click.Path(path_type=Path, file_okay=False))
@click.argument("template_name", required=False)
@click.option("--repo", "repo_path", required=True, type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--rev", "repo_rev", default=None, help="Git revision to checkout in the repository.")
@click.option("--config", "config_file", show_default=True, default=None)
@click.option("-p", "--parameter", "parameter_overrides", multiple=True, help="Parameter override in KEY=VALUE form.")
@click.option("--non-interactive", is_flag=True, help="Fail if required parameters are missing.")
@click.option("--force", is_flag=True, help="Overwrite existing files in target directory.")  # TODO
def apply_template_command(
    target_dir: Path,
    template_name: str | None,
    repo_path: Path,
    repo_rev: str | None,
    config_file: str | None,
    parameter_overrides: tuple[str, ...],
    non_interactive: bool,
    force: bool,
) -> None:
    try:
        resolved_repo_path = repo_path.expanduser().resolve()
        repository = load_template_repository(repo_path=resolved_repo_path, repo_rev=repo_rev, config_file=config_file)

        defaults_config_path = Path(DEFAULTS_CONFIG_FILE_NAME)
        default_parameter_overrides = {}
        defaults_config: DefaultsConfig | None = None
        if defaults_config_path.exists():
            with defaults_config_path.open() as f:
                _defaults_config = parse_defaults_config(f.read())
                default_parameter_overrides = _defaults_config.default_parameters
                defaults_config = _defaults_config

        selected_template_name, selected_template = _resolve_template_selection(
            repository.config.templates,
            template_name or (defaults_config.default_template if defaults_config and defaults_config.default_template else None),
        )
        _echo_info(
            f"Using template '{selected_template_name}' from {resolved_repo_path}{f" at revision '{repository.repo.get_ref()}'" if repository.repo.get_ref() else ''}."
        )
        _echo_note(f"Target directory: {target_dir.resolve()}")

        parameter_values = parse_key_value_pairs(parameter_overrides)
        if not non_interactive:
            parameter_values = _collect_missing_parameter_values(
                selected_template_name, selected_template, parameter_values, default_parameter_overrides, target_dir
            )
        state_file = apply_template(
            repository=repository,
            template=selected_template,
            template_name=selected_template_name,
            target_dir=target_dir,
            current_dir=Path.cwd(),
            parameter_values=parameter_values,
            force=force,
        )
    except TemplateSyncError as exc:
        raise click.ClickException(str(exc)) from exc

    _echo_success(f"Applied template '{selected_template_name}' to {target_dir.resolve()}")
    _echo_note(f"State written to: {state_file}")


def _resolve_template_selection(
    templates: dict[str, TemplateDefinition],
    selected_name: str | None,
) -> tuple[str, TemplateDefinition]:
    """Resolve a template choice from explicit name or interactive selection.

    Args:
        templates: Available templates keyed by template name.
        selected_name: Optional user-provided template name.

    Returns:
        A tuple containing the selected template name and the TemplateDefinition.

    Raises:
        TemplateSyncError: If selected_name is provided but not found.
    """
    if selected_name:
        template = templates.get(selected_name)
        if template is None:
            known_templates = ", ".join(sorted(templates))
            raise TemplateSyncError(f"Unknown template '{selected_name}'. Available templates: {known_templates}")
        return selected_name, template

    ordered_names = sorted(templates)
    _echo_info("Choose a template:")
    for idx, name in enumerate(ordered_names, start=1):
        description = templates[name].description
        suffix = f" - {description}" if description else ""
        _echo_note(f"{idx}. {name}{suffix}")

    selected_index = click.prompt(
        "Template number",
        type=click.IntRange(min=1, max=len(ordered_names)),
    )
    template_name = ordered_names[selected_index - 1]
    return template_name, templates[template_name]


def _collect_missing_parameter_values(
    template_name: str,
    template: TemplateDefinition,
    current_values: dict[str, str],
    default_parameter_overrides: dict[str, str],
    target_dir: Path,
) -> dict[str, str]:
    """Prompt user for any template parameters that are still missing.

    Args:
        template_name: The name of the template being processed.
        template: Template definition containing parameter metadata.
        current_values: Already provided parameter values.
        default_parameter_overrides: Default values for template parameters from the defaults configuration.
        target_dir: The target directory path, used for dirname_as_default parameters.

    Returns:
        Completed parameter map including prompted/defaulted values.
    """
    values = dict(current_values)
    missing_parameters = [parameter for parameter in template.parameters if parameter.name not in values]
    if missing_parameters:
        _echo_info(f"{len(missing_parameters)} argument(s) missing for template '{template_name}'.")
        _echo_note("You can provide these with -p KEY=VALUE to skip prompts.")

    for parameter in template.parameters:
        if parameter.name in values:
            continue

        prompt_label = parameter.prompt or parameter.name
        if parameter.dirname_as_default and target_dir.name:
            values[parameter.name] = click.prompt(prompt_label, default=target_dir.name, show_default=True)
            continue

        if parameter.default is not None:
            values[parameter.name] = click.prompt(prompt_label, default=parameter.default, show_default=True)
            continue

        if parameter.name in default_parameter_overrides:
            values[parameter.name] = click.prompt(prompt_label, default=default_parameter_overrides[parameter.name], show_default=True)
            continue

        if parameter.required:
            values[parameter.name] = click.prompt(prompt_label)
            continue

        optional_value = click.prompt(prompt_label, default="", show_default=False)
        if optional_value != "":
            values[parameter.name] = optional_value

    return values


if __name__ == "__main__":
    entrypoint()
