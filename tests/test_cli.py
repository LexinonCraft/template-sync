from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from template_sync.cli import entrypoint


def _create_cli_repository(repo_root: Path) -> None:
    (repo_root / "templates").mkdir(parents=True, exist_ok=True)
    (repo_root / "templates" / "base.typ").write_text("STATIC\n", encoding="utf-8")
    (repo_root / "templates" / "doc.typ.j2").write_text("Hello {{ name }}\n", encoding="utf-8")

    with open("tests/templates_cli.yaml", "r", encoding="utf-8") as f:
        config = f.read()
    config_path = repo_root / "templates.yaml"
    config_path.write_text(config)


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
            str(target),
            "assignment",
            "--repo",
            str(repo_root),
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
        ["apply", str(target), "--repo", str(repo_root)],
        input="1\nNora\n",
    )

    assert result.exit_code == 0
    assert "Choose a template" in result.output
    assert "1 argument(s) missing for template 'assignment'." in result.output
    assert (target / "main.typ").read_text(encoding="utf-8") == "Hello Nora\n"


def test_regenerate_command_inplace(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _create_cli_repository(repo_root)

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        apply_result = runner.invoke(
            entrypoint,
            ["apply", ".", "assignment", "--repo", str(repo_root), "--non-interactive", "-p", "name=Lin"],
        )
        assert apply_result.exit_code == 0

        Path("base.typ").write_text("MODIFIED\n", encoding="utf-8")
        result = runner.invoke(entrypoint, ["regenerate", "--inplace", "base.typ"])

        assert result.exit_code == 0
        assert "1 file(s) regenerated." in result.output
        assert Path("base.typ").read_text(encoding="utf-8") == "STATIC\n"


def test_regenerate_command_prints_rendered_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _create_cli_repository(repo_root)

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        apply_result = runner.invoke(
            entrypoint,
            ["apply", ".", "assignment", "--repo", str(repo_root), "--non-interactive", "-p", "name=Lin"],
        )
        assert apply_result.exit_code == 0

        result = runner.invoke(entrypoint, ["regenerate", "main.typ"])

        assert result.exit_code == 0
        assert result.output == "Hello Lin\n\n"
