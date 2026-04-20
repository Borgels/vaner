from __future__ import annotations

import subprocess
import sys


def test_server_module_imports_when_mcp_is_unavailable() -> None:
    script = """
import builtins
import importlib
import sys

real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.startswith("mcp"):
        raise ModuleNotFoundError("No module named 'mcp'")
    return real_import(name, *args, **kwargs)

builtins.__import__ = blocked
mod = importlib.import_module("vaner.mcp.server")
assert hasattr(mod, "build_server")
"""
    result = subprocess.run([sys.executable, "-c", script], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
