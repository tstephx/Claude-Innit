"""Regression tests for scripts/materialize_sessions.py and materialize_memories.py.

These scripts are invoked directly by cron/launchd (not imported as a package),
so `scripts/` must be on sys.path the same way it is when run standalone.

This file exists because materialize_sessions.py and materialize_memories.py both
called materialize_common.resolve_card_path() without importing it (missing name
in the `from materialize_common import (...)` block after a refactor). That's a
NameError only raised when the function body actually executes, so importing the
module alone doesn't catch it — these tests call the note-builder functions
directly for that reason.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from materialize_common import resolve_card_path  # noqa: E402
from materialize_memories import build_project_note  # noqa: E402
from materialize_sessions import build_vault_note  # noqa: E402


@pytest.fixture
def vault_with_cards(tmp_path, monkeypatch):
    import materialize_common

    monkeypatch.setattr(materialize_common, "VAULT_ROOT", tmp_path)
    card = tmp_path / "Projects/mcp (category)/briefcase/_PROJECT_CARD.md"
    card.parent.mkdir(parents=True)
    card.write_text("x")
    return tmp_path


class TestResolveCardPath:
    """Tests for materialize_common.resolve_card_path."""

    def test_known_project_returns_mapped_path(self):
        """A project slug present in PROJECT_CARD_MAP resolves to its mapped path."""
        assert (
            resolve_card_path("briefcase")
            == "Projects/mcp (category)/briefcase/_PROJECT_CARD"
        )

    def test_known_project_link_emitted_only_if_card_exists(self, tmp_path):
        """A mapped slug whose card is absent from the vault yields None."""
        assert resolve_card_path("briefcase", vault_root=tmp_path) is None

    def test_mapped_project_with_existing_card(self, tmp_path):
        card = tmp_path / "Projects/mcp (category)/briefcase/_PROJECT_CARD.md"
        card.parent.mkdir(parents=True)
        card.write_text("x")
        assert (
            resolve_card_path("briefcase", vault_root=tmp_path)
            == "Projects/mcp (category)/briefcase/_PROJECT_CARD"
        )

    def test_unknown_project_without_card_returns_none(self, tmp_path):
        assert resolve_card_path("some-new-project", vault_root=tmp_path) is None

    def test_unknown_project_with_existing_card_falls_back(self, tmp_path):
        card = tmp_path / "Projects/some-new-project/_PROJECT_CARD.md"
        card.parent.mkdir(parents=True)
        card.write_text("x")
        assert (
            resolve_card_path("some-new-project", vault_root=tmp_path)
            == "Projects/some-new-project/_PROJECT_CARD"
        )

    def test_fallback_is_case_insensitive_and_returns_canonical_case(self, tmp_path):
        card = tmp_path / "Projects/Some-New-Project/_PROJECT_CARD.md"
        card.parent.mkdir(parents=True)
        card.write_text("x")
        assert (
            resolve_card_path("some-new-project", vault_root=tmp_path)
            == "Projects/Some-New-Project/_PROJECT_CARD"
        )

    def test_map_lookup_is_case_insensitive(self, tmp_path):
        card = tmp_path / "Projects/mcp (category)/briefcase/_PROJECT_CARD.md"
        card.parent.mkdir(parents=True)
        card.write_text("x")
        assert (
            resolve_card_path("Briefcase", vault_root=tmp_path)
            == "Projects/mcp (category)/briefcase/_PROJECT_CARD"
        )

    def test_vault_root_defaults_to_module_vault_root(self, tmp_path, monkeypatch):
        import materialize_common

        monkeypatch.setattr(materialize_common, "VAULT_ROOT", tmp_path)
        card = tmp_path / "Projects/zeta/_PROJECT_CARD.md"
        card.parent.mkdir(parents=True)
        card.write_text("x")
        assert resolve_card_path("zeta") == "Projects/zeta/_PROJECT_CARD"

    def test_stale_map_entries_corrected(self):
        from materialize_common import PROJECT_CARD_MAP

        assert (
            PROJECT_CARD_MAP["dev-scripts"]
            == "Projects/claude-projects/dev-scripts/_PROJECT_CARD"
        )
        assert (
            PROJECT_CARD_MAP["dotfiles"]
            == "Projects/claude-projects/dotfiles/_PROJECT_CARD"
        )
        assert (
            PROJECT_CARD_MAP["write-pipeline"]
            == "Projects/write-pipeline/_PROJECT_CARD"
        )
        assert "api-dashboard" not in PROJECT_CARD_MAP
        assert "_lab" not in PROJECT_CARD_MAP

    def test_vault_root_env_override(self, tmp_path):
        import subprocess

        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import materialize_common as m;print(m.VAULT_ROOT)",
            ],
            env={"VAULT_ROOT": str(tmp_path), "PATH": "/usr/bin:/bin"},
            cwd=SCRIPTS_DIR,
            capture_output=True,
            text=True,
        )
        assert out.stdout.strip() == str(tmp_path)


class TestBuildVaultNote:
    """Tests for materialize_sessions.build_vault_note."""

    def test_builds_note_without_raising(self, vault_with_cards):
        """build_vault_note must not raise NameError — it calls resolve_card_path internally."""
        note = build_vault_note(
            session_date="2026-08-01",
            project="briefcase",
            topics=["testing"],
            memory_id="test-memory-id",
            body="Some session body text.",
        )
        assert "[[Projects/mcp (category)/briefcase/_PROJECT_CARD]]" in note
        assert "memory_id: test-memory-id" in note


class TestBuildProjectNote:
    """Tests for materialize_memories.build_project_note."""

    def test_builds_note_without_raising(self, vault_with_cards):
        """build_project_note must not raise NameError — it calls resolve_card_path internally."""
        note = build_project_note(
            project_slug="briefcase",
            fragments=[("frag1.md", "Some fragment body.")],
        )
        assert "[[Projects/mcp (category)/briefcase/_PROJECT_CARD]]" in note
        assert "innit_fragment_count: 1" in note

    def test_missing_card_omits_related(self, vault_with_cards):
        """A project with no card on disk builds a valid note with no related: link."""
        note = build_project_note(
            project_slug="brand-new-project",
            fragments=[("frag1.md", "Body.")],
        )
        assert "_PROJECT_CARD" not in note
        assert "related:" not in note
        assert "innit_fragment_count: 1" in note
