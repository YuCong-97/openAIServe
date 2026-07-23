# AGENTS.md

This file is the deployment and operations entry point for AI coding agents working on this repository.

## Project Purpose

`openAIServe` is a one-command OpenAI-compatible provider server.

- Text generation: Ollama
- Image generation: ComfyUI with Flux schnell FP8 and SDXL
- Video generation: ComfyUI with the RTX 3090-friendly Wan2.1 T2V 1.3B workflow
- Character locking: image and video generation both support character LoRA presets
- Audio generation: CosyVoice 3 provider boundary is reserved but not implemented yet

## Important Agent Rules

- Do not download models, install GPU dependencies, or start long-running services unless the user asks for deployment or verification that requires it.
- Do not commit generated folders such as `.venv/`, `deps/`, `outputs/`, `logs/`, or model files.
- `config.yaml` is intentionally committed because this project ships an RTX 3090 default deployment profile.
- LoRA files are not downloaded by default. Users must place them in `deps/ComfyUI/models/loras`.
- On Windows, use PowerShell scripts. On Linux, use Bash scripts.

## Repository Layout

- `openaiserve/app.py`: FastAPI OpenAI-compatible API server
- `openaiserve/providers/ollama.py`: Ollama chat/completions provider
- `openaiserve/providers/comfyui.py`: ComfyUI image/video provider
- `openaiserve/providers/cosyvoice.py`: reserved CosyVoice 3 provider
- `openaiserve/workflows.py`: ComfyUI workflow builders
- `config.yaml`: active RTX 3090 deployment config
- `config.example.yaml`: example config copied by installers when `config.yaml` is absent
- `scripts/install.ps1`: Windows deployment script
- `scripts/install.sh`: Linux deployment script
- `scripts/start.ps1`: Windows startup script
- `scripts/start.sh`: Linux startup script
- `scripts/download_models.py`: model downloader for Ollama and Hugging Face assets
- `workflows/`: optional custom ComfyUI API workflow templates

## Default RTX 3090 Profile

The default profile targets RTX 3090 24GB VRAM.

- Ollama default model: `qwen3:30b`
- Ollama coding model: `qwen2.5-coder:32b`
- Image default: `flux1-schnell-fp8.safetensors`, 1024x1024, 4 steps
- Image fallback: `sd_xl_base_1.0.safetensors`, 1024x1024, 28 steps
- Video default: Wan2.1 T2V 1.3B, 832x480, 33 frames, 30 steps, 16 fps

ComfyUI model destinations:

- Checkpoints: `deps/ComfyUI/models/checkpoints`
- Diffusion models: `deps/ComfyUI/models/diffusion_models`
- Text encoders: `deps/ComfyUI/models/text_encoders`
- VAE: `deps/ComfyUI/models/vae`
- LoRA: `deps/ComfyUI/models/loras`

## Deployment Commands

Windows full deployment:

```powershell
.\scripts\install.ps1 -Components all -Profile rtx3090 -DownloadModels -Start
```

Linux full deployment:

```bash
bash scripts/install.sh --components all --profile rtx3090 --download-models --start
```

The Linux installer checks for `python3`, venv support, `git`, `curl`, CA certificates, and `zstd` before creating virtual environments. It can install missing prerequisites on apt, dnf, yum, pacman, zypper, and apk based systems. On apt systems it retries `apt-get update` and `apt-get install --fix-missing` because some mirrors temporarily serve mismatched indexes during sync. If `ollama.com` is unreachable, it tries the China mirror `https://ollama.ac.cn/install.sh`, then `https://ollama.ac.cn/download/ollama-linux-*.tar.zst`, then the GitHub release archive. If all defaults are unreachable, set `OLLAMA_INSTALL_SCRIPT_URLS`, `OLLAMA_ARCHIVE_URLS`, or `OLLAMA_INSTALL_URL` to reachable mirrors. If the host uses another package manager, install Python 3, venv support, Git, Curl, CA certificates, and zstd manually before rerunning.

ComfyUI repository install tries official GitHub, GitCode, and Gitee mirrors by default. Override with `COMFYUI_GIT_URL` or a space-separated `COMFYUI_GIT_URLS`. ComfyUI model downloads try `https://huggingface.co` and `https://hf-mirror.com` by default. Override with `HF_ENDPOINT` or space-separated `HF_ENDPOINTS`.

Windows text-only deployment:

```powershell
.\scripts\install.ps1 -Components ollama -Profile rtx3090 -DownloadModels
```

Windows image/video-only deployment:

```powershell
.\scripts\install.ps1 -Components comfyui -Profile rtx3090 -DownloadModels
```

Manual startup on Windows:

```powershell
.\scripts\start.ps1 -Components all
```

Manual startup on Linux:

```bash
bash scripts/start.sh --components all
```

Do not use `.\scripts\start.ps1` on Linux. That is a Windows PowerShell command; Linux shells must run `bash scripts/start.sh --components ...`.

## Model Download Notes

The `rtx3090` profile downloads:

