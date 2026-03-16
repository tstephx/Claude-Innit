---
status: active
tags: []
type: note
created: '2026-03-16'
modified: '2026-03-16'
---

# Vault Module Detection & Frontmatter Tagger

<!-- project: claude-innit -->

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix module detection for extra-path files (8,128 currently "unassigned"), tag the ~60 vault files missing frontmatter via a two-phase MCP tool, and add a status filter to vault_search.

**Architecture:** Four changes: (1) Extend `_detect_module()` with path-prefixed module names and extra-path framework dir exclusions, (2) expand the default exclusion list for build artifacts, (3) add a `vault_tag` MCP tool with two-phase preview/apply flow and folder-batch overrides, (4) add optional `status` filter to `vault_search`. All backward-compatible.

**Tech Stack:** Python 3.12, pathlib, PyYAML, pytest, SQLite, macOS stat (st_birthtime)

---

## Interview Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Status inference for extra paths | Out of scope | Module detection alone fixes 88% of the problem |
| Extra-path framework dirs | Exclusion list (`ref`, `scripts`, `docs`, `shared`) | Organizational dirs aren't projects |
| Module name collisions | Path-prefixed (`lab/scripts`, `projects/scripts`) | Unique keys, flat dict preserved |
| Default status for tagger | Interactive (group by folder, batch decide) | 60 files = 2 min with batch UX |
| Context in interactive mode | Group by folder, batch decide | Best practice for bulk categorization |
| Stats display | Flat dict with prefixed keys | Consistent with prefix collision solution |
| Properties to add | status, tags, type, created, modified | Full Obsidian property coverage |
| Type mapping | Explicit dict with 'note' fallback | Avoids awkward auto-generated types |
| Created date source | macOS `st_birthtime` | Accurate on APFS, matches this setup |
| Search output | Add `status` filter parameter | DB-level filtering, keeps results compact |
| Migration strategy | Flat with prefix (non-breaking) | No API shape change |
| Exclusion list | Expand to cover Python/JS artifacts | Reduce noise from build dirs |
| Script vs MCP tool | MCP tool (`vault_tag`) | Claude can tag new files during sessions |
| Dataview compat | No existing queries to conflict | Free to pick property names |
| MCP UX | Two-phase: preview then apply | Claude shows list, user decides, then apply |
| Override granularity | Both folder-level and file-level | Folder defaults + per-file exceptions |
| Auto re-index after tag | No — separate manual step | Keep tools independent |

---

## Task 1: Extend `_detect_module` for extra paths with prefixed names

**Files:**
- Modify: `claude_innit/tools/vault.py:22-46` (`_FRAMEWORK_DIRS`, `_detect_module`)
- Test: `tests/test_vault.py` (TestParsingHelpers class)

### Current behavior

`_detect_module(file_path, vault_root)` returns `None` for any file not under `vault_root`. All 8,128 extra-path files show as "unassigned".

### Target behavior

`_detect_module(file_path, vault_root, extra_paths=None)` returns **path-prefixed module names** for extra-path files, using the extra path's directory name as prefix and the project folder as module:

```
~/Dev/_Lab/periodical-parser/CLAUDE.md    → "lab/periodical-parser"
~/Dev/_Projects/book-mcp-server/server.py → "projects/book-mcp-server"
~/Dev/_Projects/Claude-Innit/docs/x.md    → "projects/claude-innit"
~/Dev/_Lab/ref/guides-index.md            → None (framework dir)
```

Extra-path framework dirs (organizational, not projects): `ref`, `scripts`, `docs`, `shared`.
Vault framework dirs remain: `daily`, `inbox`, `archive`, `claude-memory`.
Vault modules are NOT prefixed (preserves existing behavior): `Projects/x.md` → `"projects"`.

**Step 1: Write failing tests**

Add these tests to `TestParsingHelpers` in `tests/test_vault.py`:

