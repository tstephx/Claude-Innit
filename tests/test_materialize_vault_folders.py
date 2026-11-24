"""The materializers write into the vault's system folder.

The vault keeps generated session and memory notes under `6_System/`. These
tests run both scripts as cron does (standalone, in a subprocess) against a
throwaway HOME and a fixture vault, so no real memory store or vault is read.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SESSION = (
    "---\ndate: 2026-01-02\nproject: demo\ntopics: [testing]\n---\n\nSession body.\n"
)
MEMORY = "---\nproject: demo\n---\n\nA fragment.\n"


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    store = home / "Dev/_Projects/Claude-Innit/data/memories"
    for sub in ("sessions", "personal", "project"):
        (store / sub).mkdir(parents=True)
    (store / "sessions" / "2026-01-02-101010.md").write_text(SESSION)
    (store / "personal" / "p1.md").write_text(MEMORY)
    (store / "project" / "m1.md").write_text(MEMORY)
    vault = tmp_path / "vault"
    vault.mkdir()
    return home, vault


def run(script, home, vault, *args):
    env = {"HOME": str(home), "VAULT_ROOT": str(vault), "PATH": os.environ["PATH"]}
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


def test_constants_name_the_system_folder(sandbox):
    home, vault = sandbox
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import materialize_common as m;print(m.VAULT_SESSIONS);print(m.VAULT_MEMORY)",
        ],
        env={"HOME": str(home), "VAULT_ROOT": str(vault), "PATH": os.environ["PATH"]},
        cwd=SCRIPTS_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert out == [
        str(vault / "6_System/Sessions"),
        str(vault / "6_System/Claude-Memory"),
    ]


def test_session_lands_in_6_system_sessions_only(sandbox):
    home, vault = sandbox
    (vault / "6_System/Sessions").mkdir(parents=True)
    proc = run("materialize_sessions.py", home, vault)
    assert proc.returncode == 0, proc.stderr
    notes = list((vault / "6_System/Sessions").glob("*.md"))
    assert len(notes) == 1
    assert "memory_id: sessions/2026-01-02-101010.md" in notes[0].read_text()
    assert sorted(p.name for p in vault.iterdir()) == ["6_System"]


def test_memories_land_in_6_system_claude_memory_only(sandbox):
    home, vault = sandbox
    (vault / "6_System/Claude-Memory").mkdir(parents=True)
    proc = run("materialize_memories.py", home, vault)
    assert proc.returncode == 0, proc.stderr
    names = sorted(p.name for p in (vault / "6_System/Claude-Memory").iterdir())
    assert names == ["demo-innit.md", "personal-innit.md"]
    assert sorted(p.name for p in vault.iterdir()) == ["6_System"]


@pytest.mark.parametrize(
    "script", ["materialize_sessions.py", "materialize_memories.py"]
)
def test_missing_destination_folder_fails_and_creates_nothing(sandbox, script):
    # The scripts never create a vault folder: a missing one means the vault is
    # absent or laid out differently, and the run must fail loudly.
    home, vault = sandbox
    proc = run(script, home, vault)
    assert proc.returncode != 0
    assert tree(vault) == []


@pytest.mark.parametrize(
    "script", ["materialize_sessions.py", "materialize_memories.py"]
)
def test_dry_run_writes_nothing(sandbox, script):
    home, vault = sandbox
    (vault / "6_System/Sessions").mkdir(parents=True)
    (vault / "6_System/Claude-Memory").mkdir(parents=True)
    proc = run(script, home, vault, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert tree(vault) == ["6_System", "6_System/Claude-Memory", "6_System/Sessions"]


def test_empty_store_creates_no_session_note(sandbox):
    home, vault = sandbox
    (
        home / "Dev/_Projects/Claude-Innit/data/memories/sessions/2026-01-02-101010.md"
    ).unlink()
    (vault / "6_System/Sessions").mkdir(parents=True)
    proc = run("materialize_sessions.py", home, vault)
    assert proc.returncode == 0, proc.stderr
    assert list((vault / "6_System/Sessions").iterdir()) == []
