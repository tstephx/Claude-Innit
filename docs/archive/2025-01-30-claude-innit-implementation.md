---
status: active
tags: []
type: note
created: '2026-01-30'
modified: '2026-01-30'
---

# Claude Innit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an MCP server that provides Claude with persistent memory across sessions via markdown files, SQLite with FTS5, and vector embeddings.

**Architecture:** Markdown files are the source of truth. A sync process indexes them into SQLite with FTS5 for text search and embeddings for semantic search. The MCP server exposes tools for context loading, search, and memory management.

**Tech Stack:** Python 3.11+, mcp library, SQLite with FTS5, sentence-transformers (all-MiniLM-L6-v2), PyYAML for frontmatter parsing.

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `claude_innit/__init__.py`
- Create: `README.md`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "claude-innit"
version = "0.1.0"
description = "Claude's persistent memory context system"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.0.0",
    "pyyaml>=6.0",
    "sentence-transformers>=2.2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[project.scripts]
claude-innit = "claude_innit.server:main"
```

**Step 2: Create claude_innit/__init__.py**

```python
"""Claude Innit - Persistent memory context for Claude."""

__version__ = "0.1.0"
```

**Step 3: Create README.md**

```markdown
# Claude Innit

Claude's persistent memory context system.

## Features

- Personal context (identity, preferences, workflows)
- Project context (per-project state and decisions)
- Session continuity (recent session summaries)
- Hybrid search (FTS5 + semantic vectors)

## Installation

```bash
pip install -e .
```

## Usage

Add to `~/.claude/mcp_servers.json`:

```json
{
  "claude-innit": {
    "command": "python",
    "args": ["-m", "claude_innit.server"],
    "cwd": "/path/to/Claude-Innit"
  }
}
```
```

**Step 4: Create virtual environment and install**

Run:
```bash
cd /Users/taylorstephens/_Lab/Claude-Innit
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Expected: Package installs successfully

**Step 5: Commit**

```bash
git init
git add pyproject.toml claude_innit/__init__.py README.md
git commit -m "feat: initial project scaffolding"
```

---

## Task 2: Database Schema

**Files:**
- Create: `claude_innit/db/__init__.py`
- Create: `claude_innit/db/database.py`
- Create: `tests/__init__.py`
- Create: `tests/test_database.py`

**Step 1: Write the failing test**

```python
# tests/test_database.py
"""Tests for database module."""

import pytest
from pathlib import Path

from claude_innit.db.database import MemoryDatabase


class TestMemoryDatabase:
    """Tests for MemoryDatabase."""

    def test_creates_tables(self, tmp_path):
        """Database creates required tables on init."""
        db_path = tmp_path / "test.db"
        db = MemoryDatabase(db_path)

        # Verify tables exist
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {row[0] for row in tables}

        assert "memories" in table_names
        assert "memories_fts" in table_names
        assert "embeddings" in table_names

    def test_insert_memory(self, tmp_path):
        """Can insert and retrieve a memory."""
        db_path = tmp_path / "test.db"
        db = MemoryDatabase(db_path)

        db.insert_memory(
            id="test-1",
            category="personal",
            source_file="personal/identity.md",
            content="My name is Taylor",
            metadata={"type": "identity"},
        )

        memory = db.get_memory("test-1")
        assert memory["content"] == "My name is Taylor"
        assert memory["category"] == "personal"

    def test_fts_search(self, tmp_path):
        """Full-text search finds matching memories."""
        db_path = tmp_path / "test.db"
        db = MemoryDatabase(db_path)

        db.insert_memory(
            id="test-1",
            category="personal",
            source_file="personal/identity.md",
            content="My name is Taylor Stephens",
            metadata={},
        )
        db.insert_memory(
            id="test-2",
            category="project",
            source_file="projects/test.md",
            content="Working on book processing",
            metadata={},
        )

        results = db.fts_search("Taylor")
        assert len(results) == 1
        assert results[0]["id"] == "test-1"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_database.py -v`

