---
status: active
tags: []
type: note
created: '2026-03-04'
modified: '2026-03-04'
---

# Claude-Innit MCP Optimizations Implementation Plan

<!-- project: claude-innit -->

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix reliability bugs, improve LLM usability, and add missing MCP patterns identified in the architectural review.

**Architecture:** Changes are layered bottom-up — database layer first (WAL, delete_memory), then tool layer (forget durability, embedding_store threading, list_memories), then server layer (error boundary, async sync, tool descriptions). Each task is independently testable and committable.

**Tech Stack:** Python 3.12, SQLite/FTS5, sentence-transformers (all-MiniLM-L6-v2), MCP SDK, pytest

---

## Task 1: Enable WAL Mode (database.py)

**Files:**
- Modify: `claude_innit/db/database.py:17`
- Test: `tests/test_database.py`

**Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_wal_mode_enabled(tmp_path):
    """Database uses WAL journal mode."""
    db = MemoryDatabase(tmp_path / "test.db")
    row = db._conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_database.py::test_wal_mode_enabled -v
```

Expected: FAIL — `assert 'delete' == 'wal'`

**Step 3: Write minimal implementation**

In `claude_innit/db/database.py`, update `__init__` after the connect call:

```python
self._conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
self._conn.row_factory = sqlite3.Row
self._conn.execute("PRAGMA journal_mode=WAL")
self._conn.execute("PRAGMA synchronous=NORMAL")
self._create_tables()
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_database.py::test_wal_mode_enabled -v
```

Expected: PASS

**Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all existing tests still pass

**Step 6: Commit**

```bash
git add claude_innit/db/database.py tests/test_database.py
git commit -m "fix: enable WAL mode and connection timeout to prevent SQLITE_BUSY"
```

---

## Task 2: Add `delete_memory()` to MemoryDatabase

**Files:**
- Modify: `claude_innit/db/database.py`
- Test: `tests/test_database.py`

This adds a proper public API method so `forget()` doesn't need to access `db._conn` directly.

**Step 1: Write the failing test**

```python
def test_delete_memory(tmp_path):
    """delete_memory removes record and embeddings atomically."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(id="test/abc", category="personal", content="to delete", metadata={})

    db.delete_memory("test/abc")

    assert db.get_memory("test/abc") is None

def test_delete_memory_nonexistent_is_noop(tmp_path):
    """Deleting a nonexistent memory does not raise."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.delete_memory("does/not/exist")  # should not raise
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_database.py::test_delete_memory tests/test_database.py::test_delete_memory_nonexistent_is_noop -v
```

Expected: FAIL — `AttributeError: 'MemoryDatabase' object has no attribute 'delete_memory'`

**Step 3: Write minimal implementation**

Add after `get_memory()` in `claude_innit/db/database.py`:

```python
def delete_memory(self, id: str) -> None:
    """Delete a memory and its embedding by ID."""
    self._conn.execute("DELETE FROM embeddings WHERE memory_id = ?", (id,))
    self._conn.execute("DELETE FROM memories WHERE id = ?", (id,))
    self._conn.commit()
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_database.py::test_delete_memory tests/test_database.py::test_delete_memory_nonexistent_is_noop -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add claude_innit/db/database.py tests/test_database.py
git commit -m "feat: add delete_memory() public method to MemoryDatabase"
```

---

## Task 3: Fix `forget()` Durability — Delete Markdown File

**Files:**
- Modify: `claude_innit/tools/memory.py:88-108`
- Modify: `claude_innit/server.py:185-186` (pass memories_dir)
- Test: `tests/test_tools.py`

This fixes the critical bug where `forget()` leaves the markdown file on disk, causing the memory to be re-inserted on next server startup.

**Step 1: Write the failing regression test**

Add to `tests/test_tools.py`:

```python
from claude_innit.sync.markdown_sync import MarkdownSync

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

        # Verify file exists
        md_file = memories_dir / f"{memory_id}.md"
        assert md_file.exists()

        # Forget it
        from claude_innit.tools.memory import forget
        forget_result = forget(db, memory_id, memories_dir=memories_dir)
        assert forget_result["success"] is True

        # Markdown file must be gone
        assert not md_file.exists()

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

        from claude_innit.tools.memory import forget
        forget(db, memory_id, memories_dir=memories_dir)

        # Simulate server restart (new db + sync)
        db2 = MemoryDatabase(tmp_path / "test.db")
        sync = MarkdownSync(tmp_path / "test.db", memories_dir, generate_embeddings=False)
        sync.sync_all()

        assert db2.get_memory(memory_id) is None
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_tools.py::TestForgetDurability -v
```

Expected: FAIL — either file still exists, or memory reappears after sync

**Step 3: Update `forget()` signature and implementation**

Replace the entire `forget()` function in `claude_innit/tools/memory.py`:

```python
def forget(db: MemoryDatabase, memory_id: str, memories_dir: Optional[Path] = None) -> dict:
    """
    Remove a memory permanently.

    Args:
        db: Database connection
        memory_id: ID of memory to remove
        memories_dir: Path to memories directory — required for durable deletion.
                      Without it, the markdown file survives and sync will re-insert.

    Returns:
        Dict with success status
    """
    try:
        # Delete markdown file first — if this fails, abort before touching DB
        if memories_dir is not None:
            md_file = memories_dir / f"{memory_id}.md"
            if md_file.exists():
                md_file.unlink()

        db.delete_memory(memory_id)

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

