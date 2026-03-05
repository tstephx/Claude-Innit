"""Tests for MCP tools."""

import pytest
from pathlib import Path

import yaml

from claude_innit.db.database import MemoryDatabase
from claude_innit.tools.context import get_context
from claude_innit.tools.search import search
from claude_innit.tools.memory import remember, forget
from claude_innit.tools.session import save_session
from claude_innit.sync.markdown_sync import MarkdownSync


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

    def test_writes_session_markdown_file(self, tmp_path):
        """Save session writes a markdown file when memories_dir is provided."""
        db = MemoryDatabase(tmp_path / "test.db")
        memories_dir = tmp_path / "memories"

        result = save_session(
            db,
            summary="Built the authentication module",
            topics=["auth", "security"],
            project="my-app",
            memories_dir=memories_dir,
        )

        assert result["success"] is True

        # Find the written file
        session_files = list((memories_dir / "sessions").glob("*.md"))
        assert len(session_files) == 1

        content = session_files[0].read_text()
        assert "---" in content
        assert "Built the authentication module" in content

        # Parse frontmatter
        parts = content.split("---")
        frontmatter = yaml.safe_load(parts[1])
        assert frontmatter["project"] == "my-app"
        assert frontmatter["topics"] == ["auth", "security"]
        assert "date" in frontmatter

    def test_no_file_without_memories_dir(self, tmp_path):
        """Save session does not write a file when memories_dir is not provided."""
        db = MemoryDatabase(tmp_path / "test.db")

        result = save_session(
            db,
            summary="No file should be written",
        )

        assert result["success"] is True
        # No memories directory should be created
        assert not (tmp_path / "memories").exists()


class TestRememberMarkdown:
    """Tests for remember tool markdown file writing."""

    def test_writes_memory_markdown_file(self, tmp_path):
        """Remember writes a markdown file when memories_dir is provided."""
        db = MemoryDatabase(tmp_path / "test.db")
        memories_dir = tmp_path / "memories"

        result = remember(
            db,
            content="I prefer dark mode",
            category="personal",
            generate_embedding=False,
            memories_dir=memories_dir,
        )

        assert result["success"] is True

        # Find the written file
        memory_files = list((memories_dir / "personal").glob("*.md"))
        assert len(memory_files) == 1

        content = memory_files[0].read_text()
        assert "---" in content
        assert "I prefer dark mode" in content

        # Parse frontmatter
        parts = content.split("---")
        frontmatter = yaml.safe_load(parts[1])
        assert frontmatter["category"] == "personal"

    def test_writes_memory_with_project(self, tmp_path):
        """Remember writes project metadata in frontmatter."""
        db = MemoryDatabase(tmp_path / "test.db")
        memories_dir = tmp_path / "memories"

        result = remember(
            db,
            content="Using adapter pattern",
            category="project",
            project="book-mcp",
            generate_embedding=False,
            memories_dir=memories_dir,
        )

        assert result["success"] is True

        memory_files = list((memories_dir / "project").glob("*.md"))
        assert len(memory_files) == 1

        content = memory_files[0].read_text()
        parts = content.split("---")
        frontmatter = yaml.safe_load(parts[1])
        assert frontmatter["project"] == "book-mcp"
        assert frontmatter["category"] == "project"

    def test_no_file_without_memories_dir(self, tmp_path):
        """Remember does not write a file when memories_dir is not provided."""
        db = MemoryDatabase(tmp_path / "test.db")

        result = remember(
            db,
            content="No file please",
            category="personal",
            generate_embedding=False,
        )

        assert result["success"] is True
        assert not (tmp_path / "memories").exists()


class TestForgetDurability:
    """Tests that forget() survives a sync cycle."""

    def test_forget_deletes_markdown_file(self, tmp_path):
        """forget() removes the markdown file so sync cannot re-insert it."""
        db = MemoryDatabase(tmp_path / "test.db")
        memories_dir = tmp_path / "memories"

        # Create memory with markdown file
        result = remember(
            db,
            content="This should be forgotten",
            category="personal",
            generate_embedding=False,
            memories_dir=memories_dir,
        )
        memory_id = result["memory_id"]

        # Verify file exists before forgetting
        md_files_before = list(memories_dir.rglob("*.md"))
        assert len(md_files_before) == 1

        # Forget it
        forget_result = forget(db, memory_id, memories_dir=memories_dir)
        assert forget_result["success"] is True

        # Markdown file must be gone
        md_files_after = list(memories_dir.rglob("*.md"))
        assert len(md_files_after) == 0

    def test_forget_survives_sync(self, tmp_path):
        """After forget(), a full sync does not re-insert the memory."""
        db = MemoryDatabase(tmp_path / "test.db")
        memories_dir = tmp_path / "memories"

        result = remember(
            db,
            content="Temporary improvement note",
            category="personal",
            generate_embedding=False,
            memories_dir=memories_dir,
        )
        memory_id = result["memory_id"]

        forget(db, memory_id, memories_dir=memories_dir)

        # Simulate server restart (new db + sync)
        db2 = MemoryDatabase(tmp_path / "test.db")
        sync = MarkdownSync(tmp_path / "test.db", memories_dir, generate_embeddings=False)
        sync.sync_all()

        assert db2.get_memory(memory_id) is None
