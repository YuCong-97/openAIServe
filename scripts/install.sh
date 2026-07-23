#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPONENTS="all"
PROFILE="rtx3090"
DOWNLOAD_MODELS="false"
INCLUDE_OPTIONAL="false"
START_AFTER="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --components)
      COMPONENTS="$2"
      shift 2
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --download-models)
      DOWNLOAD_MODELS="true"
      shift
      ;;
    --include-optional-models)
      INCLUDE_OPTIONAL="true"
      shift
      ;;
    --start)
      START_AFTER="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

has_component() {
  local name="$1"
  [[ ",${COMPONENTS,,}," == *",all,"* || ",${COMPONENTS,,}," == *",${name},"* ]]
}

run_privileged() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "This installer needs root privileges to install system packages. Re-run as root or install sudo." >&2
    exit 1
  fi
}

extract_zstd_archive_to_usr() {
  local archive="$1"
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    zstd -dc "$archive" | tar -C /usr -xf -
  elif command -v sudo >/dev/null 2>&1; then
    zstd -dc "$archive" | sudo tar -C /usr -xf -
  else
    echo "This installer needs root privileges to install Ollama under /usr. Re-run as root or install sudo." >&2
    return 1
  fi
}

apt_install_prereqs() {
  local packages=(python3 python3-venv git curl ca-certificates zstd)
  for attempt in 1 2 3; do
    echo "[system] apt install attempt $attempt/3"
    run_privileged apt-get clean
    if run_privileged apt-get -o Acquire::Retries=3 update &&
      run_privileged apt-get -o Acquire::Retries=3 install -y --no-install-recommends --fix-missing "${packages[@]}"; then
      return
    fi
    echo "[system] apt mirror may be syncing; retrying after a short delay" >&2
    sleep $((attempt * 10))
  done

  echo "apt failed after retries. Your configured mirror may be mid-sync; run apt-get update later or switch to another Ubuntu mirror, then rerun this script." >&2
  exit 1
}

install_linux_prereqs() {
  echo "[system] checking Linux prerequisites"
  local missing="false"
  for cmd in git curl zstd; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing="true"
    fi
  done
  if ! command -v python3 >/dev/null 2>&1; then
    missing="true"
  else
    local venv_check_dir
    venv_check_dir="$(mktemp -d)"
    if ! python3 -m venv "$venv_check_dir/check" >/dev/null 2>&1; then
      missing="true"
    fi
    rm -rf "$venv_check_dir"
  fi

  if [[ "$missing" == "false" ]]; then
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt_install_prereqs
  elif command -v dnf >/dev/null 2>&1; then
    run_privileged dnf install -y python3 git curl ca-certificates zstd
  elif command -v yum >/dev/null 2>&1; then
    run_privileged yum install -y python3 git curl ca-certificates zstd
  elif command -v pacman >/dev/null 2>&1; then
    run_privileged pacman -Sy --noconfirm --needed python git curl ca-certificates zstd
  elif command -v zypper >/dev/null 2>&1; then
    run_privileged zypper --non-interactive install python3 git curl ca-certificates zstd
  elif command -v apk >/dev/null 2>&1; then
    run_privileged apk add --no-cache python3 py3-virtualenv git curl ca-certificates zstd
  else
    echo "Unsupported Linux package manager. Install python3, python3-venv, git, curl, ca-certificates, and zstd, then rerun this script." >&2
    exit 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is still unavailable after prerequisite installation." >&2
    exit 1
  fi
  if ! command -v zstd >/dev/null 2>&1; then
    echo "zstd is still unavailable after prerequisite installation." >&2
    exit 1
  fi
  local final_venv_check_dir
  final_venv_check_dir="$(mktemp -d)"
  if ! python3 -m venv "$final_venv_check_dir/check" >/dev/null 2>&1; then
    rm -rf "$final_venv_check_dir"
    echo "python3 venv support is unavailable. Install python3-venv or the equivalent package, then rerun." >&2
    exit 1
  fi
  rm -rf "$final_venv_check_dir"
}

download_with_retries() {
  local url="$1"
  local output="$2"
  for attempt in 1 2 3; do
    if curl -fL --connect-timeout 20 --max-time 120 "$url" -o "$output"; then
      return
    fi
    echo "[download] failed attempt $attempt/3 for $url" >&2
    sleep $((attempt * 5))
  done
  return 1
}

python_cmd() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
  elif command -v python >/dev/null 2>&1; then
    python "$@"
  else
    echo "Neither python3 nor python is available. Install Python 3 and rerun this script." >&2
    exit 1
  fi
}

