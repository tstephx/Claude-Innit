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
            "INNIT_BACKUP_LOCAL_RETENTION": "2",
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    remaining = sorted(backup_dir.glob("innit-*.db"))
    assert len(remaining) == 2


def _remote_env(tmp_path, ensure_body):
    """Env for a remote-enabled run with rsync and the ssh-agent helper stubbed."""
    src = tmp_path / "innit.db"
    _make_source_db(src)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    rsync = bindir / "rsync"
    rsync.write_text(f'#!/bin/bash\necho "$@" > "{tmp_path}/rsync_args"\n')
    rsync.chmod(0o755)
    ssh = bindir / "ssh"
    ssh.write_text(f'#!/bin/bash\necho "$@" > "{tmp_path}/ssh_args"\n')
    ssh.chmod(0o755)
    ensure = tmp_path / "ensure-ssh-agent.sh"
    ensure.write_text(ensure_body)
    return {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "INNIT_DB_SRC": str(src),
        "INNIT_BACKUP_DIR": str(tmp_path / "backups"),
        "INNIT_BACKUP_LOG_DIR": str(tmp_path / "logs"),
        "INNIT_ENSURE_SSH_AGENT": str(ensure),
    }


def test_remote_sync_loads_agent_and_pins_identity(tmp_path):
    env = _remote_env(tmp_path, f'touch "{tmp_path}/ensure_ran"\n')

    result = _run_backup(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "ensure_ran").exists()
    args = (tmp_path / "rsync_args").read_text()
    assert "IdentityAgent=SSH_AUTH_SOCK" in args
    assert "IdentitiesOnly=yes" in args
    assert "id_ed25519_whatbox.pub" in args


def test_remote_sync_aborts_before_rsync_when_key_unavailable(tmp_path):
    env = _remote_env(tmp_path, "echo 'could not load key' >&2\nexit 1\n")

    result = _run_backup(env)

    assert result.returncode != 0
    assert not (tmp_path / "rsync_args").exists()
    assert "backup completed" not in result.stdout
    assert list((tmp_path / "backups").glob("innit-*.db")), "local snapshot must still be kept"


def test_remote_sync_moves_snapshots_off_machine(tmp_path):
    """Local dir is staging only: rsync removes sources after upload, never mirrors-deletes."""
    env = _remote_env(tmp_path, "true\n")

    result = _run_backup(env)

    assert result.returncode == 0, result.stdout + result.stderr
    args = (tmp_path / "rsync_args").read_text()
    assert "--remove-source-files" in args
    assert "--delete" not in args.split()
    assert "innit-" in args and ".db" in args


def test_remote_prune_keeps_retention_on_server(tmp_path):
    env = _remote_env(tmp_path, "true\n")
    env["INNIT_BACKUP_RETENTION"] = "5"

    result = _run_backup(env)

    assert result.returncode == 0, result.stdout + result.stderr
    ssh_args = (tmp_path / "ssh_args").read_text()
    assert "IdentityAgent=SSH_AUTH_SOCK" in ssh_args
    assert "echobyte@cucumber.whatbox.ca" in ssh_args
    assert "tail -n +6" in ssh_args


def test_local_retention_defaults_to_one_unsent_snapshot(tmp_path):
    """A failed upload leaves at most one snapshot behind, not an ever-growing pile."""
    env = _remote_env(tmp_path, "exit 1\n")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(2):
        fake = backup_dir / f"innit-2020010{i}-000000.db"
        _make_source_db(fake)
        old_time = time.time() - (1000 - i * 10)
        os.utime(fake, (old_time, old_time))

    result = _run_backup(env)

    assert result.returncode != 0
    remaining = list(backup_dir.glob("innit-*.db"))
    assert len(remaining) == 1
    assert "20200" not in remaining[0].name
