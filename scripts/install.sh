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

python_cmd() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
  else
    python "$@"
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

