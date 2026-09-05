from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from template_sync.cli import entrypoint


def _create_cli_repository(repo_root: Path) -> None:
    (repo_root / "templates").mkdir(parents=True, exist_ok=True)
    (repo_root / "templates" / "base.typ").write_text("STATIC\n", encoding="utf-8")
    (repo_root / "templates" / "doc.typ.j2").write_text("Hello {{ name }}\n", encoding="utf-8")

    config = {
        "templates": {
            "assignment": {
                "description": "Test template",
                "parameters": ["name"],
                "files": [
                    {
                        "source": "templates/base.typ",
                        "target": "base.typ",
                        "mode": "static",
                        "jinja": False,
                    },
                    {
                        "source": "templates/doc.typ.j2",
                        "target": "main.typ",
                        "mode": "dynamic",
                        "jinja": True,
                    },
                ],
            }
        }
    }
    (repo_root / "templates.json").write_text(json.dumps(config), encoding="utf-8")


def test_list_command_shows_templates(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _create_cli_repository(repo_root)

    runner = CliRunner()
    result = runner.invoke(entrypoint, ["list", "--repo", str(repo_root)])

    assert result.exit_code == 0
    assert "assignment" in result.output
    assert "parameters: 1" in result.output


def test_apply_command_non_interactive_writes_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    target = tmp_path / "target"
    repo_root.mkdir()
    target.mkdir()
    _create_cli_repository(repo_root)

    runner = CliRunner()
    result = runner.invoke(
        entrypoint,
        [
            "apply",
            "assignment",
            "--repo",
            str(repo_root),
            "--target-dir",
            str(target),
            "--non-interactive",
            "-p",
            "name=Lin",
        ],
    )

    assert result.exit_code == 0
    assert "Applied template 'assignment'" in result.output
    assert (target / "base.typ").read_text(encoding="utf-8") == "STATIC\n"
    assert (target / "main.typ").read_text(encoding="utf-8") == "Hello Lin\n"


def test_apply_command_interactive_selection_and_prompt(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    target = tmp_path / "target"
    repo_root.mkdir()
    _create_cli_repository(repo_root)

    runner = CliRunner()
    result = runner.invoke(
        entrypoint,
        ["apply", "--repo", str(repo_root), "--target-dir", str(target)],
        input="1\nNora\n",
    )

    assert result.exit_code == 0
    assert "Choose a template" in result.output
    assert "1 argument(s) missing for template 'assignment'." in result.output
    assert (target / "main.typ").read_text(encoding="utf-8") == "Hello Nora\n"
