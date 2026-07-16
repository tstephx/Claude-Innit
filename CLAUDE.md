---
status: active
tags: [project/claude-innit, format/readme]
type: note
created: '2026-03-16'
modified: '2026-03-16'
related: ["[[Claude-Config/mcp-servers/claude-innit]]"]
---

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

## MCP Tools (15)

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
| `vault_search` | Hybrid FTS+semantic search (auto/text/semantic, scope: vault/configs/all, optional status filter) |
| `vault_related` | Find notes similar to a given note (embeddings or filename fallback) |
| `vault_stats` | Vault health metrics (by module, status, inbox count, stale count, embedding health) |
| `vault_rechunk` | Force re-chunk all vault files and regenerate embeddings |
| `vault_tag` | Two-phase frontmatter tagger: preview untagged files, then apply with folder/file overrides |
| `federated_search` | Two-level RRF fusion across vault (hybrid), book-library, and session memory |

## Key Design Decisions

→ Full architecture: [`ref/architecture.md`](ref/architecture.md) | Data model: [`ref/data-model.md`](ref/data-model.md)

- **Markdown-first**: `data/memories/` is truth; DB is index
- **Search routing**: 1-3 words → FTS5 (fast), 4+ → semantic (conceptual)
- **Heading-level chunking**: vault files split at `##`/`###` headings with paragraph fallback for large sections (`utils_chunking.py`). Chunks stored in `vault_chunks` table with FK to `vault_files`
- **Hybrid search**: `vault_search(method="auto")` runs both FTS5 and semantic, merges via mini-RRF (k=20, FTS=0.4, semantic=0.6). `method="text"` or `method="semantic"` for single-mode
- **Pre-computed matrix**: `EmbeddingStore.load_matrix()` builds normalized numpy embedding matrix at startup for fast vectorized cosine similarity via `np.dot`. Recency weights pre-applied as numpy array
- **Query cache**: `@functools.lru_cache(maxsize=64)` on query embeddings — repeated queries skip model inference
- **Content-hash dedup**: `_dedup_results()` in vault.py — removes duplicate results where the same file is indexed at multiple paths (vault copy + source repo + lab). Prefers vault > _Projects > _Lab > backups. Applied to both `vault_search` and federated `_search_vault`
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
- **Module detection**: `_detect_module()` in `tools/vault.py` — vault files use lowercased top-level folder name (no prefix), extra-path files use path-prefixed names (`lab/project-name`, `projects/project-name`). Framework dirs excluded: vault (`daily`, `inbox`, `archive`, `claude-memory`), extra-paths (`ref`, `scripts`, `docs`, `shared`)
- **Exclusion patterns**: VaultIndexer excludes build artifacts (`node_modules`, `site-packages`, `dist`, `build`, `.hypothesis`, `htmlcov`, `.mypy_cache`, `.ruff_cache`, `.tox`, `.eggs`, `.egg-info`, etc.) with `/` prefix to prevent substring false positives
- **Status filter**: `vault_search(status="active")` filters at SQL level via `json_extract(frontmatter, '$.status')` — applies to both FTS and semantic legs
- **Vault tagger**: `vault_tag` tool uses two-phase preview/apply flow, `FOLDER_TYPE_MAP` for type inference, `st_birthtime` for created dates, canonical field ordering in YAML output
- **Metadata key**: memories use `metadata["project"]` — queries use `json_extract(metadata, '$.project')`
- **Schema**: 9 tables — `vault_chunks` + `vault_chunk_embeddings` with FK constraints. Legacy `vault_embeddings` deprecated (retained for backward compat)

## Commands
```bash
.venv/bin/python -m pytest tests/ -v    # all tests (307 total)
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
| `claude_innit/server.py` | MCP server, 15 tools, call_tool dispatch with error boundary |
| `claude_innit/db/database.py` | SQLite schema (9 tables), FTS5, WAL, chunk methods, integrity check |
| `claude_innit/db/embeddings.py` | EmbeddingStore: generate, search_chunks, matrix ops, batch embedding |
| `claude_innit/tools/vault.py` | VaultIndexer, vault_search (hybrid + status filter), vault_related, vault_stats |
| `claude_innit/tools/tag.py` | vault_tag: two-phase frontmatter tagger with folder/file overrides |
| `claude_innit/tools/federation.py` | Two-level RRF fusion across vault, books, sessions, portfolio |
| `claude_innit/tools/search.py` | Memory search routing (FTS5/semantic) |
| `claude_innit/tools/memory.py` | remember/forget with markdown file sync |
| `claude_innit/utils.py` | parse_frontmatter, sanitize_fts_query |
| `claude_innit/utils_chunking.py` | Heading-level text chunking (chunk_by_headings) |
| `claude_innit/sync/markdown_sync.py` | Markdown → DB sync engine |

## Vault Materialization Scripts
One-way sync from `data/memories/` → Obsidian vault. Both are idempotent and automated.

| Script | Trigger | Output |
|--------|---------|--------|
| `scripts/materialize_sessions.py` | `save_session` hook + cron 2:00AM | `Sessions/YYYY-MM-DD-{project}.md` — dedup via `memory_id:` frontmatter |
| `scripts/materialize_memories.py` | `remember` hook + cron 2:15AM | `Claude-Memory/personal-innit.md`, `Claude-Memory/{project}-innit.md` — dedup via `innit_fragment_count` |

Both scripts maintain `PROJECT_CARD_MAP` mapping project slugs → vault `_PROJECT_CARD` paths. **Update this map when new projects get a `_PROJECT_CARD`.** Env var `EXCLUDE_INDEX_PATTERNS` (colon-separated) controls which paths `vault_index` skips; defaults to `/rss-news/` and `/vault-rss-feeds/`.

## Documentation
- Reference docs: `ref/` — tools, architecture, data model, development guide
- ADRs: `docs/decisions/` — permanent decision log, numbered ADR-NNN
- Active work: `docs/active/` — open items only; files >14 days old are stale, flag them
- Archive: `docs/archive/` — executed plans, read-only reference
- Plans: `docs/plans/` — legacy staging area, gitignored; do not add new plans here

## Documentation System

`docs/decisions/` is the permanent, append-only decision log — each ADR (`ADR-NNN-slug.md`) records one architectural choice, why it was made, and what was rejected; when a decision changes, a new ADR supersedes the old one rather than editing it in place. `docs/active/` tracks genuinely open work only — in-progress code, open GitHub issues, unexecuted plans — and is subject to the >14-day staleness check (flag anything older for resolution or archival). `docs/archive/` holds executed, read-only implementation plans kept for historical reference. `docs/plans/` is legacy: it predates the decisions/active/archive split, is now gitignored, and should not receive new content — plans that get executed today move straight to `docs/archive/` when done.

## Git
- Branch: `main` — Style: `feat:`, `fix:`, `test:`, `refactor:`
- TDD: tests before implementation

---

*Last updated: 2026-07-16*
