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
VANER_VERIFY="${VANER_VERIFY:-1}"
VANER_BACKEND="${VANER_BACKEND:-}"
VANER_VERSION="${VANER_VERSION:-}"
VANER_NO_MODIFY_PATH="${VANER_NO_MODIFY_PATH:-0}"
VANER_VERBOSE="${VANER_VERBOSE:-0}"
VANER_WITH_OLLAMA="${VANER_WITH_OLLAMA:-0}"
VANER_NO_MCP="${VANER_NO_MCP:-0}"
VANER_MINIMAL="${VANER_MINIMAL:-0}"
VANER_BACKEND_PRESET="${VANER_BACKEND_PRESET:-}"
VANER_BACKEND_URL="${VANER_BACKEND_URL:-}"
VANER_BACKEND_MODEL="${VANER_BACKEND_MODEL:-}"
VANER_BACKEND_API_KEY_ENV="${VANER_BACKEND_API_KEY_ENV:-}"
VANER_COMPUTE_PRESET="${VANER_COMPUTE_PRESET:-}"
VANER_MAX_SESSION_MINUTES="${VANER_MAX_SESSION_MINUTES:-}"
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
  --yes                           Non-interactive mode; auto-approve prompts
  --dry-run                       Print the install plan without making changes
  --verify                        Run post-install smoke checks
  --no-verify                     Skip post-install smoke checks
  --backend uv|pipx               Force installer backend
  --version VERSION               Install a specific PyPI version (e.g. 0.2.0)
  --no-modify-path                Do not run ensurepath/path integration steps
  --verbose                       Print executed commands
  --with-ollama                   Install/start Ollama and configure a local model
                                  (alias for --backend-preset ollama)
  --minimal                       Install the old minimal package extras only
                                  (mcp or empty when --no-mcp is used)
  --no-mcp                        Install without the [mcp] extra (read-only tools
                                  will be disabled)
  --backend-preset PRESET         Configure LLM backend non-interactively.
                                  One of: ollama | lmstudio | vllm | openai |
                                  anthropic | openrouter | skip
  --backend-url URL               Override backend base_url (for vllm/lmstudio/custom)
  --backend-model MODEL           Override backend model name
  --backend-api-key-env VAR       Env var holding the cloud provider API key
  --compute-preset PRESET         Compute budget: background (default) | balanced |
                                  dedicated
  --max-session-minutes N         Cap continuous ponder-session wall clock to N
                                  minutes. Leave unset for unbounded.
  --help, -h                      Show help

Environment variable mirrors:
  VANER_YES=0|1
  VANER_DRY_RUN=0|1
  VANER_VERIFY=0|1
  VANER_BACKEND=uv|pipx
  VANER_VERSION=<version>
  VANER_NO_MODIFY_PATH=0|1
  VANER_VERBOSE=0|1
  VANER_WITH_OLLAMA=0|1
  VANER_MINIMAL=0|1
  VANER_NO_MCP=0|1
  VANER_BACKEND_PRESET=<preset>
  VANER_BACKEND_URL=<url>
  VANER_BACKEND_MODEL=<model>
  VANER_BACKEND_API_KEY_ENV=<var>
  VANER_COMPUTE_PRESET=background|balanced|dedicated
  VANER_MAX_SESSION_MINUTES=<minutes>

Examples:
  curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash
  curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- --yes
  curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- \
    --backend-preset ollama --compute-preset background
  curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- \
    --backend-preset openai --backend-api-key-env OPENAI_API_KEY --backend-model gpt-4o
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
      --no-verify)
        VANER_VERIFY=0
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
        VANER_BACKEND_PRESET="${VANER_BACKEND_PRESET:-ollama}"
        shift
        ;;
      --minimal)
        VANER_MINIMAL=1
        shift
        ;;
      --no-mcp)
        VANER_NO_MCP=1
        shift
        ;;
      --backend-preset)
        VANER_BACKEND_PRESET="$2"
        shift 2
        ;;
      --backend-url)
        VANER_BACKEND_URL="$2"
        shift 2
        ;;
      --backend-model)
        VANER_BACKEND_MODEL="$2"
        shift 2
        ;;
      --backend-api-key-env)
        VANER_BACKEND_API_KEY_ENV="$2"
        shift 2
        ;;
      --compute-preset)
        VANER_COMPUTE_PRESET="$2"
        shift 2
        ;;
      --max-session-minutes)
        VANER_MAX_SESSION_MINUTES="$2"
        shift 2
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
  local target_model="${VANER_BACKEND_MODEL:-qwen2.5-coder:7b}"
  ensure_ollama || return 1
  if ollama_has_model "$target_model"; then
    ui_success "Ollama model already available: $target_model"
    return 0
  fi
  if is_promptable && ! confirm "Ollama model '$target_model' is not installed. Pull it now?"; then
    ui_warn "Skipping model pull. Run 'ollama pull $target_model' later."
    return 0
  fi
  ui_stage "Preparing local model"
  run_cmd ollama pull "$target_model"
}