Expected: FAIL with "No module named 'claude_innit.db'"

**Step 3: Create db/__init__.py**

```python
# claude_innit/db/__init__.py
"""Database module for Claude Innit."""

from claude_innit.db.database import MemoryDatabase

__all__ = ["MemoryDatabase"]
```

**Step 4: Write minimal implementation**

```python
# claude_innit/db/database.py
"""SQLite database with FTS5 for memory storage."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class MemoryDatabase:
    """SQLite database for storing and searching memories."""

    def __init__(self, db_path: Path):
        """Initialize database, creating tables if needed."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Create database tables."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                source_file TEXT,
                content TEXT NOT NULL,
                metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                id,
                content,
                category,
                content='memories',
                content_rowid='rowid'
            );

            CREATE TABLE IF NOT EXISTS embeddings (
                memory_id TEXT PRIMARY KEY,
                embedding BLOB,
                model TEXT,
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            );

            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(id, content, category)
                VALUES (new.id, new.content, new.category);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, id, content, category)
                VALUES ('delete', old.id, old.content, old.category);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, id, content, category)
                VALUES ('delete', old.id, old.content, old.category);
                INSERT INTO memories_fts(id, content, category)
                VALUES (new.id, new.content, new.category);
            END;
        """)
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute raw SQL."""
        return self._conn.execute(sql, params)

    def insert_memory(
        self,
        id: str,
        category: str,
        content: str,
        source_file: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Insert or update a memory."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO memories (id, category, source_file, content, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                category,
                source_file,
                content,
                json.dumps(metadata or {}),
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def get_memory(self, id: str) -> Optional[dict]:
        """Get a memory by ID."""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (id,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    def fts_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories using FTS5."""
        rows = self._conn.execute(
            """
            SELECT m.* FROM memories m
            JOIN memories_fts fts ON m.id = fts.id
            WHERE memories_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self):
        """Close database connection."""
        self._conn.close()
```

**Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_database.py -v`

Expected: PASS (3 tests)

**Step 6: Commit**

```bash
git add claude_innit/db/ tests/
git commit -m "feat: add SQLite database with FTS5 search"
```

---

## Task 3: Embeddings Storage

**Files:**
- Create: `claude_innit/db/embeddings.py`
- Create: `tests/test_embeddings.py`

**Step 1: Write the failing test**

```python
# tests/test_embeddings.py
"""Tests for embeddings module."""

import pytest
import numpy as np

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore


class TestEmbeddingStore:
    """Tests for EmbeddingStore."""

    def test_generates_embedding(self):
        """Can generate embedding for text."""
        store = EmbeddingStore()
        embedding = store.generate("Hello world")

        assert isinstance(embedding, np.ndarray)
        assert embedding.shape == (384,)  # MiniLM dimension

    def test_stores_and_retrieves_embedding(self, tmp_path):
        """Can store and retrieve embeddings."""
        db = MemoryDatabase(tmp_path / "test.db")
        store = EmbeddingStore(db)

        # Insert a memory first
        db.insert_memory(
            id="test-1",
            category="personal",
            content="My name is Taylor",
            metadata={},
        )

        # Generate and store embedding
        store.store_embedding("test-1", "My name is Taylor")

        # Retrieve it
        embedding = store.get_embedding("test-1")
        assert embedding is not None
        assert embedding.shape == (384,)

    def test_semantic_search(self, tmp_path):
        """Semantic search finds similar content."""
        db = MemoryDatabase(tmp_path / "test.db")
        store = EmbeddingStore(db)

        # Insert memories
        db.insert_memory(id="m1", category="personal", content="I love Python programming", metadata={})
        db.insert_memory(id="m2", category="personal", content="The weather is nice today", metadata={})
        db.insert_memory(id="m3", category="personal", content="JavaScript and TypeScript are fun", metadata={})

        # Generate embeddings
        store.store_embedding("m1", "I love Python programming")
        store.store_embedding("m2", "The weather is nice today")
        store.store_embedding("m3", "JavaScript and TypeScript are fun")

        # Search for programming-related content
        results = store.semantic_search("coding languages")

        # Programming-related should rank higher
        assert len(results) >= 2
        result_ids = [r["id"] for r in results]
        # m1 and m3 should be in top results
        assert "m1" in result_ids[:2] or "m3" in result_ids[:2]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_embeddings.py -v`

Expected: FAIL with "No module named 'claude_innit.db.embeddings'"

**Step 3: Write minimal implementation**

```python
# claude_innit/db/embeddings.py
"""Embedding generation and semantic search."""

