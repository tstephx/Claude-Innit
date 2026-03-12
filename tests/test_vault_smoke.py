"""Smoke tests for vault MCP tools.

Part 1 of the Vault Tools Manual Test Plan.
Verifies every vault tool returns a non-error response through the MCP dispatch layer.

Run: pytest tests/test_vault_smoke.py -v
"""

import json

import pytest

from claude_innit.server import create_server


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_dir(tmp_path):
    """Realistic vault with modules, inbox, daily, and frontmatter."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # Module: behavioral-studio
    stories = vault / "behavioral-studio" / "Stories"
    stories.mkdir(parents=True)
    (stories / "API Migration.md").write_text(
        "---\nstatus: draft\nsignals:\n  - Scope\ntags:\n  - core\n---\n\n"
        "# API Migration\n\n## Context\nLed a cross-team API migration affecting 50 services.\n\n"
        "## Actions\nDesigned the migration strategy and coordinated rollout.\n\n"
        "## Results\nCompleted migration in 3 months with zero downtime.\n"
    )
    (stories / "Conflict Resolution.md").write_text(
        "---\nstatus: ready\nsignals:\n  - Conflict-Resolution\n---\n\n"
        "# Conflict Resolution\n\n## Context\nDisagreed with PM on feature priority.\n\n"
        "## Actions\nScheduled a 1:1 to discuss data behind each option.\n\n"
        "## Results\nAligned on a data-driven approach.\n"
    )

    # Module: portfolio
    portfolio = vault / "Portfolio"
    portfolio.mkdir()
    (portfolio / "DSP Redesign.md").write_text(
        "---\nstatus: ready\n---\n\n# DSP Redesign\n\nRedesigned the DSP platform UX.\n"
    )

    # Inbox
    inbox = vault / "Inbox"
    inbox.mkdir()
    (inbox / "capture.md").write_text(
        "---\ntype: capture\n---\nQuick thought about stakeholders.\n"
    )

    # Daily
    daily = vault / "Daily"
    daily.mkdir()
    (daily / "2026-03-09.md").write_text(
        "---\ntype: daily\n---\n# Today\nWorked on stories.\n"
    )

    return vault


@pytest.fixture
def server(tmp_path, vault_dir):
    """MCP server with vault root configured."""
    return create_server(
        db_path=tmp_path / "test.db",
        memories_dir=tmp_path / "memories",
        vault_root=str(vault_dir),
    )


async def _call(server, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool and parse the JSON response."""
    result = await server.call_tool(tool_name, arguments)
    assert len(result) >= 1, f"{tool_name} returned empty result"
    return json.loads(result[0].text)


# ---------------------------------------------------------------------------
# Vault Indexing (1 tool, 3 scenarios)
# ---------------------------------------------------------------------------


class TestVaultIndexSmoke:
    """vault_index — indexes vault markdown files into the search database."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "desc, args",
        [
            ("default index", {}),
            ("force reindex", {"force": True}),
            ("custom vault_root", None),  # handled specially below
        ],
        ids=["default", "force", "custom-root"],
    )
    async def test_vault_index_responds(self, server, vault_dir, desc, args):
        if args is None:
            args = {"vault_root": str(vault_dir)}
        result = await _call(server, "vault_index", args)
        assert "error" not in result, f"vault_index({desc}) returned error: {result}"
        assert "indexed" in result or "updated" in result or "unchanged" in result


# ---------------------------------------------------------------------------
# Vault Search (1 tool, 4 scenarios)
# ---------------------------------------------------------------------------


class TestVaultSearchSmoke:
    """vault_search — hybrid FTS + semantic search over vault files."""

    @pytest.mark.asyncio
    async def _index_first(self, server):
        await _call(server, "vault_index", {})

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "desc, args",
        [
            ("default auto", {"query": "API migration"}),
            ("text method", {"query": "conflict", "method": "text"}),
            ("scope vault", {"query": "stakeholder", "scope": "vault"}),
            ("scope configs", {"query": "daily", "scope": "configs"}),
            ("with limit", {"query": "migration", "limit": 5}),
        ],
        ids=["auto", "text", "scope-vault", "scope-configs", "limit"],
    )
    async def test_vault_search_responds(self, server, desc, args):
        await self._index_first(server)
        result = await _call(server, "vault_search", args)
        assert "error" not in result, f"vault_search({desc}) returned error: {result}"
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Vault Related (1 tool, 2 scenarios)
# ---------------------------------------------------------------------------


class TestVaultRelatedSmoke:
    """vault_related — find notes similar to a given note."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "desc, note_suffix",
        [
            ("existing note", "behavioral-studio/Stories/API Migration.md"),
            ("with limit", "behavioral-studio/Stories/Conflict Resolution.md"),
        ],
        ids=["existing", "with-limit"],
    )
    async def test_vault_related_responds(self, server, vault_dir, desc, note_suffix):
        await _call(server, "vault_index", {})
        args = {"note_path": str(vault_dir / note_suffix)}
        if "limit" in desc:
            args["limit"] = 3
        result = await _call(server, "vault_related", args)
        assert "error" not in result, f"vault_related({desc}) returned error: {result}"
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Vault Stats (1 tool)
# ---------------------------------------------------------------------------