**Step 4: Pass `memories_dir` in `server.py`**

In `server.py` line 185-186, update the `forget` call:

```python
elif name == "forget":
    result = forget(self.db, arguments["memory_id"], memories_dir=self.memories_dir)
```

**Step 5: Run test to verify it passes**

```bash
pytest tests/test_tools.py::TestForgetDurability -v
```

Expected: PASS

**Step 6: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass

**Step 7: Commit**

```bash
git add claude_innit/tools/memory.py claude_innit/server.py tests/test_tools.py
git commit -m "fix: forget() now deletes markdown file for durable removal across server restarts"
```

---

## Task 4: Thread Shared `embedding_store` into `remember()`

**Files:**
- Modify: `claude_innit/tools/memory.py:34,76-81`
- Modify: `claude_innit/server.py:177-184`
- Test: `tests/test_tools.py`

This fixes model reload on every `remember()` call by reusing the server's singleton EmbeddingStore.

**Step 1: Write the failing test**

```python
class TestRememberEmbeddingStore:
    """remember() uses the provided embedding_store instead of creating a new one."""

    def test_remember_uses_provided_embedding_store(self, tmp_path):
        """remember() calls store_embedding on the provided store, not a new one."""
        from unittest.mock import MagicMock
        from claude_innit.tools.memory import remember

        db = MemoryDatabase(tmp_path / "test.db")
        mock_store = MagicMock()

        result = remember(
            db,
            content="Test with mock store",
            category="personal",
            generate_embedding=True,
            embedding_store=mock_store,
        )

        assert result["success"] is True
        mock_store.store_embedding.assert_called_once()
        call_args = mock_store.store_embedding.call_args
        assert "Test with mock store" in call_args[0][1]
```

**Step 2: Run test to verify it fails**

```bash
pytest "tests/test_tools.py::TestRememberEmbeddingStore::test_remember_uses_provided_embedding_store" -v
```

Expected: FAIL — `TypeError: remember() got an unexpected keyword argument 'embedding_store'`

**Step 3: Update `remember()` signature**

Update the function signature and embedding block in `claude_innit/tools/memory.py`:

```python
def remember(
    db: MemoryDatabase,
    content: str,
    category: str,
    project: Optional[str] = None,
    generate_embedding: bool = True,
    memories_dir: Optional[Path] = None,
    embedding_store: Optional[EmbeddingStore] = None,
) -> dict:
```

And replace the embedding generation block (lines 76-81):

```python
        if generate_embedding:
            try:
                store = embedding_store if embedding_store is not None else EmbeddingStore(db)
                store.store_embedding(memory_id, content)
            except Exception as e:
                pass  # Embedding is optional; memory is still stored
```

**Step 4: Pass `embedding_store` in `server.py`**

Update the `remember` call in `server.py`:

```python
elif name == "remember":
    result = remember(
        self.db,
        content=arguments["content"],
        category=arguments["category"],
        project=arguments.get("project"),
        memories_dir=self.memories_dir,
        embedding_store=self.embedding_store,
    )
```

**Step 5: Run test to verify it passes**

```bash
pytest "tests/test_tools.py::TestRememberEmbeddingStore" -v
```

Expected: PASS

**Step 6: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass

**Step 7: Commit**

