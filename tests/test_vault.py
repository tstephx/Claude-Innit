"""Tests for vault indexing, search, and stats."""

import json

import pytest

from claude_innit.db.database import MemoryDatabase
from claude_innit.tools.vault import (
    vault_index,
    vault_search,
    vault_related,
    vault_stats,
    _content_hash,
    _detect_module,
    _parse_frontmatter,
    _hybrid_merge,
)


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like database for each test."""
    db_path = tmp_path / "test.db"
    return MemoryDatabase(db_path)


@pytest.fixture
def vault_dir(tmp_path):
    """Create a minimal vault directory with test files."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # Framework files
    brain = vault / ".brain"
    brain.mkdir()
    (brain / "config.yaml").write_text("framework_version: 0.1.0\n")

    # Module files
    bs = vault / "behavioral-studio"
    stories = bs / "Stories"
    stories.mkdir(parents=True)
    (stories / "API Migration.md").write_text(
        "---\nstatus: draft\nsignals:\n  - Scope\ntags:\n  - core\n---\n\n"
        "# API Migration\n\n## Context\nLed a cross-team API migration affecting 50 services.\n\n"
        "## Actions\nI designed the migration strategy and coordinated rollout.\n\n"
        "## Results\nCompleted migration in 3 months with zero downtime.\n"
    )
    (stories / "Conflict Resolution.md").write_text(
        "---\nstatus: ready\nsignals:\n  - Conflict-Resolution\n---\n\n"
        "# Conflict Resolution\n\n## Context\nDisagreed with PM on feature priority.\n\n"
        "## Actions\nI scheduled a 1:1 to discuss data behind each option.\n\n"
        "## Results\nWe aligned on a data-driven approach.\n"
    )

    # Inbox
    inbox = vault / "Inbox"
    inbox.mkdir()
    (inbox / "capture.md").write_text(
        "---\ntype: capture\n---\nQuick thought about stakeholders.\n"
    )

    # Daily note
    daily = vault / "Daily"
    daily.mkdir()
    (daily / "2026-03-09.md").write_text(
        "---\ntype: daily\n---\n# Today\nWorked on stories.\n"
    )

    return vault


