#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPONENTS="all"
HOST="0.0.0.0"
PORT="8000"

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

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

if has_component "ollama"; then
  if url_ok "http://127.0.0.1:11434/api/tags"; then
    echo "[ollama] already running"
  else
    echo "[ollama] starting"
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
    (cd "$ROOT/deps/ComfyUI" && "$ROOT/deps/ComfyUI/.venv/bin/python" main.py --listen 127.0.0.1 --port 8188) &
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

