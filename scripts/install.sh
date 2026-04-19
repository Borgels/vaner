#!/usr/bin/env bash
#
# Vaner installer for macOS and Linux.
# Canonical source: https://github.com/Borgels/Vaner/blob/main/scripts/install.sh
# Docs: https://docs.vaner.ai/getting-started
#
# Usage:
#   curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash
#   curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- --yes

set -euo pipefail

BOLD=$'\033[1m'
INFO=$'\033[38;5;245m'
SUCCESS=$'\033[38;5;42m'
WARN=$'\033[38;5;214m'
ERROR=$'\033[38;5;196m'
NC=$'\033[0m'

VANER_YES="${VANER_YES:-0}"
VANER_DRY_RUN="${VANER_DRY_RUN:-0}"
VANER_VERIFY="${VANER_VERIFY:-0}"
VANER_BACKEND="${VANER_BACKEND:-}"
VANER_VERSION="${VANER_VERSION:-}"
VANER_NO_MODIFY_PATH="${VANER_NO_MODIFY_PATH:-0}"
VANER_VERBOSE="${VANER_VERBOSE:-0}"
VANER_WITH_OLLAMA="${VANER_WITH_OLLAMA:-0}"
HELP=0

INSTALL_STAGE_TOTAL=3
INSTALL_STAGE_CURRENT=0
OS="unknown"
PKG_MGR="none"
DOWNLOADER=""
TMPFILES=()
BACKEND=""
IS_UPGRADE=0
OLD_VERSION=""

cleanup_tmpfiles() {
  local f
  for f in "${TMPFILES[@]:-}"; do
    rm -rf "$f" 2>/dev/null || true
  done
}
trap cleanup_tmpfiles EXIT

mktempfile() {
  local f
  f="$(mktemp)"
  TMPFILES+=("$f")
  printf '%s' "$f"
}

ui_info() {
  printf '%s·%s %s\n' "$INFO" "$NC" "$*"
}

ui_warn() {
  printf '%s!%s %s\n' "$WARN" "$NC" "$*"
}

ui_success() {
  printf '%s✓%s %s\n' "$SUCCESS" "$NC" "$*"
}

ui_error() {
  printf '%s✗%s %s\n' "$ERROR" "$NC" "$*"
}

ui_section() {
  printf '\n%s%s%s\n' "$BOLD" "$*" "$NC"
}

ui_stage() {
  INSTALL_STAGE_CURRENT=$((INSTALL_STAGE_CURRENT + 1))
  ui_section "[${INSTALL_STAGE_CURRENT}/${INSTALL_STAGE_TOTAL}] $1"
}

print_usage() {
  cat <<'EOF'
Vaner installer (Linux/macOS)

Options:
  --yes                    Non-interactive mode; auto-approve prompts
  --dry-run                Print the install plan without making changes
  --verify                 Run post-install smoke checks
  --backend uv|pipx        Force installer backend
  --version VERSION        Install a specific PyPI version (e.g. 0.1.0)
  --no-modify-path         Do not run ensurepath/path integration steps
  --verbose                Print executed commands
  --with-ollama            Install/start Ollama and pull qwen2.5-coder:7b
  --help, -h               Show help

Environment variable mirrors:
  VANER_YES=0|1
  VANER_DRY_RUN=0|1
  VANER_VERIFY=0|1
  VANER_BACKEND=uv|pipx
  VANER_VERSION=<version>
  VANER_NO_MODIFY_PATH=0|1
  VANER_VERBOSE=0|1
  VANER_WITH_OLLAMA=0|1

Examples:
  curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash
  curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- --yes
  curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- --backend pipx --verify
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes)
        VANER_YES=1
        shift
        ;;
      --dry-run)
        VANER_DRY_RUN=1
        shift
        ;;
      --verify)
        VANER_VERIFY=1
        shift
        ;;
      --backend)
        VANER_BACKEND="$2"
        shift 2
        ;;
      --version)
        VANER_VERSION="$2"
        shift 2
        ;;
      --no-modify-path)
        VANER_NO_MODIFY_PATH=1
        shift
        ;;
      --verbose)
        VANER_VERBOSE=1
        shift
        ;;
      --with-ollama)
        VANER_WITH_OLLAMA=1
        shift
        ;;
      --help|-h)
        HELP=1
        shift
        ;;
      *)
        ui_error "Unknown argument: $1"
        exit 2
        ;;
    esac
  done
}

