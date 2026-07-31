from __future__ import annotations

from pathlib import Path

import click

from template_sync.core import (
    TemplateDefinition,
    TemplateSyncError,
    apply_template,
    load_template_repository,
    parse_key_value_pairs,
)


@click.group()
def entrypoint() -> None:
    """Synchronize template bundles from a template repository into target directories."""


@entrypoint.command("list")
@click.option("--repo", "repo_path", required=True, type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--config", "config_file", default="templates.json", show_default=True)
def list_templates(repo_path: Path, config_file: str) -> None:
    """List available templates in the template repository."""
    try:
        repository = load_template_repository(repo_path=repo_path, config_file=config_file)
    except TemplateSyncError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Templates in {repository.root}:")
    for name in sorted(repository.templates):
        template = repository.templates[name]
        description = f" - {template.description}" if template.description else ""
        click.echo(
            f"- {template.name}{description}"
            f" (parameters: {len(template.parameters)}, files: {len(template.files)})"
        )


@entrypoint.command("apply")
@click.argument("template_name", required=False)
@click.option("--repo", "repo_path", required=True, type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--target-dir", default=".", type=click.Path(path_type=Path, file_okay=False), show_default=True)
@click.option("--config", "config_file", default="templates.json", show_default=True)
@click.option("--set", "parameter_overrides", multiple=True, help="Parameter override in KEY=VALUE form.")
@click.option("--non-interactive", is_flag=True, help="Fail if required parameters are missing.")
@click.option("--force", is_flag=True, help="Overwrite existing files in target directory.")
def apply_template_command(
    template_name: str | None,
    repo_path: Path,
    target_dir: Path,
    config_file: str,
    parameter_overrides: tuple[str, ...],
    non_interactive: bool,
    force: bool,
) -> None:
    """Apply a template to the target directory."""
    try:
        repository = load_template_repository(repo_path=repo_path, config_file=config_file)
        selected_template = _resolve_template_selection(repository.templates, template_name)

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

    click.echo(f"Applied template '{selected_template.name}' to {target_dir.resolve()}")
    click.echo(f"State written to: {state_file}")


@entrypoint.command("config-example")
def config_example() -> None:
    """Print an example templates.json configuration."""
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
    if selected_name:
        template = templates.get(selected_name)
        if template is None:
            known_templates = ", ".join(sorted(templates))
            raise TemplateSyncError(
                f"Unknown template '{selected_name}'. Available templates: {known_templates}"
            )
        return template

    ordered_names = sorted(templates)
    click.echo("Choose a template:")
    for idx, name in enumerate(ordered_names, start=1):
        description = templates[name].description
        suffix = f" - {description}" if description else ""
        click.echo(f"{idx}. {name}{suffix}")

    selected_index = click.prompt(
        "Template number",
        type=click.IntRange(min=1, max=len(ordered_names)),
    )
    return templates[ordered_names[selected_index - 1]]


def _collect_missing_parameter_values(
    template: TemplateDefinition,
    current_values: dict[str, str],
) -> dict[str, str]:
    values = dict(current_values)
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
