# CLAUDE.md — Claude-Innit Memory System
<!-- project-name: claude-innit -->

**DO NOT scan directories on startup.** This is a focused MCP server project.

## Operational Rules
- Markdown in `data/memories/` is the source of truth. Do not hand-edit the SQLite DB except for debugging.
- Any change to memory markdown format, sync logic, or search routing MUST include test updates.
- Do not paste user memory content into chat; summarize and reference file paths.

## Project Purpose
MCP server giving Claude persistent memory across sessions. Three categories (personal, project, session), dual search (FTS5 + semantic), markdown-first storage.

## Data & Git Hygiene
- `data/innit.db` — gitignored (`*.db`)
- `data/memories/` — contains personal context. **Verify this is gitignored before committing.** Add `data/memories/sessions/` to `.gitignore` if not already excluded.
- For deployed use: store DB + memories outside repo and point server at that location.

## MCP Tools (14)

→ Full tool reference: [`ref/tools.md`](ref/tools.md)

### Memory Tools
| Tool | Purpose |
|------|---------|
| `get_context` | Load memories for session start |
| `search` | Find memories (auto-routes FTS5/semantic) |
| `remember` | Store new memory |
| `forget` | Remove a memory (durable — deletes markdown file) |
| `list_memories` | List memory IDs/previews (use before forget) |
| `save_session` | Save session summary |
| `admin_sync` | Re-sync markdown → database (operator only) |
| `admin_check_integrity` | Verify and repair database (operator only) |

### Vault Tools (OBF)
| Tool | Purpose |
|------|---------|
| `vault_index` | Index vault .md files, generate chunk embeddings, reload matrix |
| `vault_search` | Hybrid FTS+semantic search (auto/text/semantic, scope: vault/configs/all) |
| `vault_related` | Find notes similar to a given note (embeddings or filename fallback) |
| `vault_stats` | Vault health metrics (by module, status, inbox count, stale count, embedding health) |
| `vault_rechunk` | Force re-chunk all vault files and regenerate embeddings |
| `federated_search` | Two-level RRF fusion across vault (hybrid), book-library, and session memory |

## Key Design Decisions

→ Full architecture: [`ref/architecture.md`](ref/architecture.md) | Data model: [`ref/data-model.md`](ref/data-model.md)

- **Markdown-first**: `data/memories/` is truth; DB is index
- **Search routing**: 1-3 words → FTS5 (fast), 4+ → semantic (conceptual)
- **Heading-level chunking**: vault files split at `##`/`###` headings with paragraph fallback for large sections (`utils_chunking.py`). Chunks stored in `vault_chunks` table with FK to `vault_files`
- **Hybrid search**: `vault_search(method="auto")` runs both FTS5 and semantic, merges via mini-RRF (k=20, FTS=0.4, semantic=0.6). `method="text"` or `method="semantic"` for single-mode
- **Pre-computed matrix**: `EmbeddingStore.load_matrix()` builds normalized numpy embedding matrix at startup for fast vectorized cosine similarity via `np.dot`. Recency weights pre-applied as numpy array
- **Query cache**: `@functools.lru_cache(maxsize=64)` on query embeddings — repeated queries skip model inference
- **Two-level RRF**: inner hybrid k=20 for vault search, outer federated k=60 across sources (vault, book-library, sessions)
- **Eager embedding**: `EmbeddingStore.warm()` pre-loads model at server startup to avoid MCP timeout on first semantic query
- **Model**: all-MiniLM-L6-v2 (384-dim), `min_similarity=0.35` threshold; numpy/torch are optional deps (`pip install .[embeddings]`)
- **WAL mode**: enabled on all connections — concurrent reads + single writer, eliminates SQLITE_BUSY
- **Error boundary**: `call_tool` wraps all dispatches — no tool failure can crash the MCP connection
- **Async startup sync**: `sync_all()` runs in background after server accepts connections
- **Thread safety**: `vault_index` creates a dedicated DB connection in `asyncio.to_thread()` — never shares the server's connection across threads. `load_matrix()` also wrapped in `asyncio.to_thread()`
- **Vault root**: configurable via `VAULT_ROOT` env var, defaults to `~/Dev/Obsidian-Second-Brain`
- **Extra index paths**: `EXTRA_INDEX_PATHS` env var (colon-separated), defaults to `~/Dev/_Lab:~/Dev/_Projects` — indexed alongside vault on `vault_index`
- **Fail-loud semantic**: `vault_search(method="semantic")` raises `ValueError` when no embedding store, instead of returning empty results
- **Orphan cleanup**: `vault_index` auto-cleans orphaned vault/chunk embeddings after each run
- **Encapsulated search**: `EmbeddingStore.search_chunks()` encapsulates all matrix access — `vault_semantic_search()` is a thin wrapper with scope filter only
- **FTS sanitization**: `sanitize_fts_query()` in `utils.py` — strips operators, quotes each word. Used by all FTS call sites (memory, vault, federation)
- **Module detection**: `_detect_module()` in `tools/vault.py` — lowercased top-level folder name, excludes framework dirs (Daily, Inbox, Archive, Claude-Memory)
- **Metadata key**: memories use `metadata["project"]` — queries use `json_extract(metadata, '$.project')`
- **Schema**: 9 tables — `vault_chunks` + `vault_chunk_embeddings` with FK constraints. Legacy `vault_embeddings` deprecated (retained for backward compat)

