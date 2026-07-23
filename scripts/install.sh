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

apt_install_prereqs() {
  local packages=(python3 python3-venv git curl ca-certificates)
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
  for cmd in git curl; do
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
    run_privileged dnf install -y python3 git curl ca-certificates
  elif command -v yum >/dev/null 2>&1; then
    run_privileged yum install -y python3 git curl ca-certificates
  elif command -v pacman >/dev/null 2>&1; then
    run_privileged pacman -Sy --noconfirm --needed python git curl ca-certificates
  elif command -v zypper >/dev/null 2>&1; then
    run_privileged zypper --non-interactive install python3 git curl ca-certificates
  elif command -v apk >/dev/null 2>&1; then
    run_privileged apk add --no-cache python3 py3-virtualenv git curl ca-certificates
  else
    echo "Unsupported Linux package manager. Install python3, python3-venv, git, and curl, then rerun this script." >&2
    exit 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is still unavailable after prerequisite installation." >&2
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
  curl -fsSL https://ollama.com/install.sh | sh
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

for pid in "${pids[@]}"; do
  wait "$pid"
done

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