detect_downloader() {
  if command -v curl >/dev/null 2>&1; then
    DOWNLOADER="curl"
    return
  fi
  if command -v wget >/dev/null 2>&1; then
    DOWNLOADER="wget"
    return
  fi
  ui_error "Missing downloader (curl or wget required)"
  exit 1
}

download_file() {
  local url="$1"
  local output="$2"
  detect_downloader
  if [[ "$DOWNLOADER" == "curl" ]]; then
    run_cmd curl -fsSL --proto '=https' --tlsv1.2 --retry 3 --retry-delay 1 --retry-connrefused -o "$output" "$url"
    return
  fi
  run_cmd wget -q --https-only --secure-protocol=TLSv1_2 --tries=3 --timeout=20 -O "$output" "$url"
}

format_cmd() {
  local arg
  local out=""
  for arg in "$@"; do
    if [[ -z "$out" ]]; then
      out="$(printf '%q' "$arg")"
    else
      out="${out} $(printf '%q' "$arg")"
    fi
  done
  printf '%s' "$out"
}

run_cmd() {
  if [[ "$VANER_DRY_RUN" == "1" ]]; then
    printf 'would run: %s\n' "$(format_cmd "$@")"
    return 0
  fi
  if [[ "$VANER_VERBOSE" == "1" ]]; then
    printf '+ %s\n' "$(format_cmd "$@")"
  fi
  "$@"
}

run_shell() {
  local command="$1"
  if [[ "$VANER_DRY_RUN" == "1" ]]; then
    printf 'would run: %s\n' "$command"
    return 0
  fi
  if [[ "$VANER_VERBOSE" == "1" ]]; then
    printf '+ %s\n' "$command"
  fi
  bash -c "$command"
}

is_promptable() {
  if [[ "$VANER_YES" == "1" ]]; then
    return 1
  fi
  [[ -r /dev/tty && -w /dev/tty ]]
}

confirm() {
  local prompt="$1"
  local answer=""
  if [[ "$VANER_YES" == "1" ]]; then
    return 0
  fi
  if ! is_promptable; then
    ui_error "No interactive TTY available for prompt: $prompt"
    ui_info "Re-run with --yes for non-interactive installs."
    return 1
  fi
  printf '%s [Y/n] ' "$prompt" > /dev/tty
  read -r answer < /dev/tty || true
  case "$answer" in
    n|N|no|NO)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

detect_os_or_die() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
  elif [[ "$OSTYPE" == "linux-gnu"* || -n "${WSL_DISTRO_NAME:-}" ]]; then
    OS="linux"
  else
    OS="unknown"
  fi

  if [[ "$OS" == "unknown" ]]; then
    ui_error "Unsupported OS: ${OSTYPE:-unknown}"
    ui_info "This installer currently supports Linux and macOS."
    exit 1
  fi
  ui_success "Detected OS: $OS"
}

detect_pkg_mgr() {
  case "$OS" in
    macos)
      if command -v brew >/dev/null 2>&1; then
        PKG_MGR="brew"
      else
        PKG_MGR="none"
      fi
      ;;
    linux)
      if command -v apt-get >/dev/null 2>&1; then
        PKG_MGR="apt-get"
      elif command -v dnf >/dev/null 2>&1; then
        PKG_MGR="dnf"
      elif command -v yum >/dev/null 2>&1; then
        PKG_MGR="yum"
      elif command -v pacman >/dev/null 2>&1; then
        PKG_MGR="pacman"
      elif command -v zypper >/dev/null 2>&1; then
        PKG_MGR="zypper"
      elif command -v apk >/dev/null 2>&1; then
        PKG_MGR="apk"
      elif command -v brew >/dev/null 2>&1; then
        PKG_MGR="brew"
      else
        PKG_MGR="none"
      fi
      ;;
    *)
      PKG_MGR="none"
      ;;
  esac
  ui_info "Package manager: $PKG_MGR"
}

