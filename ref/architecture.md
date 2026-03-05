# Architecture Reference

## System Overview

```
MCP Client (Claude)
       │ stdio / JSON-RPC
       ▼
InnitServer (server.py)
  ├── list_tools()
  └── call_tool() ← error boundary wraps all dispatches
       │
       ├── tools/context.py     get_context
       ├── tools/memory.py      remember, forget
       ├── tools/search.py      search
       ├── tools/session.py     save_session
       ├── tools/list.py        list_memories
       └── tools/maintenance.py admin_check_integrity
            │
            ├── db/database.py   MemoryDatabase (SQLite/FTS5)
            └── db/embeddings.py EmbeddingStore (all-MiniLM-L6-v2)
                     │
                     └── data/innit.db

sync/markdown_sync.py ← background task on startup
       │
       └── data/memories/      ← SOURCE OF TRUTH
```

---

## Key Design Decisions

### Markdown-first storage

`data/memories/` is the source of truth. `data/innit.db` is a derived index that can be rebuilt from markdown at any time via `admin_sync`. This means:

- Human-readable memory files can be edited directly
- DB corruption is recoverable: delete DB, run `admin_sync`, everything is back
- `forget()` must delete the markdown file — DB-only deletion is re-inserted on next sync

### Dual search routing

`search(method="auto")` routes based on query length:
- **1-3 words → FTS5** (fast, exact phrase match)
- **4+ words → semantic** (conceptual, all-MiniLM-L6-v2)

FTS5 queries are phrase-wrapped (`"query"`) to prevent operator injection. Semantic results are filtered at `min_similarity=0.35`.

### Lazy embedding model

`EmbeddingStore._model` loads on first `semantic_search` or `remember` call. The server singleton (`InnitServer.embedding_store`) is shared across all tool calls to avoid reloading.

### Async startup sync

`MarkdownSync.sync_all()` runs as an `asyncio.create_task` inside the `stdio_server` context — after the server is accepting connections. This prevents blocking the MCP `initialize` handshake.

### Error boundary

`call_tool` wraps all dispatch in `try/except Exception`, returning `{"error": "ExceptionType", "message": "...", "tool": "..."}` instead of propagating. No single tool failure can crash the server.

---

## Data Flow: `remember()`

```
call_tool("remember", args)
  → remember(db, content, category, project, memories_dir, embedding_store)
      1. Generate memory_id: "{category}/{uuid8}"  e.g. "personal/a3f8c2d1"
      2. db.insert_memory(id, category, content, metadata)
         → INSERT OR REPLACE INTO memories
         → AFTER INSERT trigger: INSERT INTO memories_fts
      3. Write markdown: memories_dir/personal/a3f8c2d1.md
      4. embedding_store.store_embedding(memory_id, content)
         → model.encode(content)
         → INSERT OR REPLACE INTO embeddings
```

## Data Flow: `forget()`

```
call_tool("forget", {"memory_id": "personal/a3f8c2d1"})
  → forget(db, memory_id, memories_dir)
      1. Delete markdown: memories_dir/personal/a3f8c2d1.md (FIRST)
      2. db.delete_memory(memory_id)
         → DELETE FROM embeddings WHERE memory_id = ?
         → DELETE FROM memories WHERE id = ?
         → AFTER DELETE trigger: DELETE FROM memories_fts
```

File deletion happens first: if DB deletion fails, the markdown is already gone so sync cannot re-insert.

---

## Two-Connection Architecture (Known Limitation)

`MarkdownSync` opens its own `MemoryDatabase` instance, separate from the server's connection. WAL mode (enabled since 2026-03-04) allows concurrent reads alongside a single writer, which eliminates `SQLITE_BUSY` in practice. The two connections are never in the same transaction context.

---

## Memory ID Format

IDs have two formats depending on how the memory was created:

| Source | Format | Example |
|--------|--------|---------|
| `remember()` tool | `{category}/{uuid8}` | `personal/a3f8c2d1` |
| `admin_sync` (from file) | relative path from memories_dir | `personal/identity.md` |

The sync-derived IDs include the `.md` extension. Tool-created IDs do not.
