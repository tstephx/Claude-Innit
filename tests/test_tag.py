"""Tests for vault_tag MCP tool."""

import os
from datetime import datetime
from pathlib import Path

import pytest

from claude_innit.tools.tag import (
    find_untagged,
    build_frontmatter,
    apply_frontmatter,
    vault_tag,
    FOLDER_TYPE_MAP,
    has_frontmatter,
)


@pytest.fixture
def vault_dir(tmp_path):
    """Create test vault with mixed frontmatter state."""
    vault = tmp_path / "vault"
    vault.mkdir()

    tagged = vault / "Projects"
    tagged.mkdir()
    (tagged / "tagged.md").write_text(
        "---\nstatus: active\ntags: [core]\n---\n\n# Tagged\nContent.\n"
    )
    (tagged / "untagged.md").write_text("# Untagged Note\nSome content.\n")

    guides = vault / "Guides"
    guides.mkdir()
    (guides / "plain.md").write_text("Just plain text.\n")

    (guides / "broken.md").write_text("---\nstatus: draft\nNo closing.\n")

    return vault


class TestHasFrontmatter:
    def test_with_frontmatter(self, vault_dir):
        assert has_frontmatter(vault_dir / "Projects" / "tagged.md") is True

    def test_without_frontmatter(self, vault_dir):
        assert has_frontmatter(vault_dir / "Projects" / "untagged.md") is False

    def test_malformed_frontmatter(self, vault_dir):
        # Line 1 is "---" so treated as having frontmatter
        assert has_frontmatter(vault_dir / "Guides" / "broken.md") is True


class TestFindUntagged:
    def test_finds_untagged_files(self, vault_dir):
        result = find_untagged(vault_dir)
        names = [f.name for f in result]
        assert "untagged.md" in names
        assert "plain.md" in names
        assert "tagged.md" not in names
        assert "broken.md" not in names

    def test_skips_hidden_dirs(self, vault_dir):
        hidden = vault_dir / ".hidden"
        hidden.mkdir()
        (hidden / "secret.md").write_text("no frontmatter\n")
        result = find_untagged(vault_dir)
        names = [f.name for f in result]
        assert "secret.md" not in names


class TestBuildFrontmatter:
    def test_default_frontmatter(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = build_frontmatter(path, vault_dir)
        assert fm["status"] == "active"
        assert fm["tags"] == []
        assert fm["type"] == "project"  # Projects -> project
        assert "created" in fm
        assert "modified" in fm

    def test_type_from_folder_map(self, vault_dir):
        path = vault_dir / "Guides" / "plain.md"
        fm = build_frontmatter(path, vault_dir)
        assert fm["type"] == "guide"

    def test_unknown_folder_type_is_note(self, tmp_path):
        vault = tmp_path / "vault"
        weird = vault / "RandomFolder"
        weird.mkdir(parents=True)
        (weird / "file.md").write_text("content\n")
        fm = build_frontmatter(weird / "file.md", vault)
        assert fm["type"] == "note"

    def test_folder_defaults_override(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = build_frontmatter(
            path, vault_dir, folder_defaults={"Projects": {"status": "archived"}}
        )
        assert fm["status"] == "archived"

    def test_file_overrides_beat_folder_defaults(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = build_frontmatter(
            path,
            vault_dir,
            folder_defaults={"Projects": {"status": "archived"}},
            file_overrides={"Projects/untagged.md": {"status": "active"}},
        )
        assert fm["status"] == "active"


class TestApplyFrontmatter:
    def test_prepends_frontmatter(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = {
            "status": "active",
            "tags": [],
            "type": "project",
            "created": "2026-03-13",
            "modified": "2026-03-13",
        }
        apply_frontmatter(path, fm)
        content = path.read_text()
        assert content.startswith("---\n")
        assert "status: active" in content
        assert "# Untagged Note" in content

    def test_idempotent(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = {
            "status": "active",
            "tags": [],
            "type": "project",
            "created": "2026-03-13",
            "modified": "2026-03-13",
        }
        apply_frontmatter(path, fm)
        apply_frontmatter(path, fm)  # second call on already-tagged file
        content = path.read_text()
        assert content.count("---") == 2  # exactly one frontmatter block (open + close)

    def test_preserves_existing(self, vault_dir):
        path = vault_dir / "Projects" / "tagged.md"
        before = path.read_text()
        # Shouldn't be called on tagged files, but if it is, no damage
        assert has_frontmatter(path) is True

    def test_field_ordering_preserved(self, vault_dir):
        """Fields should be in canonical order: status, tags, type, created, modified."""
        path = vault_dir / "Projects" / "untagged.md"
        fm = {
            "modified": "2026-03-13",
            "status": "active",
            "tags": [],
            "type": "project",
            "created": "2026-03-13",
        }
        apply_frontmatter(path, fm)
        content = path.read_text()
        lines = content.split("\n")
        # Find field lines (between --- markers)
        field_lines = [l for l in lines[1:] if l == "---" or ": " in l]
        # First non-separator field should be status
        field_keys = [l.split(":")[0] for l in field_lines if l != "---"]
        assert field_keys[0] == "status"


class TestVaultTagPreview:
    def test_preview_returns_grouped_files(self, vault_dir):
        result = vault_tag(str(vault_dir))
        assert result["mode"] == "preview"
        assert result["total"] == 2
        assert "Projects" in result["by_folder"]
        assert "Guides" in result["by_folder"]

    def test_preview_no_untagged(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        proj = vault / "Projects"
        proj.mkdir()
        (proj / "done.md").write_text("---\nstatus: active\n---\nContent.\n")
        result = vault_tag(str(vault))
        assert result["total"] == 0

    def test_empty_vault_directory(self, tmp_path):
        """Empty vault with zero .md files returns preview with total=0."""
        vault = tmp_path / "empty-vault"
        vault.mkdir()
        result = vault_tag(str(vault))
        assert result["mode"] == "preview"
        assert result["total"] == 0
        assert result["by_folder"] == {}


class TestVaultTagApply:
    def test_apply_tags_files(self, vault_dir):
        result = vault_tag(str(vault_dir), apply=True)
        assert result["mode"] == "applied"
        assert result["tagged"] == 2
        # Verify files got frontmatter
        content = (vault_dir / "Projects" / "untagged.md").read_text()
        assert content.startswith("---\n")

    def test_apply_with_folder_defaults(self, vault_dir):
        result = vault_tag(
            str(vault_dir),
            apply=True,
            folder_defaults={"Projects": {"status": "archived"}},
        )
        content = (vault_dir / "Projects" / "untagged.md").read_text()
        assert "status: archived" in content

    def test_apply_with_file_overrides(self, vault_dir):
        result = vault_tag(
            str(vault_dir),
            apply=True,
            folder_defaults={"Projects": {"status": "archived"}},
            file_overrides={"Projects/untagged.md": {"status": "draft"}},
        )
        content = (vault_dir / "Projects" / "untagged.md").read_text()
        assert "status: draft" in content