ollama_archive_name() {
  case "$(uname -m)" in
    x86_64|amd64)
      echo "ollama-linux-amd64.tar.zst"
      ;;
    aarch64|arm64)
      echo "ollama-linux-arm64.tar.zst"
      ;;
    *)
      echo "Unsupported CPU architecture for Ollama archive: $(uname -m)" >&2
      return 1
      ;;
  esac
}

install_ollama_from_archive() {
  local archive_name
  archive_name="$(ollama_archive_name)"
  local url="${OLLAMA_INSTALL_URL:-https://github.com/ollama/ollama/releases/latest/download/$archive_name}"
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  local archive="$tmp_dir/$archive_name"

  echo "[ollama] downloading archive from $url"
  if ! download_with_retries "$url" "$archive"; then
    rm -rf "$tmp_dir"
    echo "Ollama archive download failed. Set OLLAMA_INSTALL_URL to a reachable mirror URL and rerun." >&2
    return 1
  fi

  echo "[ollama] extracting archive to /usr"
  extract_zstd_archive_to_usr "$archive"
  rm -rf "$tmp_dir"

  if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama archive extracted, but ollama is still not on PATH." >&2
    return 1
  fi
}

install_ollama_from_official_script() {
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  local installer="$tmp_dir/install.sh"

  if ! download_with_retries "https://ollama.com/install.sh" "$installer"; then
    rm -rf "$tmp_dir"
    return 1
  fi

  sh "$installer"
  local status=$?
  rm -rf "$tmp_dir"
  return "$status"
}

install_server() {
  echo "[server] creating Python venv and installing API server dependencies"
  if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    python_cmd -m venv "$ROOT/.venv"
  fi
  "$ROOT/.venv/bin/python" -m pip install --upgrade pip
  "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
  if [[ ! -f "$ROOT/config.yaml" ]]; then
    cp "$ROOT/config.example.yaml" "$ROOT/config.yaml"
    echo "[server] created config.yaml from config.example.yaml"
  fi
}

install_ollama() {
  echo "[ollama] checking Ollama"
  if command -v ollama >/dev/null 2>&1; then
    echo "[ollama] already installed"
    return
  fi
  echo "[ollama] installing from ollama.com"
  if install_ollama_from_official_script && command -v ollama >/dev/null 2>&1; then
    return
  fi
  echo "[ollama] ollama.com install failed; falling back to GitHub release archive" >&2
  install_ollama_from_archive
}

install_comfyui() {
  echo "[comfyui] installing ComfyUI"
  mkdir -p "$ROOT/deps"
  if [[ ! -d "$ROOT/deps/ComfyUI" ]]; then
    git clone https://github.com/comfyanonymous/ComfyUI.git "$ROOT/deps/ComfyUI"
  else
    git -C "$ROOT/deps/ComfyUI" pull --ff-only
  fi

  if [[ ! -x "$ROOT/deps/ComfyUI/.venv/bin/python" ]]; then
    python_cmd -m venv "$ROOT/deps/ComfyUI/.venv"
  fi
  "$ROOT/deps/ComfyUI/.venv/bin/python" -m pip install --upgrade pip
  "$ROOT/deps/ComfyUI/.venv/bin/python" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  "$ROOT/deps/ComfyUI/.venv/bin/python" -m pip install -r "$ROOT/deps/ComfyUI/requirements.txt"
}

install_linux_prereqs
install_server

pids=()
if has_component "ollama"; then
  install_ollama &
  pids+=("$!")
fi
if has_component "comfyui"; then
  install_comfyui &
  pids+=("$!")
fi

failed_jobs=()
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    failed_jobs+=("$pid")
  fi
done

if [[ "${#failed_jobs[@]}" -gt 0 ]]; then
  echo "One or more component installers failed. Review the log above and rerun after fixing network or package manager access." >&2
  exit 1
fi

if [[ "$DOWNLOAD_MODELS" == "true" ]]; then
  args=("$ROOT/scripts/download_models.py" --profile "$PROFILE" --components "$COMPONENTS")
  if [[ "$INCLUDE_OPTIONAL" == "true" ]]; then
    args+=(--include-optional)
  fi
  "$ROOT/.venv/bin/python" "${args[@]}"
fi

if [[ "$START_AFTER" == "true" ]]; then
  bash "$ROOT/scripts/start.sh" --components "$COMPONENTS"
fi

echo "Install complete."
