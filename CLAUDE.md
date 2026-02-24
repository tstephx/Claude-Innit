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

## MCP Tools (7)

| Tool | Purpose |
|------|---------|
| `get_context` | Load memories for session start |
| `search` | Find memories (auto-routes FTS5/semantic) |
| `remember` | Store new memory |
| `forget` | Remove a memory |
| `save_session` | Save session summary |
| `sync` | Re-sync markdown → database |
| `check_integrity` | Verify and repair database |

## Key Design Decisions
- **Markdown-first**: `data/memories/` is truth; DB is index
- **Search routing**: 1-3 words → FTS5 (fast), 4+ → semantic (conceptual)
- **Lazy embedding**: model loads only on first semantic search
- **Model**: all-MiniLM-L6-v2 (384-dim)

## Commands
```bash
pytest tests/ -v                    # all tests
python -m claude_innit.server       # run MCP server
pip install -e .                    # dev install
```

## Common Footguns

| Problem | Fix |
|---------|-----|
| DB locked / SQLITE_BUSY | Stop concurrent runs; retry |
| Semantic search slow first time | Expected — embedding model lazy-loads. Rerun query. |
| Short query misses semantic matches | Rephrase as longer natural-language query |
| Long query misses exact match | Use shorter keyword query |
| Memories out of sync | `sync(force=True)` then `check_integrity(auto_repair=True)` |

## Change Protocol
1. `pytest tests/ -v` (before)
2. Change smallest surface area possible
3. `pytest tests/ -v` (after)
4. If schema or markdown format changed: `sync(force=True)` + validate

## Common Tasks

### Add a new MCP tool
1. Create in `claude_innit/tools/`, register in `__init__.py`
2. Add to `server.py`, write tests in `tests/test_tools.py`

### Modify database schema
1. Update `db/database.py`, update sync engine if format changes
2. `sync(force=True)` to rebuild

### Debug memory issues
1. Check `data/memories/` markdown files
2. `check_integrity(auto_repair=True)`
3. Inspect `data/innit.db` with sqlite3 if needed

## Quick Read Order (Debugging)
1. `claude_innit/server.py`
2. `claude_innit/tools/search.py` + `claude_innit/db/database.py`
3. `claude_innit/sync/sync_engine.py`
4. `tests/test_sync.py`, `tests/test_tools.py`

## Git
- Branch: `main` — Style: `feat:`, `fix:`, `test:`, `refactor:`
- TDD: tests before implementation

---

*Last updated: 2026-02-18*
