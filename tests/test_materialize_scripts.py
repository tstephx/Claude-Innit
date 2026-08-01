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

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from materialize_common import resolve_card_path  # noqa: E402
from materialize_memories import build_project_note  # noqa: E402
from materialize_sessions import build_vault_note  # noqa: E402


class TestResolveCardPath:
    """Tests for materialize_common.resolve_card_path."""

    def test_known_project_returns_mapped_path(self):
        """A project slug present in PROJECT_CARD_MAP resolves to its mapped path."""
        assert (
            resolve_card_path("briefcase")
            == "Projects/mcp (category)/briefcase/_PROJECT_CARD"
        )

    def test_unknown_project_falls_back_to_default_pattern(self):
        """A project slug absent from PROJECT_CARD_MAP falls back to Projects/{slug}/_PROJECT_CARD."""
        assert (
            resolve_card_path("some-new-project")
            == "Projects/some-new-project/_PROJECT_CARD"
        )


class TestBuildVaultNote:
    """Tests for materialize_sessions.build_vault_note."""

    def test_builds_note_without_raising(self):
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

    def test_builds_note_without_raising(self):
        """build_project_note must not raise NameError — it calls resolve_card_path internally."""
        note = build_project_note(
            project_slug="briefcase",
            fragments=[("frag1.md", "Some fragment body.")],
        )
        assert "[[Projects/mcp (category)/briefcase/_PROJECT_CARD]]" in note
        assert "innit_fragment_count: 1" in note

    def test_unknown_project_falls_back(self):
        """A project slug with no PROJECT_CARD_MAP entry still builds a valid note."""
        note = build_project_note(
            project_slug="brand-new-project",
            fragments=[("frag1.md", "Body.")],
        )
        assert "[[Projects/brand-new-project/_PROJECT_CARD]]" in note