ollama_list_models() {
  if ! command -v ollama >/dev/null 2>&1; then
    return 0
  fi
  ollama list 2>/dev/null | awk 'NR > 1 && $1 != "" { print $1 }'
}

ollama_has_model() {
  local target="$1"
  if [[ -z "$target" ]]; then
    return 1
  fi
  ollama_list_models | awk -v target="$target" '$0 == target { found=1 } END { exit(found ? 0 : 1) }'
}

select_existing_ollama_model() {
  local default_model="${1:-qwen2.5-coder:7b}"
  local models=()
  local idx=1
  local choice=""
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    models+=("$line")
  done < <(ollama_list_models)
  if [[ "${#models[@]}" -eq 0 ]]; then
    printf '%s' "$default_model"
    return 0
  fi
  if ! is_promptable; then
    printf '%s' "${models[0]}"
    return 0
  fi
  {
    printf '\n'
    printf 'Detected existing Ollama models:\n'
    for model in "${models[@]}"; do
      printf '  %d) %s\n' "$idx" "$model"
      idx=$((idx + 1))
    done
    printf '  %d) Pull default (%s)\n' "$idx" "$default_model"
  } > /dev/tty
  choice="$(prompt_line "Use which model" "1")"
  if [[ "$choice" =~ ^[0-9]+$ ]]; then
    if (( choice >= 1 && choice <= ${#models[@]} )); then
      printf '%s' "${models[$((choice - 1))]}"
      return 0
    fi
    if (( choice == ${#models[@]} + 1 )); then
      printf '%s' "$default_model"
      return 0
    fi
  fi
  for model in "${models[@]}"; do
    if [[ "$choice" == "$model" ]]; then
      printf '%s' "$model"
      return 0
    fi
  done
  ui_warn "Unrecognized choice '$choice'; using default model '$default_model'."
  printf '%s' "$default_model"
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

package_extras() {
  if [[ "$VANER_MINIMAL" == "1" ]]; then
    if [[ "$VANER_NO_MCP" == "1" ]]; then
      printf ''
    else
      printf '[mcp]'
    fi
    return
  fi
  if [[ "$VANER_NO_MCP" == "1" ]]; then
    printf '[embeddings]'
  else
    printf '[all]'
  fi
}

print_extra_hint() {
  if [[ "$VANER_MINIMAL" == "1" ]]; then
    ui_warn "Minimal mode selected. Some capabilities may require manual dependency installs."
    return
  fi
  if [[ "$VANER_NO_MCP" == "1" ]]; then
    ui_info "Installing runtime deps without MCP extras ([embeddings])."
  else
    ui_info "Installing full runtime extras ([all]) for a ready-to-use setup."
  fi
}

package_spec() {
  local extras
  extras="$(package_extras)"
  if [[ -n "$VANER_VERSION" ]]; then
    printf 'vaner%s==%s' "$extras" "$VANER_VERSION"
  else
    printf 'vaner%s' "$extras"
  fi
}

package_base_spec() {
  if [[ -n "$VANER_VERSION" ]]; then
    printf 'vaner==%s' "$VANER_VERSION"
  else
    printf 'vaner'
  fi
}

github_package_spec() {
  local extras
  extras="$(package_extras)"
  if [[ -n "$extras" ]]; then
    printf 'vaner%s @ git+https://github.com/Borgels/vaner.git' "$extras"
  else
    printf 'git+https://github.com/Borgels/vaner.git'
  fi
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
  if [[ "$VANER_NO_MCP" != "1" ]]; then
    ui_warn "uv extra resolution failed; retrying with explicit MCP dependencies."
    local base_spec
    base_spec="$(package_base_spec)"
    local uv_args=(tool install --upgrade "$base_spec" --with "mcp[cli]>=1.0" --with "starlette>=0.40")
    if [[ "$VANER_MINIMAL" != "1" ]]; then
      uv_args+=(--with "sentence-transformers>=3.0" --with "torch>=2.3")
    fi
    if run_cmd uv "${uv_args[@]}"; then
      return 0
    fi
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
  run_cmd vaner init --path "$tmpdir" --no-interactive --clients none
  if [[ "$VANER_DRY_RUN" != "1" ]]; then
    if [[ ! -d "$tmpdir/.vaner" ]]; then
      ui_error "Verification failed: workspace was not initialized."
      exit 1
    fi
    local doctor_json
    doctor_json="$(mktempfile)"
    if ! vaner doctor --path "$tmpdir" --json > "$doctor_json"; then
      ui_warn "vaner doctor returned non-zero; validating required smoke checks."
    fi
    python3 - "$doctor_json" <<'PY'
import json
import sys

path = sys.argv[1]
payload = json.loads(open(path, encoding="utf-8").read() or "{}")
checks = {item.get("name"): item for item in payload.get("checks", []) if isinstance(item, dict)}
required = ("python_deps", "mcp_smoke")
failed = [name for name in required if not bool(checks.get(name, {}).get("ok", False))]
if failed:
    raise SystemExit(f"required doctor checks failed: {', '.join(failed)}")
PY
    run_cmd vaner mcp --path "$tmpdir" --smoke
  fi
  ui_success "Smoke verification passed"
}

print_install_plan() {
  ui_section "Install plan"
  printf '  backend: %s\n' "$BACKEND"
  local extras
  extras="$(package_extras)"
  if [[ -n "$VANER_VERSION" ]]; then
    printf '  package: vaner%s==%s\n' "$extras" "$VANER_VERSION"
  else
    printf '  package: vaner%s (latest; fallback to GitHub source if PyPI unavailable)\n' "$extras"
  fi
  printf '  dry-run: %s\n' "$VANER_DRY_RUN"
  printf '  verify: %s\n' "$VANER_VERIFY"
  printf '  with-ollama: %s\n' "$VANER_WITH_OLLAMA"
  if [[ -n "$VANER_BACKEND_PRESET" ]]; then
    printf '  backend-preset: %s\n' "$VANER_BACKEND_PRESET"
  fi
  if [[ -n "$VANER_COMPUTE_PRESET" ]]; then
    printf '  compute-preset: %s\n' "$VANER_COMPUTE_PRESET"
  fi
  if [[ -n "$VANER_MAX_SESSION_MINUTES" ]]; then
    printf '  max-session-minutes: %s\n' "$VANER_MAX_SESSION_MINUTES"
  else
    printf '  max-session-minutes: unbounded\n'
  fi
}

prompt_line() {
  local prompt="$1"
  local default_value="${2:-}"
  local answer=""
  if ! is_promptable; then
    printf '%s' "$default_value"
    return 0
  fi
  if [[ -n "$default_value" ]]; then
    printf '%s [%s]: ' "$prompt" "$default_value" > /dev/tty
  else
    printf '%s: ' "$prompt" > /dev/tty
  fi
  read -r answer < /dev/tty || true
  if [[ -z "$answer" ]]; then
    printf '%s' "$default_value"
  else
    printf '%s' "$answer"
  fi
}

is_cloud_backend_preset() {
  case "$1" in
    openai|anthropic|openrouter) return 0 ;;
    *) return 1 ;;
  esac
}

confirm_cloud_backend_costs() {
  local preset="$1"
  if ! is_cloud_backend_preset "$preset"; then
    return 0
  fi
  if confirm "You selected cloud backend '${preset}'. This can incur API costs. Continue?"; then
    return 0
  fi
  ui_warn "Cloud backend selection cancelled; falling back to 'skip'."
  VANER_BACKEND_PRESET="skip"
  VANER_BACKEND_URL=""
  VANER_BACKEND_MODEL=""
  unset VANER_BACKEND_API_KEY_ENV
  VANER_WITH_OLLAMA=0
  return 0
}

resolve_backend_preset() {
  if [[ -n "$VANER_BACKEND_PRESET" ]]; then
    confirm_cloud_backend_costs "$VANER_BACKEND_PRESET"
    return 0
  fi
  if ! is_promptable; then
    VANER_BACKEND_PRESET="skip"
    return 0
  fi
  {
    printf '\n'
    printf 'Pick a model backend (Vaner needs an LLM for scenario expansion):\n'
    printf '  1) Ollama     — local, auto-install, privacy-first (recommended)\n'
    printf '  2) LM Studio  — local app you already run\n'
    printf '  3) vLLM / OpenAI-compatible self-hosted\n'
    printf '  4) OpenAI     — cloud, needs API key\n'
    printf '  5) Anthropic  — cloud, needs API key\n'
    printf '  6) OpenRouter — cloud, 100+ models via one key\n'
    printf '  7) Skip for now (read-only MCP tools still work)\n'
  } > /dev/tty
  local choice
  choice="$(prompt_line "Choice" "1")"
  case "$choice" in
    1|ollama) VANER_BACKEND_PRESET="ollama" ;;
    2|lmstudio|lm-studio) VANER_BACKEND_PRESET="lmstudio" ;;
    3|vllm) VANER_BACKEND_PRESET="vllm" ;;
    4|openai) VANER_BACKEND_PRESET="openai" ;;
    5|anthropic|claude) VANER_BACKEND_PRESET="anthropic" ;;
    6|openrouter) VANER_BACKEND_PRESET="openrouter" ;;
    7|skip|"") VANER_BACKEND_PRESET="skip" ;;
    *)
      ui_warn "Unrecognized choice '$choice'; skipping backend configuration."
      VANER_BACKEND_PRESET="skip"
      ;;
  esac
  confirm_cloud_backend_costs "$VANER_BACKEND_PRESET"
}

