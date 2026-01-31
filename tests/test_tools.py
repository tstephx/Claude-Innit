"""Tests for MCP tools."""

import pytest
from pathlib import Path

from claude_innit.db.database import MemoryDatabase
from claude_innit.tools.context import get_context
from claude_innit.tools.search import search


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
