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

pip_install_with_retries() {
  local python_bin="$1"
  shift
  for attempt in 1 2; do
    if "$python_bin" -m pip install --timeout 60 --retries 2 "$@"; then
      return
    fi
    echo "[pip] failed attempt $attempt/2: $*" >&2
    sleep $((attempt * 5))
  done
  return 1
}

git_with_retries() {
  for attempt in 1 2 3; do
    if GIT_TERMINAL_PROMPT=0 git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 "$@"; then
      return
    fi
    echo "[git] failed attempt $attempt/3: git $*" >&2
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

comfyui_git_urls() {
  if [[ -n "${COMFYUI_GIT_URL:-}" ]]; then
    urls=("$COMFYUI_GIT_URL")
  elif [[ -n "${COMFYUI_GIT_URLS:-}" ]]; then
    read -r -a urls <<<"$COMFYUI_GIT_URLS"
  else
    urls=(
      "https://github.com/Comfy-Org/ComfyUI.git"
      "https://gitcode.com/gh_mirrors/co/ComfyUI.git"
      "https://gitee.com/mirrors/ComfyUI.git"
      "https://gitee.com/mirrors/comfyui.git"
    )
  fi
}

clone_comfyui() {
  local comfy_dir="$1"
  local urls=()
  comfyui_git_urls

  for url in "${urls[@]}"; do
    local tmp_dir="$comfy_dir.clone.$$"
    rm -rf "$tmp_dir"
    echo "[comfyui] cloning from $url"
    if git_with_retries clone --depth 1 "$url" "$tmp_dir"; then
      mv "$tmp_dir" "$comfy_dir"
      return
    fi
    rm -rf "$tmp_dir"
  done

  echo "ComfyUI clone failed. Set COMFYUI_GIT_URL or COMFYUI_GIT_URLS to reachable mirror URL(s) and rerun." >&2
  return 1
}

update_comfyui() {
  local comfy_dir="$1"
  local urls=()
  comfyui_git_urls

  for url in "${urls[@]}"; do
    echo "[comfyui] updating from $url"
    git -C "$comfy_dir" remote set-url origin "$url" || true
    if git_with_retries -C "$comfy_dir" pull --ff-only; then
      return
    fi
  done

  echo "ComfyUI update failed. Set COMFYUI_GIT_URL or COMFYUI_GIT_URLS to reachable mirror URL(s) and rerun." >&2
  return 1
}

torch_packages() {
  if [[ -n "${TORCH_PACKAGES:-}" ]]; then
    read -r -a packages <<<"$TORCH_PACKAGES"
  else
    packages=(torch torchvision torchaudio)
  fi
}

install_torch() {
  local python_bin="$1"
  if "$python_bin" -c "import torch" >/dev/null 2>&1; then
    echo "[comfyui] torch already installed"
    return
  fi

  local packages
  torch_packages
  packages=("${packages[@]}")

  if [[ -n "${TORCH_INSTALL_CMD:-}" ]]; then
    echo "[comfyui] running custom TORCH_INSTALL_CMD"
    COMFYUI_PYTHON="$python_bin" sh -c "$TORCH_INSTALL_CMD"
    return
  fi

  local indexes=()
  if [[ -n "${TORCH_INDEX_URL:-}" ]]; then
    indexes+=("$TORCH_INDEX_URL")
  else
    indexes+=(
      "https://download.pytorch.org/whl/cu128"
      "https://mirrors.aliyun.com/pytorch-wheels/cu128"
      "https://mirror.nju.edu.cn/pytorch/whl/cu128"
      "https://download.pytorch.org/whl/cu126"
      "https://mirrors.aliyun.com/pytorch-wheels/cu126"
      "https://mirror.nju.edu.cn/pytorch/whl/cu126"
    )
  fi

  for index_url in "${indexes[@]}"; do
    echo "[comfyui] installing torch from $index_url"
    if pip_install_with_retries "$python_bin" "${packages[@]}" --index-url "$index_url"; then
      return
    fi
  done

  echo "[comfyui] torch index installs failed; trying default pip index" >&2
  if pip_install_with_retries "$python_bin" "${packages[@]}"; then
    return
  fi

  echo "Torch install failed. Set TORCH_INDEX_URL to a reachable PyTorch wheel mirror or set TORCH_INSTALL_CMD for a custom install command." >&2
  return 1
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
  local urls=()
  if [[ -n "${OLLAMA_INSTALL_URL:-}" ]]; then
    urls+=("$OLLAMA_INSTALL_URL")
  elif [[ -n "${OLLAMA_ARCHIVE_URLS:-}" ]]; then
    read -r -a urls <<<"$OLLAMA_ARCHIVE_URLS"
  else
    urls+=(
      "https://ollama.ac.cn/download/$archive_name"
      "https://github.com/ollama/ollama/releases/latest/download/$archive_name"
    )
  fi
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  local archive="$tmp_dir/$archive_name"

  for url in "${urls[@]}"; do
    echo "[ollama] downloading archive from $url"
    if ! download_with_retries "$url" "$archive"; then
      continue
    fi

    echo "[ollama] extracting archive to /usr"
    if extract_zstd_archive_to_usr "$archive" && command -v ollama >/dev/null 2>&1; then
      rm -rf "$tmp_dir"
      return
    fi
  done

  rm -rf "$tmp_dir"
  echo "Ollama archive download/install failed. Set OLLAMA_INSTALL_URL or OLLAMA_ARCHIVE_URLS to reachable mirror URL(s) and rerun." >&2
  return 1
}

install_ollama_from_official_script() {
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  local installer="$tmp_dir/install.sh"
  local urls=()
  if [[ -n "${OLLAMA_INSTALL_SCRIPT_URL:-}" ]]; then
    urls+=("$OLLAMA_INSTALL_SCRIPT_URL")
  elif [[ -n "${OLLAMA_INSTALL_SCRIPT_URLS:-}" ]]; then
    read -r -a urls <<<"$OLLAMA_INSTALL_SCRIPT_URLS"
  else
    urls+=(
      "https://ollama.com/install.sh"
      "https://ollama.ac.cn/install.sh"
    )
  fi

  for url in "${urls[@]}"; do
    echo "[ollama] downloading install script from $url"
    if ! download_with_retries "$url" "$installer"; then
      continue
    fi
    if sh "$installer" && command -v ollama >/dev/null 2>&1; then
      rm -rf "$tmp_dir"
      return
    fi
  done
  rm -rf "$tmp_dir"
  return 1
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
  echo "[ollama] installing from script mirrors"
  if install_ollama_from_official_script && command -v ollama >/dev/null 2>&1; then
    return
  fi
  echo "[ollama] install scripts failed; falling back to archive mirrors" >&2
  install_ollama_from_archive
}

install_comfyui() {
  echo "[comfyui] installing ComfyUI"
  mkdir -p "$ROOT/deps"
  if [[ ! -d "$ROOT/deps/ComfyUI" ]]; then
    clone_comfyui "$ROOT/deps/ComfyUI"
  else
    update_comfyui "$ROOT/deps/ComfyUI"
  fi

  if [[ ! -x "$ROOT/deps/ComfyUI/.venv/bin/python" ]]; then
    python_cmd -m venv "$ROOT/deps/ComfyUI/.venv"
  fi
  "$ROOT/deps/ComfyUI/.venv/bin/python" -m pip install --upgrade pip
  install_torch "$ROOT/deps/ComfyUI/.venv/bin/python"
  "$ROOT/deps/ComfyUI/.venv/bin/python" -m pip install -r "$ROOT/deps/ComfyUI/requirements.txt"
}

install_linux_prereqs
install_server

pids=()
pid_names=()
if has_component "ollama"; then
  install_ollama &
  pids+=("$!")
  pid_names+=("ollama")
fi
if has_component "comfyui"; then
  install_comfyui &
  pids+=("$!")
  pid_names+=("comfyui")
fi

failed_jobs=()
for index in "${!pids[@]}"; do
  pid="${pids[$index]}"
  if ! wait "$pid"; then
    failed_jobs+=("${pid_names[$index]}")
  fi
done

if [[ "${#failed_jobs[@]}" -gt 0 ]]; then
  echo "Component installer(s) failed: ${failed_jobs[*]}. Review the log above and rerun after fixing network or package manager access." >&2
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