class TestParsingHelpers:
    def test_parse_frontmatter_with_yaml(self):
        text = "---\nstatus: draft\ntags:\n  - core\n---\n\n# Title\nBody text."
        fm, body = _parse_frontmatter(text)
        assert fm["status"] == "draft"
        assert fm["tags"] == ["core"]
        assert body.startswith("# Title")

    def test_parse_frontmatter_no_yaml(self):
        text = "# Title\nJust a plain file."
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert "Just a plain file" in body

    def test_content_hash_deterministic(self):
        h1 = _content_hash("hello world")
        h2 = _content_hash("hello world")
        assert h1 == h2
        assert isinstance(h1, str)
        assert len(h1) == 64  # SHA-256 hex

    def test_content_hash_different_for_different_content(self):
        assert _content_hash("a") != _content_hash("b")

    def test_detect_module_in_module_dir(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        assert (
            _detect_module(
                str(vault / "behavioral-studio" / "Stories" / "x.md"), str(vault)
            )
            == "behavioral-studio"
        )

    def test_detect_module_root_file(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        assert _detect_module(str(vault / "CLAUDE.md"), str(vault)) is None

    def test_detect_module_framework_dirs(self, tmp_path):
        """Named framework dirs (Daily, Inbox, Archive, Claude-Memory) return None."""
        vault = tmp_path / "vault"
        vault.mkdir()
        assert (
            _detect_module(str(vault / "Daily" / "2026-01-01.md"), str(vault)) is None
        )
        assert _detect_module(str(vault / "Inbox" / "note.md"), str(vault)) is None
        assert _detect_module(str(vault / "Archive" / "old.md"), str(vault)) is None
        assert (
            _detect_module(str(vault / "Claude-Memory" / "ctx.md"), str(vault)) is None
        )

    def test_detect_module_dot_dirs_are_modules(self, tmp_path):
        """Dot-prefixed dirs (.brain, .claude) are excluded by VaultIndexer
        exclude_patterns, not by _detect_module — so _detect_module returns them."""
        vault = tmp_path / "vault"
        vault.mkdir()
        assert (
            _detect_module(str(vault / ".brain" / "config.yaml"), str(vault))
            == ".brain"
        )
        assert (
            _detect_module(str(vault / ".claude" / "settings.json"), str(vault))
            == ".claude"
        )

    def test_detect_module_lowercases_name(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        # Portfolio/ folder (capital P) should return "portfolio" (lowercase)
        assert (
            _detect_module(str(vault / "Portfolio" / "doc.md"), str(vault))
            == "portfolio"
        )
        # Content folders like Projects/, Books/ are detected as modules (lowercase)
        assert (
            _detect_module(str(vault / "Projects" / "readme.md"), str(vault))
            == "projects"
        )
        assert (
            _detect_module(str(vault / "Books" / "summary.md"), str(vault)) == "books"
        )


class TestVaultIndexer:
    def test_index_counts(self, db, vault_dir):
        result = vault_index(db, vault_root=str(vault_dir))
        # 4 .md files: 2 stories + 1 capture + 1 daily
        assert result["indexed"] == 4
        assert result["errors"] == 0
        assert result["unchanged"] == 0
        assert isinstance(result["duration_ms"], int)

    def test_index_skip_unchanged(self, db, vault_dir):
        # First index
        vault_index(db, vault_root=str(vault_dir))
        # Second index — all should be unchanged
        result = vault_index(db, vault_root=str(vault_dir))
        assert result["indexed"] == 0
        assert result["unchanged"] == 4

    def test_index_force_reindex(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        result = vault_index(db, vault_root=str(vault_dir), force=True)
        assert result["updated"] == 4
        assert result["indexed"] == 0

    def test_index_removes_deleted_files(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        # Delete a file
        (vault_dir / "Inbox" / "capture.md").unlink()
        result = vault_index(db, vault_root=str(vault_dir))
        assert result["removed"] == 1

    def test_index_detects_module(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        story = db.get_vault_file(
            str(vault_dir / "behavioral-studio" / "Stories" / "API Migration.md")
        )
        assert story is not None
        assert story["module"] == "behavioral-studio"

    def test_index_stores_frontmatter(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        story = db.get_vault_file(
            str(vault_dir / "behavioral-studio" / "Stories" / "API Migration.md")
        )
        fm = json.loads(story["frontmatter"])
        assert fm["status"] == "draft"
        assert "core" in fm["tags"]

    def test_index_excludes_patterns(self, db, vault_dir):
        # Create a node_modules dir with a .md file
        nm = vault_dir / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "README.md").write_text("# Package\n")
        vault_index(db, vault_root=str(vault_dir))
        # node_modules file should be excluded
        assert db.get_vault_file(str(nm / "README.md")) is None


class TestVaultSearch:
    def test_fts_search_finds_content(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="API migration", method="text")
        assert len(results) >= 1
        assert any("API Migration" in r["filename"] for r in results)

    def test_fts_search_by_module(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        # Direct DB search with module filter
        results = db.vault_fts_search("migration", module="behavioral-studio")
        assert len(results) >= 1

    def test_search_returns_score(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="conflict", method="text")
        assert len(results) >= 1
        assert "score" in results[0]
        assert isinstance(results[0]["score"], float)

    def test_search_sanitizes_query(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        # Queries with FTS operators should not crash
        results = vault_search(db, query='test "OR" AND NOT', method="text")
        assert isinstance(results, list)

    def test_search_empty_results(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="xyznonexistent12345", method="text")
        assert results == []


class TestVaultRelated:
    def test_related_finds_similar_notes(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        story_path = str(
            vault_dir / "behavioral-studio" / "Stories" / "API Migration.md"
        )
        results = vault_related(db, story_path)
        # Should find at least the other story
        assert isinstance(results, list)
        # Should NOT include itself
        assert all(r["file_path"] != story_path for r in results)

    def test_related_nonexistent_note(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_related(db, "/nonexistent/path.md")
        assert results == []


class TestVaultStats:
    def test_stats_structure(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        stats = vault_stats(db)

        assert isinstance(stats["total_notes"], int)
        assert stats["total_notes"] == 4
        assert isinstance(stats["by_module"], dict)
        assert isinstance(stats["by_status"], dict)
        assert isinstance(stats["inbox_count"], int)
        assert isinstance(stats["stale_count"], int)
        assert isinstance(stats["index_age_seconds"], float)
        assert stats["last_indexed"] is not None
        assert "embeddings" in stats
        assert isinstance(stats["embeddings"], dict)

    def test_stats_by_module(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        stats = vault_stats(db)
        assert "behavioral-studio" in stats["by_module"]
        assert stats["by_module"]["behavioral-studio"] == 2  # 2 stories

    def test_stats_inbox_count(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        stats = vault_stats(db)
        assert stats["inbox_count"] == 1  # 1 capture in Inbox/

    def test_stats_empty_db(self, db):
        stats = vault_stats(db)
        assert stats["total_notes"] == 0
        assert stats["by_module"] == {}
        assert stats["index_age_seconds"] == -1.0


class TestVaultStatsEmbeddings:
    def test_stats_includes_embedding_health(self, db, vault_dir):
        from claude_innit.db.embeddings import EmbeddingStore

        vault_index(db, vault_root=str(vault_dir))
        stats = vault_stats(db, embedding_store=EmbeddingStore(db))

        assert "embeddings" in stats
        emb = stats["embeddings"]
        assert "total_files" in emb
        assert "chunk_embeddings" in emb
        assert "legacy_embeddings" in emb
        assert "model" in emb
        assert "mode" in emb
        assert "self_test" in emb
        assert emb["self_test"] in ("pass", "fail", "unavailable")

    def test_stats_embeddings_without_store(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        stats = vault_stats(db)

        assert "embeddings" in stats
        assert stats["embeddings"]["self_test"] == "unavailable"


class TestVaultSearchCompactResults:
    """Bug #1: FTS results should return compact results with snippet, not full DB rows."""

    def test_fts_results_have_snippet_not_content(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="migration", method="text")
        assert len(results) >= 1
        for r in results:
            assert "snippet" in r, "FTS results should have 'snippet' field"
            assert "content" not in r, "FTS results should not expose full 'content'"

    def test_fts_results_strip_db_internals(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="conflict", method="text")
        assert len(results) >= 1
        for r in results:
            assert "content_hash" not in r, "Should not expose content_hash"
            assert "file_size" not in r, "Should not expose file_size"
            assert "indexed_at" not in r, "Should not expose indexed_at"
            assert "frontmatter" not in r, "Should not expose frontmatter"

    def test_fts_snippet_is_truncated(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="migration", method="text")
        assert len(results) >= 1
        for r in results:
            assert len(r["snippet"]) <= 200

    def test_fts_results_have_title(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="conflict", method="text")
        assert len(results) >= 1
        for r in results:
            assert "title" in r, "FTS results should have 'title' field"
            assert not r["title"].endswith(".md"), (
                "Title should not include .md extension"
            )

    def test_fts_results_keep_essential_fields(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="migration", method="text")
        assert len(results) >= 1
        r = results[0]
        assert "file_path" in r
        assert "filename" in r
        assert "module" in r
        assert "score" in r


class TestVaultSearchLimitClamping:
    """Bug #3: Negative/zero limits should be clamped, not passed to SQLite."""

    def test_negative_limit_does_not_dump_all(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="conflict", method="text", limit=-1)
        assert len(results) <= 20, (
            f"limit=-1 returned {len(results)} results, expected <= 20"
        )

    def test_zero_limit_returns_empty(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="conflict", method="text", limit=0)
        assert results == []

    def test_large_limit_is_capped(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        results = vault_search(db, query="conflict", method="text", limit=10000)
        assert len(results) <= 100, "Limit should be capped at a reasonable maximum"


class TestVaultSearchFailLoud:
    def test_semantic_fails_without_store(self, db, vault_dir):
        vault_index(db, vault_root=str(vault_dir))
        with pytest.raises(ValueError, match="[Ss]emantic search.*unavailable"):
            vault_search(db, "test query", method="semantic", embedding_store=None)


class TestDatabaseVaultMethods:
    def test_upsert_and_get(self, db):
        db.upsert_vault_file(
            file_path="/vault/test.md",
            filename="test.md",
            content="Hello world",
            content_hash="abc123",
            frontmatter={"status": "draft"},
            module="test-module",
            file_size=100,
        )
        result = db.get_vault_file("/vault/test.md")
        assert result is not None
        assert result["filename"] == "test.md"
        assert result["content"] == "Hello world"
        assert result["content_hash"] == "abc123"
        assert result["module"] == "test-module"
        assert isinstance(result["file_size"], int)

    def test_upsert_updates_existing(self, db):
        db.upsert_vault_file("/vault/test.md", "test.md", "v1", "hash1")
        db.upsert_vault_file("/vault/test.md", "test.md", "v2", "hash2")
        result = db.get_vault_file("/vault/test.md")
        assert result["content"] == "v2"
        assert result["content_hash"] == "hash2"
        # Should still be 1 file total
        assert db.vault_file_count() == 1

    def test_delete_vault_file(self, db):
        db.upsert_vault_file("/vault/test.md", "test.md", "content", "hash")
        assert db.get_vault_file("/vault/test.md") is not None
        db.delete_vault_file("/vault/test.md")
        assert db.get_vault_file("/vault/test.md") is None

    def test_vault_fts_search(self, db):
        db.upsert_vault_file(
            "/vault/a.md", "a.md", "stakeholder alignment process", "h1"
        )
        db.upsert_vault_file(
            "/vault/b.md", "b.md", "technical architecture review", "h2"
        )
        results = db.vault_fts_search("stakeholder")
        assert len(results) == 1
        assert results[0]["filename"] == "a.md"

    def test_vault_fts_search_with_module(self, db):
        db.upsert_vault_file("/vault/a.md", "a.md", "test content", "h1", module="mod1")
        db.upsert_vault_file("/vault/b.md", "b.md", "test content", "h2", module="mod2")
        results = db.vault_fts_search("test", module="mod1")
        assert len(results) == 1
        assert results[0]["module"] == "mod1"

    def test_vault_file_count(self, db):
        assert db.vault_file_count() == 0
        db.upsert_vault_file("/a.md", "a.md", "x", "h1", module="m1")
        db.upsert_vault_file("/b.md", "b.md", "y", "h2", module="m2")
        assert db.vault_file_count() == 2
        assert db.vault_file_count(module="m1") == 1

    def test_vault_files_by_module(self, db):
        db.upsert_vault_file("/a.md", "a.md", "x", "h1", module="m1")
        db.upsert_vault_file("/b.md", "b.md", "y", "h2", module="m1")
        db.upsert_vault_file("/c.md", "c.md", "z", "h3", module="m2")
        result = db.vault_files_by_module()
        assert result["m1"] == 2
        assert result["m2"] == 1

    def test_vault_files_by_status(self, db):
        db.upsert_vault_file(
            "/a.md", "a.md", "x", "h1", frontmatter={"status": "draft"}
        )
        db.upsert_vault_file(
            "/b.md", "b.md", "y", "h2", frontmatter={"status": "ready"}
        )
        db.upsert_vault_file(
            "/c.md", "c.md", "z", "h3", frontmatter={"status": "draft"}
        )
        result = db.vault_files_by_status()
        assert result["draft"] == 2
        assert result["ready"] == 1


# ---------------------------------------------------------------------------
# _hybrid_merge tests
# ---------------------------------------------------------------------------


def _fts_item(file_path: str, score: float = 1.0, **extra) -> dict:
    """Build a minimal FTS result dict as _fts_search would produce."""
    return {
        "file_path": file_path,
        "filename": file_path.split("/")[-1],
        "score": score,
        **extra,
    }


def _sem_item(file_path: str, similarity: float = 0.9, **extra) -> dict:
    """Build a minimal semantic result dict as vault_semantic_search would produce."""
    return {
        "file_path": file_path,
        "filename": file_path.split("/")[-1],
        "similarity": similarity,
        **extra,
    }


class TestHybridMerge:
    def test_fts_only_items_get_fts_match_type(self):
        """Items present only in FTS results receive match_type='fts'."""
        fts = [_fts_item("/a.md")]
        sem = []
        results = _hybrid_merge(fts, sem, limit=10)
        assert len(results) == 1
        assert results[0]["match_type"] == "fts"

    def test_semantic_only_items_get_semantic_match_type(self):
        """Items present only in semantic results receive match_type='semantic'."""
        fts = []
        sem = [_sem_item("/b.md")]
        results = _hybrid_merge(fts, sem, limit=10)
        assert len(results) == 1
        assert results[0]["match_type"] == "semantic"

    def test_items_in_both_lists_get_hybrid_match_type(self):
        """Items appearing in both lists receive match_type='hybrid'."""
        fts = [_fts_item("/c.md")]
        sem = [_sem_item("/c.md")]
        results = _hybrid_merge(fts, sem, limit=10)
        assert len(results) == 1
        assert results[0]["match_type"] == "hybrid"

    def test_hybrid_rrf_score_is_sum_of_fts_and_semantic(self):
        """The rrf_score for a hybrid item equals the sum of both individual RRF contributions."""
        fts = [_fts_item("/shared.md")]
        sem = [_sem_item("/shared.md")]
        results = _hybrid_merge(fts, sem, limit=10)

        k = 20
        expected = 0.4 / (k + 0) + 0.6 / (k + 0)  # rank 0 for each
        assert abs(results[0]["rrf_score"] - expected) < 1e-9

    def test_results_sorted_by_rrf_score_descending(self):
        """Output list is sorted by rrf_score highest-first."""
        fts = [_fts_item("/a.md"), _fts_item("/b.md"), _fts_item("/c.md")]
        sem = [_sem_item("/c.md")]  # /c.md gets boosted to hybrid
        results = _hybrid_merge(fts, sem, limit=10)

        scores = [r["rrf_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_limit_parameter_truncates_output(self):
        """Result list is no longer than the requested limit."""
        fts = [_fts_item(f"/{i}.md") for i in range(10)]
        sem = [_sem_item(f"/{i}.md") for i in range(10)]
        results = _hybrid_merge(fts, sem, limit=3)
        assert len(results) == 3

    def test_limit_zero_returns_empty_list(self):
        """limit=0 returns an empty list."""
        fts = [_fts_item("/x.md")]
        results = _hybrid_merge(fts, [], limit=0)
        assert results == []

    def test_raw_score_stripped_from_fts_items(self):
        """The 'score' field from FTS results is not present in merged output."""
        fts = [_fts_item("/a.md", score=0.99)]
        results = _hybrid_merge(fts, [], limit=10)
        assert "score" not in results[0]

    def test_raw_similarity_stripped_from_semantic_items(self):
        """The 'similarity' field from semantic results is not present in merged output."""
        sem = [_sem_item("/b.md", similarity=0.87)]
        results = _hybrid_merge([], sem, limit=10)
        assert "similarity" not in results[0]

    def test_raw_score_and_similarity_stripped_from_hybrid_items(self):
        """Both 'score' and 'similarity' are absent from hybrid items."""
        fts = [_fts_item("/c.md", score=1.0)]
        sem = [_sem_item("/c.md", similarity=0.75)]
        results = _hybrid_merge(fts, sem, limit=10)
        assert "score" not in results[0]
        assert "similarity" not in results[0]

    def test_rrf_score_present_in_all_output_items(self):
        """Every item in the output has an rrf_score field."""
        fts = [_fts_item("/a.md"), _fts_item("/b.md")]
        sem = [_sem_item("/b.md"), _sem_item("/c.md")]
        results = _hybrid_merge(fts, sem, limit=10)
        assert all("rrf_score" in r for r in results)

    def test_empty_inputs_return_empty_list(self):
        """Both inputs empty produces empty output."""
        assert _hybrid_merge([], [], limit=10) == []

    def test_matched_heading_propagated_from_semantic_into_hybrid(self):
        """When a semantic item with matched_heading merges with an FTS item,
        matched_heading is carried into the hybrid result."""
        fts = [_fts_item("/d.md")]
        sem = [_sem_item("/d.md", matched_heading="## Overview")]
        results = _hybrid_merge(fts, sem, limit=10)
        assert results[0]["matched_heading"] == "## Overview"
