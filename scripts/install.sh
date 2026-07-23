#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPONENTS="all"
PROFILE="rtx3090"
DOWNLOAD_MODELS="false"
INCLUDE_OPTIONAL="false"
START_AFTER="false"

# Linux deployment defaults are tuned for RTX 3090 hosts on restricted China networks.
export OLLAMA_PULL_FALLBACK="${OLLAMA_PULL_FALLBACK:-false}"
export MODEL_DIRECT_URL_TEMPLATES="${MODEL_DIRECT_URL_TEMPLATES:-https://modelscope.cn/models/{repo_id}/resolve/master/{filename}}"
export HF_ENDPOINTS="${HF_ENDPOINTS:-https://hf-mirror.com https://huggingface.co}"

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
    --include-optional-models|--include-optional)
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
  local connect_timeout="${DOWNLOAD_CONNECT_TIMEOUT:-20}"
  local max_time="${DOWNLOAD_MAX_TIME:-0}"
  for attempt in 1 2 3; do
    if curl -fL --retry 3 --retry-delay 5 --connect-timeout "$connect_timeout" --max-time "$max_time" -C - "$url" -o "$output"; then
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

cuda_version_code() {
  local version="$1"
  local major="${version%%.*}"
  local rest="${version#*.}"
  local minor="${rest%%.*}"
  if [[ -z "$major" || "$major" == "$version" ]]; then
    minor="0"
  fi
  echo $((10#$major * 100 + 10#$minor))
}

nvidia_cuda_version() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    return 1
  fi
  nvidia-smi | sed -n 's/.*CUDA Version: \([0-9][0-9.]*\).*/\1/p' | head -n 1
}

torch_cuda_variant() {
  if [[ -n "${TORCH_CUDA_VARIANT:-}" ]]; then
    echo "$TORCH_CUDA_VARIANT"
    return
  fi

  local cuda_version
  cuda_version="$(nvidia_cuda_version || true)"
  if [[ -z "$cuda_version" ]]; then
    echo "cpu"
    return
  fi

  local code
  code="$(cuda_version_code "$cuda_version")"
  if ((code >= 1208)); then
    echo "cu128"
  elif ((code >= 1206)); then
    echo "cu126"
  elif ((code >= 1204)); then
    echo "cu124"
  elif ((code >= 1201)); then
    echo "cu121"
  elif ((code >= 1108)); then
    echo "cu118"
  else
    echo "cpu"
  fi
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

torch_index_urls_for_variant() {
  local variant="$1"
  urls=()

  if [[ -n "${TORCH_INDEX_URL:-}" ]]; then
    urls+=("$TORCH_INDEX_URL")
  fi
  if [[ -n "${TORCH_INDEX_URLS:-}" ]]; then
    local configured_urls=()
    read -r -a configured_urls <<<"$TORCH_INDEX_URLS"
    urls+=("${configured_urls[@]}")
  fi
  if [[ "${TORCH_INDEX_URL_ONLY:-false}" == "true" ]]; then
    return
  fi
  if [[ "$variant" == "cpu" ]]; then
    urls+=(
      "https://mirrors.aliyun.com/pytorch-wheels/cpu"
      "https://mirror.nju.edu.cn/pytorch/whl/cpu"
      "https://download.pytorch.org/whl/cpu"
    )
    return
  fi

  urls+=(
    "https://mirrors.aliyun.com/pytorch-wheels/$variant"
    "https://mirror.nju.edu.cn/pytorch/whl/$variant"
    "https://download.pytorch.org/whl/$variant"
  )
}

torch_runtime_ok() {
  local python_bin="$1"
  if ! "$python_bin" -c "import torch" >/dev/null 2>&1; then
    return 1
  fi

  if command -v nvidia-smi >/dev/null 2>&1 && [[ "${TORCH_ALLOW_CPU:-false}" != "true" ]]; then
    "$python_bin" -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; torch.cuda.current_device()" >/dev/null 2>&1
    return
  fi

  return 0
}

comfyui_git_urls() {
  if [[ -n "${COMFYUI_GIT_URL:-}" ]]; then
    urls=("$COMFYUI_GIT_URL")
  elif [[ -n "${COMFYUI_GIT_URLS:-}" ]]; then
    read -r -a urls <<<"$COMFYUI_GIT_URLS"
  else
    urls=(
      "https://gitcode.com/gh_mirrors/co/ComfyUI.git"
      "https://gitee.com/mirrors/ComfyUI.git"
      "https://gitee.com/mirrors/comfyui.git"
      "https://github.com/Comfy-Org/ComfyUI.git"
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
  if torch_runtime_ok "$python_bin"; then
    echo "[comfyui] torch already installed and runtime check passed"
    return
  fi

  local packages
  torch_packages
  packages=("${packages[@]}")
  local variant
  variant="$(torch_cuda_variant)"
  local driver_cuda
  driver_cuda="$(nvidia_cuda_version || true)"

  if [[ -n "$driver_cuda" ]]; then
    echo "[comfyui] NVIDIA driver reports CUDA $driver_cuda; selecting PyTorch $variant wheels"
  else
    echo "[comfyui] NVIDIA driver not detected; selecting PyTorch $variant wheels"
  fi

  if "$python_bin" -c "import torch" >/dev/null 2>&1; then
    echo "[comfyui] existing torch failed runtime check; reinstalling"
    "$python_bin" -m pip uninstall -y torch torchvision torchaudio >/dev/null 2>&1 || true
  fi

  if [[ -n "${TORCH_INSTALL_CMD:-}" ]]; then
    echo "[comfyui] running custom TORCH_INSTALL_CMD"
    COMFYUI_PYTHON="$python_bin" sh -c "$TORCH_INSTALL_CMD"
    if torch_runtime_ok "$python_bin"; then
      return
    fi
    echo "Custom TORCH_INSTALL_CMD completed, but torch runtime check still failed." >&2
    return 1
  fi

  local indexes=()
  torch_index_urls_for_variant "$variant"
  indexes=("${urls[@]}")

  if [[ -n "${TORCH_INDEX_URL:-}" && "${TORCH_INDEX_URL_ONLY:-false}" != "true" ]]; then
    echo "[comfyui] TORCH_INDEX_URL is tried first; auto-selected $variant mirrors will be tried after it if runtime check fails"
  fi

  for index_url in "${indexes[@]}"; do
    echo "[comfyui] installing torch from $index_url"
    if pip_install_with_retries "$python_bin" --upgrade --force-reinstall "${packages[@]}" --index-url "$index_url" &&
      torch_runtime_ok "$python_bin"; then
      return
    fi
    "$python_bin" -m pip uninstall -y torch torchvision torchaudio >/dev/null 2>&1 || true
  done

  if [[ "$variant" == "cpu" || "${TORCH_ALLOW_DEFAULT_PIP:-false}" == "true" ]]; then
    echo "[comfyui] torch index installs failed; trying default pip index" >&2
    if pip_install_with_retries "$python_bin" --upgrade --force-reinstall "${packages[@]}" &&
      torch_runtime_ok "$python_bin"; then
      return
    fi
  fi

  if [[ "$variant" == "cu128" || "$variant" == "cu126" ]]; then
    echo "[comfyui] trying lower CUDA 12.4 wheels as a compatibility fallback"
    local fallback_urls=()
    urls=()
    torch_index_urls_for_variant "cu124"
    fallback_urls=("${urls[@]}")
    for index_url in "${fallback_urls[@]}"; do
      echo "[comfyui] installing torch from $index_url"
      if pip_install_with_retries "$python_bin" --upgrade --force-reinstall "${packages[@]}" --index-url "$index_url" &&
        torch_runtime_ok "$python_bin"; then
        return
      fi
      "$python_bin" -m pip uninstall -y torch torchvision torchaudio >/dev/null 2>&1 || true
    done
  fi

  echo "Torch install failed or CUDA runtime check failed. Your driver CUDA is '${driver_cuda:-not detected}', selected variant was '$variant'. Set TORCH_CUDA_VARIANT=cu124 or TORCH_INDEX_URL to a reachable compatible PyTorch wheel mirror." >&2
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

ollama_archive_urls() {
  local archive_name="$1"
  if [[ -n "${OLLAMA_INSTALL_URL:-}" ]]; then
    printf '%s\n' "$OLLAMA_INSTALL_URL"
    return
  fi
  if [[ -n "${OLLAMA_ARCHIVE_URLS:-}" ]]; then
    local configured_urls=()
    read -r -a configured_urls <<<"$OLLAMA_ARCHIVE_URLS"
    printf '%s\n' "${configured_urls[@]}"
    return
  fi

  local modelscope_ref="${OLLAMA_MODELSCOPE_REVISION:-master}"
  if [[ -n "${OLLAMA_VERSION:-}" ]]; then
    modelscope_ref="v${OLLAMA_VERSION#v}"
  fi
  local modelscope_model="${OLLAMA_MODELSCOPE_MODEL:-modelscope/ollama-linux}"

  if [[ "$archive_name" == "ollama-linux-amd64.tar.zst" && "${OLLAMA_DISABLE_MODELSCOPE:-false}" != "true" ]]; then
    printf '%s\n' "https://modelscope.cn/models/$modelscope_model/resolve/$modelscope_ref/$archive_name"
    if [[ "$modelscope_ref" != "master" ]]; then
      printf '%s\n' "https://modelscope.cn/models/$modelscope_model/resolve/master/$archive_name"
    fi
  fi
  printf '%s\n' "https://ollama.ac.cn/download/$archive_name"
  printf '%s\n' "https://github.com/ollama/ollama/releases/latest/download/$archive_name"
}

ollama_local_archive_candidates() {
  local archive_name="$1"
  if [[ -n "${OLLAMA_ARCHIVE_FILE:-}" ]]; then
    printf '%s\n' "$OLLAMA_ARCHIVE_FILE"
  fi
  printf '%s\n' "$ROOT/packages/$archive_name"
  printf '%s\n' "$ROOT/downloads/$archive_name"
  printf '%s\n' "$ROOT/deps/downloads/$archive_name"
  printf '%s\n' "$ROOT/$archive_name"
}

install_ollama_from_local_archive() {
  local archive_name
  archive_name="$(ollama_archive_name)"
  local archive
  while IFS= read -r archive; do
    if [[ -f "$archive" ]]; then
      echo "[ollama] installing local archive $archive"
      if extract_zstd_archive_to_usr "$archive" && command -v ollama >/dev/null 2>&1; then
        return
      fi
    fi
  done < <(ollama_local_archive_candidates "$archive_name")
  return 1
}

install_ollama_from_archive() {
  local archive_name
  archive_name="$(ollama_archive_name)"
  local urls=()
  mapfile -t urls < <(ollama_archive_urls "$archive_name")
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  local archive="$tmp_dir/$archive_name"

  for url in "${urls[@]}"; do
    rm -f "$archive"
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
  echo "Ollama archive download/install failed. The default list includes ModelScope, ollama.ac.cn, and GitHub release URLs. Set OLLAMA_ARCHIVE_FILE, OLLAMA_INSTALL_URL, or OLLAMA_ARCHIVE_URLS to a reachable local file or mirror URL and rerun." >&2
  return 1
}

configure_ollama_service() {
  if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    echo "[ollama] systemd not detected; start.sh will run ollama serve when needed"
    return
  fi

  echo "[ollama] configuring systemd service"
  local ollama_bin
  ollama_bin="$(command -v ollama)"
  local service_user="root"
  local service_group="root"
  if command -v useradd >/dev/null 2>&1; then
    run_privileged useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama 2>/dev/null || true
    if id ollama >/dev/null 2>&1; then
      service_user="ollama"
      service_group="ollama"
      for group in render video; do
        if getent group "$group" >/dev/null 2>&1; then
          run_privileged usermod -a -G "$group" ollama 2>/dev/null || true
        fi
      done
    fi
  fi

  local service_file
  service_file="$(mktemp)"
  cat >"$service_file" <<SERVICE
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=$ollama_bin serve
User=$service_user
Group=$service_group
Restart=always
RestartSec=3
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="OLLAMA_HOST=127.0.0.1:11434"

[Install]
WantedBy=default.target
SERVICE

  run_privileged install -m 0644 "$service_file" /etc/systemd/system/ollama.service
  rm -f "$service_file"
  run_privileged systemctl daemon-reload || true
  run_privileged systemctl enable --now ollama || echo "[ollama] systemd service created but failed to start; start.sh can still run ollama serve" >&2
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
      "https://ollama.ac.cn/install.sh"
      "https://ollama.com/install.sh"
    )
  fi

  for url in "${urls[@]}"; do
    rm -f "$installer"
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
    ollama --version || true
    configure_ollama_service
    return
  fi

  local method="${OLLAMA_INSTALL_METHOD:-auto}"
  case "$method" in
    local)
      echo "[ollama] installing from local archive"
      install_ollama_from_local_archive
      ;;
    archive)
      echo "[ollama] installing from archive mirrors"
      install_ollama_from_local_archive || install_ollama_from_archive
      ;;
    script)
      echo "[ollama] installing from script mirrors"
      install_ollama_from_official_script || install_ollama_from_archive
      ;;
    skip)
      echo "[ollama] skipping Ollama installation because OLLAMA_INSTALL_METHOD=skip"
      return
      ;;
    auto)
      echo "[ollama] installing from local archive or archive mirrors, then script mirrors if needed"
      install_ollama_from_local_archive || install_ollama_from_archive || install_ollama_from_official_script
      ;;
    *)
      echo "Unknown OLLAMA_INSTALL_METHOD: $method. Use local, archive, script, skip, or auto." >&2
      return 1
      ;;
  esac

  if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama installation completed without an ollama command on PATH." >&2
    return 1
  fi

  ollama --version || true
  configure_ollama_service
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
