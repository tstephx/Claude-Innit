"""Vault file indexing, search, and stats for OBF unified search."""

import hashlib
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


_FRAMEWORK_DIRS = frozenset({"daily", "inbox", "archive", "claude-memory"})


def _detect_module(file_path: str, vault_root: str) -> Optional[str]:
    """Detect which module a file belongs to from its path.

    Convention: files under `<vault_root>/<module_name>/` belong to that module.
    Files at vault root return None. Named framework dirs (Daily, Inbox, Archive,
    Claude-Memory) return None — they are organizational, not content modules.
    Dot-prefixed dirs (.brain/, .claude/) are excluded via VaultIndexer.exclude_patterns.
    """
    try:
        rel = Path(file_path).relative_to(vault_root)
    except ValueError:
        return None

    parts = rel.parts
    if len(parts) < 2:
        return None

    first_dir = parts[0]
    lowered = first_dir.lower()
    if lowered in _FRAMEWORK_DIRS:
        return None
    return lowered


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
            "venv/",
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

                existing = self.db.get_vault_file(file_path_str)

                if not force:
                    if existing and existing["content_hash"] == h:
                        stats["unchanged"] += 1
                        continue

                frontmatter, body = _parse_frontmatter(content)
                module = _detect_module(file_path_str, str(self.vault_root))
                mod_time = datetime.fromtimestamp(md_file.stat().st_mtime).isoformat()

                is_update = existing is not None

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


_MAX_LIMIT = 100
_SNIPPET_LEN = 200


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
    limit = max(0, min(limit, _MAX_LIMIT))
    if limit == 0:
        return []

    module_filter = None
    # scope="configs" means framework dirs only (module is None)
    scope_module = "configs" if scope == "configs" else None

    if method == "text" or (method == "auto" and embedding_store is None):
        return _fts_search(db, query, scope, limit, module_filter)

    elif method == "semantic":
        if embedding_store is None:
            raise ValueError(
                "Semantic search unavailable: no embedding store configured."
            )
        return vault_semantic_search(
            embedding_store,
            query,
            limit=limit,
            scope_module=scope_module,
        )

    elif method == "auto":
        fts_results = _fts_search(db, query, scope, limit, module_filter)
        semantic_results = vault_semantic_search(
            embedding_store,
            query,
            limit=limit,
            scope_module=scope_module,
        )
        return _hybrid_merge(fts_results, semantic_results, limit)

    return []


def _fts_search(db, query, scope, limit, module_filter):
    """Run FTS search and return compact results with snippet."""
    if scope == "configs":
        raw = db.vault_fts_search(query, limit=limit * 2)
        raw = [r for r in raw if r.get("module") is None][:limit]
    else:
        raw = db.vault_fts_search(query, limit=limit, module=module_filter)
    results = []
    for i, r in enumerate(raw):
        filename = r.get("filename", "")
        results.append(
            {
                "file_id": r.get("file_id"),
                "file_path": r.get("file_path", ""),
                "filename": filename,
                "title": Path(filename).stem if filename else "",
                "module": r.get("module"),
                "snippet": (r.get("content") or "")[:_SNIPPET_LEN],
                "score": 1.0 - (i * 0.02),
            }
        )
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
            scored[key].pop("similarity", None)
            if item.get("matched_heading"):
                scored[key]["matched_heading"] = item["matched_heading"]
        else:
            entry = {**item, "rrf_score": rrf_score, "match_type": "semantic"}
            entry.pop("score", None)
            entry.pop("similarity", None)
            scored[key] = entry

    merged = sorted(scored.values(), key=lambda x: x["rrf_score"], reverse=True)
    return merged[:limit]


def vault_semantic_search(
    embedding_store: EmbeddingStore,
    query: str,
    limit: int = 10,
    min_similarity: float = 0.35,
    scope_module: Optional[str] = None,
) -> list[dict]:
    """Search vault files by semantic similarity using chunk embeddings.

    Delegates to EmbeddingStore.search_chunks() for matrix search,
    deduplication, and recency weighting. This function only applies
    the scope_module filter.

    Args:
        scope_module: If "configs", only return files where module is None
                      (framework config dirs). None means no filter.
    """
    file_filter = None
    if scope_module == "configs":
        file_filter = lambda f: f.get("module") is None

    return embedding_store.search_chunks(
        query,
        limit=limit,
        min_similarity=min_similarity,
        file_filter=file_filter,
    )


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


def vault_stats(
    db: MemoryDatabase, embedding_store: Optional[EmbeddingStore] = None
) -> dict:
    """Return vault health metrics.

    Returns: {"total_notes": int, "by_module": dict, "by_status": dict,
              "inbox_count": int, "stale_count": int,
              "index_age_seconds": float, "last_indexed": str,
              "embeddings": dict}
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

    # Embedding health
    emb_stats = db.vault_embedding_stats()
    self_test = "unavailable"
    if embedding_store is not None:
        try:
            vec = embedding_store.generate("self-test query")
            self_test = "pass" if vec is not None and len(vec) == 384 else "fail"
        except Exception:
            self_test = "fail"
    emb_stats["self_test"] = self_test

    return {
        "total_notes": total,
        "by_module": by_module,
        "by_status": by_status,
        "inbox_count": inbox_count,
        "stale_count": stale_count,
        "index_age_seconds": index_age_seconds,
        "last_indexed": last_indexed,
        "embeddings": emb_stats,
    }
