# OpenAI Supplier Server

Linux 一键部署的 OpenAI 兼容供应商服务器：

- 文本生成：Ollama
- 图片生成：ComfyUI，默认 Flux schnell FP8，备选 SDXL
- 视频生成：ComfyUI，RTX 3090 默认 Wan2.1 T2V 1.3B 工作流
- 角色锁定：图片与视频都支持多角色 LoRA
- 音频生成：预留 CosyVoice 3 provider

## 本机准备离线包

在网络更快的本机执行：

```bash
python scripts/prepare_offline_bundle.py --components all --profile rtx3090 --torch-variant cu124
```

生成的 `packages/` 包含 Ollama Linux 安装包、Ollama GGUF、ComfyUI 源码包、ComfyUI 模型和 Linux Python wheels。把项目和 `packages/` 一起上传到云服务器即可。

## 云端部署

Ollama 模型仓库默认使用 `deps/ollama-store`，请把项目放在空间充足的磁盘。

ComfyUI 模型盘空间不足时先执行：`export COMFYUI_MODEL_DIR=/data/openAIServe-comfyui-models`

不带 `--start` 时只安装/下载，不启动 API 服务。

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

## 外部调用

公网访问时启动到 `0.0.0.0`，只需要放行 API 端口 `8000`：

先在 `config.yaml` 中设置：

```yaml
auth:
  api_key: your-key
  allow_no_key: false
```

```bash
bash scripts/start.sh --components all --host 0.0.0.0 --port 8000
```

云服务器安全组/防火墙放行入站 `TCP 8000` 后，外部客户端使用：

```text
http://服务器公网IP:8000
```

健康检查不需要 key：

```bash
curl http://服务器公网IP:8000/health
```

OpenAI 兼容接口需要 Bearer key：

```bash
curl http://服务器公网IP:8000/v1/models \
  -H "Authorization: Bearer your-key"
```

```bash
curl http://服务器公网IP:8000/v1/chat/completions \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:30b","messages":[{"role":"user","content":"写一个三行部署说明"}]}'
```

后台启动：

```bash
mkdir -p logs
nohup bash scripts/start.sh --components all --host 0.0.0.0 --port 8000 > logs/openaiserve.log 2>&1 &
```

## LoRA

LoRA 文件放到：

```text
deps/ComfyUI/models/loras
```

`config.yaml` 中按模型家族配置角色 LoRA：

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
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:30b","messages":[{"role":"user","content":"写一个三行部署说明"}]}'
```

图片锁主角：

```bash
curl http://127.0.0.1:8000/v1/images/generations \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"comfyui-flux","character":"protagonist","prompt":"cinematic portrait, soft rim light","size":"1024x1024","response_format":"url"}'
```

视频多角色：

```bash
curl http://127.0.0.1:8000/v1/videos/generations \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"comfyui-video","characters":["protagonist","supporting_a"],"prompt":"two characters walk through a rainy neon street","frames":33,"response_format":"url"}'
```

## 鉴权配置

默认从 `config.yaml` 读取 API key：

```yaml
auth:
  api_key: your-key
  allow_no_key: false
```

外部请求时带上：

```bash
-H "Authorization: Bearer your-key"
```

也可以用环境变量 `OPENAISERVE_API_KEY` 临时覆盖配置文件中的 key。