with_root_prefix() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    printf ''
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    printf 'sudo '
    return
  fi
  ui_error "Root privileges required but sudo is not installed."
  exit 1
}

has_python_311() {
  python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

ensure_python_311() {
  if has_python_311; then
    ui_success "Python 3.11+ already available"
    return 0
  fi

  confirm "Python 3.11+ is required. Install it now using ${PKG_MGR}?" || return 1

  case "$PKG_MGR" in
    brew)
      run_cmd brew install python@3.12
      ;;
    apt-get)
      local root
      root="$(with_root_prefix)"
      run_shell "${root}apt-get update -qq"
      run_shell "${root}apt-get install -y -qq python3 python3-venv python3-pip"
      ;;
    dnf)
      run_shell "$(with_root_prefix)dnf install -y -q python3 python3-pip"
      ;;
    yum)
      run_shell "$(with_root_prefix)yum install -y -q python3 python3-pip"
      ;;
    pacman)
      run_shell "$(with_root_prefix)pacman -Sy --noconfirm python python-pip"
      ;;
    zypper)
      run_shell "$(with_root_prefix)zypper --non-interactive install python311 python311-pip"
      ;;
    apk)
      run_shell "$(with_root_prefix)apk add --no-cache python3 py3-pip"
      ;;
    *)
      ui_error "Cannot install Python automatically without a supported package manager."
      return 1
      ;;
  esac

  if [[ "$VANER_DRY_RUN" == "1" ]]; then
    ui_success "Dry-run: assumed Python 3.11+ installed"
    return 0
  fi

  if ! has_python_311; then
    ui_error "Python 3.11+ is still not available after attempted install."
    return 1
  fi
  ui_success "Python 3.11+ installed"
}

ensure_pipx() {
  if command -v pipx >/dev/null 2>&1; then
    ui_success "pipx already available"
    return 0
  fi

  confirm "pipx is missing. Install pipx now?" || return 1

  case "$PKG_MGR" in
    brew)
      run_cmd brew install pipx
      ;;
    apt-get)
      run_shell "$(with_root_prefix)apt-get update -qq"
      run_shell "$(with_root_prefix)apt-get install -y -qq pipx"
      ;;
    dnf)
      run_shell "$(with_root_prefix)dnf install -y -q pipx"
      ;;
    yum)
      run_shell "$(with_root_prefix)yum install -y -q pipx"
      ;;
    pacman)
      run_shell "$(with_root_prefix)pacman -Sy --noconfirm pipx"
      ;;
    zypper)
      run_shell "$(with_root_prefix)zypper --non-interactive install pipx"
      ;;
    apk)
      run_shell "$(with_root_prefix)apk add --no-cache py3-pipx"
      ;;
    *)
      ui_warn "No package manager pipx recipe found; falling back to python -m pip."
      ;;
  esac

  if [[ "$VANER_DRY_RUN" == "1" ]]; then
    ui_success "Dry-run: assumed pipx installed"
    return 0
  fi

  if ! command -v pipx >/dev/null 2>&1; then
    run_cmd python3 -m pip install --user --upgrade pipx
  fi

  if ! command -v pipx >/dev/null 2>&1; then
    export PATH="$HOME/.local/bin:$PATH"
  fi

  if ! command -v pipx >/dev/null 2>&1; then
    ui_error "pipx was not found after installation attempts."
    return 1
  fi

  if [[ "$VANER_NO_MODIFY_PATH" != "1" ]]; then
    run_cmd python3 -m pipx ensurepath
  fi
  ui_success "pipx installed"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    ui_success "uv already available"
    return 0
  fi

  confirm "uv is missing. Install uv (recommended) from astral.sh now?" || return 1
  local tmp
  tmp="$(mktempfile)"
  download_file "https://astral.sh/uv/install.sh" "$tmp"
  run_cmd sh "$tmp"

  if [[ "$VANER_DRY_RUN" == "1" ]]; then
    ui_success "Dry-run: assumed uv installed"
    return 0
  fi

  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

  if ! command -v uv >/dev/null 2>&1; then
    ui_error "uv was not found after installation."
    return 1
  fi
  ui_success "uv installed"
}