## Commands
```bash
.venv/bin/python -m pytest tests/ -v    # all tests (268 total)
.venv/bin/python -m claude_innit.server # run MCP server
pip install -e ".[embeddings,dev]"      # dev install with all deps
pip install -e .                        # minimal install (no embeddings)
```

## Common Footguns

→ Full debugging guide: [`ref/development.md`](ref/development.md)

| Problem | Fix |
|---------|-----|
| DB locked / SQLITE_BUSY | Stop concurrent runs; check for zombie pytest (`ps aux \| grep pytest`). WAL mode makes this rare. |
| Semantic search slow first time | Should be rare — `warm()` pre-loads at startup. If still slow, check model cache |
| Memory comes back after forget() | File wasn't deleted — use `forget()` via MCP (passes memories_dir) |
| Memories out of sync | `admin_sync` then `admin_check_integrity` |
| search_chunks returns empty | Verify `load_matrix()` ran and chunk embeddings exist in DB |

## Change Protocol
1. `pytest tests/ -v` (before)
2. Change smallest surface area possible
3. `pytest tests/ -v` (after)
4. If schema or markdown format changed: call `admin_sync` + validate

## Common Tasks

### Add a new MCP tool
1. Create in `claude_innit/tools/`, register in `__init__.py`
2. Add `Tool(...)` to `_define_tools()` and dispatch case to `call_tool()` in `server.py`
3. Write tests in `tests/test_tools.py`
4. Description format: "Trigger condition. Precondition. Consequence."

### Modify database schema
1. Update `db/database.py`, update sync engine if format changes
2. Delete `data/innit.db` and call `admin_sync` to rebuild

### Debug memory issues
1. Check `data/memories/` markdown files
2. `admin_check_integrity`
3. Inspect `data/innit.db` with sqlite3 if needed

## Key Files
| File | Purpose |
|------|---------|
| `claude_innit/server.py` | MCP server, 14 tools, call_tool dispatch with error boundary |
| `claude_innit/db/database.py` | SQLite schema (9 tables), FTS5, WAL, chunk methods, integrity check |
| `claude_innit/db/embeddings.py` | EmbeddingStore: generate, search_chunks, matrix ops, batch embedding |
| `claude_innit/tools/vault.py` | VaultIndexer, vault_search (hybrid), vault_related, vault_stats |
| `claude_innit/tools/federation.py` | Two-level RRF fusion across vault, books, sessions, portfolio |
| `claude_innit/tools/search.py` | Memory search routing (FTS5/semantic) |
| `claude_innit/tools/memory.py` | remember/forget with markdown file sync |
| `claude_innit/utils.py` | parse_frontmatter, sanitize_fts_query |
| `claude_innit/utils_chunking.py` | Heading-level text chunking (chunk_by_headings) |
| `claude_innit/sync/markdown_sync.py` | Markdown → DB sync engine |

## Documentation
- Reference docs: `ref/` — tools, architecture, data model, development guide
- ADRs: `docs/decisions/` — permanent decision log, numbered ADR-NNN
- Active work: `docs/active/` — open items only; files >14 days old are stale, flag them
- Archive: `docs/archive/` — executed plans, read-only reference
- Plans: `docs/plans/` — staging area; move to `docs/archive/` when done

## Git
- Branch: `main` — Style: `feat:`, `fix:`, `test:`, `refactor:`
- TDD: tests before implementation

---

*Last updated: 2026-03-12*
