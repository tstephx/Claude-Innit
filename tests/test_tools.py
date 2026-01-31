"""Tests for MCP tools."""

import pytest
from pathlib import Path

from claude_innit.db.database import MemoryDatabase
from claude_innit.tools.context import get_context
from claude_innit.tools.search import search
from claude_innit.tools.memory import remember, forget
from claude_innit.tools.session import save_session


class TestGetContext:
    """Tests for get_context tool."""

    def test_returns_personal_context(self, tmp_path):
        """Returns personal memories."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory(
            id="personal/identity.md",
            category="personal",
            content="My name is Taylor Stephens",
            metadata={"type": "identity"},
        )

        result = get_context(db)

        assert "personal" in result
        assert len(result["personal"]) == 1
        assert "Taylor" in result["personal"][0]["content"]

    def test_filters_by_project(self, tmp_path):
        """Filters project context by name."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory(
            id="projects/book-mcp.md",
            category="project",
            content="Book MCP Server project",
            metadata={"name": "book-mcp-server"},
        )
        db.insert_memory(
            id="projects/other.md",
            category="project",
            content="Other project",
            metadata={"name": "other"},
        )

        result = get_context(db, project="book-mcp-server")

        assert "project" in result
        assert len(result["project"]) == 1
        assert "Book MCP" in result["project"][0]["content"]


class TestSearch:
    """Tests for search tool."""

    def test_auto_selects_fts_for_short_query(self, tmp_path):
        """Short queries use FTS search."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory(
            id="test-1",
            category="personal",
            content="Taylor Stephens is the user",
            metadata={},
        )

        result = search(db, "Taylor", method="auto")

        assert len(result) == 1
        assert result[0]["id"] == "test-1"

    def test_explicit_fts_method(self, tmp_path):
        """Can explicitly use FTS search."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory(
            id="test-1",
            category="personal",
            content="Python programming is fun",
            metadata={},
        )

        result = search(db, "Python", method="text")

        assert len(result) == 1


class TestRemember:
    """Tests for remember tool."""

    def test_stores_memory(self, tmp_path):
        """Remember stores a new memory."""
        db = MemoryDatabase(tmp_path / "test.db")

        result = remember(
            db,
            content="I prefer dark mode",
            category="personal",
            generate_embedding=False,
        )

        assert result["success"] is True
        assert result["memory_id"] is not None

        # Verify stored
        memory = db.get_memory(result["memory_id"])
        assert "dark mode" in memory["content"]

    def test_remember_with_project(self, tmp_path):
        """Remember can associate with a project."""
        db = MemoryDatabase(tmp_path / "test.db")

        result = remember(
            db,
            content="Using ProcessingAdapter pattern",
            category="project",
            project="book-mcp-server",
            generate_embedding=False,
        )

        memory = db.get_memory(result["memory_id"])
        assert memory["category"] == "project"


class TestForget:
    """Tests for forget tool."""

    def test_removes_memory(self, tmp_path):
        """Forget removes a memory."""
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory(
            id="to-forget",
            category="personal",
            content="Something to forget",
            metadata={},
        )

        result = forget(db, "to-forget")

        assert result["success"] is True
        assert db.get_memory("to-forget") is None


class TestSaveSession:
    """Tests for save_session tool."""

    def test_saves_session_summary(self, tmp_path):
        """Save session creates session memory."""
        db = MemoryDatabase(tmp_path / "test.db")

        result = save_session(
            db,
            summary="Worked on ProcessingAdapter integration",
            topics=["book-ingestion", "MCP"],
            project="book-mcp-server",
        )

        assert result["success"] is True

        # Verify stored
        memory = db.get_memory(result["session_id"])
        assert memory["category"] == "session"
        assert "ProcessingAdapter" in memory["content"]