```python
def test_detect_module_extra_path_prefixed(self, tmp_path):
    """Files under extra_paths get path-prefixed module names."""
    vault = tmp_path / "vault"
    vault.mkdir()
    lab = tmp_path / "lab"
    proj = lab / "my-project"
    proj.mkdir(parents=True)

    result = _detect_module(
        str(proj / "README.md"), str(vault), extra_paths=[str(lab)]
    )
    assert result == "lab/my-project"

def test_detect_module_extra_path_nested(self, tmp_path):
    """Nested files under extra_paths still use top-level project folder."""
    vault = tmp_path / "vault"
    vault.mkdir()
    lab = tmp_path / "lab"
    deep = lab / "my-project" / "src" / "lib"
    deep.mkdir(parents=True)

    result = _detect_module(
        str(deep / "utils.py"), str(vault), extra_paths=[str(lab)]
    )
    assert result == "lab/my-project"

def test_detect_module_extra_path_root_file(self, tmp_path):
    """Files directly in the extra path root (no project folder) return None."""
    vault = tmp_path / "vault"
    vault.mkdir()
    lab = tmp_path / "lab"
    lab.mkdir()

    result = _detect_module(
        str(lab / "stray.md"), str(vault), extra_paths=[str(lab)]
    )
    assert result is None

def test_detect_module_extra_path_lowercases(self, tmp_path):
    """Extra path modules are lowercased like vault modules."""
    vault = tmp_path / "vault"
    vault.mkdir()
    projects = tmp_path / "projects"
    proj = projects / "My-Project"
    proj.mkdir(parents=True)

    result = _detect_module(
        str(proj / "README.md"), str(vault), extra_paths=[str(projects)]
    )
    assert result == "projects/my-project"

def test_detect_module_extra_path_framework_dirs(self, tmp_path):
    """Extra-path framework dirs (ref, scripts, docs, shared) return None."""
    vault = tmp_path / "vault"
    vault.mkdir()
    lab = tmp_path / "lab"
    for d in ["ref", "scripts", "docs", "shared"]:
        (lab / d).mkdir(parents=True)

    for d in ["ref", "scripts", "docs", "shared"]:
        result = _detect_module(
            str(lab / d / "file.md"), str(vault), extra_paths=[str(lab)]
        )
        assert result is None, f"Expected None for framework dir '{d}'"

def test_detect_module_multiple_extra_paths(self, tmp_path):
    """Module detected from whichever extra path the file is under."""
    vault = tmp_path / "vault"
    vault.mkdir()
    lab = tmp_path / "lab"
    projects = tmp_path / "projects"
    (lab / "tool-a").mkdir(parents=True)
    (projects / "tool-b").mkdir(parents=True)

    assert _detect_module(
        str(lab / "tool-a" / "x.md"), str(vault),
        extra_paths=[str(lab), str(projects)]
    ) == "lab/tool-a"
    assert _detect_module(
        str(projects / "tool-b" / "y.md"), str(vault),
        extra_paths=[str(lab), str(projects)]
    ) == "projects/tool-b"

def test_detect_module_no_extra_paths_backward_compat(self, tmp_path):
    """Without extra_paths, existing behavior is unchanged."""
    vault = tmp_path / "vault"
    vault.mkdir()
    result = _detect_module(str(tmp_path / "other" / "file.md"), str(vault))
    assert result is None

def test_detect_module_vault_not_prefixed(self, tmp_path):
    """Vault modules are NOT prefixed — preserves existing behavior."""
    vault = tmp_path / "vault"
    vault.mkdir()
    result = _detect_module(
        str(vault / "Projects" / "readme.md"), str(vault),
        extra_paths=[str(tmp_path / "lab")]
    )
    assert result == "projects"  # no prefix for vault files
```

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_vault.py::TestParsingHelpers -v -k "extra_path"
```

Expected: FAIL — `_detect_module()` doesn't accept `extra_paths` parameter.

**Step 3: Implement the fix**

Replace `_FRAMEWORK_DIRS` and `_detect_module` in `claude_innit/tools/vault.py:22-46`:

```python
_FRAMEWORK_DIRS = frozenset({"daily", "inbox", "archive", "claude-memory"})
_EXTRA_FRAMEWORK_DIRS = frozenset({"ref", "scripts", "docs", "shared"})


