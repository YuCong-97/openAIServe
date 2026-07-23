from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException


class OllamaProvider:
    def __init__(self, base_url: str, default_model: str, timeout_seconds: int = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = httpx.Timeout(timeout_seconds)

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        return payload.get("models", [])

    async def chat_completion(self, request: dict[str, Any]) -> dict[str, Any]:
        model = request.get("model") or self.default_model
        payload = {
            "model": model,
            "messages": request.get("messages") or [],
            "stream": False,
            "options": request.get("options") or {},
        }
        if request.get("temperature") is not None:
            payload["options"]["temperature"] = request["temperature"]
        if request.get("top_p") is not None:
            payload["options"]["top_p"] = request["top_p"]
        if request.get("max_tokens") is not None:
            payload["options"]["num_predict"] = request["max_tokens"]

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}") from exc

        data = response.json()
        content = (data.get("message") or {}).get("content", "")
        created = int(time.time())
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop" if data.get("done", True) else None,
                }
            ],
            "usage": {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            },
        }

    async def stream_chat_completion(self, request: dict[str, Any]) -> AsyncIterator[str]:
        model = request.get("model") or self.default_model
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        payload = {
            "model": model,
            "messages": request.get("messages") or [],
            "stream": True,
            "options": request.get("options") or {},
        }
        if request.get("temperature") is not None:
            payload["options"]["temperature"] = request["temperature"]
        if request.get("top_p") is not None:
            payload["options"]["top_p"] = request["top_p"]
        if request.get("max_tokens") is not None:
            payload["options"]["num_predict"] = request["max_tokens"]

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        delta = (data.get("message") or {}).get("content", "")
                        if delta:
                            chunk = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                            }
                            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                        if data.get("done"):
                            done_chunk = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            }
                            yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"
                            yield "data: [DONE]\n\n"
                            return
        except httpx.HTTPError as exc:
            error_chunk = {"error": {"message": f"Ollama request failed: {exc}", "type": "upstream_error"}}
            yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

