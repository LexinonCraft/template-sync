from __future__ import annotations

import json

import pytest

from template_sync.model import parse_state_file, serialize_state_file


def _valid_state() -> dict:
    return {
        "files": [
            {
                "jinja": True,
                "mode": "dynamic",
                "source": "test.tex.jinja2",
                "source_sha256": "4d833aed5e4294f96b4562608db21972e378d28184be32ea8a61a566cb7b988c",
                "target": "test.tex",
            }
        ],
        "generated_at": "2026-09-20T15:15:54.586995+00:00",
        "parameters": {"class_name": "Logik I", "student_name": "Merle", "year": "2026"},
        "schema_version": 1,
        "template": "latex-assignment",
        "template_repository": {
            "commit": "9033430c48f9f0abf9dc593d3a86b7d38a866ac9",
            "config_file": "templates.yaml",
            "path": "/tmp/templaterepo",
            "ref": "main",
        },
    }


def test_parse_state_file_validates_attached_state_shape() -> None:
    state = parse_state_file(json.dumps(_valid_state()))

    assert state.template == "latex-assignment"
    assert state.generated_at.year == 2026
    assert state.parameters["student_name"] == "Merle"
    assert state.files[0].source_sha256.startswith("4d833a")
    assert state.template_repository.commit is not None


def test_serialize_state_file_round_trips() -> None:
    state = parse_state_file(json.dumps(_valid_state()))

    serialized = serialize_state_file(state)

    assert serialized.endswith("\n")
    assert parse_state_file(serialized) == state


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "Unsupported state schema version"),
        ("generated_at", "yesterday", "ISO-8601 timestamp"),
        ("files", {}, "must be a list"),
    ],
)
def test_parse_state_file_rejects_invalid_top_level_fields(field: str, value: object, message: str) -> None:
    state = _valid_state()
    state[field] = value

    with pytest.raises(ValueError, match=message):
        parse_state_file(json.dumps(state))


def test_parse_state_file_rejects_invalid_checksum() -> None:
    state = _valid_state()
    state["files"][0]["source_sha256"] = "not-a-checksum"

    with pytest.raises(ValueError, match="SHA-256"):
        parse_state_file(json.dumps(state))