def _detect_module(
    file_path: str,
    vault_root: str,
    extra_paths: Optional[list[str]] = None,
) -> Optional[str]:
    """Detect which module a file belongs to from its path.

    Vault files: uses first directory under vault_root as module (no prefix).
    Extra-path files: uses path-prefixed module name (e.g. "lab/my-project").
    Framework dirs return None in both contexts.
    """
    # Try vault root first (no prefix)
    try:
        rel = Path(file_path).relative_to(vault_root)
        parts = rel.parts
        if len(parts) < 2:
            return None
        first_dir = parts[0]
        lowered = first_dir.lower()
        if lowered in _FRAMEWORK_DIRS:
            return None
        return lowered
    except ValueError:
        pass

    # Try each extra path (with prefix)
    for ep in (extra_paths or []):
        try:
            rel = Path(file_path).relative_to(ep)
            parts = rel.parts
            if len(parts) < 2:
                return None
            project_dir = parts[0].lower()
            if project_dir in _EXTRA_FRAMEWORK_DIRS:
                return None
            prefix = Path(ep).name.lower()
            return f"{prefix}/{project_dir}"
        except ValueError:
            continue

    return None
```

**Step 4: Update the indexer call site**

In `claude_innit/tools/vault.py:118`:

```python
# Before:
module = _detect_module(file_path_str, str(self.vault_root))

# After:
module = _detect_module(
    file_path_str,
    str(self.vault_root),
    extra_paths=[str(p) for p in self.extra_paths],
)
```

**Step 5: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_vault.py::TestParsingHelpers -v
```

Expected: ALL PASS

**Step 6: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: 268+ passed, 0 failed

**Step 7: Commit**

```bash
git add claude_innit/tools/vault.py tests/test_vault.py
git commit -m "feat: path-prefixed module detection for extra-path files"
```

---

## Task 2: Expand default exclusion list

**Files:**
- Modify: `claude_innit/tools/vault.py:62-72` (VaultIndexer `__init__`)
- Test: `tests/test_vault.py`

Add common Python/JS build artifact dirs to prevent indexing changelogs, READMEs from build output, and cache files.

**Step 1: Write failing tests**

```python
def test_indexer_excludes_site_packages(self, tmp_path, db):
    """site-packages inside any venv variant is excluded."""
    vault = tmp_path / "vault"
    vault.mkdir()
    extra = tmp_path / "extra"
    sp = extra / "myproject" / "mcpenv" / "lib" / "site-packages" / "requests"
    sp.mkdir(parents=True)
    (sp / "README.md").write_text("# Requests library\n")

    indexer = VaultIndexer(db, str(vault), extra_paths=[str(extra)])
    assert indexer._should_exclude(sp / "README.md")

def test_indexer_excludes_build_artifacts(self, tmp_path, db):
    """Common build/cache directories are excluded."""
    vault = tmp_path / "vault"
    vault.mkdir()
    extra = tmp_path / "extra"

    for artifact_dir in [".hypothesis", "htmlcov", "dist", "build",
                         ".mypy_cache", ".ruff_cache", ".tox"]:
        d = extra / "proj" / artifact_dir
        d.mkdir(parents=True)
        (d / "README.md").write_text("artifact\n")

        indexer = VaultIndexer(db, str(vault), extra_paths=[str(extra)])
        assert indexer._should_exclude(d / "README.md"), \
            f"Expected {artifact_dir}/ to be excluded"
```

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_vault.py -v -k "excludes"
```

Expected: FAIL for new artifact dirs.

**Step 3: Expand exclusion list**

```python
self.exclude_patterns = exclude_patterns or [
    "/node_modules/",
    "/.git/",
    "/.obsidian/",
    ".DS_Store",
    "/__pycache__/",
    "/.pytest_cache/",
    "/.venv/",
    "/venv/",
    "/site-packages/",
    "/.hypothesis/",
    "/htmlcov/",
    "/dist/",
    "/build/",
    "/.mypy_cache/",
    "/.ruff_cache/",
    "/.tox/",
    "/.eggs/",
    ".egg-info/",
    "/.brain/tests/",
]
```

**Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_vault.py -v -k "excludes"
```