import struct
from typing import Optional

import numpy as np

from claude_innit.db.database import MemoryDatabase


class EmbeddingStore:
    """Generates and stores embeddings for semantic search."""

    def __init__(self, db: Optional[MemoryDatabase] = None):
        """Initialize with optional database connection."""
        self.db = db
        self._model = None

    def _get_model(self):
        """Lazy-load the embedding model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")
        return self._model

    def generate(self, text: str) -> np.ndarray:
        """Generate embedding for text."""
        model = self._get_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.astype(np.float32)

    def store_embedding(self, memory_id: str, text: str) -> None:
        """Generate and store embedding for a memory."""
        if self.db is None:
            raise ValueError("Database required for storage")

        embedding = self.generate(text)
        blob = self._embedding_to_blob(embedding)

        self.db.execute(
            """
            INSERT OR REPLACE INTO embeddings (memory_id, embedding, model)
            VALUES (?, ?, ?)
            """,
            (memory_id, blob, "all-MiniLM-L6-v2"),
        )
        self.db._conn.commit()

    def get_embedding(self, memory_id: str) -> Optional[np.ndarray]:
        """Get embedding for a memory."""
        if self.db is None:
            return None

        row = self.db.execute(
            "SELECT embedding FROM embeddings WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()

        if row and row[0]:
            return self._blob_to_embedding(row[0])
        return None

    def semantic_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories by semantic similarity."""
        if self.db is None:
            return []

        query_embedding = self.generate(query)

        # Get all embeddings
        rows = self.db.execute(
            """
            SELECT e.memory_id, e.embedding, m.*
            FROM embeddings e
            JOIN memories m ON e.memory_id = m.id
            """
        ).fetchall()

        # Calculate similarities
        results = []
        for row in rows:
            embedding = self._blob_to_embedding(row["embedding"])
            similarity = self._cosine_similarity(query_embedding, embedding)
            memory = dict(row)
            memory["similarity"] = float(similarity)
            results.append(memory)

        # Sort by similarity
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    def _embedding_to_blob(self, embedding: np.ndarray) -> bytes:
        """Convert numpy array to bytes."""
        return embedding.tobytes()

    def _blob_to_embedding(self, blob: bytes) -> np.ndarray:
        """Convert bytes to numpy array."""
        return np.frombuffer(blob, dtype=np.float32)

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
```

**Step 4: Update db/__init__.py**

```python
# claude_innit/db/__init__.py
"""Database module for Claude Innit."""

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore

__all__ = ["MemoryDatabase", "EmbeddingStore"]
```

**Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_embeddings.py -v`

Expected: PASS (3 tests)

**Step 6: Commit**

```bash
git add claude_innit/db/ tests/test_embeddings.py
git commit -m "feat: add embedding generation and semantic search"
```

---

## Task 4: Markdown Sync

**Files:**
- Create: `claude_innit/sync/__init__.py`
- Create: `claude_innit/sync/markdown_sync.py`
- Create: `tests/test_sync.py`

**Step 1: Write the failing test**

```python
# tests/test_sync.py
"""Tests for markdown sync module."""

import pytest
from pathlib import Path

from claude_innit.db.database import MemoryDatabase
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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_sync.py -v`

