# Development Reference

## Setup

```bash
pip install -e .                        # install with all dependencies
pip install -e ".[dev]"                 # + pytest, pytest-asyncio
```

**Note:** `sentence-transformers` and `torch` are in core dependencies but heavy. The semantic search tests in `tests/test_embeddings.py::TestEmbeddingStore` require the model to be downloaded. All other tests run without it.

---

## Running Tests

```bash
pytest tests/ -v                        # full suite
pytest tests/ -v -k "not TestEmbeddingStore"  # skip model-dependent tests
pytest tests/test_tools.py -v          # tools only
pytest tests/test_database.py -v       # database only
```

**Expected baseline:** 42 pass, 3 fail (`TestEmbeddingStore` — requires `sentence-transformers` model)

---

## Running the Server

```bash
python -m claude_innit.server           # stdio MCP server
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

---

## Adding a New MCP Tool

1. Create `claude_innit/tools/<name>.py` with the function
2. Export from `claude_innit/tools/__init__.py`
3. Add `Tool(name=..., description=..., inputSchema=...)` to `InnitServer._define_tools()` in `server.py`
4. Add dispatch case to `call_tool()` in `server.py` (inside the try block)
5. Write tests in `tests/test_tools.py`

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
| DB locked / SQLITE_BUSY | Stop concurrent server processes; WAL mode makes this rare |
| Semantic search slow first call | Expected — model lazy-loads. Subsequent calls are fast. |
| Memory comes back after forget() | `forget()` was called without `memories_dir`. File not deleted. |
| Memories out of sync | `admin_sync` to rebuild DB from markdown files |
| FTS returns no results | Check if query has special chars — they're phrase-wrapped, so "OR AND" matches literal text |
| 3 failing tests on `TestEmbeddingStore` | Normal if `sentence-transformers` model not downloaded |

---

## Change Protocol

1. `pytest tests/ -v` (baseline)
2. Smallest surface area possible
3. `pytest tests/ -v` (after)
4. If schema or markdown format changed: call `admin_sync` and validate

---

## Key File Locations

| File | Purpose |
|------|---------|
| `claude_innit/server.py` | MCP server, tool registration, call_tool dispatch |
| `claude_innit/db/database.py` | SQLite schema, FTS search, WAL config |
| `claude_innit/db/embeddings.py` | Embedding generation, semantic search |
| `claude_innit/sync/markdown_sync.py` | Markdown → DB sync engine |
| `claude_innit/tools/memory.py` | `remember()`, `forget()` |
| `claude_innit/tools/context.py` | `get_context()` |
| `claude_innit/tools/search.py` | `search()` routing |
| `claude_innit/tools/list.py` | `list_memories()` |
| `data/memories/` | Source of truth (markdown files) |
| `data/innit.db` | Derived index (gitignored) |
| `docs/decisions/` | ADRs — permanent decision log |
| `docs/archive/` | Executed plans — read-only reference |
| `ref/` | This reference documentation |