collect_backend_details() {
  case "$VANER_BACKEND_PRESET" in
    ollama)
      VANER_BACKEND_URL="${VANER_BACKEND_URL:-http://127.0.0.1:11434/v1}"
      local default_ollama_model="qwen2.5-coder:7b"
      if [[ -z "$VANER_BACKEND_MODEL" ]]; then
        VANER_BACKEND_MODEL="$(select_existing_ollama_model "$default_ollama_model")"
      fi
      VANER_BACKEND_API_KEY_ENV="${VANER_BACKEND_API_KEY_ENV:-}"
      VANER_WITH_OLLAMA=1
      ;;
    lmstudio)
      local default_url="http://127.0.0.1:1234/v1"
      VANER_BACKEND_URL="${VANER_BACKEND_URL:-$(prompt_line "LM Studio base URL" "$default_url")}"
      VANER_BACKEND_MODEL="${VANER_BACKEND_MODEL:-$(prompt_line "Model name" "")}"
      VANER_BACKEND_API_KEY_ENV="${VANER_BACKEND_API_KEY_ENV:-}"
      ;;
    vllm)
      local default_url="http://127.0.0.1:8000/v1"
      VANER_BACKEND_URL="${VANER_BACKEND_URL:-$(prompt_line "Server base URL" "$default_url")}"
      VANER_BACKEND_MODEL="${VANER_BACKEND_MODEL:-$(prompt_line "Model name" "")}"
      VANER_BACKEND_API_KEY_ENV="${VANER_BACKEND_API_KEY_ENV:-}"
      ;;
    openai)
      VANER_BACKEND_URL="${VANER_BACKEND_URL:-https://api.openai.com/v1}"
      VANER_BACKEND_MODEL="${VANER_BACKEND_MODEL:-$(prompt_line "Model name" "gpt-4o")}"
      VANER_BACKEND_API_KEY_ENV="${VANER_BACKEND_API_KEY_ENV:-OPENAI_API_KEY}"
      ;;
    anthropic)
      VANER_BACKEND_URL="${VANER_BACKEND_URL:-https://api.anthropic.com/v1}"
      VANER_BACKEND_MODEL="${VANER_BACKEND_MODEL:-$(prompt_line "Model name" "claude-opus-4-5")}"
      VANER_BACKEND_API_KEY_ENV="${VANER_BACKEND_API_KEY_ENV:-ANTHROPIC_API_KEY}"
      ;;
    openrouter)
      VANER_BACKEND_URL="${VANER_BACKEND_URL:-https://openrouter.ai/api/v1}"
      VANER_BACKEND_MODEL="${VANER_BACKEND_MODEL:-$(prompt_line "Model name" "anthropic/claude-3.5-sonnet")}"
      VANER_BACKEND_API_KEY_ENV="${VANER_BACKEND_API_KEY_ENV:-OPENROUTER_API_KEY}"
      ;;
    skip|"")
      ;;
    *)
      ui_warn "Unknown backend preset: $VANER_BACKEND_PRESET. Skipping."
      VANER_BACKEND_PRESET="skip"
      ;;
  esac
}