```bash
git add claude_innit/tools/memory.py claude_innit/server.py tests/test_tools.py
git commit -m "fix: thread shared embedding_store into remember() to avoid model reload per call"
```

---

## Task 5: Add `min_similarity` Threshold to Semantic Search

**Files:**
- Modify: `claude_innit/db/embeddings.py:64`
- Test: `tests/test_embeddings.py`

**Step 1: Write the failing test**

Add to `tests/test_embeddings.py` (using mock embeddings to avoid loading real model):

```python
def test_semantic_search_filters_low_similarity(tmp_path):
    """Results below min_similarity threshold are excluded."""
    from unittest.mock import patch
    import numpy as np

    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(id="mem/1", category="personal", content="Python programming", metadata={})

    store = EmbeddingStore(db)

    # Store a fixed embedding
    fixed_embedding = np.ones(384, dtype=np.float32)
    fixed_embedding /= np.linalg.norm(fixed_embedding)
    blob = fixed_embedding.tobytes()
    db.execute("INSERT INTO embeddings (memory_id, embedding, model) VALUES (?, ?, ?)",
               ("mem/1", blob, "test"))
    db._conn.commit()

    # Query with orthogonal vector (similarity ~0)
    orthogonal = np.zeros(384, dtype=np.float32)
    orthogonal[0] = 1.0  # different direction

    with patch.object(store, 'generate', return_value=orthogonal):
        results = store.semantic_search("anything", limit=10, min_similarity=0.5)

    assert len(results) == 0  # filtered out due to low similarity

def test_semantic_search_returns_high_similarity(tmp_path):
    """Results above min_similarity are included."""
    from unittest.mock import patch
    import numpy as np

    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(id="mem/1", category="personal", content="Python programming", metadata={})

    store = EmbeddingStore(db)

    # Store embedding identical to query
    fixed_embedding = np.ones(384, dtype=np.float32)
    fixed_embedding /= np.linalg.norm(fixed_embedding)
    blob = fixed_embedding.tobytes()
    db.execute("INSERT INTO embeddings (memory_id, embedding, model) VALUES (?, ?, ?)",
               ("mem/1", blob, "test"))
    db._conn.commit()

    with patch.object(store, 'generate', return_value=fixed_embedding.copy()):
        results = store.semantic_search("anything", limit=10, min_similarity=0.5)

    assert len(results) == 1
    assert results[0]["similarity"] >= 0.5
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_embeddings.py::test_semantic_search_filters_low_similarity tests/test_embeddings.py::test_semantic_search_returns_high_similarity -v
```

Expected: FAIL — `TypeError: semantic_search() got an unexpected keyword argument 'min_similarity'`

**Step 3: Update `semantic_search()` in `embeddings.py`**

```python
def semantic_search(self, query: str, limit: int = 10, min_similarity: float = 0.35) -> list[dict]:
    """Search memories by semantic similarity."""
    if self.db is None:
        return []

    query_embedding = self.generate(query)

    rows = self.db.execute(
        """
        SELECT e.memory_id, e.embedding, m.*
        FROM embeddings e
        JOIN memories m ON e.memory_id = m.id
        """
    ).fetchall()

    results = []
    for row in rows:
        embedding = self._blob_to_embedding(row["embedding"])
        similarity = self._cosine_similarity(query_embedding, embedding)
        if similarity >= min_similarity:
            memory = dict(row)
            memory["similarity"] = float(similarity)
            results.append(memory)

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:limit]
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_embeddings.py::test_semantic_search_filters_low_similarity tests/test_embeddings.py::test_semantic_search_returns_high_similarity -v
```

Expected: PASS

**Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass

**Step 6: Commit**

```bash
git add claude_innit/db/embeddings.py tests/test_embeddings.py
git commit -m "feat: add min_similarity threshold to semantic_search (default 0.35)"
```

---

## Task 6: Add Error Boundary in `call_tool`

**Files:**
- Modify: `claude_innit/server.py:166-205`
- Test: `tests/test_server.py`

**Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
import pytest
import asyncio
from pathlib import Path
from claude_innit.server import create_server


@pytest.mark.asyncio
async def test_call_tool_unknown_tool_returns_error(tmp_path):
    """Unknown tool name returns error TextContent, does not raise."""
    server = create_server(tmp_path / "test.db", tmp_path / "memories")
    result = await server.call_tool("nonexistent_tool", {})
    assert len(result) == 1
    import json
    payload = json.loads(result[0].text)
    assert "error" in payload


