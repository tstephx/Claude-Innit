"""Tests for launchd/run-http-server.sh exit-status logging.

Runs the wrapper under /bin/bash (3.2 on macOS, what launchd uses) with a
fake interpreter, per ~/.claude/references/wrapper-exit-status-logging.md.
"""

import plistlib
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "launchd" / "run-http-server.sh"
PLIST = REPO / "launchd" / "com.taylorstephens.claude-innit-http.plist"


def _fake_python(tmp_path, body):
    py = tmp_path / "fakepy"
    py.write_text("#!/bin/bash\n" + body)
    py.chmod(0o755)
    return py


def _env(tmp_path, py):
    return {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "CLAUDE_INNIT_REPO": str(REPO),
        "CLAUDE_INNIT_PYTHON": str(py),
        "CLAUDE_INNIT_LOG": str(tmp_path / "logs" / "server.log"),
    }


def _run(tmp_path, body, timeout=20):
    py = _fake_python(tmp_path, body)
    proc = subprocess.run(
        ["/bin/bash", str(WRAPPER)],
        env=_env(tmp_path, py),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc, (tmp_path / "logs" / "server.log").read_text()


def test_failure_logs_rc_duration_and_last_stderr(tmp_path):
    proc, log = _run(
        tmp_path,
        'echo "INFO booting" >&2\necho "OSError: [Errno 48] address already in use" >&2\nexit 3\n',
    )
    assert proc.returncode == 3
    assert re.search(
        r"\[wrapper\] server FAILED rc=3 after \d+s: OSError: \[Errno 48\] address already in use",
        log,
    ), log


def test_failure_quotes_only_this_runs_stderr(tmp_path):
    _run(tmp_path, 'echo "old run error" >&2\nexit 1\n')
    _, log = _run(tmp_path, "exit 2\n")
    last = log.strip().splitlines()[-1]
    assert "rc=2" in last
    assert "old run error" not in last
    assert last.endswith("<no stderr>") or "FAILED" in last


def test_clean_exit_logs_ok(tmp_path):
    proc, log = _run(tmp_path, "exit 0\n")
    assert proc.returncode == 0
    assert re.search(r"server exited ok rc=0 after \d+s", log)


def test_sets_http_transport(tmp_path):
    _, log = _run(tmp_path, 'echo "transport=$CLAUDE_INNIT_TRANSPORT"\nexit 0\n')
    assert "transport=http" in log


def test_missing_interpreter_is_a_logged_failure(tmp_path):
    env = _env(tmp_path, tmp_path / "nope")
    proc = subprocess.run(["/bin/bash", str(WRAPPER)], env=env, timeout=10)
    assert proc.returncode == 127
    log = (tmp_path / "logs" / "server.log").read_text()
    assert "FAILED rc=127" in log


def test_sigterm_is_forwarded_and_exit_logged(tmp_path):
    py = _fake_python(
        tmp_path,
        "trap 'echo \"child got TERM\" >&2; exit 143' TERM\n"
        'echo "child ready" >&2\n'
        "while :; do sleep 0.1; done\n",
    )
    proc = subprocess.Popen(["/bin/bash", str(WRAPPER)], env=_env(tmp_path, py))
    log_path = tmp_path / "logs" / "server.log"
    for _ in range(100):
        if log_path.exists() and "child ready" in log_path.read_text():
            break
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    rc = proc.wait(timeout=10)
    log = log_path.read_text()
    assert "received TERM" in log
    assert "child got TERM" in log
    assert rc == 0
    assert re.search(r"server stopped on TERM rc=143 after \d+s", log), log
    assert "FAILED" not in log


def test_unrequested_signal_death_is_a_failure(tmp_path):
    # Killed by TERM from somewhere other than launchd (e.g. OOM tooling):
    # the wrapper did not ask for the stop, so it is logged as a failure.
    proc, log = _run(tmp_path, "kill -TERM $$\n")
    assert proc.returncode == 143
    assert "server FAILED rc=143" in log


def test_plist_is_valid_and_loopback_http():
    data = plistlib.loads(PLIST.read_bytes())
    assert data["Label"] == "com.taylorstephens.claude-innit-http"
    assert data["ProgramArguments"][0] == "/bin/bash"
    assert data["ProgramArguments"][1].endswith("/launchd/run-http-server.sh")
    assert data["KeepAlive"] is True
    assert data["RunAtLoad"] is True
    env = data["EnvironmentVariables"]
    assert "CLAUDE_INNIT_HOST" not in env  # default is loopback-only
    assert int(env["CLAUDE_INNIT_PORT"]) > 1024


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="macOS only")
def test_plist_lints():
    subprocess.run(["/usr/bin/plutil", "-lint", str(PLIST)], check=True)