resolve_compute_preferences() {
  if [[ -z "$VANER_COMPUTE_PRESET" ]]; then
    if is_promptable; then
      {
        printf '\n'
        printf 'Pick a compute budget:\n'
        printf '  1) background — idle-first and battery-safe (recommended)\n'
        printf '  2) balanced   — available while you work, still bounded\n'
        printf '  3) dedicated  — aggressive use for dedicated hardware\n'
      } > /dev/tty
      local preset_choice
      preset_choice="$(prompt_line "Choice" "1")"
      case "$preset_choice" in
        1|background|"") VANER_COMPUTE_PRESET="background" ;;
        2|balanced) VANER_COMPUTE_PRESET="balanced" ;;
        3|dedicated) VANER_COMPUTE_PRESET="dedicated" ;;
        *)
          ui_warn "Unrecognized compute preset '$preset_choice'; defaulting to background."
          VANER_COMPUTE_PRESET="background"
          ;;
      esac
    else
      VANER_COMPUTE_PRESET="${VANER_COMPUTE_PRESET:-background}"
    fi
  fi

  if [[ -z "$VANER_MAX_SESSION_MINUTES" ]] && is_promptable; then
    local minutes
    minutes="$(prompt_line "Cap a continuous vaner daemon session (blank for unlimited, minutes)" "")"
    if [[ -n "$minutes" ]]; then
      if [[ "$minutes" =~ ^[0-9]+$ ]] && [[ "$minutes" -gt 0 ]]; then
        VANER_MAX_SESSION_MINUTES="$minutes"
      else
        ui_warn "Ignoring invalid max-session-minutes value: '$minutes' (must be a positive integer)."
      fi
    fi
  fi
}