Expected: PASS

**Step 5: Commit**

```bash
git add claude_innit/tools/vault.py tests/test_vault.py
git commit -m "fix: expand default exclusion list for build artifacts"
```

---

## Task 3: `vault_tag` MCP tool (two-phase preview/apply)

**Files:**
- Create: `claude_innit/tools/tag.py`
- Modify: `claude_innit/tools/__init__.py` (register)
- Modify: `claude_innit/server.py` (Tool definition + call_tool dispatch)
- Test: `tests/test_tag.py`

### Design

**Two-phase flow:**
1. `vault_tag()` → preview: returns untagged files grouped by folder with file count
2. `vault_tag(apply=True, folder_defaults={...}, file_overrides={...})` → applies frontmatter

**Frontmatter template:**
```yaml
---
status: active
tags: []
type: note
created: 2026-03-13
modified: 2026-03-13
---
```

**Folder-to-type mapping dict:**
```python
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
# Unknown folders → "note"
```

**Created date:** `os.stat(path).st_birthtime` (macOS APFS birth time)
**Modified date:** `path.stat().st_mtime`

**Override structure:**
```python
# folder_defaults: set status/type for all files in a folder
folder_defaults = {"Projects": {"status": "archived"}}

# file_overrides: override specific files (takes precedence)
file_overrides = {"Projects/active-thing.md": {"status": "active"}}
```

**Step 1: Write failing tests**

Create `tests/test_tag.py`:

```python
"""Tests for vault_tag MCP tool."""

import os
from datetime import datetime
from pathlib import Path

import pytest

from claude_innit.tools.tag import (
    find_untagged,
    build_frontmatter,
    apply_frontmatter,
    vault_tag,
    FOLDER_TYPE_MAP,
    has_frontmatter,
)


@pytest.fixture
def vault_dir(tmp_path):
    """Create test vault with mixed frontmatter state."""
    vault = tmp_path / "vault"
    vault.mkdir()

    tagged = vault / "Projects"
    tagged.mkdir()
    (tagged / "tagged.md").write_text(
        "---\nstatus: active\ntags: [core]\n---\n\n# Tagged\nContent.\n"
    )
    (tagged / "untagged.md").write_text("# Untagged Note\nSome content.\n")

    guides = vault / "Guides"
    guides.mkdir()
    (guides / "plain.md").write_text("Just plain text.\n")

    (guides / "broken.md").write_text("---\nstatus: draft\nNo closing.\n")

    return vault


class TestHasFrontmatter:
    def test_with_frontmatter(self, vault_dir):
        assert has_frontmatter(vault_dir / "Projects" / "tagged.md") is True

    def test_without_frontmatter(self, vault_dir):
        assert has_frontmatter(vault_dir / "Projects" / "untagged.md") is False

    def test_malformed_frontmatter(self, vault_dir):
        # Line 1 is "---" so treated as having frontmatter
        assert has_frontmatter(vault_dir / "Guides" / "broken.md") is True


class TestFindUntagged:
    def test_finds_untagged_files(self, vault_dir):
        result = find_untagged(vault_dir)
        names = [f.name for f in result]
        assert "untagged.md" in names
        assert "plain.md" in names
        assert "tagged.md" not in names
        assert "broken.md" not in names

    def test_skips_hidden_dirs(self, vault_dir):
        hidden = vault_dir / ".hidden"
        hidden.mkdir()
        (hidden / "secret.md").write_text("no frontmatter\n")
        result = find_untagged(vault_dir)
        names = [f.name for f in result]
        assert "secret.md" not in names


class TestBuildFrontmatter:
    def test_default_frontmatter(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = build_frontmatter(path, vault_dir)
        assert fm["status"] == "active"
        assert fm["tags"] == []
        assert fm["type"] == "project"  # Projects → project
        assert "created" in fm
        assert "modified" in fm

    def test_type_from_folder_map(self, vault_dir):
        path = vault_dir / "Guides" / "plain.md"
        fm = build_frontmatter(path, vault_dir)
        assert fm["type"] == "guide"

    def test_unknown_folder_type_is_note(self, tmp_path):
        vault = tmp_path / "vault"
        weird = vault / "RandomFolder"
        weird.mkdir(parents=True)
        (weird / "file.md").write_text("content\n")
        fm = build_frontmatter(weird / "file.md", vault)
        assert fm["type"] == "note"

    def test_folder_defaults_override(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = build_frontmatter(
            path, vault_dir,
            folder_defaults={"Projects": {"status": "archived"}}
        )
        assert fm["status"] == "archived"

    def test_file_overrides_beat_folder_defaults(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = build_frontmatter(
            path, vault_dir,
            folder_defaults={"Projects": {"status": "archived"}},
            file_overrides={"Projects/untagged.md": {"status": "active"}}
        )
        assert fm["status"] == "active"


class TestApplyFrontmatter:
    def test_prepends_frontmatter(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = {"status": "active", "tags": [], "type": "project",
              "created": "2026-03-13", "modified": "2026-03-13"}
        apply_frontmatter(path, fm)
        content = path.read_text()
        assert content.startswith("---\n")
        assert "status: active" in content
        assert "# Untagged Note" in content

    def test_idempotent(self, vault_dir):
        path = vault_dir / "Projects" / "untagged.md"
        fm = {"status": "active", "tags": [], "type": "project",
              "created": "2026-03-13", "modified": "2026-03-13"}
        apply_frontmatter(path, fm)
        apply_frontmatter(path, fm)  # second call on already-tagged file
        content = path.read_text()
        assert content.count("---") == 2  # exactly one frontmatter block (open + close)

    def test_preserves_existing(self, vault_dir):
        path = vault_dir / "Projects" / "tagged.md"
        before = path.read_text()
        # Shouldn't be called on tagged files, but if it is, no damage
        assert has_frontmatter(path) is True


class TestVaultTagPreview:
    def test_preview_returns_grouped_files(self, vault_dir):
        result = vault_tag(str(vault_dir))
        assert result["mode"] == "preview"
        assert result["total"] == 2
        assert "Projects" in result["by_folder"]
        assert "Guides" in result["by_folder"]

    def test_preview_no_untagged(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        proj = vault / "Projects"
        proj.mkdir()
        (proj / "done.md").write_text("---\nstatus: active\n---\nContent.\n")
        result = vault_tag(str(vault))
        assert result["total"] == 0


class TestVaultTagApply:
    def test_apply_tags_files(self, vault_dir):
        result = vault_tag(str(vault_dir), apply=True)
        assert result["mode"] == "applied"
        assert result["tagged"] == 2
        # Verify files got frontmatter
        content = (vault_dir / "Projects" / "untagged.md").read_text()
        assert content.startswith("---\n")

    def test_apply_with_folder_defaults(self, vault_dir):
        result = vault_tag(
            str(vault_dir), apply=True,
            folder_defaults={"Projects": {"status": "archived"}}
        )
        content = (vault_dir / "Projects" / "untagged.md").read_text()
        assert "status: archived" in content

    def test_apply_with_file_overrides(self, vault_dir):
        result = vault_tag(
            str(vault_dir), apply=True,
            folder_defaults={"Projects": {"status": "archived"}},
            file_overrides={"Projects/untagged.md": {"status": "draft"}}
        )
        content = (vault_dir / "Projects" / "untagged.md").read_text()
        assert "status: draft" in content

    def test_empty_vault_directory(self, tmp_path):
        """Empty vault with zero .md files returns preview with total=0."""
        vault = tmp_path / "empty-vault"
        vault.mkdir()
        result = vault_tag(str(vault))
        assert result["mode"] == "preview"
        assert result["total"] == 0
        assert result["by_folder"] == {}
```

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_tag.py -v
```

Expected: FAIL — `claude_innit/tools/tag.py` doesn't exist.

**Step 3: Implement `claude_innit/tools/tag.py`**

```python
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