@pytest.mark.asyncio
async def test_call_tool_bad_args_returns_error(tmp_path):
    """Tool called with missing required args returns error, does not raise."""
    server = create_server(tmp_path / "test.db", tmp_path / "memories")
    # forget requires memory_id; passing empty dict should not crash server
    result = await server.call_tool("forget", {})
    assert len(result) == 1
    import json
    payload = json.loads(result[0].text)
    assert "error" in payload
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_server.py::test_call_tool_unknown_tool_returns_error tests/test_server.py::test_call_tool_bad_args_returns_error -v
```

Expected: FAIL or Exception raised

**Step 3: Wrap `call_tool` in error boundary**

Replace the `call_tool` method body in `claude_innit/server.py`:

```python
async def call_tool(self, name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls with error boundary — never drops the MCP connection."""
    try:
        if name == "get_context":
            result = get_context(self.db, project=arguments.get("project"))
        elif name == "search":
            result = search(
                self.db,
                query=arguments["query"],
                method=arguments.get("method", "auto"),
                embedding_store=self.embedding_store,
            )
        elif name == "remember":
            result = remember(
                self.db,
                content=arguments["content"],
                category=arguments["category"],
                project=arguments.get("project"),
                memories_dir=self.memories_dir,
                embedding_store=self.embedding_store,
            )
        elif name == "forget":
            result = forget(self.db, arguments["memory_id"], memories_dir=self.memories_dir)
        elif name == "save_session":
            result = save_session(
                self.db,
                summary=arguments["summary"],
                topics=arguments.get("topics"),
                project=arguments.get("project"),
                memories_dir=self.memories_dir,
            )
        elif name == "admin_sync":
            result = self.sync.sync_all()
        elif name == "admin_check_integrity":
            result = check_integrity(
                self.db,
                auto_repair=arguments.get("auto_repair", True),
            )
        elif name == "list_memories":
            result = list_memories(
                self.db,
                category=arguments.get("category"),
                project=arguments.get("project"),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": type(e).__name__, "message": str(e), "tool": name}

    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_server.py::test_call_tool_unknown_tool_returns_error tests/test_server.py::test_call_tool_bad_args_returns_error -v
```

Expected: PASS

**Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass

**Step 6: Commit**

```bash
git add claude_innit/server.py tests/test_server.py
git commit -m "fix: wrap call_tool in error boundary — exceptions return JSON error, never crash connection"
```

---

## Task 7: Add `list_memories` Tool

**Files:**
- Create: `claude_innit/tools/list.py`
- Modify: `claude_innit/tools/__init__.py`
- Modify: `claude_innit/server.py` (register tool + add import)
- Test: `tests/test_tools.py`

**Step 1: Write the failing test**

```python
class TestListMemories:
    """Tests for list_memories tool."""

    def test_lists_all_memories(self, tmp_path):
        """list_memories returns all memories with id, preview, category, updated_at."""
        from claude_innit.tools.list import list_memories
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory(id="personal/a1", category="personal", content="I prefer dark mode", metadata={})
        db.insert_memory(id="project/b2", category="project", content="Using adapter pattern", metadata={"name": "myapp"})

        result = list_memories(db)

        assert isinstance(result, list)
        assert len(result) == 2
        ids = [m["id"] for m in result]
        assert "personal/a1" in ids
        assert "project/b2" in ids
        # Each entry has required keys
        for m in result:
            assert "id" in m
            assert "preview" in m
            assert "category" in m
            assert "updated_at" in m

    def test_filters_by_category(self, tmp_path):
        """list_memories filters by category."""
        from claude_innit.tools.list import list_memories
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory(id="personal/a1", category="personal", content="personal thing", metadata={})
        db.insert_memory(id="project/b2", category="project", content="project thing", metadata={})

        result = list_memories(db, category="personal")

        assert len(result) == 1
        assert result[0]["id"] == "personal/a1"

    def test_filters_by_project(self, tmp_path):
        """list_memories filters project memories by project name."""
        from claude_innit.tools.list import list_memories
        db = MemoryDatabase(tmp_path / "test.db")
        db.insert_memory(id="project/a1", category="project", content="myapp memory", metadata={"name": "myapp"})
        db.insert_memory(id="project/b2", category="project", content="other memory", metadata={"name": "other"})

        result = list_memories(db, project="myapp")

        assert len(result) == 1
        assert result[0]["id"] == "project/a1"

    def test_preview_is_truncated(self, tmp_path):
        """Preview is max 80 chars."""
        from claude_innit.tools.list import list_memories
        db = MemoryDatabase(tmp_path / "test.db")
        long_content = "x" * 200
        db.insert_memory(id="personal/a1", category="personal", content=long_content, metadata={})

        result = list_memories(db)

        assert len(result[0]["preview"]) <= 83  # 80 chars + "..."
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_tools.py::TestListMemories -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'claude_innit.tools.list'`

**Step 3: Create `claude_innit/tools/list.py`**

```python
"""List memories tool."""

from typing import Optional

from claude_innit.db.database import MemoryDatabase


def list_memories(
    db: MemoryDatabase,
    category: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    List stored memories with ID and content preview.

    Use this to discover memory IDs before calling forget(), or to audit
    what is stored. Returns id, preview (first 80 chars), category, updated_at.

    Args:
        db: Database connection
        category: Filter by "personal", "project", or "session"
        project: Filter project memories by project name
        limit: Max results (default 100)

    Returns:
        List of dicts with id, preview, category, updated_at
    """
    if project:
        rows = db.execute(
            """
            SELECT id, content, category, updated_at FROM memories
            WHERE category = 'project'
            AND json_extract(metadata, '$.name') = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (project, limit),
        ).fetchall()
    elif category:
        rows = db.execute(
            """
            SELECT id, content, category, updated_at FROM memories
            WHERE category = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (category, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT id, content, category, updated_at FROM memories
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results = []
    for row in rows:
        content = row["content"]
        preview = content[:80] + "..." if len(content) > 80 else content
        results.append({
            "id": row["id"],
            "preview": preview,
            "category": row["category"],
            "updated_at": row["updated_at"],
        })
    return results
```

**Step 4: Export from `__init__.py`**

Add to `claude_innit/tools/__init__.py`:

```python
from claude_innit.tools.list import list_memories

__all__ = ["get_context", "search", "remember", "forget", "save_session", "check_integrity", "list_memories"]
```

**Step 5: Register tool + import in `server.py`**

Add import at top of `server.py`:

```python
from claude_innit.tools import (
    get_context,
    search,
    remember,
    forget,
    save_session,
    check_integrity,
    list_memories,
)
```

Add to `_define_tools()` list in `server.py`:

```python
Tool(
    name="list_memories",
    description=(
        "List stored memories with their IDs, previews, and categories. "
        "Call this to discover memory IDs before using forget(), or to audit what is stored. "
        "Filter by category ('personal', 'project', 'session') or project name."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["personal", "project", "session"],
                "description": "Filter by memory category",
            },
            "project": {
                "type": "string",
                "description": "Filter project memories by project name",
            },
        },
    },
),
```

**Step 6: Run test to verify it passes**

```bash
pytest tests/test_tools.py::TestListMemories -v
```

Expected: PASS

**Step 7: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass

**Step 8: Commit**

```bash
git add claude_innit/tools/list.py claude_innit/tools/__init__.py claude_innit/server.py tests/test_tools.py
git commit -m "feat: add list_memories tool for memory ID discovery before forget()"
```

---

## Task 8: Rewrite Tool Descriptions + Rename Admin Tools

**Files:**
- Modify: `claude_innit/server.py` (all Tool descriptions + sync/check_integrity names)

No tests needed (description changes are not behavior changes), but run the test suite to confirm nothing broke.

**Step 1: Update all Tool definitions in `_define_tools()`**

Replace the entire `_define_tools()` return list in `server.py`:

```python
return [
    Tool(
        name="get_context",
        description=(
            "Load all persistent memory for this session. "
            "Call this once at the start of every session before doing any other work. "
            "Pass the project name to filter to relevant project and session memories."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name to filter context (e.g. 'claude-innit', 'my-app')",
                },
            },
        },
    ),
    Tool(
        name="search",
        description=(
            "Find stored memories by keyword or concept. "
            "Call this when you need information from past sessions that is not in the current get_context result. "
            "Short keywords (1-3 words) use exact text match. Longer phrases use semantic/concept search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — use short keywords for exact match, longer phrases for concept recall",
                },
                "method": {
                    "type": "string",
                    "enum": ["auto", "text", "semantic"],
                    "description": "Search method — auto (default) chooses based on query length",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="remember",
        description=(
            "Store new information persistently across sessions. "
            "Use for facts, decisions, preferences, or project state that should be recalled in future sessions. "
            "Choose category: 'personal' for user preferences/identity, 'project' for per-project state, 'session' for session summaries."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information to remember",
                },
                "category": {
                    "type": "string",
                    "enum": ["personal", "project", "session"],
                    "description": "Memory category",
                },
                "project": {
                    "type": "string",
                    "description": "Project name — required when category is 'project'",
                },
            },
            "required": ["content", "category"],
        },
    ),
    Tool(
        name="forget",
        description=(
            "Permanently delete a memory. "
            "Requires the memory_id — use list_memories first to discover IDs. "
            "Use when an improvement has been implemented, a fact is no longer true, or a memory is outdated."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of memory to delete (e.g. 'personal/a3f8c2d1') — get from list_memories or a prior remember call",
                },
            },
            "required": ["memory_id"],
        },
    ),
    Tool(
        name="save_session",
        description=(
            "Save a summary of this session for future recall. "
            "Call once at the end of a working session — not after each sub-task. "
            "Include what was completed, what to do next, and any key decisions made."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Session summary (use format: LAST: [...] | NEXT: [...] | DECISIONS: [...])",
                },
                "topics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Topics covered in this session",
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
        name="list_memories",
        description=(
            "List stored memories with their IDs, previews, and categories. "
            "Call this to discover memory IDs before using forget(), or to audit what is stored. "
            "Filter by category ('personal', 'project', 'session') or project name."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["personal", "project", "session"],
                    "description": "Filter by memory category",
                },
                "project": {
                    "type": "string",
                    "description": "Filter project memories by project name",
                },
            },
        },
    ),
    Tool(
        name="admin_sync",
        description=(
            "Operator tool: Re-sync markdown files to database. "
            "Not needed in normal sessions — only call if memories are out of sync after manual file edits. "
            "Use force=true to rebuild the entire index."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "force": {
                    "type": "boolean",
                    "description": "Force full resync (default: false)",
                },
            },
        },
    ),
    Tool(
        name="admin_check_integrity",
        description=(
            "Operator tool: Check database health and repair issues. "
            "Not needed in normal sessions — only call when experiencing search or sync failures. "
            "Checks FTS index sync, orphaned embeddings, and SQLite integrity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "auto_repair": {
                    "type": "boolean",
                    "description": "Automatically fix issues found (default: true)",
                },
            },
        },
    ),
]
```

Also update the routing in `call_tool` to use `admin_sync` and `admin_check_integrity` (already done in Task 6).

**Step 2: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass

**Step 3: Commit**

```bash
git add claude_innit/server.py
git commit -m "refactor: rewrite tool descriptions for LLM audience, rename sync/check_integrity to admin_*"
```

---

## Task 9: FTS Query Sanitization

**Files:**
- Modify: `claude_innit/db/database.py:107-119`
- Test: `tests/test_database.py`

**Step 1: Write the failing test**

```python
import pytest

