# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import socket
from pathlib import Path

_DISALLOWED_ROOTS = {
    Path("/").resolve(),
    Path("/home").resolve(),
    Path("/root").resolve(),
    Path.home().resolve(),
}


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _count_files_limited(root: Path, limit: int = 100_000) -> int:
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.name in {".git", ".venv", "node_modules", ".next", ".cache", ".vaner"}:
                    continue
                stack.append(Path(entry.path))
                continue
            total += 1
            if total >= limit:
                return total
    return total


def check_repo_root(repo_root: Path, *, force: bool = False) -> dict[str, object]:
    resolved = repo_root.resolve()
    if resolved in _DISALLOWED_ROOTS and not force:
        return {
            "ok": False,
            "reason": "unsafe_root",
            "detail": f"Refusing broad root path: {resolved}",
            "fix": "Pick your project root, e.g. `vaner up --path ~/repos/my-project`.",
        }
    is_git = _is_git_repo(resolved)
    if is_git:
        return {"ok": True, "reason": "git_repo", "detail": str(resolved)}
    approx_files = _count_files_limited(resolved)
    if approx_files >= 100_000 and not force:
        return {
            "ok": False,
            "reason": "non_git_large_root",
            "detail": f"Path is not a git repo and is very large (files >= {approx_files}).",
            "fix": "Pick your project root, e.g. `vaner up --path ~/repos/my-project`.",
        }
    return {"ok": True, "reason": "small_non_git", "detail": f"non-git path files={approx_files}"}


def _read_int_file(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _estimate_dir_count(root: Path, limit: int = 200_000) -> int:
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        total += 1
        if total >= limit:
            return total
        try:
            for entry in os.scandir(current):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name in {".git", ".venv", "node_modules", ".next", ".cache", ".vaner"}:
                    continue
                stack.append(Path(entry.path))
        except OSError:
            continue
    return total


def check_inotify_budget(repo_root: Path) -> dict[str, object]:
    budget = _read_int_file(Path("/proc/sys/fs/inotify/max_user_watches"))
    if not budget:
        return {"ok": True, "level": "warn", "detail": "Could not read inotify watch budget."}
    approx_dirs = _estimate_dir_count(repo_root)
    usage_ratio = min(1.0, float(approx_dirs) / float(max(1, budget)))
    headroom_pct = max(0.0, (1.0 - usage_ratio) * 100.0)
    ok = usage_ratio < 0.5
    payload = {
        "ok": ok,
        "level": "pass" if ok else "warn",
        "detail": (f"dirs~{approx_dirs} budget={budget} headroom={headroom_pct:.1f}% (watch estimates are approximate)"),
        "headroom_pct": round(headroom_pct, 2),
        "fix": "sudo sysctl fs.inotify.max_user_watches=524288",
    }
    return payload


def _is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def _pick_port(host: str, preferred: int, window: int = 10) -> int:
    for candidate in range(preferred, preferred + window + 1):
        if _is_port_free(host, candidate):
            return candidate
    return preferred


def check_ports(host: str, cockpit_port: int, mcp_sse_port: int) -> dict[str, object]:
    resolved_cockpit = _pick_port(host, cockpit_port)
    resolved_mcp = _pick_port(host, mcp_sse_port)
    return {
        "ok": True,
        "cockpit_port": resolved_cockpit,
        "mcp_sse_port": resolved_mcp,
        "cockpit_changed": resolved_cockpit != cockpit_port,
        "mcp_changed": resolved_mcp != mcp_sse_port,
    }