def find_untagged(vault_root: Path) -> list[Path]:
    """Find .md files under vault_root that lack frontmatter."""
    untagged = []
    for md_file in sorted(vault_root.rglob("*.md")):
        if not md_file.is_file():
            continue
        parts_lower = [p.lower() for p in md_file.relative_to(vault_root).parts]
        if any(p.startswith(".") for p in parts_lower):
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
        "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
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
```

**Step 4: Register in `claude_innit/tools/__init__.py`**

Add import and register in `__all__`:
```python
from claude_innit.tools.tag import vault_tag

# In __all__ list, add:
__all__ = [..., "vault_tag"]
```

**Step 5: Add Tool definition and dispatch in `claude_innit/server.py`**

Tool definition in `_define_tools()`:
```python
Tool(
    name="vault_tag",
    description=(
        "Tag vault .md files (vault root only, not extra index paths) missing YAML frontmatter. "
        "Phase 1: call without apply to preview untagged files grouped by folder. "
        "Phase 2: call with apply=true and optional folder_defaults/file_overrides to write frontmatter. "
        "Run vault_index after to update the search index."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "vault_path": {"type": "string", "description": "Path to vault root (default: VAULT_ROOT env var)"},
            "apply": {"type": "boolean", "description": "True to write frontmatter, false for preview (default: false)"},
            "folder_defaults": {
                "type": "object",
                "description": "Per-folder default overrides, e.g. {\"Projects\": {\"status\": \"archived\"}}",
                "additionalProperties": {"type": "object"},
            },
            "file_overrides": {
                "type": "object",
                "description": "Per-file overrides (relative path), e.g. {\"Projects/old.md\": {\"status\": \"archived\"}}",
                "additionalProperties": {"type": "object"},
            },
        },
    },
),
```

Dispatch in `call_tool()`:
```python
elif name == "vault_tag":
    vault_path = arguments.get("vault_path") or os.environ.get("VAULT_ROOT", "")
    if not vault_path:
        return [TextContent(type="text", text="Error: vault_path required (set VAULT_ROOT or pass vault_path)")]
    return vault_tag(
        vault_path,
        apply=arguments.get("apply", False),
        folder_defaults=arguments.get("folder_defaults"),
        file_overrides=arguments.get("file_overrides"),
    )
```

**Step 6: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_tag.py -v
```

Expected: ALL PASS

**Step 7: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: All pass

**Step 8: Commit**

```bash
git add claude_innit/tools/tag.py tests/test_tag.py claude_innit/tools/__init__.py claude_innit/server.py
git commit -m "feat: add vault_tag MCP tool with two-phase preview/apply flow"
```

---

## Task 4: Add status filter to `vault_search`

**Files:**
- Modify: `claude_innit/tools/vault.py:175-223` (`vault_search`)
- Modify: `claude_innit/db/database.py` (`vault_fts_search`)
- Modify: `claude_innit/server.py` (update vault_search tool schema)
- Test: `tests/test_vault.py`

### Design

Add optional `status` parameter to `vault_search`. When provided, results are filtered at the SQL level via `json_extract(frontmatter, '$.status')`. Default `None` returns all results (backward-compatible).

**Scope:** The status filter applies to **both** FTS and semantic search legs of hybrid mode. For FTS, filter in the SQL query. For semantic, post-filter results by joining back to `vault_files.frontmatter`. This ensures `method="auto"` (hybrid) doesn't leak unfiltered semantic results past the status gate.

**Step 1: Write failing tests**