- `Comfy-Org/flux1-schnell` -> `flux1-schnell-fp8.safetensors`
- `stabilityai/stable-diffusion-xl-base-1.0` -> `sd_xl_base_1.0.safetensors`
- `Comfy-Org/Wan_2.1_ComfyUI_repackaged` -> Wan2.1 T2V, UMT5 FP8, and Wan VAE files
- Ollama: `qwen3:30b`, `qwen2.5-coder:32b`

Optional model:

- `flux1-dev-fp8.safetensors`

Download optional models only when explicitly requested:

```powershell
.\scripts\install.ps1 -Components all -Profile rtx3090 -DownloadModels -IncludeOptionalModels
```

## Character LoRA Deployment

The active config expects these example LoRA file names under `deps/ComfyUI/models/loras`:

- `protagonist_flux_v1.safetensors`
- `protagonist_sdxl_v1.safetensors`
- `protagonist_wan21_video_v1.safetensors`
- `supporting_a_flux_v1.safetensors`
- `supporting_a_sdxl_v1.safetensors`
- `supporting_a_wan21_video_v1.safetensors`

Image LoRA nodes use `LoraLoader`.
Video LoRA nodes use `LoraLoaderModelOnly` and are inserted into:

```text
UNETLoader -> LoraLoaderModelOnly* -> ModelSamplingSD3
```

Single character request:

```json
{
  "model": "comfyui-video",
  "character": "protagonist",
  "prompt": "the protagonist turns toward camera",
  "response_format": "url"
}
```

Multi-character request:

```json
{
  "model": "comfyui-video",
  "characters": ["protagonist", "supporting_a"],
  "prompt": "two characters walk side by side in a cinematic street scene",
  "response_format": "url"
}
```

## Verification Commands

Run static Python verification:

```powershell
python -m compileall openaiserve scripts
python -c "import openaiserve.app as app; print(app.app.title)"
```

Check API health after startup:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/models
```

Check text generation:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:30b","messages":[{"role":"user","content":"hello"}]}'
```

Check image generation:

```bash
curl http://127.0.0.1:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"comfyui-flux","character":"protagonist","prompt":"cinematic portrait","response_format":"url"}'
```

Check video generation:

```bash
curl http://127.0.0.1:8000/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"comfyui-video","character":"protagonist","prompt":"the protagonist turns toward camera","response_format":"url"}'
```

## Common Troubleshooting

- If `/v1/chat/completions` fails, verify Ollama is running at `http://127.0.0.1:11434`.
- If `curl http://127.0.0.1:8000/...` returns connection refused, the API server is not running. On Linux start it with `bash scripts/start.sh --components all` or `bash scripts/start.sh --components comfyui`.
- If Linux deployment fails with `python: command not found`, pull the latest repository version and rerun `bash scripts/install.sh ...`; older installers did not auto-install Python.
- If apt fails with `File has unexpected size` or `Mirror sync in progress`, pull the latest repository version and rerun. The installer retries apt with `--fix-missing`; if the mirror keeps failing, wait a few minutes or switch `/etc/apt/sources.list` to another Ubuntu mirror.
- If Ollama install fails with `Failed to connect to ollama.com port 443`, pull the latest repository version and rerun. The installer tries `ollama.ac.cn` before GitHub releases; for restricted networks, set `OLLAMA_ARCHIVE_URLS=https://your-mirror/ollama-linux-amd64.tar.zst`.
- If Torch install fails because `download.pytorch.org` is unreachable, rerun with `TORCH_INDEX_URL=https://mirrors.aliyun.com/pytorch-wheels/cu128` or set `TORCH_INSTALL_CMD` to a fully custom command using `$COMFYUI_PYTHON`.
- If ComfyUI startup fails with `NVIDIA driver on your system is too old` and logs `found version 12040`, the driver supports CUDA 12.4 but the installed Torch wheel is newer. Rerun with `TORCH_CUDA_VARIANT=cu124` and optionally `TORCH_INDEX_URL=https://mirrors.aliyun.com/pytorch-wheels/cu124`.
- If ComfyUI clone fails because `github.com` is unreachable, rerun after pull. The installer tries GitCode and Gitee mirrors; for restricted networks, set `COMFYUI_GIT_URLS=https://gitcode.com/gh_mirrors/co/ComfyUI.git`.
- If ComfyUI model download fails because Hugging Face is unreachable, rerun with `HF_ENDPOINTS="https://hf-mirror.com https://huggingface.co"`.
- If image/video generation fails, verify ComfyUI is running at `http://127.0.0.1:8188`.
- If ComfyUI reports missing nodes, update ComfyUI and confirm the installed version includes Wan video nodes.
- If ComfyUI reports missing models, compare `config.yaml` model file names with files under `deps/ComfyUI/models`.
- If character identity is not locked, confirm the relevant Flux, SDXL, or Wan LoRA file exists and matches the config name exactly.
- If video VRAM usage is too high, reduce `frames`, `width`, `height`, or `steps` in `config.yaml` under `providers.comfyui.video.models.wan2_1_t2v_1_3b_480p`.

## Git Workflow For Agents

Before edits:

```bash
git status --short --branch
```

After edits:

```bash
python -m compileall openaiserve scripts
git status --short
git add <changed-files>
git commit -m "<concise commit message>"
git push
```
