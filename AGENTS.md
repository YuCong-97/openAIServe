# AGENTS.md

AI agents should treat this repository as a Linux-only one-command OpenAI-compatible provider server.

## Scope

- Text generation: Ollama
- Image generation: ComfyUI with Flux schnell FP8 and SDXL
- Video generation: ComfyUI with Wan2.1 T2V 1.3B for RTX 3090
- Character locking: image and video LoRA presets
- Audio generation: CosyVoice 3 boundary reserved

## Rules

- Use Bash scripts only.
- Do not commit `.venv/`, `deps/`, `downloads/`, `packages/`, `outputs/`, `logs/`, model files, or generated media.
- `config.yaml` is committed intentionally as the RTX 3090 default profile.
- Put LoRA files in `deps/ComfyUI/models/loras`.
- Deployment defaults and China mirror parameters are embedded in scripts and `config.yaml`; do not add long environment-variable instructions to docs.

## Files

- `scripts/install.sh`: Linux installer
- `scripts/start.sh`: Linux starter
- `scripts/prepare_offline_bundle.py`: local offline bundle preparer for Linux deployment assets
- `scripts/download_models.py`: Ollama/ComfyUI model downloader
- `openaiserve/app.py`: FastAPI OpenAI-compatible API
- `openaiserve/providers/ollama.py`: Ollama provider
- `openaiserve/providers/comfyui.py`: ComfyUI image/video provider
- `openaiserve/workflows.py`: ComfyUI workflow builders
- `config.yaml`: RTX 3090 default runtime config

## Commands

Prepare offline bundle locally:

```bash
python scripts/prepare_offline_bundle.py --components all --profile rtx3090 --torch-variant cu124
```

Full deployment:

```bash
bash scripts/install.sh --components all --profile rtx3090 --download-models --start
```

Text only:

```bash
bash scripts/install.sh --components ollama --profile rtx3090 --download-models
```

Image/video only:

```bash
bash scripts/install.sh --components comfyui --profile rtx3090 --download-models
```

Manual start:

```bash
bash scripts/start.sh --components all
```

## Defaults

- Ollama install: local archive, ModelScope archive, `ollama.ac.cn`, GitHub, then script fallback.
- Ollama models: ModelScope GGUF direct downloads, then `ollama create`.
- Ollama model store: `OLLAMA_MODELS` defaults to `deps/ollama-store`; GGUF files are hardlinked into the blob store before create when possible.
- Ollama registry pulls are disabled by default.
- ComfyUI clone: GitCode/Gitee first, GitHub fallback.
- PyTorch wheels: Aliyun/NJU first, official fallback.
- Local offline bundle: `packages/ollama-linux-*.tar.zst`, `packages/repos/ComfyUI(.tar.gz)`, `packages/wheels/*`, `packages/ollama-models/*`, and `packages/comfyui-models/*` are preferred before network downloads.
- ComfyUI models: ModelScope direct URLs in `config.yaml`, Hugging Face fallback.

## Verify

```bash
python -m compileall openaiserve scripts
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/models
```