```python
class TestVaultSearchStatusFilter:
    def test_filter_by_status_active(self, db, vault_dir):
        """vault_search with status='active' excludes non-active files."""
        indexer = VaultIndexer(db, str(vault_dir))
        indexer.index()
        # "API Migration" has status: draft, "Conflict Resolution" has status: ready
        results = vault_search(db, "migration", status="draft")
        statuses = []
        for r in results:
            f = db.get_vault_file(r["file_path"])
            if f:
                import json
                fm = json.loads(f["frontmatter"]) if f["frontmatter"] else {}
                statuses.append(fm.get("status"))
        assert all(s == "draft" for s in statuses if s is not None)

    def test_no_status_filter_returns_all(self, db, vault_dir):
        """vault_search without status returns all matching files."""
        indexer = VaultIndexer(db, str(vault_dir))
        indexer.index()
        results = vault_search(db, "context")
        assert len(results) > 0  # should find files regardless of status
```

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_vault.py -v -k "status_filter"
```

Expected: FAIL — `vault_search` doesn't accept `status` parameter.

**Step 3: Implement**

In `vault_search()` signature, add `status: Optional[str] = None`. Pass it through to `_fts_search` and the semantic search path. In `_fts_search`, add a SQL filter:

```python
if status:
    raw = [r for r in raw if _matches_status(db, r, status)]
```

Or better, filter at the DB level in `vault_fts_search`:

```python
def vault_fts_search(self, query, limit=20, module=None, status=None):
    # ... existing query ...
    if status:
        # Post-filter by frontmatter status
        results = [r for r in results
                   if json.loads(r["frontmatter"] or "{}").get("status") == status]
```

**Step 4: Update tool schema in server.py**

Add to vault_search inputSchema properties:
```python
"status": {"type": "string", "description": "Filter by frontmatter status (e.g. 'active', 'archived')"},
```

**Step 5: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_vault.py -v -k "status_filter"
```

Expected: PASS

**Step 6: Commit**

```bash
git add claude_innit/tools/vault.py claude_innit/db/database.py claude_innit/server.py tests/test_vault.py
git commit -m "feat: add status filter parameter to vault_search"
```

---

## Task 5: Integration validation

**Files:**
- No new files

**Step 1: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: All tests pass (290+ total with new tests).

**Step 2: Restart MCP server and re-index**

```
vault_index(force=True)
vault_stats()
```

Expected `by_module`:
- `unassigned` drops from 8,114 to near-zero
- New prefixed modules: `lab/periodical-parser`, `projects/book-mcp-server`, etc.
- Vault modules unchanged: `projects`, `books`, `guides`, etc.

**Step 3: Preview untagged vault files**

```
vault_tag()  # preview mode
```

Review grouped output. Discuss with user which folders should have non-default status.

**Step 4: Apply tags with overrides**

```
vault_tag(apply=True, folder_defaults={...}, file_overrides={...})
```

**Step 5: Re-index and verify**

```
vault_index(force=True)
vault_stats()
```

Expected `by_status`:
- `unknown` drops by ~60
- `active` increases correspondingly

**Step 6: Test search with status filter**

```
vault_search(query="migration", status="active")
```

Verify only active results returned.

**Step 7: Update CLAUDE.md**

```bash
git commit -m "docs: update CLAUDE.md for vault_tag tool, prefixed modules, status filter"
```

---

## Summary of impact

| Metric | Before | After |
|--------|--------|-------|
| `by_module` unassigned | 8,114 | ~0 |
| `by_status` unknown | 8,286 | ~8,226 |
| Vault files with frontmatter | ~1,100/1,161 | ~1,161/1,161 |
| Search result module labels | Missing for 88% | Present for all |
| MCP tools | 14 | 15 (+ vault_tag) |
| vault_search filters | scope, method | scope, method, status |
| Build artifact noise in index | Present | Excluded |

## Future considerations (not in scope)

- **Status inference for extra-path files**: Could derive from git activity (recent commits → active)
- **Tag inference from content**: Per Zettelkasten research, connections > categories — defer
- **Obsidian Bases views**: Once properties are consistent, create `.base` files for filtered views
- **Dataview queries**: Properties are now Dataview-compatible; queries can be added later
- **.vaultignore file**: If exclusion list grows unwieldy, extract to config file
