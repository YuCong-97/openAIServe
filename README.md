# OpenAI Supplier Server

Linux 一键部署的 OpenAI 兼容供应商服务器：

- 文本生成：Ollama
- 图片生成：ComfyUI，默认 Flux schnell FP8，备选 SDXL
- 视频生成：ComfyUI，RTX 3090 默认 Wan2.1 T2V 1.3B 工作流
- 角色锁定：图片与视频均支持多角色 LoRA
- 音频生成：预留 CosyVoice 3 provider

## 快速开始

完整部署并启动：

```bash
bash scripts/install.sh --components all --profile rtx3090 --download-models --start
```

只部署文本：

```bash
bash scripts/install.sh --components ollama --profile rtx3090 --download-models
```

只部署图片/视频：

```bash
bash scripts/install.sh --components comfyui --profile rtx3090 --download-models
```

手动启动：

```bash
bash scripts/start.sh --components all
```

默认服务地址：`http://127.0.0.1:8000`

## 内置默认

- Linux-only 部署，安装脚本自动检查并安装基础依赖。
- 国内网络优先：Ollama、ComfyUI、PyTorch、Ollama GGUF、ComfyUI 模型下载源已写入脚本和 `config.yaml`。
- Ollama 模型默认使用 ModelScope GGUF 下载后 `ollama create`，不默认访问 `registry.ollama.ai`。
- 大文件下载默认支持断点续传。
- 离线包目录：`packages/`、`downloads/`、`deps/ollama-models/`、`packages/ollama-models/`。

RTX 3090 profile 默认：

- 文本：`qwen3:30b`
- 代码：`qwen2.5-coder:32b`
- 图片：Flux schnell FP8 / SDXL
- 视频：Wan2.1 T2V 1.3B，832x480，33 frames

## LoRA

LoRA 文件放到：

```text
deps/ComfyUI/models/loras
```

`config.yaml` 中按模型族配置角色 LoRA：

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

请求中单角色使用 `character`，多角色使用 `characters`。

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

视频多角色：

```bash
curl http://127.0.0.1:8000/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"comfyui-video","characters":["protagonist","supporting_a"],"prompt":"two characters walk through a rainy neon street","frames":33,"response_format":"url"}'
```

## 鉴权

本地默认允许无 key。生产环境设置：

```bash
export OPENAISERVE_API_KEY="your-key"
```

或在 `config.yaml` 中配置：

```yaml
auth:
  api_key: your-key
  allow_no_key: false
```
