from __future__ import annotations

import subprocess
from pathlib import Path


def _run_install_shell(body: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "install.sh"
    script_text = script_path.read_text(encoding="utf-8")
    prefix, marker, _ = script_text.rpartition('\nmain "$@"\n')
    assert marker, "expected install.sh to end with main invocation"
    command = f"{prefix}\n{body}\n"
    return subprocess.run(
        ["bash", "-lc", command],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def test_version_pinned_uv_install_falls_back_to_matching_git_tag() -> None:
    result = _run_install_shell(
        """
VANER_VERSION=0.6.2
VANER_NO_MCP=0
VANER_MINIMAL=0
calls=()
run_cmd() {
  local joined="$*"
  calls+=("$joined")
  if [[ "$joined" == *"vaner[all]==0.6.2"* ]]; then
    return 1
  fi
  if [[ "$joined" == *"git+https://github.com/Borgels/vaner.git@v0.6.2"* ]]; then
    return 0
  fi
  return 1
}
ui_warn() { :; }
install_vaner_with_uv
printf '%s\n' "${calls[@]}"
"""
    )

    assert result.returncode == 0, result.stderr
    assert "uv tool install --upgrade vaner[all]==0.6.2" in result.stdout
    assert "git+https://github.com/Borgels/vaner.git@v0.6.2" in result.stdout
