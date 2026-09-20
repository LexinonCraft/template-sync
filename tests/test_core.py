from __future__ import annotations

import json
from pathlib import Path

import pytest

from template_sync.core import (
    TemplateSyncError,
    apply_template,
    load_template_repository,
    parse_key_value_pairs,
)


def _create_basic_repository(repo_root: Path) -> Path:
    (repo_root / "templates" / "base.typ").parent.mkdir(parents=True, exist_ok=True)
    (repo_root / "templates" / "base.typ").write_text("#set page(margin: 2cm)\n", encoding="utf-8")
    (repo_root / "templates" / "doc.typ.j2").write_text(
        "= {{ title }}\nAuthor: {{ author }}\nYear: {{ year }}\n",
        encoding="utf-8",
    )

    with open("tests/templates_core.yaml", "r", encoding="utf-8") as f:
        config = f.read()
    config_path = repo_root / "templates.yaml"
    config_path.write_text(config)
    return config_path


def test_load_template_repository_parses_expected_fields(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _create_basic_repository(repo_root)

    repository = load_template_repository(repo_root)

    # assert repository.root == repo_root.resolve()
    assert "typst-assignment" in repository.config.templates
    template = repository.config.templates["typst-assignment"]
    assert template.description == "Typst assignment template"
    assert len(template.parameters) == 3
    assert len(template.files) == 2
    assert template.files[0].mode == "static"
    assert template.files[1].jinja is True


def test_parse_key_value_pairs_rejects_invalid_format() -> None:
    with pytest.raises(TemplateSyncError, match="KEY=VALUE"):
        parse_key_value_pairs(("title",))


def test_apply_template_writes_rendered_files_and_state(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    target_dir = tmp_path / "target"
    repo_root.mkdir()
    _create_basic_repository(repo_root)

    repository = load_template_repository(repo_root)
    template = repository.config.templates["typst-assignment"]

    state_path = apply_template(
        repository=repository,
        template_name="typst-assignment",
        template=template,
        target_dir=target_dir,
        parameter_values={"title": "Sheet 1", "author": "Ada"},
        force=False,
    )

    assert (target_dir / "base.typ").read_text(encoding="utf-8") == "#set page(margin: 2cm)\n"
    rendered = (target_dir / "main.typ").read_text(encoding="utf-8")
    assert "= Sheet 1" in rendered
    assert "Author: Ada" in rendered
    assert "Year: 2026" in rendered

    assert state_path == target_dir / ".template-sync" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["template"] == "typst-assignment"
    assert state["parameters"] == {"title": "Sheet 1", "author": "Ada", "year": "2026"}
    assert state["template_repository"]["path"] == str(repo_root.resolve())
    assert state["template_repository"]["commit"] is None
    assert len(state["files"]) == 2
    assert {entry["mode"] for entry in state["files"]} == {"static", "dynamic"}


def test_apply_template_requires_missing_parameters(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _create_basic_repository(repo_root)

    repository = load_template_repository(repo_root)
    template = repository.config.templates["typst-assignment"]

    with pytest.raises(TemplateSyncError, match="Missing required parameters"):
        apply_template(
            repository=repository,
            template_name="typst-assignment",
            template=template,
            target_dir=tmp_path / "target",
            parameter_values={"title": "Sheet 1"},
            force=False,
        )


def test_apply_template_force_overwrites_existing_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    target_dir = tmp_path / "target"
    repo_root.mkdir()
    _create_basic_repository(repo_root)

    repository = load_template_repository(repo_root)
    template = repository.config.templates["typst-assignment"]

    target_dir.mkdir(parents=True, exist_ok=True)
    existing = target_dir / "base.typ"
    existing.write_text("old\n", encoding="utf-8")

    with pytest.raises(TemplateSyncError, match="already exists"):
        apply_template(
            repository=repository,
            template_name="typst-assignment",
            template=template,
            target_dir=target_dir,
            parameter_values={"title": "Sheet 1", "author": "Ada"},
            force=False,
        )

    apply_template(
        repository=repository,
        template_name="typst-assignment",
        template=template,
        target_dir=target_dir,
        parameter_values={"title": "Sheet 1", "author": "Ada"},
        force=True,
    )
    assert existing.read_text(encoding="utf-8") == "#set page(margin: 2cm)\n"
