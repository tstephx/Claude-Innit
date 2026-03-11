"""Vault file indexing, search, and stats for OBF unified search."""

import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from claude_innit.db.database import MemoryDatabase
from claude_innit.db.embeddings import EmbeddingStore
from claude_innit.utils import parse_frontmatter as _parse_frontmatter

logger = logging.getLogger(__name__)


def _content_hash(content: str) -> str:
    """SHA-256 hash of file content for staleness detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _detect_module(file_path: str, vault_root: str) -> Optional[str]:
    """Detect which module a file belongs to from its path.

    Convention: files under `<vault_root>/<module_name>/` belong to that module.
    Files at vault root return None. Dot-prefixed framework dirs (.brain/, .claude/)
    are excluded from indexing via exclude_patterns, not here.
    """
    try:
        rel = Path(file_path).relative_to(vault_root)
    except ValueError:
        return None

    parts = rel.parts
    if len(parts) < 2:
        return None

    first_dir = parts[0]
    # Return lowercased for consistent module naming regardless of filesystem casing
    return first_dir.lower()


class VaultIndexer:
    """Indexes vault markdown files into the claude-innit database."""

    def __init__(
        self,
        db: MemoryDatabase,
        vault_root: str,
        extra_paths: Optional[list[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
    ):
        self.db = db
        self.vault_root = Path(vault_root)
        self.extra_paths = [Path(p) for p in (extra_paths or [])]
        self.exclude_patterns = exclude_patterns or [
            "node_modules/",
            ".git/",
            ".obsidian/",
            ".DS_Store",
            "__pycache__/",
            ".pytest_cache/",
            ".venv/",
            ".brain/tests/",
        ]

    def _should_exclude(self, path: Path) -> bool:
        """Check if path matches any exclude pattern."""
        path_str = str(path)
        return any(pat in path_str for pat in self.exclude_patterns)

    def _collect_files(self) -> list[Path]:
        """Collect all .md files from vault root and extra paths."""
        files = []
        for root_dir in [self.vault_root] + self.extra_paths:
            if not root_dir.exists():
                continue
            for md_file in root_dir.rglob("*.md"):
                if not self._should_exclude(md_file) and md_file.is_file():
                    files.append(md_file)
        return files

    def index(self, force: bool = False) -> dict:
        """Index all vault files. Skip unchanged files unless force=True.

        Returns: {"indexed": int, "updated": int, "unchanged": int,
                  "removed": int, "errors": int, "duration_ms": int}
        """
        start = time.monotonic()
        stats = {"indexed": 0, "updated": 0, "unchanged": 0, "removed": 0, "errors": 0}

        files = self._collect_files()
        seen_paths = set()

        for md_file in files:
            file_path_str = str(md_file)
            seen_paths.add(file_path_str)

            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                h = _content_hash(content)

                if not force:
                    existing = self.db.get_vault_file(file_path_str)
                    if existing and existing["content_hash"] == h:
                        stats["unchanged"] += 1
                        continue

                frontmatter, body = _parse_frontmatter(content)
                module = _detect_module(file_path_str, str(self.vault_root))
                mod_time = datetime.fromtimestamp(md_file.stat().st_mtime).isoformat()

                is_update = self.db.get_vault_file(file_path_str) is not None

                self.db.upsert_vault_file(
                    file_path=file_path_str,
                    filename=md_file.name,
                    content=body,
                    content_hash=h,
                    frontmatter=frontmatter,
                    module=module,
                    file_size=md_file.stat().st_size,
                    modified_at=mod_time,
                )

                if is_update:
                    stats["updated"] += 1
                else:
                    stats["indexed"] += 1

            except Exception:
                logger.debug("Error indexing %s", file_path_str, exc_info=True)
                stats["errors"] += 1

        # Remove files that no longer exist on disk
        all_indexed = self.db.execute("SELECT file_path FROM vault_files").fetchall()
        for row in all_indexed:
            if row["file_path"] not in seen_paths:
                self.db.delete_vault_file(row["file_path"])
                stats["removed"] += 1

        elapsed = int((time.monotonic() - start) * 1000)
        stats["duration_ms"] = elapsed
        return stats


def vault_index(
    db: MemoryDatabase,
    vault_root: str,
    extra_paths: Optional[list[str]] = None,
    exclude_patterns: Optional[list[str]] = None,
    force: bool = False,
) -> dict:
    """Index vault files into the database.

    Returns: {"indexed": int, "updated": int, "unchanged": int,
              "removed": int, "errors": int, "duration_ms": int}
    """
    indexer = VaultIndexer(db, vault_root, extra_paths, exclude_patterns)
    return indexer.index(force=force)


def vault_search(
    db: MemoryDatabase,
    query: str,
    scope: str = "all",
    limit: int = 20,
    embedding_store: Optional[EmbeddingStore] = None,
    method: str = "auto",
) -> list[dict]:
    """Search vault files.

    Args:
        db: Database connection
        query: Search query
        scope: "vault" (vault files only), "configs" (framework config files), "all"
        limit: Maximum results
        embedding_store: Optional for semantic search
        method: "auto", "text", or "semantic"

    Returns: List of matching vault file dicts with score/similarity
    """
    module_filter = None

    if method == "auto":
        word_count = len(query.split())
        method = "text" if word_count <= 3 else "semantic"

    if method == "text":
        if scope == "configs":
            # Config files are in framework dirs (module=None).
            # Only .md files are indexed, so filter to module=None entries.
            results = db.vault_fts_search(query, limit=limit * 2)
            results = [r for r in results if r.get("module") is None][:limit]
        else:
            # Note: scope="vault" and scope="all" are currently equivalent since
            # only vault .md files are indexed. If non-vault sources (e.g., external
            # project docs) are added to the index later, scope="vault" should filter
            # to files under the vault root only.
            results = db.vault_fts_search(query, limit=limit, module=module_filter)

        # Add score field for consistency
        for i, r in enumerate(results):
            r["score"] = 1.0 - (i * 0.02)  # FTS rank-order approximation
        return results

    elif method == "semantic":
        if embedding_store is None:
            embedding_store = EmbeddingStore(db)
        return vault_semantic_search(embedding_store, query, limit=limit)

    return []


def vault_semantic_search(
    embedding_store: EmbeddingStore,
    query: str,
    limit: int = 10,
    min_similarity: float = 0.35,
) -> list[dict]:
    """Search vault files by semantic similarity."""
    if embedding_store.db is None:
        return []

    import numpy as np

    query_embedding = embedding_store.generate(query)

    rows = embedding_store.db.execute(
        """
        SELECT ve.file_id, ve.embedding, vf.*
        FROM vault_embeddings ve
        JOIN vault_files vf ON ve.file_id = vf.file_id
        """
    ).fetchall()

    results = []
    for row in rows:
        embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        similarity = float(
            np.dot(query_embedding, embedding)
            / (np.linalg.norm(query_embedding) * np.linalg.norm(embedding))
        )
        if similarity >= min_similarity:
            result = dict(row)
            result["similarity"] = similarity
            result["score"] = similarity
            results.append(result)

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:limit]


def vault_related(
    db: MemoryDatabase,
    note_path: str,
    limit: int = 10,
    embedding_store: Optional[EmbeddingStore] = None,
) -> list[dict]:
    """Find notes related to a given note path using semantic similarity.

    Falls back to FTS search using the note's filename as query if no embeddings.
    """
    vault_file = db.get_vault_file(note_path)
    if not vault_file:
        return []

    # Try semantic search first
    if embedding_store:
        try:
            results = vault_semantic_search(
                embedding_store, vault_file["content"][:500], limit=limit + 1
            )
            filtered = [r for r in results if r["file_path"] != note_path][:limit]
            if filtered:
                return filtered
        except Exception:
            pass

    # Fallback: FTS search using filename words
    query_words = Path(note_path).stem.replace("-", " ").replace("_", " ")
    results = db.vault_fts_search(query_words, limit=limit + 1)
    return [r for r in results if r["file_path"] != note_path][:limit]


def vault_stats(db: MemoryDatabase) -> dict:
    """Return vault health metrics.

    Returns: {"total_notes": int, "by_module": dict, "by_status": dict,
              "inbox_count": int, "stale_count": int, "orphan_estimate": int,
              "index_age_seconds": float, "last_indexed": str}
    """
    total = db.vault_file_count()
    by_module = db.vault_files_by_module()
    by_status = db.vault_files_by_status()

    # Inbox count: files in Inbox/ directory
    inbox_count = 0
    try:
        inbox_rows = db.execute(
            "SELECT COUNT(*) FROM vault_files WHERE file_path LIKE '%/Inbox/%'"
        ).fetchone()
        inbox_count = inbox_rows[0] if inbox_rows else 0
    except Exception:
        logger.debug("Error counting inbox files", exc_info=True)

    # Stale count: files not re-indexed in 30+ days (may need re-scan)
    stale_count = len(db.vault_stale_files(days=30))

    # Index age: time since most recent indexed_at
    last_indexed = None
    index_age_seconds = -1.0
    try:
        row = db.execute("SELECT MAX(indexed_at) as latest FROM vault_files").fetchone()
        if row and row["latest"]:
            last_indexed = row["latest"]
            latest_dt = datetime.fromisoformat(last_indexed)
            index_age_seconds = (datetime.now() - latest_dt).total_seconds()
    except Exception:
        logger.debug("Error computing index age", exc_info=True)

    return {
        "total_notes": total,
        "by_module": by_module,
        "by_status": by_status,
        "inbox_count": inbox_count,
        "stale_count": stale_count,
        "index_age_seconds": index_age_seconds,
        "last_indexed": last_indexed,
    }