Expected: FAIL with "No module named 'claude_innit.sync'"

**Step 3: Write minimal implementation**

```python
# claude_innit/sync/__init__.py
"""Sync module for Claude Innit."""

from claude_innit.sync.markdown_sync import MarkdownSync

__all__ = ["MarkdownSync"]
```

```python
# claude_innit/sync/markdown_sync.py
"""Sync markdown files to database."""

import re
from pathlib import Path
from typing import Optional

import yaml

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore


class MarkdownSync:
    """Syncs markdown files to database with optional embeddings."""

    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

    def __init__(
        self,
        db_path: Path,
        memories_dir: Path,
        generate_embeddings: bool = False,
    ):
        """Initialize sync with database and memories directory."""
        self.db = MemoryDatabase(db_path)
        self.memories_dir = Path(memories_dir)
        self.generate_embeddings = generate_embeddings
        self._embedding_store: Optional[EmbeddingStore] = None

    def _get_embedding_store(self) -> EmbeddingStore:
        """Lazy-load embedding store."""
        if self._embedding_store is None:
            self._embedding_store = EmbeddingStore(self.db)
        return self._embedding_store

    def parse_markdown(self, file_path: Path) -> tuple[dict, str]:
        """Parse markdown file, extracting frontmatter and content."""
        text = file_path.read_text()

        frontmatter = {}
        content = text

        match = self.FRONTMATTER_PATTERN.match(text)
        if match:
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                pass
            content = text[match.end():]

        return frontmatter, content.strip()

    def detect_category(self, file_path: Path) -> str:
        """Detect category from file path."""
        rel_path = file_path.relative_to(self.memories_dir)
        parts = rel_path.parts

        if len(parts) > 0:
            first_dir = parts[0].lower()
            if first_dir in ("personal", "projects", "sessions"):
                # Normalize: projects -> project, sessions -> session
                if first_dir == "projects":
                    return "project"
                if first_dir == "sessions":
                    return "session"
                return first_dir

        return "unknown"

    def sync_file(self, file_path: Path) -> bool:
        """Sync a single markdown file to database."""
        try:
            rel_path = file_path.relative_to(self.memories_dir)
            memory_id = str(rel_path)

            frontmatter, content = self.parse_markdown(file_path)
            category = self.detect_category(file_path)

            self.db.insert_memory(
                id=memory_id,
                category=category,
                source_file=str(rel_path),
                content=content,
                metadata=frontmatter,
            )

            if self.generate_embeddings:
                store = self._get_embedding_store()
                store.store_embedding(memory_id, content)

            return True
        except Exception as e:
            print(f"Error syncing {file_path}: {e}")
            return False

    def sync_all(self) -> dict:
        """Sync all markdown files in memories directory."""
        stats = {"synced": 0, "errors": 0, "skipped": 0}

        for md_file in self.memories_dir.rglob("*.md"):
            # Skip files starting with underscore (templates, indexes)
            if md_file.name.startswith("_"):
                stats["skipped"] += 1
                continue

            if self.sync_file(md_file):
                stats["synced"] += 1
            else:
                stats["errors"] += 1

        return stats
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_sync.py -v`

Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add claude_innit/sync/ tests/test_sync.py
git commit -m "feat: add markdown file sync to database"
```

---

## Task 5: MCP Tools - Context & Search

**Files:**
- Create: `claude_innit/tools/__init__.py`
- Create: `claude_innit/tools/context.py`
- Create: `claude_innit/tools/search.py`
- Create: `tests/test_tools.py`

**Step 1: Write the failing test**

```python
# tests/test_tools.py
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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_tools.py -v`

Expected: FAIL with "No module named 'claude_innit.tools'"

**Step 3: Write minimal implementation**

```python
# claude_innit/tools/__init__.py
"""MCP tools for Claude Innit."""

from claude_innit.tools.context import get_context
from claude_innit.tools.search import search

