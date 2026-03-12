"""Tests for markdown sync module."""


from claude_innit.sync.markdown_sync import MarkdownSync


class TestMarkdownSync:
    """Tests for MarkdownSync."""

    def test_parses_frontmatter(self, tmp_path):
        """Extracts YAML frontmatter from markdown."""
        md_file = tmp_path / "test.md"
        md_file.write_text("""---
type: personal
priority: high
---

# My Identity

I am Taylor Stephens.
""")

        sync = MarkdownSync(tmp_path / "test.db", tmp_path)
        frontmatter, content = sync.parse_markdown(md_file)

        assert frontmatter["type"] == "personal"
        assert frontmatter["priority"] == "high"
        assert "Taylor Stephens" in content

    def test_syncs_directory(self, tmp_path):
        """Syncs all markdown files to database."""
        # Create memory directory structure
        memories_dir = tmp_path / "memories"
        personal_dir = memories_dir / "personal"
        personal_dir.mkdir(parents=True)

        # Create markdown files
        (personal_dir / "identity.md").write_text("""---
type: identity
---

My name is Taylor.
""")
        (personal_dir / "preferences.md").write_text("""---
type: preferences
---

I prefer concise responses.
""")

        # Sync
        sync = MarkdownSync(tmp_path / "test.db", memories_dir)
        stats = sync.sync_all()

        assert stats["synced"] == 2
        assert stats["errors"] == 0

        # Verify in database
        memory = sync.db.get_memory("personal/identity.md")
        assert memory is not None
        assert "Taylor" in memory["content"]

    def test_detects_category_from_path(self, tmp_path):
        """Determines category from file path."""
        memories_dir = tmp_path / "memories"
        (memories_dir / "personal").mkdir(parents=True)
        (memories_dir / "projects").mkdir(parents=True)
        (memories_dir / "sessions").mkdir(parents=True)

        (memories_dir / "personal" / "test.md").write_text("Personal content")
        (memories_dir / "projects" / "test.md").write_text("Project content")
        (memories_dir / "sessions" / "test.md").write_text("Session content")

        sync = MarkdownSync(tmp_path / "test.db", memories_dir)
        sync.sync_all()

        personal = sync.db.get_memory("personal/test.md")
        project = sync.db.get_memory("projects/test.md")
        session = sync.db.get_memory("sessions/test.md")

        assert personal["category"] == "personal"
        assert project["category"] == "project"
        assert session["category"] == "session"