ensure_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    ui_success "Ollama already available"
    return 0
  fi

  confirm "Ollama is missing. Install Ollama now?" || return 1
  case "$OS" in
    macos)
      if [[ "$PKG_MGR" == "brew" ]]; then
        run_cmd brew install ollama
      else
        local tmp
        tmp="$(mktempfile)"
        download_file "https://ollama.com/install.sh" "$tmp"
        run_cmd sh "$tmp"
      fi
      ;;
    linux)
      local tmp
      tmp="$(mktempfile)"
      download_file "https://ollama.com/install.sh" "$tmp"
      run_cmd sh "$tmp"
      ;;
    *)
      ui_warn "Automatic Ollama install is unsupported on this OS."
      return 1
      ;;
  esac

  if [[ "$VANER_DRY_RUN" == "1" ]]; then
    ui_success "Dry-run: assumed Ollama installed"
    return 0
  fi

  if ! command -v ollama >/dev/null 2>&1; then
    ui_error "Ollama installation did not provide 'ollama' on PATH."
    return 1
  fi
  ui_success "Ollama installed"
}

ensure_ollama_model() {
  if [[ "$VANER_WITH_OLLAMA" != "1" ]]; then
    return 0
  fi
  ensure_ollama || return 1
  ui_stage "Preparing local model"
  run_cmd ollama pull qwen2.5-coder:7b
}

pick_backend() {
  if [[ -n "$VANER_BACKEND" ]]; then
    case "$VANER_BACKEND" in
      uv|pipx)
        printf '%s' "$VANER_BACKEND"
        return
        ;;
      *)
        ui_error "Invalid backend '${VANER_BACKEND}'. Use 'uv' or 'pipx'."
        exit 2
        ;;
    esac
  fi

  if command -v uv >/dev/null 2>&1; then
    printf 'uv'
    return
  fi
  if command -v pipx >/dev/null 2>&1; then
    printf 'pipx'
    return
  fi
  printf ''
}

package_spec() {
  if [[ -n "$VANER_VERSION" ]]; then
    printf 'vaner==%s' "$VANER_VERSION"
  else
    printf 'vaner'
  fi
}

github_package_spec() {
  printf 'git+https://github.com/Borgels/vaner.git'
}

check_existing_vaner() {
  if ! command -v vaner >/dev/null 2>&1; then
    return 1
  fi
  OLD_VERSION="$(vaner --version 2>/dev/null | tr -d '\r' || true)"
  IS_UPGRADE=1
  return 0
}

install_vaner_with_uv() {
  local spec
  spec="$(package_spec)"
  if run_cmd uv tool install --upgrade "$spec"; then
    return 0
  fi
  if [[ -z "$VANER_VERSION" ]]; then
    ui_warn "PyPI package 'vaner' not found yet. Falling back to GitHub source install."
    run_cmd uv tool install --upgrade "$(github_package_spec)"
    return 0
  fi
  return 1
}

install_vaner_with_pipx() {
  local spec
  spec="$(package_spec)"
  if ! run_cmd pipx install --force "$spec"; then
    if [[ -z "$VANER_VERSION" ]]; then
      ui_warn "PyPI package 'vaner' not found yet. Falling back to GitHub source install."
      run_cmd pipx install --force "$(github_package_spec)"
    else
      return 1
    fi
  fi
  if [[ "$VANER_NO_MODIFY_PATH" != "1" ]]; then
    run_cmd pipx ensurepath
  fi
}