__all__ = ["get_context", "search"]
```

```python
# claude_innit/tools/context.py
"""Context loading tools."""

from typing import Optional

from claude_innit.db.database import MemoryDatabase


def get_context(
    db: MemoryDatabase,
    project: Optional[str] = None,
    include_sessions: bool = True,
    session_limit: int = 5,
) -> dict:
    """
    Load all relevant context for a session.

    Args:
        db: Database connection
        project: Optional project name to filter by
        include_sessions: Whether to include recent sessions
        session_limit: Max number of sessions to include

    Returns:
        Dictionary with personal, project, and session context
    """
    result = {
        "personal": [],
        "project": [],
        "recent_sessions": [],
    }

    # Load personal memories (always)
    personal_rows = db.execute(
        "SELECT * FROM memories WHERE category = 'personal'"
    ).fetchall()
    result["personal"] = [dict(row) for row in personal_rows]

    # Load project memories (filtered if project specified)
    if project:
        project_rows = db.execute(
            """
            SELECT * FROM memories
            WHERE category = 'project'
            AND json_extract(metadata, '$.name') = ?
            """,
            (project,),
        ).fetchall()
    else:
        project_rows = db.execute(
            "SELECT * FROM memories WHERE category = 'project'"
        ).fetchall()
    result["project"] = [dict(row) for row in project_rows]

    # Load recent sessions
    if include_sessions:
        session_rows = db.execute(
            """
            SELECT * FROM memories
            WHERE category = 'session'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (session_limit,),
        ).fetchall()
        result["recent_sessions"] = [dict(row) for row in session_rows]

    return result
```

```python
# claude_innit/tools/search.py
"""Search tools."""

from typing import Optional

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore


def search(
    db: MemoryDatabase,
    query: str,
    method: str = "auto",
    limit: int = 10,
    embedding_store: Optional[EmbeddingStore] = None,
) -> list[dict]:
    """
    Search memories using FTS or semantic search.

    Args:
        db: Database connection
        query: Search query
        method: "auto", "text", or "semantic"
        limit: Maximum results
        embedding_store: Optional embedding store for semantic search

    Returns:
        List of matching memories
    """
    if method == "auto":
        # Short queries (1-3 words) use FTS
        word_count = len(query.split())
        if word_count <= 3:
            method = "text"
        else:
            method = "semantic"

    if method == "text":
        return db.fts_search(query, limit=limit)
    elif method == "semantic":
        if embedding_store is None:
            embedding_store = EmbeddingStore(db)
        return embedding_store.semantic_search(query, limit=limit)
    else:
        raise ValueError(f"Unknown search method: {method}")
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_tools.py -v`

Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add claude_innit/tools/ tests/test_tools.py
git commit -m "feat: add get_context and search tools"
```

---

## Task 6: MCP Tools - Memory Management

**Files:**
- Create: `claude_innit/tools/memory.py`
- Create: `claude_innit/tools/session.py`
- Modify: `tests/test_tools.py`

**Step 1: Write the failing test**

Add to `tests/test_tools.py`:

```python
from claude_innit.tools.memory import remember, forget
from claude_innit.tools.session import save_session


class TestRemember:
    """Tests for remember tool."""

    def test_stores_memory(self, tmp_path):
        """Remember stores a new memory."""
        db = MemoryDatabase(tmp_path / "test.db")

        result = remember(
            db,
            content="I prefer dark mode",
            category="personal",
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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_tools.py::TestRemember -v`

Expected: FAIL with "cannot import name 'remember'"

**Step 3: Write minimal implementation**

```python
# claude_innit/tools/memory.py
"""Memory management tools."""

import uuid
from typing import Optional

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore


def remember(
    db: MemoryDatabase,
    content: str,
    category: str,
    project: Optional[str] = None,
    generate_embedding: bool = True,
) -> dict:
    """
    Store a new memory.

    Args:
        db: Database connection
        content: The content to remember
        category: "personal", "project", or "session"
        project: Optional project name for project memories
        generate_embedding: Whether to generate embedding

    Returns:
        Dict with success status and memory_id
    """
    memory_id = f"{category}/{uuid.uuid4().hex[:8]}"
    metadata = {}

    if project:
        metadata["project"] = project

    try:
        db.insert_memory(
            id=memory_id,
            category=category,
            content=content,
            metadata=metadata,
        )

        if generate_embedding:
            try:
                store = EmbeddingStore(db)
                store.store_embedding(memory_id, content)
            except Exception:
                pass  # Embedding is optional

        return {"success": True, "memory_id": memory_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


def forget(db: MemoryDatabase, memory_id: str) -> dict:
    """
    Remove a memory.

    Args:
        db: Database connection
        memory_id: ID of memory to remove

    Returns:
        Dict with success status
    """
    try:
        # Delete from embeddings first (foreign key)
        db.execute("DELETE FROM embeddings WHERE memory_id = ?", (memory_id,))
        # Delete from memories (triggers handle FTS)
        db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        db._conn.commit()

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

```python
# claude_innit/tools/session.py
"""Session management tools."""

from datetime import datetime
from typing import Optional

from claude_innit.db.database import MemoryDatabase


def save_session(
    db: MemoryDatabase,
    summary: str,
    topics: Optional[list[str]] = None,
    project: Optional[str] = None,
) -> dict:
    """
    Save a session summary.

    Args:
        db: Database connection
        summary: Summary of what happened in the session
        topics: Optional list of topics covered
        project: Optional project name

    Returns:
        Dict with success status and session_id
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    session_id = f"sessions/{date_str}-{datetime.now().strftime('%H%M%S')}"

    metadata = {
        "date": date_str,
        "topics": topics or [],
    }
    if project:
        metadata["project"] = project

    try:
        db.insert_memory(
            id=session_id,
            category="session",
            content=summary,
            metadata=metadata,
        )

        return {"success": True, "session_id": session_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

**Step 4: Update tools/__init__.py**

```python
# claude_innit/tools/__init__.py
"""MCP tools for Claude Innit."""

from claude_innit.tools.context import get_context
from claude_innit.tools.search import search
from claude_innit.tools.memory import remember, forget
from claude_innit.tools.session import save_session

__all__ = ["get_context", "search", "remember", "forget", "save_session"]
```

**Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_tools.py -v`

Expected: PASS (all 8 tests)

**Step 6: Commit**

```bash
git add claude_innit/tools/ tests/test_tools.py
git commit -m "feat: add remember, forget, and save_session tools"
```

---

## Task 7: MCP Server

**Files:**
- Create: `claude_innit/server.py`
- Create: `tests/test_server.py`

**Step 1: Write the failing test**

```python
# tests/test_server.py
"""Tests for MCP server."""

import pytest

from claude_innit.server import create_server


class TestServer:
    """Tests for MCP server creation."""

    def test_creates_server(self, tmp_path):
        """Server creates with valid configuration."""
        server = create_server(
            db_path=tmp_path / "test.db",
            memories_dir=tmp_path / "memories",
        )

        assert server is not None
        assert server.name == "claude-innit"

    def test_registers_tools(self, tmp_path):
        """Server registers all expected tools."""
        server = create_server(
            db_path=tmp_path / "test.db",
            memories_dir=tmp_path / "memories",
        )

        tool_names = [tool.name for tool in server.list_tools()]

        assert "get_context" in tool_names
        assert "search" in tool_names
        assert "remember" in tool_names
        assert "forget" in tool_names
        assert "save_session" in tool_names
        assert "sync" in tool_names
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_server.py -v`

Expected: FAIL with "cannot import name 'create_server'"

**Step 3: Write minimal implementation**

```python
# claude_innit/server.py
"""MCP server for Claude Innit."""

import asyncio
import json
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
from claude_innit.sync.markdown_sync import MarkdownSync
from claude_innit.tools import (
    get_context,
    search,
    remember,
    forget,
    save_session,
)


def create_server(
    db_path: Path,
    memories_dir: Path,
) -> Server:
    """Create and configure the MCP server."""
    server = Server("claude-innit")

    # Initialize database
    db = MemoryDatabase(db_path)
    embedding_store = EmbeddingStore(db)
    sync = MarkdownSync(db_path, memories_dir, generate_embeddings=True)

    # Sync on startup
    if memories_dir.exists():
        sync.sync_all()

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_context",
                description="Load personal, project, and session context",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {
                            "type": "string",
                            "description": "Optional project name to filter by",
                        },
                    },
                },
            ),
            Tool(
                name="search",
                description="Search memories using FTS or semantic search",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "method": {
                            "type": "string",
                            "enum": ["auto", "text", "semantic"],
                            "description": "Search method (default: auto)",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="remember",
                description="Store a new memory",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Content to remember",
                        },
                        "category": {
                            "type": "string",
                            "enum": ["personal", "project", "session"],
                            "description": "Memory category",
                        },
                        "project": {
                            "type": "string",
                            "description": "Optional project name",
                        },
                    },
                    "required": ["content", "category"],
                },
            ),
            Tool(
                name="forget",
                description="Remove a memory",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "ID of memory to remove",
                        },
                    },
                    "required": ["memory_id"],
                },
            ),
            Tool(
                name="save_session",
                description="Save session summary",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Session summary",
                        },
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Topics covered",
                        },
                        "project": {
                            "type": "string",
                            "description": "Project worked on",
                        },
                    },
                    "required": ["summary"],
                },
            ),
            Tool(
                name="sync",
                description="Re-sync markdown files to database",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "force": {
                            "type": "boolean",
                            "description": "Force full resync",
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "get_context":
            result = get_context(db, project=arguments.get("project"))
        elif name == "search":
            result = search(
                db,
                query=arguments["query"],
                method=arguments.get("method", "auto"),
                embedding_store=embedding_store,
            )
        elif name == "remember":
            result = remember(
                db,
                content=arguments["content"],
                category=arguments["category"],
                project=arguments.get("project"),
            )
        elif name == "forget":
            result = forget(db, arguments["memory_id"])
        elif name == "save_session":
            result = save_session(
                db,
                summary=arguments["summary"],
                topics=arguments.get("topics"),
                project=arguments.get("project"),
            )
        elif name == "sync":
            result = sync.sync_all()
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


async def main():
    """Run the MCP server."""
    # Default paths
    base_dir = Path(__file__).parent.parent
    db_path = base_dir / "data" / "innit.db"
    memories_dir = base_dir / "data" / "memories"

    server = create_server(db_path, memories_dir)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main_sync():
    """Synchronous entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_server.py -v`

Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add claude_innit/server.py tests/test_server.py
git commit -m "feat: add MCP server with all tools"
```

---

## Task 8: Initial Memory Files

**Files:**
- Create: `data/memories/personal/identity.md`
- Create: `data/memories/personal/preferences.md`
- Create: `data/memories/personal/workflows.md`
- Create: `data/memories/projects/_template.md`
- Create: `data/memories/sessions/_index.md`

**Step 1: Create directory structure**

```bash
mkdir -p data/memories/personal data/memories/projects data/memories/sessions
```

**Step 2: Create identity.md**

```markdown
---
type: identity
---

# Identity

- Name: Taylor Stephens
- Role: Program Manager with MBA background
- Learning: Technical concepts for better engineering collaboration
```

**Step 3: Create preferences.md**

```markdown
---
type: preferences
---

# Preferences

## Communication
- Concise responses preferred
- Use business analogies for technical concepts
- No emojis unless requested

## Working Style
- Prefer planning before implementation
- Like seeing options with trade-offs
- Appreciate systematic approaches
```

**Step 4: Create workflows.md**

```markdown
---
type: workflows
---

# Workflows

## Development
- Use TDD when implementing features
- Commit frequently with clear messages
- Use git worktrees for feature isolation

## Planning
- Brainstorm before building
- Create detailed plans with bite-sized tasks
- Review checkpoints after major milestones
```

**Step 5: Create projects/_template.md**

```markdown
---
type: project
name: project-name
status: active
---

# Project Name

## Current State
[What's the current status?]

## Key Decisions
- [Major decision 1]
- [Major decision 2]

## Recent Work
- [Recent task 1]
- [Recent task 2]

## Open Items
- [TODO 1]
- [TODO 2]
```

**Step 6: Create sessions/_index.md**

```markdown
---
type: session-index
---

# Recent Sessions

This file summarizes recent sessions for quick context loading.

## Latest Sessions

_No sessions recorded yet._
```

**Step 7: Commit**

```bash
git add data/
git commit -m "feat: add initial memory files"
```

---

## Task 9: Claude Code Integration

**Files:**
- Create: `scripts/install.sh`
- Modify: `~/.claude/mcp_servers.json`

**Step 1: Create install script**

```bash
#!/bin/bash
# scripts/install.sh - Install Claude Innit

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Installing Claude Innit..."

# Create virtual environment if needed
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    python3 -m venv "$PROJECT_DIR/.venv"
fi

# Install package
"$PROJECT_DIR/.venv/bin/pip" install -e "$PROJECT_DIR"

# Create data directory
mkdir -p "$PROJECT_DIR/data/memories"

echo "Claude Innit installed!"
echo ""
echo "Add to ~/.claude/mcp_servers.json:"
echo ""
cat << EOF
{
  "claude-innit": {
    "command": "$PROJECT_DIR/.venv/bin/python",
    "args": ["-m", "claude_innit.server"],
    "cwd": "$PROJECT_DIR"
  }
}
EOF
```

**Step 2: Make executable**

```bash
chmod +x scripts/install.sh
```

**Step 3: Run install**

```bash
./scripts/install.sh
```

**Step 4: Update mcp_servers.json**

Add to `~/.claude/mcp_servers.json`:

```json
{
  "claude-innit": {
    "command": "/Users/taylorstephens/_Lab/Claude-Innit/.venv/bin/python",
    "args": ["-m", "claude_innit.server"],
    "cwd": "/Users/taylorstephens/_Lab/Claude-Innit"
  }
}
```

**Step 5: Commit**

```bash
git add scripts/
git commit -m "feat: add install script and Claude Code integration"
```

---

## Task 10: Final Verification

**Step 1: Run all tests**

```bash
.venv/bin/pytest tests/ -v
```

Expected: All tests pass

**Step 2: Verify MCP server starts**

```bash
.venv/bin/python -m claude_innit.server &
sleep 2
kill %1
```

Expected: No errors

**Step 3: Restart Claude Code**

Exit and restart Claude Code to pick up new MCP server.

**Step 4: Test tools work**

In Claude Code, the following tools should now be available:
- `mcp__claude-innit__get_context`
- `mcp__claude-innit__search`
- `mcp__claude-innit__remember`
- `mcp__claude-innit__forget`
- `mcp__claude-innit__save_session`
- `mcp__claude-innit__sync`

**Step 5: Final commit**

```bash
git add -A
git commit -m "chore: complete Claude Innit v0.1.0"
git tag v0.1.0
```

---

## Summary

| Task | Description | Tests |
|------|-------------|-------|
| 1 | Project scaffolding | - |
| 2 | Database schema | 3 |
| 3 | Embeddings storage | 3 |
| 4 | Markdown sync | 3 |
| 5 | Context & search tools | 4 |
| 6 | Memory management tools | 4 |
| 7 | MCP server | 2 |
| 8 | Initial memory files | - |
| 9 | Claude Code integration | - |
| 10 | Final verification | - |

**Total: 19 tests**
