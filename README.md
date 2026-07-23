# OpenAI Supplier Server

Linux 一键部署的 OpenAI 兼容供应商服务器。

- 文本：Ollama
- 图片/视频：ComfyUI，RTX 3090 默认 Flux / SDXL / Wan2.1 工作流
- 角色锁定：图片和视频都支持多角色 LoRA
- 音频：预留 CosyVoice 3

## 快速部署

网络快的本机先准备离线包：

```bash
python scripts/prepare_offline_bundle.py
```

把项目和 `packages/` 上传到服务器，修改 `config.yaml`：

```yaml
auth:
  api_key: your-key
  allow_no_key: false
```

服务器执行：

```bash
bash scripts/install.sh --download-models --start
```

如果 ComfyUI 模型盘空间不足，安装前指定大盘目录：

```bash
export COMFYUI_MODEL_DIR=/data/openAIServe-comfyui-models
```

## 启动

```bash
bash scripts/start.sh
```

后台启动：

```bash
mkdir -p logs
nohup bash scripts/start.sh > logs/openaiserve.log 2>&1 &
```

## 外部调用

只需要在云服务器安全组/防火墙放行 `TCP 8000`。

```bash
curl http://服务器公网IP:8000/health
```

```bash
curl http://服务器公网IP:8000/v1/chat/completions \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:30b","messages":[{"role":"user","content":"写一个三行部署说明"}]}'
```

OpenAI SDK 兼容地址：

```text
http://服务器公网IP:8000/v1
```

只开放 `8000`；Ollama `11434` 和 ComfyUI `8188` 保持本机访问。

## LoRA

LoRA 放到：

```text
deps/ComfyUI/models/loras
```

使用外部模型目录时放到：

```text
$COMFYUI_MODEL_DIR/loras
```

请求里单角色用 `character`，多角色用 `characters`。

## 常用接口

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/images/generations`
- `POST /v1/videos/generations`
