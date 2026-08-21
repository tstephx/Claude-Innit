---
status: active
tags: [project/claude-innit, format/reference]
type: note
created: '2026-03-12'
modified: '2026-03-12'
---

# Development Reference

## Setup

```bash
pip install -e .                        # minimal install (no embeddings)
pip install -e ".[embeddings]"          # + sentence-transformers, numpy, torch
pip install -e ".[embeddings,dev]"      # + pytest, pytest-asyncio
```

---

## Running Tests

```bash
.venv/bin/python -m pytest tests/ -v                        # full suite (202 tests)
.venv/bin/python -m pytest tests/ -v -k "not TestEmbeddingStore"  # skip model-dependent tests
.venv/bin/python -m pytest tests/test_tools.py -v           # tools only
.venv/bin/python -m pytest tests/test_database.py -v        # database only
.venv/bin/python -m pytest tests/test_vault.py -v           # vault + hybrid merge
.venv/bin/python -m pytest tests/test_chunking.py -v        # heading-level chunking
.venv/bin/python -m pytest tests/test_embeddings.py -v      # embedding store + search_chunks
.venv/bin/python -m pytest tests/test_vault_smoke.py -v     # MCP dispatch integration tests
```

**Test files:**

| File | Tests | Scope |
|------|-------|-------|
| `test_tools.py` | Memory tools via MCP dispatch |
| `test_database.py` | MemoryDatabase, chunk DB methods, FTS |
| `test_vault.py` | VaultIndexer, _detect_module, vault_search, hybrid_merge |
| `test_chunking.py` | utils_chunking: heading splits, paragraph fallback, merging |
| `test_embeddings.py` | EmbeddingStore: generate, store, semantic_search, search_chunks |
| `test_federation.py` | Federated search, RRF fusion |
| `test_server.py` | Server initialization, tool listing |
| `test_sync.py` | Markdown → DB sync engine |
| `test_vault_smoke.py` | All 6 vault tools via MCP dispatch (integration) |

**Expected baseline:** 202 tests pass.

---

## Running the Server

```bash
.venv/bin/python -m claude_innit.server           # stdio MCP server
```

Register in `~/.claude/mcp_servers.json`:

```json
{
  "claude-innit": {
    "command": "python",
    "args": ["-m", "claude_innit.server"],
    "cwd": "/path/to/Claude-Innit"
  }
}
```

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VAULT_ROOT` | `~/Dev/Obsidian-Second-Brain` | Obsidian vault root path |
| `EXTRA_INDEX_PATHS` | `~/Dev/_Lab:~/Dev/_Projects` | Colon-separated extra dirs to index |
| `BOOK_LIBRARY_DB` | (auto-detected) | Path to book-library SQLite DB |

---

## Adding a New MCP Tool

1. Create `claude_innit/tools/<name>.py` with the function
2. Export from `claude_innit/tools/__init__.py`
3. Add tool definition to `InnitServer._define_tools()` in `server.py`
4. Add dispatch case to `call_tool()` in `server.py` (inside the try block)
5. Write tests in `tests/test_tools.py` (unit) and optionally `tests/test_vault_smoke.py` (integration)
6. Update `ref/tools.md`

**Description format:** "Trigger condition. Precondition. Consequence." — LLM-audience, not developer-audience.

---

## Modifying Database Schema

1. Update `MemoryDatabase._create_tables()` in `db/database.py`
2. Update sync engine if markdown format changes (`sync/markdown_sync.py`)
3. Delete `data/innit.db` and run `admin_sync` to rebuild
4. Update `ref/data-model.md`

---

## Common Debugging

| Problem | Fix |
|---------|-----|
| DB locked / SQLITE_BUSY | Stop concurrent server processes; WAL mode makes this rare. Check for zombie pytest processes (`ps aux \| grep pytest`). |
| Semantic search slow first time | Should be rare — `warm()` pre-loads at startup. If still slow, check model cache. |
| Memory comes back after forget() | `forget()` was called without `memories_dir`. File not deleted. Use MCP tool. |
| Memories out of sync | `admin_sync` to rebuild DB from markdown files |
| FTS returns no results | Queries are sanitized (operators stripped, words quoted). Check if content is indexed. |
| search_chunks returns empty | Check `load_matrix()` ran — `_matrix_loaded` must be True. Check chunk embeddings exist. |
| Zombie pytest from background agents | `ps aux \| grep pytest` then `kill <pid>`. Common with dispatched agents. |

---

## Change Protocol

See root `CLAUDE.md` § Change Protocol — applies repo-wide, not repeated here.

---

## Key File Locations

| File | Purpose |
|------|---------|
| `claude_innit/server.py` | MCP server, tool registration (15 tools), call_tool dispatch |
| `claude_innit/db/database.py` | SQLite schema (9 tables), FTS5, WAL config, chunk methods |
| `claude_innit/db/embeddings.py` | EmbeddingStore: generate, search_chunks, matrix ops, batch embedding |
| `claude_innit/utils.py` | parse_frontmatter, sanitize_fts_query |
| `claude_innit/utils_chunking.py` | Heading-level text chunking (chunk_by_headings) |
| `claude_innit/sync/markdown_sync.py` | Markdown → DB sync engine |
| `claude_innit/tools/memory.py` | `remember()`, `forget()` |
| `claude_innit/tools/context.py` | `get_context()` |
| `claude_innit/tools/search.py` | `search()` routing (FTS5/semantic) |
| `claude_innit/tools/vault.py` | VaultIndexer, vault_search, vault_semantic_search, vault_related, vault_stats, vault_rechunk |
| `claude_innit/tools/tag.py` | `vault_tag` — two-phase preview/apply frontmatter tagger |
| `claude_innit/tools/federation.py` | Federated search, RRF fusion across sources |
| `claude_innit/tools/list.py` | `list_memories()` |
| `claude_innit/tools/session.py` | `save_session()` |
| `claude_innit/tools/maintenance.py` | `admin_sync`, `admin_check_integrity` |
| `data/memories/` | Source of truth (markdown files) |
| `data/innit.db` | Derived index (gitignored) |
| `docs/decisions/` | ADRs — permanent decision log |
| `docs/archive/` | Executed plans — read-only reference |
| `ref/` | This reference documentation |
