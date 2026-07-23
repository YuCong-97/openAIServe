#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT/config/runtime.env" ]]; then
  source "$ROOT/config/runtime.env"
fi

COMPONENTS="all"
HOST="0.0.0.0"
PORT="8000"

export OLLAMA_MODELS="${OLLAMA_MODELS:-$ROOT/deps/ollama-store}"
export COMFYUI_MODEL_DIR="${COMFYUI_MODEL_DIR:-$ROOT/deps/ComfyUI/models}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --components)
      COMPONENTS="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
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

url_ok() {
  curl -fsS --max-time 2 "$1" >/dev/null 2>&1
}

write_comfyui_extra_model_paths_config() {
  local config_path="$ROOT/config/comfyui_extra_model_paths.yaml"
  mkdir -p "$(dirname "$config_path")" "$COMFYUI_MODEL_DIR"
  cat >"$config_path" <<YAML
openaiserve:
  base_path: "$COMFYUI_MODEL_DIR"
  checkpoints: checkpoints
  clip: clip
  clip_vision: clip_vision
  configs: configs
  controlnet: controlnet
  diffusion_models: diffusion_models
  embeddings: embeddings
  loras: loras
  text_encoders: text_encoders
  unet: unet
  upscale_models: upscale_models
  vae: vae
YAML
  printf '%s\n' "$config_path"
}

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

if has_component "ollama"; then
  mkdir -p "$OLLAMA_MODELS"
  if url_ok "http://127.0.0.1:11434/api/tags"; then
    echo "[ollama] already running; expected model store: $OLLAMA_MODELS"
  elif ! command -v ollama >/dev/null 2>&1; then
    echo "[ollama] command not found; skipping Ollama. Run scripts/install.sh --components ollama first if text generation is needed." >&2
  else
    echo "[ollama] starting with model store: $OLLAMA_MODELS"
    ollama serve &
    pids+=("$!")
  fi
fi

if has_component "comfyui"; then
  if url_ok "http://127.0.0.1:8188/system_stats"; then
    echo "[comfyui] already running"
  else
    echo "[comfyui] starting"
    if [[ ! -x "$ROOT/deps/ComfyUI/.venv/bin/python" ]]; then
      echo "ComfyUI venv not found. Run scripts/install.sh --components comfyui first." >&2
      exit 1
    fi
    comfy_args=(main.py --listen 127.0.0.1 --port 8188)
    if [[ "$COMFYUI_MODEL_DIR" != "$ROOT/deps/ComfyUI/models" ]]; then
      extra_model_paths_config="$(write_comfyui_extra_model_paths_config)"
      echo "[comfyui] using extra model path: $COMFYUI_MODEL_DIR"
      comfy_args+=(--extra-model-paths-config "$extra_model_paths_config")
    fi
    (cd "$ROOT/deps/ComfyUI" && "$ROOT/deps/ComfyUI/.venv/bin/python" "${comfy_args[@]}") &
    pids+=("$!")
  fi
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Server venv not found. Run scripts/install.sh first." >&2
  exit 1
fi

echo "[server] starting OpenAI-compatible API at http://127.0.0.1:$PORT"
cd "$ROOT"
"$ROOT/.venv/bin/python" -m uvicorn openaiserve.app:app --host "$HOST" --port "$PORT"
