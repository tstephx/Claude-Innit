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


def find_untagged(vault_root: Path) -> list[Path]:
    """Find .md files under vault_root that lack frontmatter."""
    untagged = []
    for md_file in sorted(vault_root.rglob("*.md")):
        if not md_file.is_file():
            continue
        parts_lower = [p.lower() for p in md_file.relative_to(vault_root).parts]
        if any(p.startswith(".") or p in _SKIP_DIRS for p in parts_lower):
            continue
        if ".egg-info" in str(md_file):
            continue
        if not has_frontmatter(md_file):
            untagged.append(md_file)
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


def build_frontmatter(
    path: Path,
    vault_root: Path,
    folder_defaults: Optional[dict] = None,
    file_overrides: Optional[dict] = None,
) -> dict:
    """Build frontmatter dict for a file."""
    rel = path.relative_to(vault_root)
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
) -> dict:
    """Two-phase vault tagger.

    Phase 1 (apply=False): Returns preview of untagged files grouped by folder.
    Phase 2 (apply=True): Applies frontmatter with optional overrides.
    """
    vault_root = Path(vault_path)
    untagged = find_untagged(vault_root)

    if not apply:
        # Preview phase
        by_folder = defaultdict(list)
        for f in untagged:
            rel = f.relative_to(vault_root)
            folder = rel.parts[0] if len(rel.parts) > 1 else "(root)"
            by_folder[folder].append(rel.name)

        return {
            "mode": "preview",
            "total": len(untagged),
            "by_folder": {k: sorted(v) for k, v in sorted(by_folder.items())},
        }

    # Apply phase
    tagged_count = 0
    for path in untagged:
        fm = build_frontmatter(path, vault_root, folder_defaults, file_overrides)
        apply_frontmatter(path, fm)
        tagged_count += 1

    return {
        "mode": "applied",
        "tagged": tagged_count,
        "message": f"Tagged {tagged_count} files. Run vault_index to update search index.",
    }
