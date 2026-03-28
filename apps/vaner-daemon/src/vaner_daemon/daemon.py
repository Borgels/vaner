import os
import time
import subprocess
from pathlib import Path
from vaner_daemon.config import DaemonConfig
def daemon_status(repo_path: Path) -> dict:
    """Return running status, pid, uptime, branch, active files."""
    pid_file = repo_path / ".vaner" / "daemon.pid"
    cfg = DaemonConfig.load(repo_path)
    result: dict = {
        "running": False,
        "pid": None,
        "uptime_seconds": None,
        "branch": "",
        "active_files": [],
        "proxy_running": False,
        "proxy_port": cfg.proxy_port,
        "langsmith_tracing": False,  # Added default value
        "gpu_utilization": "N/A",    # Added default value
        "memory_used": "N/A",          # Added default value
    }

    if not pid_file.exists():
        return result

    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, FileNotFoundError):
        return result
    except PermissionError:
        pass  # process exists but we can't signal it — still "running"

    # Check if process is alive
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # Stale PID file
        pid_file.unlink(missing_ok=True)
        return result
    except PermissionError:
        pass  # process exists but we can't signal it — still "running"

    result["running"] = True
    result["pid"] = pid
    result["proxy_running"] = cfg.proxy_enabled

    # Uptime from PID file mtime
    try:
        mtime = pid_file.stat().st_mtime
        result["uptime_seconds"] = time.time() - mtime
    except Exception:
        pass

    # Read state from SQLite
    db_path = repo_path / ".vaner" / "state.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT path FROM active_files ORDER BY last_touched DESC LIMIT 10"
            ).fetchall()
            result["active_files"] = [r[0] for r in rows]
            conn.close()
        except Exception:
            pass

    # Read branch from git
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            result["branch"] = r.stdout.strip()
    except Exception:
        pass

    return result
