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

## MCP Tools (8)

→ Full tool reference: [`ref/tools.md`](ref/tools.md)

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

## Key Design Decisions

→ Full architecture: [`ref/architecture.md`](ref/architecture.md) | Data model: [`ref/data-model.md`](ref/data-model.md)

- **Markdown-first**: `data/memories/` is truth; DB is index
- **Search routing**: 1-3 words → FTS5 (fast), 4+ → semantic (conceptual)
- **Lazy embedding**: model loads only on first semantic search; server singleton shared across calls
- **Model**: all-MiniLM-L6-v2 (384-dim), `min_similarity=0.35` threshold
- **WAL mode**: enabled on all connections — concurrent reads + single writer, eliminates SQLITE_BUSY
- **Error boundary**: `call_tool` wraps all dispatches — no tool failure can crash the MCP connection
- **Async startup sync**: `sync_all()` runs in background after server accepts connections

## Commands
```bash
pytest tests/ -v                    # all tests
python -m claude_innit.server       # run MCP server
pip install -e .                    # dev install
```

## Common Footguns

→ Full debugging guide: [`ref/development.md`](ref/development.md)

| Problem | Fix |
|---------|-----|
| DB locked / SQLITE_BUSY | Stop concurrent runs; WAL mode makes this rare now |
| Semantic search slow first time | Expected — embedding model lazy-loads |
| Memory comes back after forget() | File wasn't deleted — use `forget()` via MCP (passes memories_dir) |
| Memories out of sync | `admin_sync` then `admin_check_integrity` |

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

## Quick Read Order (Debugging)
1. `claude_innit/server.py`
2. `claude_innit/tools/search.py` + `claude_innit/db/database.py`
3. `claude_innit/sync/markdown_sync.py`
4. `tests/test_sync.py`, `tests/test_tools.py`

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

*Last updated: 2026-03-04*
