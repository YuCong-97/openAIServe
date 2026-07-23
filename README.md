# OpenAI Supplier Server

一键部署的 OpenAI 兼容供应商服务器：

- 文本生成：Ollama
- 图片生成：ComfyUI，默认 Flux schnell FP8，备选 SDXL
- 视频生成：ComfyUI，内置 RTX 3090 友好的 Wan2.1 T2V 1.3B 工作流
- 角色锁定：图片与视频都支持多角色 LoRA
- 音频生成：预留 CosyVoice 3 provider 与 `/v1/audio/speech`
- Windows/Linux 跨平台部署，Ollama 和 ComfyUI 安装可并行

## 快速开始

Windows PowerShell：

```powershell
.\scripts\install.ps1 -Components all -Profile rtx3090 -DownloadModels -Start
```

Linux：

```bash
bash scripts/install.sh --components all --profile rtx3090 --download-models --start
```

Linux 脚本会先检查 `python3`、`python3-venv`、`git`、`curl`；在 apt/dnf/yum/pacman/zypper/apk 系统上会尝试自动安装缺失项。最小化系统如果没有这些包管理器，请先手动安装 Python 3、venv、Git 和 Curl。
如果 apt 提示 `File has unexpected size` 或 `Mirror sync in progress`，通常是当前镜像站同步中。最新版脚本会自动清理 apt 缓存并重试；如果仍失败，稍后重跑或切换 `/etc/apt/sources.list` 到其他 Ubuntu 镜像。
如果访问 `ollama.com` 失败，脚本会自动尝试国内镜像 `https://ollama.ac.cn/install.sh`，再回退到 `https://ollama.ac.cn/download/ollama-linux-*.tar.zst` 和 GitHub release。若这些地址都不可达，可以设置自己的镜像：

```bash
export OLLAMA_INSTALL_SCRIPT_URLS="https://ollama.ac.cn/install.sh"
export OLLAMA_ARCHIVE_URLS="https://ollama.ac.cn/download/ollama-linux-amd64.tar.zst"
```

如果 `download.pytorch.org` 不可达，可以显式使用阿里云 PyTorch wheel 镜像：

```bash
export TORCH_INDEX_URL=https://mirrors.aliyun.com/pytorch-wheels/cu128
bash scripts/install.sh --components comfyui --profile rtx3090 --download-models --start
```

ComfyUI 仓库默认会依次尝试官方、GitCode、Gitee 镜像；如果你有自己的镜像，可以覆盖：

```bash
export COMFYUI_GIT_URLS="https://gitcode.com/gh_mirrors/co/ComfyUI.git https://gitee.com/mirrors/ComfyUI.git"
```

ComfyUI 模型默认会尝试 Hugging Face 官方和 `https://hf-mirror.com`，也可以覆盖：

```bash
export HF_ENDPOINTS="https://hf-mirror.com https://huggingface.co"
```

只部署文本：

```powershell
.\scripts\install.ps1 -Components ollama -DownloadModels
```

只部署图片/视频：

```powershell
.\scripts\install.ps1 -Components comfyui -DownloadModels
```

手动启动：

```powershell
.\scripts\start.ps1 -Components all
```

默认服务地址：`http://127.0.0.1:8000`。

## RTX 3090 默认工作流

`config.yaml` 已按 RTX 3090 24GB 配好：

- 文本：`qwen3:30b`
- 代码：`qwen2.5-coder:32b`
- 图片默认：`flux1-schnell-fp8.safetensors`，1024x1024，4 steps
- 图片备选：`sd_xl_base_1.0.safetensors`，适合 SDXL LoRA 生态
- 视频默认：Wan2.1 T2V 1.3B，832x480，33 frames，30 steps，16 fps

ComfyUI 模型落点：

- Checkpoint：`deps/ComfyUI/models/checkpoints`
- Wan diffusion model：`deps/ComfyUI/models/diffusion_models`
- Wan text encoder：`deps/ComfyUI/models/text_encoders`
- Wan VAE：`deps/ComfyUI/models/vae`
- LoRA：`deps/ComfyUI/models/loras`

## 角色 LoRA

`config.yaml` 里每个角色按模型族分 LoRA：

```yaml
characters:
  protagonist:
    loras_by_family:
      flux:
        - name: protagonist_flux_v1.safetensors
      sdxl:
        - name: protagonist_sdxl_v1.safetensors
      wan21_video:
        - name: protagonist_wan21_video_v1.safetensors
```

图片与视频会自动按当前工作流选择对应 LoRA。单角色用 `character`，多角色用 `characters`：

```json
{
  "characters": ["protagonist", "supporting_a"],
  "prompt": "two characters walk through a rainy neon street"
}
```

视频 LoRA 会动态串到 `UNETLoader -> LoraLoaderModelOnly* -> ModelSamplingSD3`，因此多个角色的 Wan LoRA 可以同时应用到视频生成。

## API 示例

聊天：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:30b","messages":[{"role":"user","content":"写一个三行部署说明"}]}'
```

图片锁主角：

```bash
curl http://127.0.0.1:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"comfyui-flux","character":"protagonist","prompt":"cinematic portrait, soft rim light","size":"1024x1024","response_format":"url"}'
```

视频锁主角：

```bash
curl http://127.0.0.1:8000/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"comfyui-video","character":"protagonist","prompt":"the protagonist turns toward camera, wind in hair","response_format":"url"}'
```

视频多角色：

```bash
curl http://127.0.0.1:8000/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{"characters":["protagonist","supporting_a"],"prompt":"the two characters walk side by side in a cinematic street scene","frames":33,"response_format":"url"}'
```

## 自定义视频工作流

默认视频用内置 Wan2.1 T2V 1.3B API 工作流。后续如果改用 Wan2.1 I2V、AnimateDiff 或其他 ComfyUI 自定义节点，可以改回模板模式：

```yaml
providers:
  comfyui:
    video:
      workflow_template: workflows/custom_video_api.json
```

模板中可以使用这些占位符：`{{prompt}}`、`{{negative_prompt}}`、`{{seed}}`、`{{width}}`、`{{height}}`、`{{frames}}`、`{{steps}}`、`{{lora_0_name}}`、`{{lora_0_strength_model}}`。

## CosyVoice 3 预留

`/v1/audio/speech` 已经存在，但默认返回未启用提示。后续接入 CosyVoice 3 时，实现 `openaiserve/providers/cosyvoice.py` 并打开：

```yaml
providers:
  cosyvoice3:
    enabled: true
    base_url: http://127.0.0.1:50000
```

## 鉴权

本地测试默认允许无 key。生产环境建议设置：

```powershell
$env:OPENAISERVE_API_KEY="your-key"
```

或在 `config.yaml` 中设置：

```yaml
auth:
  api_key: your-key
  allow_no_key: false
```

请求使用：

```text
Authorization: Bearer your-key
```

## 官方参考

- ComfyUI 文档：https://docs.comfy.org/
- ComfyUI Wan 示例：https://comfyanonymous.github.io/ComfyUI_examples/wan/
- Wan2.1 ComfyUI 模型：https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged
- Ollama：https://ollama.com/
- Flux FP8 for ComfyUI：https://huggingface.co/Comfy-Org/flux1-schnell
- SDXL Base 1.0：https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
- CosyVoice：https://github.com/FunAudioLLM/CosyVoice
