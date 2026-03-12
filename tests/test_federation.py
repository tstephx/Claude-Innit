"""Tests for federated search."""

import sqlite3

import pytest

from claude_innit.db.database import MemoryDatabase
from claude_innit.tools.federation import (
    federated_search,
    _search_books,
    _search_sessions,
    _search_vault,
    _reciprocal_rank_fusion,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    return MemoryDatabase(db_path)


@pytest.fixture
def book_db(tmp_path):
    """Create a mock book-library database."""
    db_path = tmp_path / "library.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT,
            author TEXT
        );

        CREATE TABLE chapters (
            id INTEGER PRIMARY KEY,
            book_id INTEGER,
            title TEXT,
            content TEXT,
            FOREIGN KEY (book_id) REFERENCES books(id)
        );

        CREATE VIRTUAL TABLE chapters_fts USING fts5(
            chapter_id,
            title,
            content,
            tokenize='porter unicode61'
        );

        INSERT INTO books VALUES (1, 'Mastering Behavioral Interviews', 'John Smith');
        INSERT INTO books VALUES (2, 'System Design Primer', 'Jane Doe');

        INSERT INTO chapters VALUES (1, 1, 'CARL Framework', 'The CARL framework structures behavioral responses: Context, Actions, Results, Learnings.');
        INSERT INTO chapters VALUES (2, 1, 'Signal Areas', 'Eight signal areas test different competencies including scope, ownership, and leadership.');
        INSERT INTO chapters VALUES (3, 2, 'Scalability', 'Horizontal scaling distributes load across multiple servers.');

        INSERT INTO chapters_fts VALUES (1, 'CARL Framework', 'The CARL framework structures behavioral responses: Context, Actions, Results, Learnings.');
        INSERT INTO chapters_fts VALUES (2, 'Signal Areas', 'Eight signal areas test different competencies including scope, ownership, and leadership.');
        INSERT INTO chapters_fts VALUES (3, 'Scalability', 'Horizontal scaling distributes load across multiple servers.');
    """)
    conn.commit()
    conn.close()
    return db_path


class TestSearchBooks:
    def test_search_finds_matching_chapters(self, book_db):
        results = _search_books("CARL framework", db_path=book_db)
        assert len(results) >= 1
        assert results[0]["source"] == "books"
        assert "CARL" in results[0]["title"]
        assert results[0]["author"] == "John Smith"

    def test_search_returns_score(self, book_db):
        results = _search_books("signal areas", db_path=book_db)
        assert len(results) >= 1
        assert isinstance(results[0]["score"], float)

    def test_search_missing_db(self, tmp_path):
        results = _search_books("test", db_path=tmp_path / "nonexistent.db")
        assert results == []

    def test_search_empty_results(self, book_db):
        results = _search_books("xyznonexistent", db_path=book_db)
        assert results == []


class TestSearchSessions:
    def test_search_finds_memories(self, db):
        db.insert_memory("m1", "session", "Worked on API migration today")
        db.insert_memory("m2", "project", "Set up database schema")
        results = _search_sessions(db, "API migration")
        assert len(results) >= 1
        assert results[0]["source"] == "sessions"
        assert "API" in results[0]["snippet"]

    def test_search_empty(self, db):
        results = _search_sessions(db, "nonexistent")
        assert results == []


class TestSearchVault:
    def test_search_vault_files(self, db):
        db.upsert_vault_file(
            "/vault/story.md",
            "story.md",
            "Led API migration project",
            "h1",
            module="bs",
        )
        results = _search_vault(db, "API migration")
        assert len(results) >= 1
        assert results[0]["source"] == "vault"
        assert results[0]["module"] == "bs"

    def test_search_vault_with_module_filter(self, db):
        db.upsert_vault_file("/vault/a.md", "a.md", "test content", "h1", module="mod1")
        db.upsert_vault_file("/vault/b.md", "b.md", "test content", "h2", module="mod2")
        results = _search_vault(db, "test", module="mod1")
        assert len(results) == 1
        assert results[0]["module"] == "mod1"


class TestReciprocalRankFusion:
    def test_merges_results(self):
        list1 = [
            {"source": "vault", "file_path": "/a.md", "score": 1.0},
            {"source": "vault", "file_path": "/b.md", "score": 0.9},
        ]
        list2 = [
            {"source": "books", "chapter_id": 1, "score": 1.0},
            {"source": "books", "chapter_id": 2, "score": 0.8},
        ]
        merged = _reciprocal_rank_fusion([list1, list2])
        assert len(merged) == 4
        # All should have rrf_score
        assert all("rrf_score" in item for item in merged)

    def test_boosting_shared_results(self):
        """Items appearing in multiple lists get higher scores."""
        # Same concept appears in both vault and books
        list1 = [{"source": "vault", "file_path": "/conflict.md", "score": 1.0}]
        list2 = [{"source": "vault", "file_path": "/conflict.md", "score": 0.9}]
        list3 = [{"source": "vault", "file_path": "/other.md", "score": 1.0}]
        merged = _reciprocal_rank_fusion([list1, list2, list3])
        # conflict.md should score higher than other.md because it appears twice
        conflict = next(m for m in merged if "conflict" in m.get("file_path", ""))
        other = next(m for m in merged if "other" in m.get("file_path", ""))
        assert conflict["rrf_score"] > other["rrf_score"]

    def test_respects_weights(self):
        list1 = [{"source": "vault", "file_path": "/a.md"}]
        list2 = [{"source": "sessions", "memory_id": "m1"}]
        merged = _reciprocal_rank_fusion(
            [list1, list2],
            weights={"vault": 2.0, "sessions": 0.1},
        )
        vault_item = next(m for m in merged if m["source"] == "vault")
        session_item = next(m for m in merged if m["source"] == "sessions")
        assert vault_item["rrf_score"] > session_item["rrf_score"]


class TestFederatedSearch:
    def test_searches_all_sources(self, db, book_db):
        # Add vault data
        db.upsert_vault_file(
            "/vault/story.md", "story.md", "behavioral interview preparation", "h1"
        )
        # Add session data
        db.insert_memory("m1", "session", "Practiced behavioral interview responses")

        results = federated_search(
            db,
            "behavioral interview",
            sources=["vault", "books", "sessions"],
            book_db_path=book_db,
        )

        assert "vault" in results
        assert "books" in results
        assert "sessions" in results
        assert "merged" in results
        assert len(results["merged"]) > 0

    def test_filters_sources(self, db, book_db):
        db.upsert_vault_file("/vault/note.md", "note.md", "some content", "h1")
        results = federated_search(
            db,
            "content",
            sources=["vault"],
            book_db_path=book_db,
        )
        assert "vault" in results
        assert "books" not in results
        assert "sessions" not in results

    def test_portfolio_searches_vault_with_module(self, db):
        db.upsert_vault_file(
            "/vault/p.md", "p.md", "DSP application redesign", "h1", module="portfolio"
        )
        db.upsert_vault_file(
            "/vault/s.md",
            "s.md",
            "DSP story about design",
            "h2",
            module="behavioral-studio",
        )
        results = federated_search(
            db,
            "DSP",
            sources=["portfolio"],
        )
        assert "portfolio" in results
        # Should only find the portfolio-tagged file
        assert len(results["portfolio"]) == 1
        assert results["portfolio"][0]["source"] == "portfolio"

    def test_empty_query(self, db):
        results = federated_search(db, "xyznonexistent12345")
        assert results["merged"] == []


class TestFederatedVaultTitle:
    """Bug #2: Vault results in merged federated output should have title field."""

    def test_vault_results_have_title(self, db):
        db.upsert_vault_file(
            "/vault/API Migration.md",
            "API Migration.md",
            "Led a cross-team API migration",
            "h1",
            module="bs",
        )
        results = federated_search(db, "API migration", sources=["vault"])
        assert len(results["vault"]) >= 1
        for item in results["vault"]:
            assert "title" in item, "Vault results should have 'title' field"

    def test_vault_title_in_merged(self, db):
        db.upsert_vault_file(
            "/vault/story.md",
            "story.md",
            "behavioral interview preparation",
            "h1",
        )
        results = federated_search(db, "behavioral interview", sources=["vault"])
        for item in results["merged"]:
            assert "title" in item, "Merged vault results should have 'title'"

    def test_vault_title_is_filename_stem(self, db):
        db.upsert_vault_file(
            "/vault/My Great Note.md",
            "My Great Note.md",
            "some content about testing",
            "h1",
        )
        results = federated_search(db, "testing", sources=["vault"])
        assert len(results["vault"]) >= 1
        assert results["vault"][0]["title"] == "My Great Note"


class TestFederatedLimitClamping:
    """Bug #3: Negative/zero limits should be clamped in federated_search."""

    def test_negative_limit(self, db):
        db.upsert_vault_file("/vault/a.md", "a.md", "test content", "h1")
        results = federated_search(db, "test", limit=-1)
        assert len(results["merged"]) <= 30, (
            f"limit=-1 returned {len(results['merged'])} merged results"
        )

    def test_zero_limit(self, db):
        db.upsert_vault_file("/vault/a.md", "a.md", "test content", "h1")
        results = federated_search(db, "test", limit=0)
        assert results["merged"] == []
