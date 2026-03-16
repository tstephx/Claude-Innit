"""Vault frontmatter tagger — two-phase preview/apply MCP tool."""

import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

FOLDER_TYPE_MAP = {
    "projects": "project",
    "guides": "guide",
    "behavioral-studio": "story",
    "portfolio": "portfolio",
    "sessions": "session",
    "books": "reference",
    "project-context": "context",
    "claude-config": "config",
}


def has_frontmatter(path: Path) -> bool:
    """Check if file starts with YAML frontmatter fence."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        return first_line == "---"
    except (OSError, UnicodeDecodeError):
        return True


_SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".obsidian",
        "dist",
        "build",
        "site-packages",
        ".hypothesis",
        "htmlcov",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
    }
)


def _find_untagged_in(root: Path) -> list[Path]:
    """Find .md files under a single root that lack frontmatter."""
    untagged = []
    for md_file in sorted(root.rglob("*.md")):
        if not md_file.is_file():
            continue
        parts_lower = [p.lower() for p in md_file.relative_to(root).parts]
        if any(p.startswith(".") or p in _SKIP_DIRS for p in parts_lower):
            continue
        if ".egg-info" in str(md_file):
            continue
        if "/rss-news/" in str(md_file):
            continue
        if not has_frontmatter(md_file):
            untagged.append(md_file)
    return untagged


def find_untagged(
    vault_root: Path, extra_paths: Optional[list[Path]] = None
) -> list[Path]:
    """Find .md files under vault_root and extra paths that lack frontmatter."""
    untagged = _find_untagged_in(vault_root)
    for ep in extra_paths or []:
        if ep.exists():
            untagged.extend(_find_untagged_in(ep))
    return untagged


def _get_created_date(path: Path) -> str:
    """Get file creation date using macOS st_birthtime."""
    try:
        stat = os.stat(path)
        ts = getattr(stat, "st_birthtime", stat.st_mtime)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d")


def _get_modified_date(path: Path) -> str:
    """Get file modification date."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d")


def _resolve_root(
    path: Path, vault_root: Path, extra_paths: Optional[list[Path]] = None
) -> Path:
    """Find which root directory a file belongs to."""
    for root in [vault_root] + (extra_paths or []):
        try:
            path.relative_to(root)
            return root
        except ValueError:
            continue
    return vault_root


def build_frontmatter(
    path: Path,
    vault_root: Path,
    folder_defaults: Optional[dict] = None,
    file_overrides: Optional[dict] = None,
    extra_paths: Optional[list[Path]] = None,
) -> dict:
    """Build frontmatter dict for a file."""
    root = _resolve_root(path, vault_root, extra_paths)
    rel = path.relative_to(root)
    folder = rel.parts[0] if len(rel.parts) > 1 else ""
    rel_str = str(rel)

    # Base values
    folder_lower = folder.lower()
    fm = {
        "status": "active",
        "tags": [],
        "type": FOLDER_TYPE_MAP.get(folder_lower, "note"),
        "created": _get_created_date(path),
        "modified": _get_modified_date(path),
    }

    # Apply folder defaults
    if folder_defaults and folder in folder_defaults:
        fm.update(folder_defaults[folder])

    # Apply file overrides (highest priority)
    if file_overrides and rel_str in file_overrides:
        fm.update(file_overrides[rel_str])

    return fm


def apply_frontmatter(path: Path, fm: dict) -> None:
    """Prepend YAML frontmatter to a file. Skips if file already has frontmatter."""
    if has_frontmatter(path):
        return
    content = path.read_text(encoding="utf-8")
    # Preserve canonical field ordering after .update() may have shuffled keys
    ordered = {}
    for key in ("status", "tags", "type", "created", "modified"):
        if key in fm:
            ordered[key] = fm[key]
    # Include any extra keys from overrides
    for key in fm:
        if key not in ordered:
            ordered[key] = fm[key]
    fm_str = yaml.dump(ordered, default_flow_style=None, sort_keys=False)
    path.write_text(f"---\n{fm_str}---\n\n{content}", encoding="utf-8")


def vault_tag(
    vault_path: str,
    apply: bool = False,
    folder_defaults: Optional[dict] = None,
    file_overrides: Optional[dict] = None,
    extra_paths: Optional[list[str]] = None,
) -> dict:
    """Two-phase vault tagger.

    Phase 1 (apply=False): Returns preview of untagged files grouped by folder.
    Phase 2 (apply=True): Applies frontmatter with optional overrides.
    """
    vault_root = Path(vault_path)
    extra = [Path(p) for p in (extra_paths or [])]
    all_roots = [vault_root] + extra
    untagged = find_untagged(vault_root, extra)

    if not apply:
        # Preview phase — group by root/folder
        by_source: dict[str, dict[str, list[str]]] = {}
        for f in untagged:
            root = _resolve_root(f, vault_root, extra)
            root_label = root.name
            rel = f.relative_to(root)
            folder = rel.parts[0] if len(rel.parts) > 1 else "(root)"
            if root_label not in by_source:
                by_source[root_label] = {}
            by_source[root_label].setdefault(folder, []).append(rel.name)

        return {
            "mode": "preview",
            "total": len(untagged),
            "by_source": {
                src: {k: sorted(v) for k, v in sorted(folders.items())}
                for src, folders in sorted(by_source.items())
            },
        }

    # Apply phase
    tagged_count = 0
    for path in untagged:
        fm = build_frontmatter(path, vault_root, folder_defaults, file_overrides, extra)
        apply_frontmatter(path, fm)
        tagged_count += 1

    return {
        "mode": "applied",
        "tagged": tagged_count,
        "message": f"Tagged {tagged_count} files. Run vault_index to update search index.",
    }