configure_backend_and_compute() {
  if [[ "$VANER_DRY_RUN" == "1" ]]; then
    ui_info "Dry-run: skipping backend/compute configuration."
    return 0
  fi
  if ! command -v vaner >/dev/null 2>&1; then
    ui_warn "vaner not yet on PATH; skipping config writeback. Run 'vaner init' manually after opening a new shell."
    return 0
  fi
  local args=("init" "--path" "$HOME" "--no-interactive")
  if [[ -n "$VANER_BACKEND_PRESET" && "$VANER_BACKEND_PRESET" != "skip" ]]; then
    args+=("--backend-preset" "$VANER_BACKEND_PRESET")
  fi
  if [[ -n "$VANER_BACKEND_URL" ]]; then
    args+=("--backend-url" "$VANER_BACKEND_URL")
  fi
  if [[ -n "$VANER_BACKEND_MODEL" ]]; then
    args+=("--backend-model" "$VANER_BACKEND_MODEL")
  fi
  if [[ -n "$VANER_BACKEND_API_KEY_ENV" ]]; then
    args+=("--backend-api-key-env" "$VANER_BACKEND_API_KEY_ENV")
  fi
  if [[ -n "$VANER_COMPUTE_PRESET" ]]; then
    args+=("--compute-preset" "$VANER_COMPUTE_PRESET")
  fi
  if [[ -n "$VANER_MAX_SESSION_MINUTES" ]]; then
    args+=("--max-session-minutes" "$VANER_MAX_SESSION_MINUTES")
  fi
  ui_info "Writing backend/compute defaults to ~/.vaner/config.toml"
  run_cmd vaner "${args[@]}" || ui_warn "vaner init returned non-zero; continuing."
}

print_footer() {
  printf '\n'
  if [[ "$IS_UPGRADE" == "1" && -n "$OLD_VERSION" ]]; then
    ui_success "Vaner upgrade complete (from: ${OLD_VERSION})"
  else
    ui_success "Vaner installed successfully"
  fi
  printf '\nNext steps:\n'
  printf '  cd /path/to/your/repo\n'
  printf '  vaner up --path .              # starts daemon + cockpit together\n'
  printf '  vaner doctor --path .          # if anything looks off\n'
  printf '\nConnect your AI client over MCP:\n'
  printf '  https://docs.vaner.ai/mcp      # Claude Code, Cursor, VS Code, Codex, ...\n'
  printf '\nTroubleshooting:\n'
  printf '  https://docs.vaner.ai/troubleshooting\n'
  if [[ "$VANER_NO_MCP" == "1" ]]; then
    printf '\n  Note: installed without the [mcp] extra. Re-run without --no-mcp to enable\n'
    printf "        'vaner mcp' for client integration.\n"
  fi
}

main() {
  parse_args "$@"

  if [[ "$HELP" == "1" ]]; then
    print_usage
    return 0
  fi

  ui_section "Vaner Installer"
  resolve_backend_preset
  if [[ "$VANER_BACKEND_PRESET" == "ollama" ]]; then
    VANER_WITH_OLLAMA=1
  fi
  collect_backend_details
  resolve_compute_preferences
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
  print_extra_hint

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

  configure_backend_and_compute

  print_footer
}

main "$@"
