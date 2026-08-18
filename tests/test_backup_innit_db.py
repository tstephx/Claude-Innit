import os
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_innit_db.sh"


def _make_source_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


def _run_backup(env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_script_is_executable():
    assert SCRIPT.exists()
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR


def test_backup_creates_verified_snapshot(tmp_path):
    src = tmp_path / "innit.db"
    _make_source_db(src)
    backup_dir = tmp_path / "backups"
    log_dir = tmp_path / "logs"

    result = _run_backup(
        {
            "INNIT_DB_SRC": str(src),
            "INNIT_BACKUP_DIR": str(backup_dir),
            "INNIT_BACKUP_LOG_DIR": str(log_dir),
            "INNIT_BACKUP_SKIP_REMOTE": "1",
            "INNIT_BACKUP_RETENTION": "3",
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    snapshots = list(backup_dir.glob("innit-*.db"))
    assert len(snapshots) == 1

    conn = sqlite3.connect(snapshots[0])
    row = conn.execute("SELECT val FROM t WHERE id = 1").fetchone()
    conn.close()
    assert row == ("hello",)


def test_backup_fails_when_source_missing(tmp_path):
    result = _run_backup(
        {
            "INNIT_DB_SRC": str(tmp_path / "does-not-exist.db"),
            "INNIT_BACKUP_DIR": str(tmp_path / "backups"),
            "INNIT_BACKUP_LOG_DIR": str(tmp_path / "logs"),
            "INNIT_BACKUP_SKIP_REMOTE": "1",
        }
    )
    assert result.returncode != 0
    assert "not found" in (result.stdout + result.stderr)


def test_backup_prunes_old_snapshots(tmp_path):
    src = tmp_path / "innit.db"
    _make_source_db(src)
    backup_dir = tmp_path / "backups"
    log_dir = tmp_path / "logs"
    backup_dir.mkdir(parents=True)

    for i in range(3):
        fake = backup_dir / f"innit-2020010{i}-000000.db"
        _make_source_db(fake)
        old_time = time.time() - (1000 - i * 10)
        os.utime(fake, (old_time, old_time))

    result = _run_backup(
        {
            "INNIT_DB_SRC": str(src),
            "INNIT_BACKUP_DIR": str(backup_dir),
            "INNIT_BACKUP_LOG_DIR": str(log_dir),
            "INNIT_BACKUP_SKIP_REMOTE": "1",
            "INNIT_BACKUP_RETENTION": "2",
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    remaining = sorted(backup_dir.glob("innit-*.db"))
    assert len(remaining) == 2