class TestVaultStatsSmoke:
    """vault_stats — vault health metrics."""

    @pytest.mark.asyncio
    async def test_vault_stats_responds(self, server):
        await _call(server, "vault_index", {})
        result = await _call(server, "vault_stats", {})
        assert "error" not in result, f"vault_stats returned error: {result}"
        assert "total_notes" in result


# ---------------------------------------------------------------------------
# Vault Rechunk (1 tool)
# ---------------------------------------------------------------------------


class TestVaultRechunkSmoke:
    """vault_rechunk — force re-chunk and re-embed all vault files."""

    @pytest.mark.asyncio
    async def test_vault_rechunk_responds(self, server):
        # Without embedding store, should return error gracefully
        result = await _call(server, "vault_rechunk", {})
        # Either succeeds or returns a structured error (not a crash)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Federated Search (1 tool, 3 scenarios)
# ---------------------------------------------------------------------------


class TestFederatedSearchSmoke:
    """federated_search — cross-source search with RRF fusion."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "desc, args",
        [
            ("default all sources", {"query": "API migration"}),
            ("vault only", {"query": "conflict", "sources": ["vault"]}),
            ("with limit", {"query": "stakeholder", "limit": 5}),
        ],
        ids=["all-sources", "vault-only", "limit"],
    )
    async def test_federated_search_responds(self, server, desc, args):
        await _call(server, "vault_index", {})
        result = await _call(server, "federated_search", args)
        assert "error" not in result, f"federated_search({desc}) error: {result}"
        assert "merged" in result


# ---------------------------------------------------------------------------
# Error paths and admin dispatch (coverage gaps)
# ---------------------------------------------------------------------------


class TestVaultIndexMissingVaultRoot:
    """vault_index without vault_root returns error, not crash."""

    @pytest.mark.asyncio
    async def test_returns_error_without_vault_root(self, tmp_path):
        server = create_server(
            db_path=tmp_path / "test.db",
            memories_dir=tmp_path / "memories",
            vault_root=None,
        )
        result = await _call(server, "vault_index", {})
        assert "error" in result


class TestAdminSyncDispatch:
    """admin_sync — dispatch through call_tool."""

    @pytest.mark.asyncio
    async def test_admin_sync_responds(self, tmp_path):
        memories = tmp_path / "memories"
        memories.mkdir()
        server = create_server(
            db_path=tmp_path / "test.db",
            memories_dir=memories,
        )
        result = await _call(server, "admin_sync", {})
        assert isinstance(result, dict)
        assert "error" not in result


class TestAdminCheckIntegrityDispatch:
    """admin_check_integrity — dispatch with auto_repair=False."""

    @pytest.mark.asyncio
    async def test_check_integrity_read_only(self, server):
        result = await _call(server, "admin_check_integrity", {"auto_repair": False})
        assert isinstance(result, dict)
        assert result["status"] == "healthy"
        assert result["repairs"] == []

    @pytest.mark.asyncio
    async def test_check_integrity_default_auto_repair(self, server):
        result = await _call(server, "admin_check_integrity", {})
        assert isinstance(result, dict)
        assert "status" in result
