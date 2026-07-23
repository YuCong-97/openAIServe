from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from openaiserve import __version__
from openaiserve.config import configured_api_key, load_config
from openaiserve.providers.comfyui import ComfyUIProvider
from openaiserve.providers.cosyvoice import CosyVoice3Provider
from openaiserve.providers.ollama import OllamaProvider


class FlexibleModel(BaseModel):
    class Config:
        extra = "allow"


class ChatMessage(FlexibleModel):
    role: str
    content: Any


class ChatCompletionRequest(FlexibleModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


class CompletionRequest(FlexibleModel):
    model: str | None = None
    prompt: str | list[str]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


class ImageGenerationRequest(FlexibleModel):
    model: str | None = None
    prompt: str
    n: int = 1
    size: str | None = None
    response_format: str = "b64_json"
    character: str | None = None


class VideoGenerationRequest(FlexibleModel):
    model: str | None = None
    prompt: str
    response_format: str = "url"


class AudioSpeechRequest(FlexibleModel):
    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str = "wav"


config = load_config()
api_key = configured_api_key(config)

ollama_config = config.get("providers", {}).get("ollama", {})
comfyui_config = config.get("providers", {}).get("comfyui", {})
cosyvoice_config = config.get("providers", {}).get("cosyvoice3", {})

ollama = OllamaProvider(
    base_url=str(ollama_config.get("base_url", "http://127.0.0.1:11434")),
    default_model=str(ollama_config.get("default_model", "qwen3:30b")),
    timeout_seconds=int(ollama_config.get("timeout_seconds", 600)),
)
comfyui = ComfyUIProvider(comfyui_config)
cosyvoice3 = CosyVoice3Provider(cosyvoice_config)

app = FastAPI(
    title="OpenAI Supplier Server",
    version=__version__,
    description="OpenAI-compatible gateway backed by Ollama for text and ComfyUI for image/video generation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    allow_no_key = bool(config.get("auth", {}).get("allow_no_key", True))
    if not api_key and allow_no_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "providers": {
            "ollama": bool(ollama_config.get("enabled", True)),
            "comfyui": bool(comfyui_config.get("enabled", True)),
            "cosyvoice3": bool(cosyvoice_config.get("enabled", False)),
        },
    }


@app.get("/v1/models", dependencies=[Depends(require_api_key)])
async def list_models() -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    if ollama_config.get("enabled", True):
        for model in await ollama.list_models():
            models.append(
                {
                    "id": model.get("name") or model.get("model"),
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "ollama",
                }
            )
    if comfyui_config.get("enabled", True):
        for image_model in (comfyui_config.get("image_models") or {}).keys():
            models.append(
                {
                    "id": f"comfyui-{image_model}",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "comfyui",
                }
            )
        for video_model in ((comfyui_config.get("video") or {}).get("models") or {}).keys():
            models.append(
                {
                    "id": f"comfyui-{video_model}",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "comfyui",
                }
            )
        models.append({"id": "comfyui-video", "object": "model", "created": int(time.time()), "owned_by": "comfyui"})
    if cosyvoice_config.get("enabled", False):
        models.append({"id": "cosyvoice3", "object": "model", "created": int(time.time()), "owned_by": "cosyvoice3"})
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions", dependencies=[Depends(require_api_key)], response_model=None)
async def chat_completions(request: ChatCompletionRequest) -> Response | dict[str, Any]:
    if not ollama_config.get("enabled", True):
        raise HTTPException(status_code=503, detail="Ollama provider is disabled")
    payload = request.model_dump()
    if request.stream:
        return StreamingResponse(ollama.stream_chat_completion(payload), media_type="text/event-stream")
    return await ollama.chat_completion(payload)


@app.post("/v1/completions", dependencies=[Depends(require_api_key)], response_model=None)
async def completions(request: CompletionRequest) -> Response | dict[str, Any]:
    prompt = request.prompt[0] if isinstance(request.prompt, list) else request.prompt
    chat_request = ChatCompletionRequest(
        model=request.model,
        messages=[ChatMessage(role="user", content=prompt)],
        stream=request.stream,
        temperature=request.temperature,
        top_p=request.top_p,
        max_tokens=request.max_tokens,
    )
    response = await chat_completions(chat_request)
    if request.stream:
        return response
    assert isinstance(response, dict)
    choice = response["choices"][0]
    return {
        "id": f"cmpl-{uuid.uuid4().hex}",
        "object": "text_completion",
        "created": response["created"],
        "model": response["model"],
        "choices": [{"index": 0, "text": choice["message"]["content"], "finish_reason": choice["finish_reason"]}],
        "usage": response.get("usage", {}),
    }


@app.post("/v1/images/generations", dependencies=[Depends(require_api_key)], response_model=None)
async def image_generations(request: ImageGenerationRequest) -> dict[str, Any]:
    if not comfyui_config.get("enabled", True):
        raise HTTPException(status_code=503, detail="ComfyUI provider is disabled")
    payload = request.model_dump()
    workflow = comfyui.build_image_workflow(payload)
    data = await comfyui.generate(workflow, response_format=request.response_format)
    return {"created": int(time.time()), "data": data}


@app.post("/v1/videos/generations", dependencies=[Depends(require_api_key)], response_model=None)
async def video_generations(request: VideoGenerationRequest) -> dict[str, Any]:
    if not comfyui_config.get("enabled", True):
        raise HTTPException(status_code=503, detail="ComfyUI provider is disabled")
    payload = request.model_dump()
    workflow = comfyui.build_video_workflow(payload)
    data = await comfyui.generate(workflow, response_format=request.response_format)
    return {"created": int(time.time()), "data": data}


@app.post("/v1/audio/speech", dependencies=[Depends(require_api_key)], response_model=None)
async def audio_speech(request: AudioSpeechRequest) -> Response:
    audio = await cosyvoice3.speech(request.model_dump())
    media_type = "audio/mpeg" if request.response_format == "mp3" else "audio/wav"
    return Response(content=audio, media_type=media_type)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": exc.detail, "type": "server_error", "code": exc.status_code}},
    )