print_path_warning() {
  local pipx_bin=""
  local uv_bin=""
  if command -v pipx >/dev/null 2>&1; then
    pipx_bin="$(python3 -m pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
    if [[ -n "$pipx_bin" && ":$PATH:" != *":$pipx_bin:"* ]]; then
      ui_warn "pipx bin dir not detected on PATH: $pipx_bin"
      ui_info "Open a new shell or run: pipx ensurepath"
    fi
  fi
  if command -v uv >/dev/null 2>&1; then
    uv_bin="$(uv tool dir --bin 2>/dev/null || true)"
    if [[ -n "$uv_bin" && ":$PATH:" != *":$uv_bin:"* ]]; then
      ui_warn "uv tool bin dir not detected on PATH: $uv_bin"
      ui_info "Open a new shell or run: uv tool update-shell"
    fi
  fi
}

verify_installation() {
  ui_stage "Verifying install"
  run_cmd vaner --version

  local tmpdir
  tmpdir="$(mktemp -d)"
  TMPFILES+=("$tmpdir")
  run_cmd vaner init --path "$tmpdir"
  if [[ "$VANER_DRY_RUN" != "1" && ! -d "$tmpdir/.vaner" ]]; then
    ui_error "Verification failed: workspace was not initialized."
    exit 1
  fi
  ui_success "Smoke verification passed"
}

print_install_plan() {
  ui_section "Install plan"
  printf '  backend: %s\n' "$BACKEND"
  if [[ -n "$VANER_VERSION" ]]; then
    printf '  package: vaner==%s\n' "$VANER_VERSION"
  else
    printf '  package: vaner (latest; fallback to GitHub source if PyPI unavailable)\n'
  fi
  printf '  dry-run: %s\n' "$VANER_DRY_RUN"
  printf '  verify: %s\n' "$VANER_VERIFY"
  printf '  with-ollama: %s\n' "$VANER_WITH_OLLAMA"
}

print_footer() {
  printf '\n'
  if [[ "$IS_UPGRADE" == "1" && -n "$OLD_VERSION" ]]; then
    ui_success "Vaner upgrade complete (from: ${OLD_VERSION})"
  else
    ui_success "Vaner installed successfully"
  fi
  printf '\nNext steps:\n'
  printf '  vaner init --path .\n'
  printf '  vaner daemon start --no-once --path .\n'
  printf '  vaner query "where is auth enforced?" --path .\n'
  printf '  vaner inspect --last --path .\n'
}

main() {
  parse_args "$@"

  if [[ "$HELP" == "1" ]]; then
    print_usage
    return 0
  fi

  ui_section "Vaner Installer"
  if [[ "$VANER_WITH_OLLAMA" == "1" ]]; then
    INSTALL_STAGE_TOTAL=4
  fi
  detect_os_or_die
  detect_pkg_mgr
  check_existing_vaner || true

  ui_stage "Preparing environment"
  BACKEND="$(pick_backend)"

  if [[ -z "$BACKEND" ]]; then
    if confirm "Neither uv nor pipx is installed. Install uv (recommended)?"; then
      ensure_uv
      BACKEND="uv"
    elif confirm "Install pipx instead?"; then
      ensure_python_311
      ensure_pipx
      BACKEND="pipx"
    else
      ui_error "Aborted by user (no install backend selected)."
      exit 1
    fi
  fi

  if [[ "$BACKEND" == "pipx" ]]; then
    ensure_python_311
    ensure_pipx
  elif [[ "$BACKEND" == "uv" ]]; then
    ensure_uv
  fi

  print_install_plan

  ui_stage "Installing Vaner"
  case "$BACKEND" in
    uv)
      install_vaner_with_uv
      ;;
    pipx)
      install_vaner_with_pipx
      ;;
    *)
      ui_error "Unexpected backend: $BACKEND"
      exit 1
      ;;
  esac

  print_path_warning

  if [[ "$VANER_VERIFY" == "1" ]]; then
    verify_installation
  fi

  if [[ "$VANER_WITH_OLLAMA" == "1" ]]; then
    ensure_ollama_model
  fi

  print_footer
}

main "$@"
