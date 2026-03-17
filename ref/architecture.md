---
status: active
tags: [project/claude-innit, format/reference]
type: note
created: '2026-03-12'
modified: '2026-03-12'
---

# Architecture Reference

## System Overview

```
MCP Client (Claude)
       │ stdio / JSON-RPC
       ▼
InnitServer (server.py)
  ├── list_tools()       → 14 tools
  └── call_tool()        ← error boundary wraps all dispatches
       │
       ├── tools/context.py     get_context
       ├── tools/memory.py      remember, forget
       ├── tools/search.py      search (FTS5/semantic routing)
       ├── tools/session.py     save_session
       ├── tools/list.py        list_memories
       ├── tools/maintenance.py admin_sync, admin_check_integrity
       ├── tools/vault.py       vault_index, vault_search, vault_related, vault_stats
       └── tools/federation.py  federated_search
            │
            ├── db/database.py       MemoryDatabase (SQLite/FTS5, WAL mode)
            ├── db/embeddings.py     EmbeddingStore (all-MiniLM-L6-v2, 384-dim)
            │        │
            │        ├── search_chunks()   ← vectorized matrix search
            │        └── load_matrix()     ← pre-computed at startup
            │
            ├── utils.py             parse_frontmatter, sanitize_fts_query
            └── utils_chunking.py    heading-level text chunking
                     │
                     └── data/innit.db

sync/markdown_sync.py ← background task on startup
       │
       └── data/memories/      ← SOURCE OF TRUTH (memory markdown)
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
- **1-3 words → FTS5** (fast, exact match)
- **4+ words → semantic** (conceptual, all-MiniLM-L6-v2)

FTS queries are sanitized via `sanitize_fts_query()` in `utils.py` — strips FTS5 operators, quotes each word. Semantic results are filtered at `min_similarity=0.35`.

### Heading-level chunking

Vault files are split into chunks at `##`/`###` headings (`utils_chunking.py`):
1. Split at h2/h3 headings (h1 is typically the title, not a section boundary)
2. Oversized sections fall back to paragraph splitting
3. Tiny sections (< `min_chunk_chars`) merge into their neighbor
4. Short files (< `max_chunk_chars`) stay as a single chunk

Chunks are stored in `vault_chunks` table with FK to `vault_files`. Each chunk gets its own embedding in `vault_chunk_embeddings`.

### Hybrid vault search

`vault_search(method="auto")` runs both FTS5 and semantic, merges via mini-RRF:
- FTS weight: 0.4, semantic weight: 0.6, k=20
- Deduplicates by file — best chunk per file
- Output field: `rrf_score` (authoritative ranking), `match_type` (fts/semantic/hybrid)
- Raw `score` and `similarity` are stripped to avoid ambiguity

### Pre-computed embedding matrix

`EmbeddingStore.load_matrix()` builds a normalized numpy matrix at startup:
- All chunk embeddings loaded into `(N, 384)` float32 matrix
- Pre-normalized → search is `np.dot(matrix, query_vec)`
- Recency weights pre-computed as numpy array (per-chunk, based on file modified_at)
- `@functools.lru_cache(maxsize=64)` on query embeddings — repeated queries skip model inference
- `search_chunks()` encapsulates all matrix access (matrix, chunk_meta, file_meta, recency_weights)

### Two-level RRF federation

`federated_search()` in `tools/federation.py`:
- **Inner**: vault hybrid search (FTS + semantic, k=20)
- **Outer**: RRF across sources — vault, books, sessions, portfolio (k=60, equal weights)
- Sources are searched independently, then merged

### Eager embedding model

`EmbeddingStore.warm()` pre-loads the sentence-transformers model at server startup to avoid MCP timeout on first semantic query.

### Async startup sync

`MarkdownSync.sync_all()` runs as an `asyncio.create_task` inside the `stdio_server` context — after the server is accepting connections. This prevents blocking the MCP `initialize` handshake.

### Thread safety

`vault_index` creates a dedicated DB connection in `asyncio.to_thread()` — never shares the server's connection across threads. `load_matrix()` also wrapped in `asyncio.to_thread()`.

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

## Data Flow: `vault_search(method="auto")`

```
call_tool("vault_search", {"query": "...", "method": "auto"})
  → vault_search(db, query, embedding_store=store, method="auto")
      1. FTS path: sanitize_fts_query(query) → db.vault_fts_search()
         → assigns descending scores: 1.0, 0.98, 0.96...
      2. Semantic path: embedding_store.search_chunks(query, file_filter=...)
         → query_embedding(query) — cached via LRU
         → np.dot(matrix, query_vec) — vectorized cosine similarity
         → multiply by recency_weights
         → deduplicate by file (keep best chunk)
      3. _hybrid_merge(fts_results, semantic_results, limit)
         → mini-RRF: FTS=0.4, semantic=0.6, k=20
         → strip raw score/similarity, output rrf_score + match_type
```

---

## Two-Connection Architecture

`MarkdownSync` opens its own `MemoryDatabase` instance, separate from the server's connection. WAL mode (enabled since 2026-03-04) allows concurrent reads alongside a single writer, which eliminates `SQLITE_BUSY` in practice. The two connections are never in the same transaction context.

---

## Memory ID Format

IDs have two formats depending on how the memory was created:

| Source | Format | Example |
|--------|--------|---------|
| `remember()` tool | `{category}/{uuid8}` | `personal/a3f8c2d1` |
| `admin_sync` (from file) | relative path from memories_dir | `personal/identity.md` |

The sync-derived IDs include the `.md` extension. Tool-created IDs do not.
