---
status: active
tags: []
type: note
created: '2026-03-11'
modified: '2026-03-11'
---

<!-- project: claude-innit -->

# Vault Search Quality: Chunking, Recency Boost, Hybrid Search

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Dramatically improve Obsidian vault semantic search by chunking large files into heading-level segments, boosting recent notes in results, and combining FTS + vector results within vault_search.

**Architecture:** Three independent improvements to the vault search pipeline:
1. **Chunking** — Split vault files at `##`/`###` headings before embedding. Falls back to paragraph splitting for notes without headings. New `vault_chunks` table stores segments; new `vault_chunk_embeddings` table stores per-chunk embeddings. Search returns deduplicated file-level results with best-chunk similarity.
2. **Recency boost** — Pre-computed recency weights stored alongside the embedding matrix. Applied uniformly to all modules (max +10% for today's edits). Vectorized numpy multiply, not a Python loop.
3. **Hybrid vault search** — When `method="auto"`, run BOTH FTS and semantic, merge with mini-RRF (fixed weights: FTS=0.4, semantic=0.6). Replaces the old word-count heuristic. Also upgrade `federated_search` vault leg to hybrid.

**Performance optimizations (from interview + data engineering review):**
- Pre-compute embedding matrix at server startup (~45MB for 30K chunks)
- Pre-compute recency weight array at matrix load time (vectorized, not per-query)
- LRU cache for query embeddings (skip inference for repeated queries)
- Batch SQLite commits every 100 chunks during generation
- Auto-rechunk on content_hash change (no stale chunks)
- Store chunking parameters in metadata table (auto-trigger rechunk on param change)
- Index on `vault_chunk_embeddings(file_id)` for cascade delete performance
- FAISS threshold at 50K chunks (warning only, not built now)

**Tech Stack:** Python 3.12, SQLite (WAL mode), sentence-transformers (all-MiniLM-L6-v2), numpy

**Current state (problems being fixed):**
- `batch_store_vault_embeddings()` uses `content[:500]` — only first 500 chars embedded. 75% of files are >2000 chars; median is 4,275 chars. Most file content is invisible to semantic search.
- `vault_search()` picks FTS OR semantic based on word count (<=3 → FTS, >=4 → semantic). Never combines them.
- `federated_search()` vault leg is FTS-only — never uses semantic search.
- `modified_at` exists on `vault_files` but is never used in scoring. A daily-edited note ranks the same as a 2-year-old orphan.
- `vault_semantic_search()` does a brute-force linear scan per query (row-by-row cosine). Should pre-compute matrix.
- `upsert_vault_file()` uses `INSERT OR REPLACE` which changes `file_id` on update — new chunk tables must handle this.

**Key files:**
- `claude_innit/db/database.py` — schema, `vault_fts_search()`, `vault_embedding_stats()`, `integrity_check()`
- `claude_innit/db/embeddings.py` — `EmbeddingStore`, `batch_store_vault_embeddings()`, `store_vault_embedding()`
- `claude_innit/tools/vault.py` — `vault_search()`, `vault_semantic_search()`, `vault_related()`
- `claude_innit/tools/federation.py` — `_reciprocal_rank_fusion()`, `federated_search()`
- `claude_innit/server.py` — MCP tool handlers

**Interview decisions (2026-03-11):**

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Stale chunks | Auto-rechunk when content_hash changes | Files with new hashes get chunks regenerated during vault_index |
| No headings | Paragraph splitting fallback | Already planned; handles bullet-heavy notes without ## headings |
| Dedup | Always one result per file | Cleaner results; matched_heading tells which section was relevant |
| Latency | Accept hybrid + query cache + pre-computed matrix | Quality over speed; optimizations keep it fast enough |
| Recency scope | Apply uniformly | Boost is mild enough (+10% max) that evergreen content won't be buried |
| Chunk config | Store params in metadata table | Auto-trigger rechunk when code params change |
| Code blocks | Embed as-is | MiniLM handles mixed content; code identifiers improve matching |
| Matrix refresh | Rebuild after vault_index | Server is idle during indexing anyway |
| Legacy table | Rename then drop after successful full rechunk | vault_files is source of truth; can regenerate if needed |
| Hybrid weights | Fixed 0.4/0.6 | Users wanting exact match use method='text' explicitly |
| Scale plan | FAISS at 50K threshold | Not built now; stub the threshold check for future |
| Related notes | Keep text-based query | Simpler, good enough with chunk-level matching |
| Federated search | Upgrade vault leg to hybrid | federated_search is primary cross-source tool; should use best available |
| Memory | 45MB is fine | Desktop MCP server on machine with plenty of RAM |
| Tool surface | vault_rechunk as MCP tool only | No skill; maintenance operation rarely used |
| Batch commit | Every 100 chunks | 30K→300 disk syncs; massive speed improvement |

**Data engineering review fixes (14 issues, all addressed):**

| # | Severity | Issue | Fix applied |
|---|----------|-------|-------------|
| 1 | Critical | `upsert_vault_chunks` commits mid-batch, defeats batching | Removed auto-commit; batch loop controls all transactions |
| 2 | Critical | `content_hash` missing from `vault_chunks` DDL | Added to DDL in Task 1; eliminated mid-plan amendment |
| 3 | Critical | Recency boost is Python loop over 30K rows per query | Pre-computed as numpy array at matrix load time |
| 4 | High | Missing index on `vault_chunk_embeddings(file_id)` | Added to DDL in Task 1 |
| 5 | High | `_merge_small_sections` keeps wrong heading on merge | Fixed: use next section's heading when merging forward |
| 6 | High | FK constraints never enforced (`PRAGMA foreign_keys`) | Added `PRAGMA foreign_keys = ON` to Task 1 |
| 7 | High | `INSERT OR REPLACE` changes `file_id`, orphaning chunks | Added `upsert_vault_file` fix to true upsert pattern in Task 1 |
| 8 | Medium | `_split_by_paragraphs` `char_offset` tracking is wrong | Fixed offset calculation in Task 2 |
| 9 | Medium | `load_matrix()` fetches redundant file-level metadata | Separated file-level dict from chunk-level metadata in Task 4 |
| 10 | Medium | Hybrid results carry both `score` and `rrf_score` | `rrf_score` is authoritative in hybrid mode; `score` stripped |
| 11 | Medium | Inner RRF scores survive into outer federated RRF | Made overwrite explicit with comment in Task 7 |
| 12 | Low | `chunk_config` version field is redundant | Removed; parameter comparison is sufficient |
| 13 | Low | Legacy table drop is irreversible with no rollback | Rename to `_deprecated` first; verify matrix loads before drop |
| 14 | Low | `integrity_check()` not updated for new tables | Added chunk table checks to integrity_check in Task 8 |

---

## Task 1: Schema, indexes, FK enforcement, and stable `file_id`

**Files:**
- Modify: `claude_innit/db/database.py`

**Step 1: Add `PRAGMA foreign_keys = ON` to `__init__`**

In `MemoryDatabase.__init__()`, after the connection is opened and before `_create_tables()`:

```python
self._conn.execute("PRAGMA foreign_keys = ON")
```

**Step 2: Add new tables and indexes to `_create_tables()`**

After the existing `vault_embeddings` table creation, add:

```sql
CREATE TABLE IF NOT EXISTS vault_chunks (
    chunk_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    heading TEXT,
    content TEXT NOT NULL,
    char_offset INTEGER DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (file_id) REFERENCES vault_files(file_id),
    UNIQUE(file_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS vault_chunk_embeddings (
    chunk_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL,
    embedding BLOB,
    model TEXT,
    FOREIGN KEY (chunk_id) REFERENCES vault_chunks(chunk_id),
    FOREIGN KEY (file_id) REFERENCES vault_files(file_id)
);

CREATE INDEX IF NOT EXISTS idx_vce_file_id
    ON vault_chunk_embeddings(file_id);

CREATE TABLE IF NOT EXISTS chunk_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

**Step 3: Fix `upsert_vault_file` to preserve `file_id`**

The current `INSERT OR REPLACE` assigns a new ROWID on every update, orphaning chunk rows. Change to a true upsert:

```python
def upsert_vault_file(self, file_path, filename, content, content_hash,
                      frontmatter=None, module=None, file_size=0,
                      modified_at=None):
    """Insert or update a vault file, preserving file_id on updates."""
    self._conn.execute(
        """INSERT INTO vault_files
               (file_path, filename, content, content_hash, frontmatter,
                module, file_size, modified_at, indexed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(file_path) DO UPDATE SET
               filename = excluded.filename,
               content = excluded.content,
               content_hash = excluded.content_hash,
               frontmatter = excluded.frontmatter,
               module = excluded.module,
               file_size = excluded.file_size,
               modified_at = excluded.modified_at,
               indexed_at = excluded.indexed_at
        """,
        (file_path, filename, content, content_hash,
         json.dumps(frontmatter or {}, default=str),
         module, file_size, modified_at,
         datetime.now().isoformat()),
    )
    self._conn.commit()
```

**Important:** The FTS sync triggers (`vault_files_au`) must handle the UPDATE case. The existing `AFTER UPDATE` trigger already does delete+re-insert into FTS, which is correct. Verify this still works after the change from `INSERT OR REPLACE` (which fired delete+insert triggers) to `INSERT ... ON CONFLICT DO UPDATE` (which fires the update trigger).

**Step 4: Add helper methods**

```python
def upsert_vault_chunks(self, file_id: int, chunks: list[dict],
                        content_hash: str = "", commit: bool = True) -> None:
    """Replace all chunks for a file_id.

    Args:
        commit: If False, caller controls transaction boundaries.
                Set False during batch operations for performance.
    """
    self._conn.execute(
        "DELETE FROM vault_chunk_embeddings WHERE file_id = ?", (file_id,)
    )
    self._conn.execute(
        "DELETE FROM vault_chunks WHERE file_id = ?", (file_id,)
    )
    for chunk in chunks:
        self._conn.execute(
            """INSERT INTO vault_chunks
               (file_id, chunk_index, heading, content, char_offset, content_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_id, chunk["chunk_index"], chunk.get("heading"),
             chunk["content"], chunk.get("char_offset", 0), content_hash),
        )
    if commit:
        self._conn.commit()

def get_chunks_for_file(self, file_id: int) -> list[dict]:
    """Get all chunks for a vault file, ordered by chunk_index."""
    rows = self._conn.execute(
        "SELECT * FROM vault_chunks WHERE file_id = ? ORDER BY chunk_index",
        (file_id,),
    ).fetchall()
    return [dict(r) for r in rows]

def get_chunk_config(self) -> dict:
    """Get stored chunking parameters."""
    try:
        rows = self._conn.execute(
            "SELECT key, value FROM chunk_config"
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {}

def set_chunk_config(self, config: dict) -> None:
    """Store chunking parameters."""
    for key, value in config.items():
        self._conn.execute(
            "INSERT OR REPLACE INTO chunk_config (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
    self._conn.commit()
```

**Step 5: Update `delete_vault_file()` cascade**

```python
def delete_vault_file(self, file_path: str) -> None:
    """Delete a vault file and all dependent data (chunks, embeddings)."""
    row = self._conn.execute(
        "SELECT file_id FROM vault_files WHERE file_path = ?", (file_path,)
    ).fetchone()
    if row:
        fid = row["file_id"]
        # Delete children before parent (FK order)
        self._conn.execute(
            "DELETE FROM vault_chunk_embeddings WHERE file_id = ?", (fid,)
        )
        self._conn.execute(
            "DELETE FROM vault_chunks WHERE file_id = ?", (fid,)
        )
        self._conn.execute(
            "DELETE FROM vault_embeddings WHERE file_id = ?", (fid,)
        )
    self._conn.execute(
        "DELETE FROM vault_files WHERE file_path = ?", (file_path,)
    )
    self._conn.commit()
```

**Step 6: Verify schema and FTS triggers**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
db = MemoryDatabase(':memory:')
print('Schema created OK')
tables = db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
print('Tables:', [t[0] for t in tables])
indexes = db.execute(\"SELECT name FROM sqlite_master WHERE type='index'\").fetchall()
print('Indexes:', [i[0] for i in indexes])
# Verify FK enforcement
fk = db.execute('PRAGMA foreign_keys').fetchone()
print(f'FK enforcement: {fk[0]}')
# Test upsert preserves file_id
db.upsert_vault_file('test.md', 'test.md', 'v1', 'hash1')
fid1 = db.execute('SELECT file_id FROM vault_files').fetchone()[0]
db.upsert_vault_file('test.md', 'test.md', 'v2', 'hash2')
fid2 = db.execute('SELECT file_id FROM vault_files').fetchone()[0]
assert fid1 == fid2, f'file_id changed: {fid1} -> {fid2}'
print(f'file_id stable: {fid1} == {fid2}')
db.close()
"
```

Expected: tables include `vault_chunks`, `vault_chunk_embeddings`, `chunk_config`. FK enforcement = 1. file_id is stable across upserts.

**Step 7: Commit**

```bash
git add claude_innit/db/database.py
git commit -m "feat: add vault_chunks, chunk_embeddings tables with FK enforcement and stable file_id"
```

---

## Task 2: Text chunking utility

**Files:**
- Create: `claude_innit/utils_chunking.py`

**Step 1: Write the chunking function**

```python
"""Text chunking utilities for vault file embeddings."""

import re
from typing import Optional

# Default chunking parameters — stored in chunk_config for versioning
DEFAULT_MAX_CHUNK_CHARS = 1000
DEFAULT_MIN_CHUNK_CHARS = 100


def chunk_by_headings(
    content: str,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    min_chunk_chars: int = DEFAULT_MIN_CHUNK_CHARS,
) -> list[dict]:
    """Split markdown content into chunks at ## headings.

    Strategy:
    1. Split at ## (h2) and ### (h3) headings
    2. If a section is still > max_chunk_chars, split at paragraph breaks
    3. Tiny sections (< min_chunk_chars) get merged into the next section
    4. Files < max_chunk_chars stay as a single chunk
    5. No heading-based splits found -> fall back to paragraph splitting
    6. Code blocks are embedded as-is (not special-cased)

    Returns list of dicts:
        [{"chunk_index": 0, "heading": "Introduction",
          "content": "...", "char_offset": 0}, ...]
    """
    if not content or not content.strip():
        return []

    # Short files: single chunk
    if len(content) <= max_chunk_chars:
        return [{"chunk_index": 0, "heading": None,
                 "content": content.strip(), "char_offset": 0}]

    # Split at ## or ### headings (not # which is typically the title)
    heading_pattern = re.compile(r'^(#{2,3})\s+(.+)$', re.MULTILINE)

    sections = []
    last_end = 0
    last_heading = None

    for match in heading_pattern.finditer(content):
        text_before = content[last_end:match.start()].strip()
        if text_before:
            sections.append({
                "heading": last_heading,
                "content": text_before,
                "char_offset": last_end,
            })
        last_heading = match.group(2).strip()
        last_end = match.end()

    # Capture text after the last heading
    remaining = content[last_end:].strip()
    if remaining:
        sections.append({
            "heading": last_heading,
            "content": remaining,
            "char_offset": last_end,
        })

    # No headings found -> fall back to paragraph splitting
    if len(sections) <= 1:
        return _split_by_paragraphs(content, max_chunk_chars)

    # Merge tiny sections into their neighbor
    merged = _merge_small_sections(sections, min_chunk_chars)

    # Split oversized sections at paragraph breaks
    final = []
    for section in merged:
        if len(section["content"]) > max_chunk_chars:
            sub_chunks = _split_by_paragraphs(
                section["content"], max_chunk_chars,
                base_heading=section["heading"],
            )
            for sc in sub_chunks:
                sc["char_offset"] += section["char_offset"]
            final.extend(sub_chunks)
        else:
            final.append(section)

    # Assign chunk indices
    for i, chunk in enumerate(final):
        chunk["chunk_index"] = i

    return final


def _split_by_paragraphs(
    text: str,
    max_chunk_chars: int,
    base_heading: Optional[str] = None,
) -> list[dict]:
    """Split text at double-newline paragraph boundaries.

    Tracks char_offset as cumulative character position in the
    original text (accounting for paragraph separators).
    """
    paragraphs = re.split(r'\n\n+', text.strip())
    chunks = []
    current = []
    current_len = 0
    # Track position in the original text
    running_offset = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > max_chunk_chars and current:
            chunks.append({
                "heading": base_heading,
                "content": "\n\n".join(current),
                "char_offset": running_offset,
            })
            running_offset += sum(len(p) for p in current) + 2 * (len(current) - 1)
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)

    if current:
        chunks.append({
            "heading": base_heading,
            "content": "\n\n".join(current),
            "char_offset": running_offset,
        })

    for i, chunk in enumerate(chunks):
        chunk["chunk_index"] = i

    return chunks


def _merge_small_sections(sections: list[dict], min_chars: int) -> list[dict]:
    """Merge sections smaller than min_chars into their neighbor.

    When merging forward (small section into next), keeps the NEXT
    section's heading — the larger section is the semantically
    meaningful one. When merging a trailing buffer backward into the
    last section, keeps the last section's heading.
    """
    if not sections:
        return []

    merged = []
    buffer = None

    for section in sections:
        if buffer is not None:
            # Merge buffer into this section — keep THIS section's heading
            section = {
                "heading": section["heading"],
                "content": buffer["content"] + "\n\n" + section["content"],
                "char_offset": buffer["char_offset"],
            }
            buffer = None

        if len(section["content"]) < min_chars:
            buffer = section
        else:
            merged.append(section)

    # Trailing buffer: merge backward into last section
    if buffer is not None:
        if merged:
            merged[-1]["content"] += "\n\n" + buffer["content"]
        else:
            merged.append(buffer)

    return merged


def get_config_dict(
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    min_chunk_chars: int = DEFAULT_MIN_CHUNK_CHARS,
) -> dict:
    """Return a config dict suitable for chunk_config storage."""
    return {
        "max_chunk_chars": str(max_chunk_chars),
        "min_chunk_chars": str(min_chunk_chars),
    }
```

**Step 2: Test the chunking function**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.utils_chunking import chunk_by_headings

# Test 1: Short file = single chunk
chunks = chunk_by_headings('Short note content')
assert len(chunks) == 1
assert chunks[0]['chunk_index'] == 0
print('Test 1 OK: short file = 1 chunk')

# Test 2: File with headings splits correctly
md = '''# Title

Intro paragraph here with some text.

## Section One

Content of section one with details about topic A.

## Section Two

Content of section two with more details about topic B.

### Subsection

Sub content here with extra detail.
'''
chunks = chunk_by_headings(md, max_chunk_chars=50)
print(f'Test 2: {len(chunks)} chunks from headed doc')
for c in chunks:
    print(f'  [{c[\"chunk_index\"]}] heading={c[\"heading\"]!r} len={len(c[\"content\"])}')

# Test 3: No headings -> paragraph splitting
no_headings = ('Paragraph one about topic A.\n\n' * 20)
chunks = chunk_by_headings(no_headings, max_chunk_chars=200)
print(f'Test 3: {len(no_headings)} char doc with no headings -> {len(chunks)} chunks')
assert len(chunks) > 1, 'Should split at paragraphs'

# Test 4: Merge keeps the right heading
md2 = '## Tiny\n\nX\n\n## Big Section\n\nLots of content here about big topics.'
chunks = chunk_by_headings(md2, max_chunk_chars=50, min_chunk_chars=20)
# The tiny section should merge into Big Section, keeping 'Big Section' heading
for c in chunks:
    if 'Lots of content' in c['content']:
        assert c['heading'] == 'Big Section', f'Wrong heading: {c[\"heading\"]}'
        print(f'Test 4 OK: merged section has heading={c[\"heading\"]!r}')

# Test 5: Real-world size
big = 'Intro.\n\n' + '\n\n'.join(
    f'## Section {i}\n\nLorem ipsum dolor sit amet. ' * 3
    for i in range(10)
)
chunks = chunk_by_headings(big, max_chunk_chars=500)
print(f'Test 5: {len(big)} char doc -> {len(chunks)} chunks')
print('All tests passed')
"
```

**Step 3: Commit**

```bash
git add claude_innit/utils_chunking.py
git commit -m "feat: add heading-level text chunking for vault embeddings"
```

---

## Task 3: Chunk-aware embedding pipeline with batch commits

**Files:**
- Modify: `claude_innit/db/embeddings.py`

**Step 1: Add `store_chunk_embedding()` (no per-call commit by default)**

```python
def store_chunk_embedding(self, chunk_id: int, file_id: int, text: str,
                          commit: bool = False) -> None:
    """Generate and store embedding for a vault chunk.

    Args:
        commit: If True, commit immediately. Default False for batch use.
    """
    if self.db is None:
        raise ValueError("Database required for storage")
    embedding = self.generate(text)
    blob = self._embedding_to_blob(embedding)
    self.db.execute(
        """INSERT OR REPLACE INTO vault_chunk_embeddings
           (chunk_id, file_id, embedding, model)
           VALUES (?, ?, ?, ?)""",
        (chunk_id, file_id, blob, "all-MiniLM-L6-v2"),
    )
    if commit:
        self.db.commit()
```

**Step 2: Add `batch_store_chunk_embeddings()` with unified transaction control**

All commits (chunks + embeddings) happen every BATCH_SIZE embeddings. No intermediate commits from `upsert_vault_chunks`.

```python
def batch_store_chunk_embeddings(self, limit: int = 0,
                                 force: bool = False) -> dict:
    """Generate chunk embeddings for vault files.

    Handles three cases:
    1. New files (no chunks yet)
    2. Changed files (content_hash differs from when chunks were made)
    3. Force mode (rechunk everything)

    Transaction strategy: commits every 100 embeddings. Both chunk
    rows and their embeddings are committed together — no window
    where chunks exist without embeddings.

    Returns: {"files_processed": int, "chunks_created": int,
              "embeddings_generated": int, "rechunked": int, "errors": int}
    """
    if self.db is None:
        raise ValueError("Database required for storage")

    from claude_innit.utils_chunking import chunk_by_headings, get_config_dict

    # Check if chunking params have changed
    stored_config = self.db.get_chunk_config()
    current_config = get_config_dict()
    config_changed = (
        stored_config.get("max_chunk_chars") != current_config["max_chunk_chars"]
        or stored_config.get("min_chunk_chars") != current_config["min_chunk_chars"]
    )
    if config_changed:
        force = True

    if force:
        self.db.execute("DELETE FROM vault_chunk_embeddings")
        self.db.execute("DELETE FROM vault_chunks")
        self.db.commit()

    # Find files needing chunking:
    # - No chunks yet (new files)
    # - Content hash changed since last chunk (edited files)
    query = """
        SELECT vf.file_id, vf.content, vf.filename, vf.content_hash
        FROM vault_files vf
        LEFT JOIN (
            SELECT file_id, content_hash AS chunk_content_hash
            FROM vault_chunks
            WHERE chunk_index = 0
        ) vc ON vf.file_id = vc.file_id
        WHERE (vc.file_id IS NULL OR vc.chunk_content_hash != vf.content_hash)
          AND vf.content IS NOT NULL
          AND length(vf.content) > 0
    """
    if limit > 0:
        query += f" LIMIT {limit}"

    rows = self.db.execute(query).fetchall()
    stats = {"files_processed": 0, "chunks_created": 0,
             "embeddings_generated": 0, "rechunked": 0, "errors": 0}

    # Track which files already had chunks (for rechunk counting)
    existing_chunk_file_ids = set()
    if not force:
        existing = self.db.execute(
            "SELECT DISTINCT file_id FROM vault_chunks"
        ).fetchall()
        existing_chunk_file_ids = {r[0] for r in existing}

    pending_commits = 0
    BATCH_SIZE = 100

    for row in rows:
        try:
            chunks = chunk_by_headings(row["content"])
            if not chunks:
                continue

            is_rechunk = row["file_id"] in existing_chunk_file_ids

            # Store chunks (commit=False — batch loop controls commits)
            self.db.upsert_vault_chunks(
                row["file_id"], chunks,
                content_hash=row["content_hash"],
                commit=False,
            )

            # Get the stored chunk_ids
            stored = self.db.get_chunks_for_file(row["file_id"])
            for chunk_row in stored:
                text = chunk_row["content"]
                if not text.strip():
                    continue
                self.store_chunk_embedding(
                    chunk_row["chunk_id"], row["file_id"], text,
                    commit=False,
                )
                stats["embeddings_generated"] += 1
                pending_commits += 1

                if pending_commits >= BATCH_SIZE:
                    self.db.commit()
                    pending_commits = 0

            stats["files_processed"] += 1
            stats["chunks_created"] += len(stored)
            if is_rechunk:
                stats["rechunked"] += 1

        except Exception:
            stats["errors"] += 1

    # Final commit for remaining
    if pending_commits > 0:
        self.db.commit()

    # Store current config
    self.db.set_chunk_config(current_config)

    return stats
```

**Step 3: Verify with small batch**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
db = MemoryDatabase('data/innit.db')
store = EmbeddingStore(db)
result = store.batch_store_chunk_embeddings(limit=5)
print(result)
print('Config:', db.get_chunk_config())
# Verify no orphan chunks (chunks without embeddings)
orphans = db.execute('''
    SELECT COUNT(*) FROM vault_chunks vc
    LEFT JOIN vault_chunk_embeddings vce ON vc.chunk_id = vce.chunk_id
    WHERE vce.chunk_id IS NULL AND length(vc.content) > 0
''').fetchone()[0]
print(f'Orphan chunks (should be 0): {orphans}')
db.close()
"
```

**Step 4: Commit**

```bash
git add claude_innit/db/embeddings.py
git commit -m "feat: chunk-aware embedding pipeline with batch commits and staleness detection"
```

---

## Task 4: Pre-computed embedding matrix and query cache

**Files:**
- Modify: `claude_innit/db/embeddings.py`

**Step 1: Add matrix, recency array, and query cache to `EmbeddingStore`**

The matrix stores pre-normalized embeddings. Recency weights are computed once at load time as a numpy array. File-level metadata is stored in a separate dict to avoid redundancy across chunks.

```python
import functools

class EmbeddingStore:
    FAISS_THRESHOLD = 50_000

    def __init__(self, db=None):
        self.db = db
        self._model = None
        self._matrix = None           # (N, 384) pre-normalized float32
        self._recency_weights = None  # (N,) float32 — pre-computed per chunk
        self._chunk_meta = None       # list of dicts: chunk_id, file_id, heading, chunk_index
        self._file_meta = None        # dict[file_id] -> {file_path, filename, module, modified_at}
        self._matrix_loaded = False

    def load_matrix(self, recency_weight: float = 0.1) -> int:
        """Pre-load all chunk embeddings into a numpy matrix.

        Also pre-computes recency weights as a numpy array.
        Call at server startup and after vault_index completes.
        Returns the number of embeddings loaded.
        """
        if self.db is None:
            return 0

        import numpy as np
        from datetime import datetime

        # Try chunk embeddings first, fall back to legacy
        rows = self.db.execute("""
            SELECT vce.chunk_id, vce.file_id, vce.embedding,
                   vc.heading, vc.chunk_index,
                   substr(vc.content, 1, 200) as snippet
            FROM vault_chunk_embeddings vce
            JOIN vault_chunks vc ON vce.chunk_id = vc.chunk_id
        """).fetchall()

        is_chunked = len(rows) > 0
        if not rows:
            rows = self.db.execute("""
                SELECT ve.file_id, ve.embedding,
                       substr(vf.content, 1, 200) as snippet
                FROM vault_embeddings ve
                JOIN vault_files vf ON ve.file_id = vf.file_id
            """).fetchall()

        if not rows:
            self._matrix = None
            self._chunk_meta = []
            self._file_meta = {}
            self._recency_weights = None
            self._matrix_loaded = True
            return 0

        # Build file-level metadata (deduplicated)
        file_ids = set()
        for r in rows:
            file_ids.add(r["file_id"])

        self._file_meta = {}
        if file_ids:
            placeholders = ",".join("?" * len(file_ids))
            file_rows = self.db.execute(
                f"SELECT file_id, file_path, filename, module, modified_at "
                f"FROM vault_files WHERE file_id IN ({placeholders})",
                tuple(file_ids),
            ).fetchall()
            self._file_meta = {r["file_id"]: dict(r) for r in file_rows}

        # Build embedding matrix (pre-normalized)
        embeddings = np.array(
            [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
        )
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        self._matrix = embeddings / norms

        # Build chunk-level metadata (compact)
        self._chunk_meta = []
        for r in rows:
            meta = {
                "file_id": r["file_id"],
                "snippet": r.get("snippet", ""),
            }
            if is_chunked:
                meta["chunk_id"] = r["chunk_id"]
                meta["heading"] = r.get("heading")
                meta["chunk_index"] = r.get("chunk_index")
            self._chunk_meta.append(meta)

        # Pre-compute recency weights as numpy array
        now = datetime.now()
        recency = np.ones(len(rows), dtype=np.float32)
        if recency_weight > 0:
            for i, meta in enumerate(self._chunk_meta):
                file_info = self._file_meta.get(meta["file_id"], {})
                mod_at = file_info.get("modified_at")
                if mod_at:
                    try:
                        mod_dt = datetime.fromisoformat(mod_at)
                        days_ago = max(0, (now - mod_dt).days)
                        recency_factor = 1.0 / (1.0 + days_ago / 365.0)
                        recency[i] = 1.0 + recency_weight * recency_factor
                    except (ValueError, TypeError):
                        pass
        self._recency_weights = recency

        self._matrix_loaded = True

        if len(rows) > self.FAISS_THRESHOLD:
            import logging
            logging.getLogger(__name__).warning(
                "Chunk count (%d) exceeds FAISS threshold (%d). "
                "Consider adding FAISS IVF index for faster search.",
                len(rows), self.FAISS_THRESHOLD,
            )

        return len(rows)

    @functools.lru_cache(maxsize=64)
    def _cached_query_embedding(self, query: str) -> tuple:
        """Cache query embeddings as tuples (hashable for LRU)."""
        embedding = self.generate(query)
        return tuple(embedding.tolist())

    def query_embedding(self, query: str):
        """Get normalized query embedding with LRU cache."""
        import numpy as np
        cached = self._cached_query_embedding(query)
        vec = np.array(cached, dtype=np.float32)
        vec /= (np.linalg.norm(vec) + 1e-10)
        return vec

    def invalidate_matrix(self) -> None:
        """Mark matrix as stale. Next search triggers reload."""
        self._matrix = None
        self._chunk_meta = None
        self._file_meta = None
        self._recency_weights = None
        self._matrix_loaded = False
        self._cached_query_embedding.cache_clear()
```

**Step 2: Verify matrix loading**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
db = MemoryDatabase('data/innit.db')
store = EmbeddingStore(db)
count = store.load_matrix()
print(f'Matrix loaded: {count} embeddings')
print(f'Matrix shape: {store._matrix.shape}')
print(f'Recency weights shape: {store._recency_weights.shape}')
print(f'File meta entries: {len(store._file_meta)}')
# Test query cache
q1 = store.query_embedding('test query')
q2 = store.query_embedding('test query')
print(f'Query embedding shape: {q1.shape}')
print(f'Cache info: {store._cached_query_embedding.cache_info()}')
db.close()
"
```

**Step 3: Commit**

```bash
git add claude_innit/db/embeddings.py
git commit -m "feat: pre-computed embedding matrix with recency weights and LRU query cache"
```

---

## Task 5: Rewrite `vault_semantic_search()` to use matrix

**Files:**
- Modify: `claude_innit/tools/vault.py`

**Step 1: Rewrite `vault_semantic_search()`**

Uses pre-computed matrix and recency weights. All numpy — no Python loops for similarity or boosting. Always deduplicates by file (one result per file, best chunk wins). `rrf_score` is not set here — `score`/`similarity` are the ranking fields for pure semantic results.

```python
def vault_semantic_search(
    embedding_store: EmbeddingStore,
    query: str,
    limit: int = 10,
    min_similarity: float = 0.35,
) -> list[dict]:
    """Search vault files by semantic similarity using chunk embeddings.

    Uses pre-computed embedding matrix for fast vectorized search.
    Always deduplicates by file — returns one result per file with
    the best-scoring chunk's metadata (matched_heading, chunk_index).
    Recency boost is pre-applied via the matrix's recency weights.
    """
    if embedding_store.db is None:
        return []

    import numpy as np

    # Ensure matrix is loaded
    if not embedding_store._matrix_loaded:
        embedding_store.load_matrix()

    if embedding_store._matrix is None or not embedding_store._chunk_meta:
        return []

    # Vectorized cosine similarity (both pre-normalized -> dot product)
    query_vec = embedding_store.query_embedding(query)
    similarities = np.dot(embedding_store._matrix, query_vec)

    # Apply pre-computed recency weights (single vectorized multiply)
    if embedding_store._recency_weights is not None:
        similarities = similarities * embedding_store._recency_weights

    # Filter and deduplicate by file (keep best chunk per file)
    results_by_file = {}
    for i, meta in enumerate(embedding_store._chunk_meta):
        sim = float(similarities[i])
        if sim < min_similarity:
            continue

        file_id = meta["file_id"]
        if file_id not in results_by_file or sim > results_by_file[file_id]["similarity"]:
            file_info = embedding_store._file_meta.get(file_id, {})
            result = {
                "file_id": file_id,
                "file_path": file_info.get("file_path", ""),
                "filename": file_info.get("filename", ""),
                "module": file_info.get("module"),
                "similarity": sim,
                "score": sim,
                "snippet": meta.get("snippet", ""),
            }
            if "heading" in meta:
                result["matched_heading"] = meta.get("heading")
                result["chunk_index"] = meta.get("chunk_index")
            results_by_file[file_id] = result

    results = sorted(
        results_by_file.values(),
        key=lambda x: x["similarity"],
        reverse=True,
    )
    return results[:limit]
```

**Step 2: Verify search**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
from claude_innit.tools.vault import vault_semantic_search
db = MemoryDatabase('data/innit.db')
store = EmbeddingStore(db)
store.load_matrix()
results = vault_semantic_search(store, 'kubernetes pod autoscaling', limit=5)
for r in results:
    print(f'{r[\"similarity\"]:.3f} | {r[\"filename\"]} | heading={r.get(\"matched_heading\", \"n/a\")}')
db.close()
"
```

**Step 3: Commit**

```bash
git add claude_innit/tools/vault.py
git commit -m "feat: matrix-based semantic search with pre-computed recency boost"
```

---

## Task 6: Hybrid FTS + semantic vault search

**Files:**
- Modify: `claude_innit/tools/vault.py`

**Step 1: Rewrite `vault_search()` and add hybrid merge**

`rrf_score` is the authoritative ranking field in hybrid mode. The raw FTS `score` field is stripped from merged results to avoid ambiguity.

```python
def vault_search(
    db: MemoryDatabase,
    query: str,
    scope: str = "all",
    limit: int = 20,
    embedding_store: Optional[EmbeddingStore] = None,
    method: str = "auto",
) -> list[dict]:
    """Search vault files with optional hybrid FTS + semantic fusion.

    method="auto": runs BOTH FTS and semantic, merges with mini-RRF.
                   Falls back to FTS-only if no embedding_store.
    method="text": FTS only
    method="semantic": semantic only
    """
    module_filter = None

    if method == "text" or (method == "auto" and embedding_store is None):
        return _fts_search(db, query, scope, limit, module_filter)

    elif method == "semantic":
        if embedding_store is None:
            raise ValueError(
                "Semantic search unavailable: no embedding store configured."
            )
        return vault_semantic_search(embedding_store, query, limit=limit)

    elif method == "auto":
        fts_results = _fts_search(db, query, scope, limit, module_filter)
        semantic_results = vault_semantic_search(
            embedding_store, query, limit=limit,
        )
        return _hybrid_merge(fts_results, semantic_results, limit)

    return []


def _fts_search(db, query, scope, limit, module_filter):
    """Run FTS search (extracted for reuse)."""
    if scope == "configs":
        results = db.vault_fts_search(query, limit=limit * 2)
        results = [r for r in results if r.get("module") is None][:limit]
    else:
        results = db.vault_fts_search(query, limit=limit, module=module_filter)
    for i, r in enumerate(results):
        r["score"] = 1.0 - (i * 0.02)
    return results


def _hybrid_merge(
    fts_results: list[dict],
    semantic_results: list[dict],
    limit: int,
) -> list[dict]:
    """Merge FTS and semantic results using mini-RRF.

    Fixed weights: FTS=0.4, semantic=0.6.
    k=20 (tighter than federated search's k=60).

    rrf_score is the authoritative ranking field in hybrid output.
    Raw FTS 'score' and semantic 'similarity' are stripped to avoid
    ambiguity for downstream consumers.
    """
    k = 20
    fts_weight = 0.4
    sem_weight = 0.6

    scored = {}

    for rank, item in enumerate(fts_results):
        key = item.get("file_path", f"fts:{rank}")
        rrf_score = fts_weight / (k + rank)
        entry = {**item, "rrf_score": rrf_score, "match_type": "fts"}
        entry.pop("score", None)  # Remove raw FTS score
        scored[key] = entry

    for rank, item in enumerate(semantic_results):
        key = item.get("file_path", f"sem:{rank}")
        rrf_score = sem_weight / (k + rank)
        if key in scored:
            scored[key]["rrf_score"] += rrf_score
            scored[key]["match_type"] = "hybrid"
            if item.get("matched_heading"):
                scored[key]["matched_heading"] = item["matched_heading"]
        else:
            entry = {**item, "rrf_score": rrf_score, "match_type": "semantic"}
            entry.pop("score", None)
            entry.pop("similarity", None)
            scored[key] = entry

    merged = sorted(scored.values(), key=lambda x: x["rrf_score"], reverse=True)
    return merged[:limit]
```

**Step 2: Verify hybrid search**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
from claude_innit.tools.vault import vault_search
db = MemoryDatabase('data/innit.db')
store = EmbeddingStore(db)
store.load_matrix()
results = vault_search(db, 'improving search quality',
                       embedding_store=store, method='auto', limit=5)
for r in results:
    mt = r.get('match_type', '?')
    heading = r.get('matched_heading', '')
    assert 'score' not in r or r.get('match_type') != 'hybrid', 'Raw score leaked into hybrid result'
    print(f'{r[\"rrf_score\"]:.4f} [{mt:>8}] {r[\"filename\"]} {heading}')
db.close()
"
```

**Step 3: Commit**

```bash
git add claude_innit/tools/vault.py
git commit -m "feat: hybrid FTS+semantic vault search with mini-RRF fusion"
```

---

## Task 7: Upgrade `federated_search` vault leg to hybrid

**Files:**
- Modify: `claude_innit/tools/federation.py`
- Modify: `claude_innit/server.py`

**Step 1: Update `federated_search()` to accept embedding_store and rrf_k**

```python
def federated_search(
    db: MemoryDatabase,
    query: str,
    sources: Optional[list[str]] = None,
    limit: int = 30,
    book_db_path: Optional[Path] = None,
    weights: Optional[dict[str, float]] = None,
    rrf_k: int = 60,
    embedding_store=None,
) -> dict:
```

**Step 2: Update vault search block to use hybrid**

```python
if "vault" in sources:
    if embedding_store is not None:
        from claude_innit.tools.vault import vault_search as _vault_search
        vault_results = _vault_search(
            db, query, limit=limit,
            embedding_store=embedding_store, method="auto",
        )
        # Re-tag source for outer RRF.
        # Note: inner hybrid rrf_score is intentionally overwritten by
        # the outer _reciprocal_rank_fusion() — outer RRF uses rank
        # position, not the inner score value. This is correct behavior.
        for r in vault_results:
            r["source"] = "vault"
    else:
        vault_results = _search_vault(db, query, limit=limit)
    results["vault"] = vault_results
    result_lists.append(vault_results)
```

**Step 3: Pass rrf_k through**

```python
results["merged"] = _reciprocal_rank_fusion(
    result_lists, weights=weights, k=rrf_k,
)[:limit]
```

**Step 4: Update server.py handler**

```python
elif name == "federated_search":
    result = federated_search(
        self.db,
        query=arguments["query"],
        sources=arguments.get("sources"),
        limit=arguments.get("limit", 30),
        rrf_k=arguments.get("rrf_k", 60),
        embedding_store=self.embedding_store,
    )
```

**Step 5: Commit**

```bash
git add claude_innit/tools/federation.py claude_innit/server.py
git commit -m "feat: upgrade federated_search vault leg to hybrid FTS+semantic"
```

---

## Task 8: Stats, cleanup, integrity_check, and matrix lifecycle

**Files:**
- Modify: `claude_innit/db/database.py`
- Modify: `claude_innit/server.py`

**Step 1: Update `vault_embedding_stats()` for chunk metrics**

```python
def vault_embedding_stats(self) -> dict:
    legacy_total = self._conn.execute(
        "SELECT COUNT(*) FROM vault_embeddings"
    ).fetchone()[0]

    chunk_emb_total = 0
    chunk_total = 0
    files_with_chunks = 0
    try:
        chunk_emb_total = self._conn.execute(
            "SELECT COUNT(*) FROM vault_chunk_embeddings"
        ).fetchone()[0]
        chunk_total = self._conn.execute(
            "SELECT COUNT(*) FROM vault_chunks"
        ).fetchone()[0]
        files_with_chunks = self._conn.execute(
            "SELECT COUNT(DISTINCT file_id) FROM vault_chunks"
        ).fetchone()[0]
    except Exception:
        pass

    total_files = self._conn.execute(
        "SELECT COUNT(*) FROM vault_files"
    ).fetchone()[0]

    model = None
    try:
        row = self._conn.execute(
            "SELECT DISTINCT model FROM vault_chunk_embeddings LIMIT 1"
        ).fetchone()
        model = row[0] if row else None
    except Exception:
        pass
    if model is None:
        try:
            row = self._conn.execute(
                "SELECT DISTINCT model FROM vault_embeddings LIMIT 1"
            ).fetchone()
            model = row[0] if row else None
        except Exception:
            pass

    return {
        "total_files": total_files,
        "files_with_chunks": files_with_chunks,
        "total_chunks": chunk_total,
        "chunk_embeddings": chunk_emb_total,
        "legacy_embeddings": legacy_total,
        "model": model,
        "mode": "chunked" if chunk_emb_total > 0 else "legacy",
    }
```

**Step 2: Update orphan cleanup for chunks**

```python
def cleanup_orphan_vault_embeddings(self) -> int:
    count = 0
    # Legacy
    try:
        cursor = self._conn.execute(
            "DELETE FROM vault_embeddings WHERE file_id NOT IN "
            "(SELECT file_id FROM vault_files)"
        )
        count += cursor.rowcount
    except Exception:
        pass  # Table may have been dropped

    # Chunk embeddings
    try:
        cursor = self._conn.execute(
            "DELETE FROM vault_chunk_embeddings WHERE file_id NOT IN "
            "(SELECT file_id FROM vault_files)"
        )
        count += cursor.rowcount
        cursor = self._conn.execute(
            "DELETE FROM vault_chunks WHERE file_id NOT IN "
            "(SELECT file_id FROM vault_files)"
        )
        count += cursor.rowcount
    except Exception:
        pass

    self._conn.commit()
    return count
```

**Step 3: Update `integrity_check()` for new tables**

Add chunk-related checks to the existing `integrity_check()` method. After the existing vault FTS checks:

```python
# Chunk integrity
try:
    chunk_count = self._conn.execute(
        "SELECT COUNT(*) FROM vault_chunks"
    ).fetchone()[0]
    chunk_emb_count = self._conn.execute(
        "SELECT COUNT(*) FROM vault_chunk_embeddings"
    ).fetchone()[0]
    orphan_chunks = self._conn.execute(
        "SELECT COUNT(*) FROM vault_chunks WHERE file_id NOT IN "
        "(SELECT file_id FROM vault_files)"
    ).fetchone()[0]
    orphan_chunk_embs = self._conn.execute(
        "SELECT COUNT(*) FROM vault_chunk_embeddings WHERE file_id NOT IN "
        "(SELECT file_id FROM vault_files)"
    ).fetchone()[0]

    if orphan_chunks > 0:
        issues.append(f"Orphan vault_chunks: {orphan_chunks}")
        if auto_repair:
            self._conn.execute(
                "DELETE FROM vault_chunks WHERE file_id NOT IN "
                "(SELECT file_id FROM vault_files)"
            )
            repairs.append(f"Removed {orphan_chunks} orphan chunks")
    if orphan_chunk_embs > 0:
        issues.append(f"Orphan vault_chunk_embeddings: {orphan_chunk_embs}")
        if auto_repair:
            self._conn.execute(
                "DELETE FROM vault_chunk_embeddings WHERE file_id NOT IN "
                "(SELECT file_id FROM vault_files)"
            )
            repairs.append(f"Removed {orphan_chunk_embs} orphan chunk embeddings")
except Exception:
    pass  # Tables may not exist yet
```

**Important:** Wrap the existing `vault_embeddings` orphan check in a try/except too, since the table may be dropped after migration (Task 9).

**Step 4: Wire matrix reload into vault_index handler**

In `server.py` vault_index handler, after cleanup:

```python
if self.embedding_store:
    chunk_result = await asyncio.to_thread(
        self.embedding_store.batch_store_chunk_embeddings,
    )
    result["chunks"] = chunk_result

    # Reload the pre-computed matrix
    matrix_count = self.embedding_store.load_matrix()
    result["matrix_loaded"] = matrix_count
```

**Step 5: Load matrix at server startup**

In `InnitServer.__init__()`, after `self.embedding_store.warm()`:

```python
self.embedding_store.load_matrix()
```

**Step 6: Add `vault_rechunk` MCP tool**

Tool definition + handler (MCP tool only, no skill):

```python
# Tool definition
Tool(
    name="vault_rechunk",
    description="Force re-chunk and re-embed all vault files. "
                "Use when chunking parameters change or chunks seem stale.",
    inputSchema={"type": "object", "properties": {}},
)

# Handler
elif name == "vault_rechunk":
    if self.embedding_store:
        result = await asyncio.to_thread(
            self.embedding_store.batch_store_chunk_embeddings,
            force=True,
        )
        matrix_count = self.embedding_store.load_matrix()
        result["matrix_reloaded"] = matrix_count
    else:
        result = {"error": "No embedding store configured"}
```

**Step 7: Commit**

```bash
git add claude_innit/db/database.py claude_innit/server.py
git commit -m "feat: chunk stats, cleanup, integrity_check, matrix lifecycle, vault_rechunk"
```

---

## Task 9: Full chunk embedding generation + legacy cleanup

**Step 1: Run chunk embedding generation for all vault files**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
db = MemoryDatabase('data/innit.db')
store = EmbeddingStore(db)
result = store.batch_store_chunk_embeddings()
print(result)
db.close()
"
```

Expected: ~9K files processed, ~25-30K chunks created, ~25-30K embeddings generated.

**Step 2: Verify stats**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
db = MemoryDatabase('data/innit.db')
stats = db.vault_embedding_stats()
print(stats)
assert stats['mode'] == 'chunked'
assert stats['files_with_chunks'] > 9000
assert stats['chunk_embeddings'] > 20000
print('Stats verified OK')
db.close()
"
```

**Step 3: Verify matrix loads correctly**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
db = MemoryDatabase('data/innit.db')
store = EmbeddingStore(db)
count = store.load_matrix()
print(f'Matrix loaded: {count} embeddings')
assert count > 20000, f'Matrix too small: {count}'
print('Matrix verification passed')
db.close()
"
```

**Step 4: Run search quality comparison**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
from claude_innit.tools.vault import vault_search

db = MemoryDatabase('data/innit.db')
store = EmbeddingStore(db)
store.load_matrix()

queries = [
    'kubernetes pod autoscaling',
    'CSS grid layout responsive',
    'STAR story interview preparation',
    'SQLite FTS5 performance tuning',
    'Obsidian vault organization',
]

for q in queries:
    print(f'\n--- {q} ---')
    results = vault_search(db, q, embedding_store=store, method='auto', limit=3)
    for r in results:
        mt = r.get('match_type', '?')
        heading = r.get('matched_heading', '')
        score = r.get('rrf_score', r.get('score', 0))
        print(f'  {score:.4f} [{mt:>8}] {r[\"filename\"]:.<50} {heading}')

db.close()
"
```

**Step 5: Rename and drop legacy table (safe migration)**

Only after confirming chunk coverage >= 99% AND matrix loads successfully:

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore

db = MemoryDatabase('data/innit.db')
stats = db.vault_embedding_stats()
coverage = stats['files_with_chunks'] / stats['total_files'] * 100
print(f'Chunk coverage: {coverage:.1f}%')

# Verify matrix loads from chunks (not legacy)
store = EmbeddingStore(db)
count = store.load_matrix()
print(f'Matrix loaded {count} chunk embeddings')

if coverage >= 99 and count > 20000:
    # Step 1: Rename to deprecated (reversible)
    db.execute('ALTER TABLE vault_embeddings RENAME TO vault_embeddings_deprecated')
    db.commit()
    print('Legacy table renamed to vault_embeddings_deprecated')

    # Step 2: Verify search still works
    from claude_innit.tools.vault import vault_search
    results = vault_search(db, 'test query', embedding_store=store, method='auto', limit=1)
    print(f'Post-rename search returned {len(results)} results')

    # Step 3: Drop deprecated table
    db.execute('DROP TABLE IF EXISTS vault_embeddings_deprecated')
    db.commit()
    print('Deprecated table dropped')
else:
    print(f'NOT SAFE TO DROP: coverage={coverage:.1f}%, matrix={count}')

db.close()
"
```

**Step 6: Run integrity_check to confirm clean state**

```bash
cd /Users/taylorstephens/Dev/_Lab/Claude-Innit
.venv/bin/python -c "
from claude_innit.db.database import MemoryDatabase
db = MemoryDatabase('data/innit.db')
result = db.integrity_check(auto_repair=False)
print(result)
db.close()
"
```

Expected: no issues, clean state.

---

## Task 10: Update ClaudeInnitClient in taylor-portfolio

**Files:**
- Modify: `/Users/taylorstephens/Dev/_Projects/my-mcp-portfolio/src/domain/claude_innit_client.py`

**Step 1: Update `get_stats()` to report chunk metrics**

```python
def get_stats(self) -> dict:
    conn = self._get_connection()
    try:
        vault_count = conn.execute(
            "SELECT COUNT(*) FROM vault_files"
        ).fetchone()[0]
        memory_count = conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        session_count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE category = 'session'"
        ).fetchone()[0]
        chunk_count = 0
        try:
            chunk_count = conn.execute(
                "SELECT COUNT(*) FROM vault_chunks"
            ).fetchone()[0]
        except Exception:
            pass
        return {
            "vault_files": vault_count,
            "vault_chunks": chunk_count,
            "memories": memory_count,
            "sessions": session_count,
        }
    except sqlite3.OperationalError:
        return {
            "vault_files": 0, "vault_chunks": 0,
            "memories": 0, "sessions": 0,
        }
```

**Step 2: Commit**

```bash
cd /Users/taylorstephens/Dev/_Projects/my-mcp-portfolio
git add src/domain/claude_innit_client.py
git commit -m "feat: report vault chunk metrics in innit stats"
```

---

## Summary

| Task | What | Impact | Review fixes applied |
|------|------|--------|---------------------|
| 1 | Schema + indexes + FK enforcement + stable file_id | Foundation | #2, #4, #6, #7 |
| 2 | Text chunking utility (heading split, paragraph fallback) | Core logic | #5, #8, #12 |
| 3 | Chunk-aware embedding pipeline (unified batch commits) | Replaces `content[:500]` | #1 |
| 4 | Pre-computed matrix + recency array + LRU cache | Performance | #3, #9 |
| 5 | Matrix-based semantic search (vectorized, deduped) | Search quality | #3 |
| 6 | Hybrid FTS + semantic vault search (clean rrf_score) | Always-on improvement | #10 |
| 7 | Upgrade `federated_search` vault leg to hybrid | Cross-source quality | #11 |
| 8 | Stats, cleanup, integrity_check, matrix lifecycle | Observability | #14 |
| 9 | Full re-generation + safe legacy cleanup | Activate everything | #13 |
| 10 | Update taylor-portfolio client | Cross-project consistency | — |
