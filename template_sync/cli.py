"""
Command-line interface for template-sync.
"""

from __future__ import annotations

from pathlib import Path

import click
from more_termcolor import colored

from template_sync.core import (
    TemplateDefinition,
    TemplateSyncError,
    apply_template,
    load_template_repository,
    parse_key_value_pairs,
)


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
@click.option("--config", "config_file", default="templates.json", show_default=True)
def list_templates(repo_path: Path, config_file: str) -> None:
    """List template names and summary info from a template repository.

    Args:
        repo_path: Path to the template repository root.
        config_file: Template config filename inside the repository.

    Returns:
        None.
    """
    try:
        resolved_repo_path = repo_path.expanduser().resolve()
        repository = load_template_repository(repo_path=resolved_repo_path, config_file=config_file)
    except TemplateSyncError as exc:
        raise click.ClickException(str(exc)) from exc

    _echo_info(f"Templates in {resolved_repo_path}:")
    for name in sorted(repository.templates):
        template = repository.templates[name]
        description = f" - {template.description}" if template.description else ""
        _echo_note(
            f"- {template.name}{description}"
            f" (parameters: {len(template.parameters)}, files: {len(template.files)})"
        )


@entrypoint.command("apply", help="Apply a template from a repository to a target directory.")
@click.argument("template_name", required=False)
@click.option("--repo", "repo_path", required=True, type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--rev", "repo_rev", default=None, help="Git revision to checkout in the repository.")
@click.option("--target-dir", default=".", type=click.Path(path_type=Path, file_okay=False), show_default=True)
@click.option("--config", "config_file", default="templates.json", show_default=True)
@click.option("-p", "--parameter", "parameter_overrides", multiple=True, help="Parameter override in KEY=VALUE form.")
@click.option("--non-interactive", is_flag=True, help="Fail if required parameters are missing.")
@click.option("--force", is_flag=True, help="Overwrite existing files in target directory.")  # TODO
def apply_template_command(
    template_name: str | None,
    repo_path: Path,
    repo_rev: str | None,
    target_dir: Path,
    config_file: str,
    parameter_overrides: tuple[str, ...],
    non_interactive: bool,
    force: bool,
) -> None:
    try:
        resolved_repo_path = repo_path.expanduser().resolve()
        repository = load_template_repository(repo_path=resolved_repo_path, repo_rev=repo_rev, config_file=config_file)
        selected_template = _resolve_template_selection(repository.templates, template_name)
        _echo_info(f"Using template '{selected_template.name}' from {resolved_repo_path}{f" at revision '{repository.repo.get_ref()}'" if repository.repo.get_ref() else ''}.")
        _echo_note(f"Target directory: {target_dir.resolve()}")

        parameter_values = parse_key_value_pairs(parameter_overrides)
        if not non_interactive:
            parameter_values = _collect_missing_parameter_values(selected_template, parameter_values)

        state_file = apply_template(
            repository=repository,
            template=selected_template,
            target_dir=target_dir,
            parameter_values=parameter_values,
            force=force,
        )
    except TemplateSyncError as exc:
        raise click.ClickException(str(exc)) from exc

    _echo_success(f"Applied template '{selected_template.name}' to {target_dir.resolve()}")
    _echo_note(f"State written to: {state_file}")


@entrypoint.command("config-example")
def config_example() -> None:
    """Print an example templates.json configuration payload.

    Args:
        None.

    Returns:
        None.
    """
    click.echo(
        """{
  "templates": {
    "typst-assignment": {
      "description": "Typst assignment submission",
      "parameters": [
        "class_name",
        {"name": "student_name", "prompt": "Student name"},
        {"name": "year", "default": "2026"}
      ],
      "files": [
        {
          "source": "typst/common.typ",
          "target": "common.typ",
          "mode": "static",
          "jinja": false
        },
        {
          "source": "typst/assignment.typ.j2",
          "target": "main.typ",
          "mode": "dynamic",
          "jinja": true
        }
      ]
    }
  }
}"""
    )


def _resolve_template_selection(
    templates: dict[str, TemplateDefinition],
    selected_name: str | None,
) -> TemplateDefinition:
    """Resolve a template choice from explicit name or interactive selection.

    Args:
        templates: Available templates keyed by template name.
        selected_name: Optional user-provided template name.

    Returns:
        The selected TemplateDefinition.

    Raises:
        TemplateSyncError: If selected_name is provided but not found.
    """
    if selected_name:
        template = templates.get(selected_name)
        if template is None:
            known_templates = ", ".join(sorted(templates))
            raise TemplateSyncError(
                f"Unknown template '{selected_name}'. Available templates: {known_templates}"
            )
        return template

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
    return templates[ordered_names[selected_index - 1]]


def _collect_missing_parameter_values(
    template: TemplateDefinition,
    current_values: dict[str, str],
) -> dict[str, str]:
    """Prompt user for any template parameters that are still missing.

    Args:
        template: Template definition containing parameter metadata.
        current_values: Already provided parameter values.

    Returns:
        Completed parameter map including prompted/defaulted values.
    """
    values = dict(current_values)
    missing_parameters = [parameter for parameter in template.parameters if parameter.name not in values]
    if missing_parameters:
        _echo_info(
            f"{len(missing_parameters)} argument(s) missing for template '{template.name}'."
        )
        _echo_note("You can provide these with -p KEY=VALUE to skip prompts.")

    for parameter in template.parameters:
        if parameter.name in values:
            continue

        prompt_label = parameter.prompt or parameter.name
        if parameter.default is not None:
            values[parameter.name] = click.prompt(prompt_label, default=parameter.default, show_default=True)
            continue

        if parameter.required:
            values[parameter.name] = click.prompt(prompt_label)
            continue

        optional_value = click.prompt(prompt_label, default="", show_default=False, required=False)  # pyright: ignore
        if optional_value != "":
            values[parameter.name] = optional_value

    return values


if __name__ == "__main__":
    entrypoint()