@pytest.mark.parametrize("bad_query", [
    '"unclosed quote',
    "OR AND NOT",
    "term*wildcard",
    "hello OR",
])
def test_fts_search_handles_special_chars(tmp_path, bad_query):
    """fts_search does not raise on FTS5 operator characters."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(id="test/1", category="personal", content="normal content", metadata={})

    # Should not raise sqlite3.OperationalError
    result = db.fts_search(bad_query)
    assert isinstance(result, list)
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_database.py::test_fts_search_handles_special_chars -v
```

Expected: FAIL — `sqlite3.OperationalError: fts5: syntax error`

**Step 3: Add query sanitization to `fts_search()`**

Replace `fts_search()` in `database.py`:

```python
def fts_search(self, query: str, limit: int = 10) -> list[dict]:
    """Search memories using FTS5. Sanitizes query to prevent operator injection."""
    # Wrap in double-quotes to treat entire query as a phrase, escaping internal quotes
    safe_query = '"' + query.replace('"', '""') + '"'
    try:
        rows = self._conn.execute(
            """
            SELECT m.* FROM memories m
            JOIN memories_fts fts ON m.id = fts.id
            WHERE memories_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (safe_query, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        # Fall back to empty results on any FTS parse error
        return []
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_database.py::test_fts_search_handles_special_chars -v
```

Expected: PASS

**Step 5: Verify normal search still works**

```bash
pytest tests/ -v
```

Expected: all tests pass

**Step 6: Commit**

```bash
git add claude_innit/db/database.py tests/test_database.py
git commit -m "fix: sanitize FTS queries to prevent OperationalError on special chars"
```

---

## Task 10: Defer Startup Sync (Async Background Task)

**Files:**
- Modify: `claude_innit/server.py:208-235` (`create_server` and `main`)

This prevents `sync_all()` from blocking the MCP `initialize` handshake on slow startups.

**Step 1: No new test needed** — existing integration tests cover sync behavior. We're only changing when it runs, not whether it runs.

**Step 2: Move sync to background in `main()`**

Update `main()` in `server.py`:

```python
async def main():
    """Run the MCP server."""
    base_dir = Path(__file__).parent.parent
    db_path = base_dir / "data" / "innit.db"
    memories_dir = base_dir / "data" / "memories"

    innit_server = create_server(db_path, memories_dir)

    async with stdio_server() as (read_stream, write_stream):
        # Defer sync to background — don't block initialize handshake
        if memories_dir.exists():
            asyncio.create_task(_background_sync(innit_server.sync))

        await innit_server.server.run(
            read_stream,
            write_stream,
            innit_server.server.create_initialization_options(),
        )


async def _background_sync(sync: MarkdownSync) -> None:
    """Run sync in background after server is accepting connections."""
    try:
        await asyncio.to_thread(sync.sync_all)
    except Exception:
        pass  # Sync failure is non-fatal; server continues without it
```

Also remove the eager sync from `create_server()`:

```python
def create_server(db_path: Path, memories_dir: Path) -> InnitServer:
    """Create and configure the MCP server."""
    server = Server("claude-innit")
    db = MemoryDatabase(db_path)
    sync = MarkdownSync(db_path, memories_dir, generate_embeddings=False)
    # Sync is now deferred to main() background task
    innit_server = InnitServer(server, db, sync, memories_dir)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return innit_server.list_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        return await innit_server.call_tool(name, arguments)

    return innit_server
```

**Step 3: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass

**Step 4: Commit**

```bash
git add claude_innit/server.py
git commit -m "feat: defer startup sync to background task — server accepts connections immediately"
```

---

## Task 11: Add `get_context` Output Shape Tests

**Files:**
- Modify: `tests/test_tools.py`

**Step 1: Add type assertion tests**

Add to `TestGetContext` in `tests/test_tools.py`:

```python
def test_get_context_output_shape(self, tmp_path):
    """get_context returns dict with list values having required keys."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(id="personal/x1", category="personal", content="I am Taylor", metadata={})
    db.insert_memory(id="project/y1", category="project", content="myapp state", metadata={"name": "myapp"})
    db.insert_memory(id="session/z1", category="session", content="session summary", metadata={})

    result = get_context(db)

    # Top-level structure
    assert isinstance(result, dict)
    assert isinstance(result["personal"], list)
    assert isinstance(result["project"], list)
    assert isinstance(result["recent_sessions"], list)

    # Each personal entry has required keys
    for entry in result["personal"]:
        assert isinstance(entry, dict)
        assert "id" in entry
        assert "content" in entry
        assert "category" in entry
        assert "updated_at" in entry

def test_get_context_fallback_to_all_sessions(self, tmp_path):
    """When project filter returns no sessions, falls back to all sessions."""
    db = MemoryDatabase(tmp_path / "test.db")
    db.insert_memory(id="session/1", category="session", content="unrelated session", metadata={"project": "other"})

    result = get_context(db, project="myapp")

    # No myapp sessions exist, should fall back to all sessions
    assert isinstance(result["recent_sessions"], list)
    assert len(result["recent_sessions"]) == 1
    assert result["recent_sessions"][0]["id"] == "session/1"
```

**Step 2: Run tests**

```bash
pytest tests/test_tools.py::TestGetContext -v
```

Expected: PASS (these validate existing behavior)

**Step 3: Commit**

```bash
git add tests/test_tools.py
git commit -m "test: add output shape and fallback path assertions for get_context"
```

---

## Final: Verify Everything

**Step 1: Run the complete test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests PASS

**Step 2: Quick smoke test of the server**

```bash
python -m claude_innit.server &
sleep 2
kill %1
```

Expected: server starts and exits cleanly with no import errors

**Step 3: Final commit if any loose ends**

```bash
git log --oneline -12
```

Verify all 10 commits are present in order.
