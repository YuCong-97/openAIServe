from __future__ import annotations

import asyncio
import base64
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from openaiserve.config import ROOT_DIR, resolve_path
from openaiserve.workflows import (
    build_checkpoint_txt2img_workflow,
    build_lora_tokens,
    build_wan21_t2v_workflow,
    load_template_workflow,
)


class ComfyUIProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.base_url = str(config.get("base_url", "http://127.0.0.1:8188")).rstrip("/")
        self.client_id = str(config.get("client_id", "openaiserve"))
        self.timeout_seconds = int(config.get("timeout_seconds", 900))

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/system_stats")
                return response.status_code < 500
        except httpx.HTTPError:
            return False

    def build_image_workflow(self, request: dict[str, Any]) -> dict[str, Any]:
        image_models = self.config.get("image_models", {})
        requested_model = str(request.get("model") or self.config.get("default_image_model", "flux")).lower()
        model_key, model_config = self._select_image_model(requested_model, image_models)
        model_family = str(model_config.get("family") or model_key).lower()

        character_names = self._requested_characters(request)
        prompt, character_loras = self._apply_characters(
            str(request.get("prompt", "")),
            character_names,
            model_key=model_key,
            model_family=model_family,
        )
        request_loras = request.get("loras") or []
        loras = [*character_loras, *request_loras]

        size = request.get("size") or ""
        width = int(request.get("width") or model_config.get("width", 1024))
        height = int(request.get("height") or model_config.get("height", 1024))
        if isinstance(size, str) and "x" in size:
            left, right = size.lower().split("x", 1)
            width, height = int(left), int(right)

        seed = int(request.get("seed") or random.randint(1, 2**63 - 1))
        negative_prompt = str(request.get("negative_prompt") or model_config.get("negative_prompt", ""))

        return build_checkpoint_txt2img_workflow(
            checkpoint=str(request.get("checkpoint") or model_config.get("checkpoint")),
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=int(request.get("steps") or model_config.get("steps", 20)),
            cfg=float(request.get("cfg") or model_config.get("cfg", 7.0)),
            sampler_name=str(request.get("sampler_name") or model_config.get("sampler_name", "euler")),
            scheduler=str(request.get("scheduler") or model_config.get("scheduler", "normal")),
            seed=seed,
            batch_size=int(request.get("n") or 1),
            loras=loras,
            filename_prefix=str(request.get("filename_prefix") or model_config.get("filename_prefix", "openaiserve/image")),
        )

    def _select_image_model(self, requested_model: str, image_models: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if not image_models:
            raise HTTPException(status_code=500, detail="No ComfyUI image models configured")

        default_key = str(self.config.get("default_image_model") or next(iter(image_models.keys())))
        normalized = requested_model.removeprefix("comfyui-").replace("-", "_")
        candidates = [requested_model, normalized]

        for candidate in candidates:
            if candidate in image_models:
                return candidate, image_models[candidate]

        for key, model_config in image_models.items():
            aliases = {str(alias).lower().replace("-", "_") for alias in model_config.get("aliases", [])}
            aliases.add(key.lower().replace("-", "_"))
            aliases.add(str(model_config.get("family", "")).lower().replace("-", "_"))
            if normalized in aliases:
                return key, model_config

        if "sdxl" in normalized:
            for key, model_config in image_models.items():
                if str(model_config.get("family", key)).lower() == "sdxl":
                    return key, model_config
        if "flux" in normalized:
            for key, model_config in image_models.items():
                if str(model_config.get("family", key)).lower() == "flux":
                    return key, model_config

        if default_key in image_models:
            return default_key, image_models[default_key]

        return next(iter(image_models.items()))

    def build_video_workflow(self, request: dict[str, Any]) -> dict[str, Any]:
        video_config = self.config.get("video", {})
        video_models = video_config.get("models") or {}
        requested_model = str(request.get("model") or video_config.get("default_model") or "").lower()
        model_key, model_config = self._select_video_model(requested_model, video_models, video_config)
        model_family = str(model_config.get("family") or model_key or "video").lower()

        character_names = self._requested_characters(request)
        prompt, character_loras = self._apply_characters(
            str(request.get("prompt", "")),
            character_names,
            model_key=model_key,
            model_family=model_family,
        )
        request_loras = request.get("loras") or []
        loras = [*character_loras, *request_loras]

        if str(model_config.get("workflow", "")).lower() == "wan21_t2v":
            return build_wan21_t2v_workflow(
                diffusion_model=str(request.get("diffusion_model") or model_config.get("diffusion_model")),
                text_encoder=str(request.get("text_encoder") or model_config.get("text_encoder")),
                vae=str(request.get("vae") or model_config.get("vae")),
                prompt=prompt,
                negative_prompt=str(request.get("negative_prompt") or model_config.get("negative_prompt", "")),
                width=int(request.get("width") or model_config.get("width", 832)),
                height=int(request.get("height") or model_config.get("height", 480)),
                frames=int(request.get("frames") or model_config.get("frames", 33)),
                steps=int(request.get("steps") or model_config.get("steps", 30)),
                cfg=float(request.get("cfg") or model_config.get("cfg", 6.0)),
                sampler_name=str(request.get("sampler_name") or model_config.get("sampler_name", "uni_pc")),
                scheduler=str(request.get("scheduler") or model_config.get("scheduler", "simple")),
                seed=int(request.get("seed") or random.randint(1, 2**63 - 1)),
                shift=float(request.get("shift") or model_config.get("shift", 8.0)),
                batch_size=int(request.get("batch_size") or model_config.get("batch_size", 1)),
                fps=int(request.get("fps") or model_config.get("fps", 16)),
                loras=loras,
                filename_prefix=str(
                    request.get("filename_prefix") or model_config.get("filename_prefix", "openaiserve/wan2_1_t2v")
                ),
            )

        template = str(
            request.get("workflow_template")
            or model_config.get("workflow_template")
            or video_config.get("workflow_template")
            or ""
        ).strip()
        if not template:
            raise HTTPException(
                status_code=501,
                detail="ComfyUI video generation needs a supported built-in workflow or workflow_template JSON.",
            )
        template_path = resolve_path(template, ROOT_DIR)
        if not template_path.exists():
            raise HTTPException(status_code=400, detail=f"Video workflow template not found: {template_path}")
        tokens = {
            "prompt": prompt,
            "negative_prompt": request.get("negative_prompt", ""),
            "seed": request.get("seed", random.randint(1, 2**63 - 1)),
            "width": request.get("width", model_config.get("width", 1024)),
            "height": request.get("height", model_config.get("height", 576)),
            "frames": request.get("frames", model_config.get("frames", 81)),
            "steps": request.get("steps", model_config.get("steps", 20)),
            **build_lora_tokens(loras),
        }
        return load_template_workflow(Path(template_path), tokens)

    def _select_video_model(
        self,
        requested_model: str,
        video_models: dict[str, Any],
        video_config: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if not video_models:
            return "", video_config

        default_key = str(video_config.get("default_model") or next(iter(video_models.keys())))
        normalized = requested_model.removeprefix("comfyui-").replace("-", "_").replace(".", "_")
        for key, model_config in video_models.items():
            aliases = {str(alias).lower().replace("-", "_").replace(".", "_") for alias in model_config.get("aliases", [])}
            aliases.add(key.lower().replace("-", "_").replace(".", "_"))
            aliases.add(str(model_config.get("family", "")).lower().replace("-", "_").replace(".", "_"))
            if normalized in aliases:
                return key, model_config

        if default_key in video_models:
            return default_key, video_models[default_key]
        return next(iter(video_models.items()))

    async def generate(self, workflow: dict[str, Any], response_format: str = "b64_json") -> list[dict[str, Any]]:
        prompt_id = await self._queue_prompt(workflow)
        history = await self._wait_for_history(prompt_id)
        files = self._extract_output_files(history, prompt_id)

        results: list[dict[str, Any]] = []
        for file_info in files:
            url = self._file_url(file_info)
            if response_format == "url":
                results.append({"url": url})
            else:
                content = await self._download_file(file_info)
                results.append({"b64_json": base64.b64encode(content).decode("ascii"), "url": url})
        return results

    async def _queue_prompt(self, workflow: dict[str, Any]) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{self.base_url}/prompt", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"ComfyUI prompt queue failed: {exc}") from exc
        data = response.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise HTTPException(status_code=502, detail=f"ComfyUI did not return prompt_id: {data}")
        return str(prompt_id)

    async def _wait_for_history(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        async with httpx.AsyncClient(timeout=30) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(f"{self.base_url}/history/{prompt_id}")
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise HTTPException(status_code=502, detail=f"ComfyUI history request failed: {exc}") from exc
                data = response.json()
                if prompt_id in data:
                    return data[prompt_id]
                await asyncio.sleep(1)
        raise HTTPException(status_code=504, detail=f"ComfyUI generation timed out after {self.timeout_seconds}s")

    def _extract_output_files(self, history: dict[str, Any], prompt_id: str) -> list[dict[str, Any]]:
        outputs = history.get("outputs") or {}
        files: list[dict[str, Any]] = []
        for node_output in outputs.values():
            for key in ("images", "gifs", "videos", "audio"):
                for item in node_output.get(key, []) or []:
                    if item.get("filename"):
                        files.append(item)
        if not files:
            raise HTTPException(status_code=502, detail=f"ComfyUI completed prompt {prompt_id} but returned no files.")
        return files

    def _file_url(self, file_info: dict[str, Any]) -> str:
        query = urlencode(
            {
                "filename": file_info.get("filename", ""),
                "subfolder": file_info.get("subfolder", ""),
                "type": file_info.get("type", "output"),
            }
        )
        return f"{self.base_url}/view?{query}"

    async def _download_file(self, file_info: dict[str, Any]) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.get(self._file_url(file_info))
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"ComfyUI output download failed: {exc}") from exc

    def _requested_characters(self, request: dict[str, Any]) -> list[str]:
        raw_values = [
            request.get("character"),
            request.get("role"),
            request.get("characters"),
            request.get("roles"),
        ]
        names: list[str] = []
        for raw in raw_values:
            if not raw:
                continue
            if isinstance(raw, list):
                names.extend(str(item) for item in raw if item)
            else:
                names.append(str(raw))

        unique_names: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name not in seen:
                unique_names.append(name)
                seen.add(name)
        return unique_names

    def _apply_characters(
        self,
        prompt: str,
        character_names: list[str],
        *,
        model_key: str,
        model_family: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        if not character_names:
            return prompt, []

        characters = self.config.get("characters") or {}
        prompt_parts: list[str] = []
        loras: list[dict[str, Any]] = []

        for character_name in character_names:
            character = characters.get(str(character_name))
            if not character:
                raise HTTPException(status_code=400, detail=f"Unknown character preset: {character_name}")

            prompt_prefix = str(character.get("prompt_prefix") or "").strip()
            trigger = str(character.get("trigger") or "").strip()
            prompt_parts.extend(part for part in (prompt_prefix, trigger) if part)
            loras.extend(
                (character.get("loras_by_model") or {}).get(model_key)
                or (character.get("loras_by_family") or {}).get(model_family)
                or character.get("loras")
                or []
            )

        prompt_parts.append(prompt)
        return ", ".join(part for part in prompt_parts if part), loras
