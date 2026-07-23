from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _replace_tokens(value: Any, tokens: dict[str, Any]) -> Any:
    if isinstance(value, str):
        for key, token_value in tokens.items():
            if value == "{{" + key + "}}":
                return token_value
        replaced = value
        for key, token_value in tokens.items():
            replaced = replaced.replace("{{" + key + "}}", str(token_value))
        return replaced
    if isinstance(value, list):
        return [_replace_tokens(item, tokens) for item in value]
    if isinstance(value, dict):
        return {key: _replace_tokens(item, tokens) for key, item in value.items()}
    return value


def load_template_workflow(path: Path, tokens: dict[str, Any]) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        workflow = json.load(fh)
    return _replace_tokens(workflow, tokens)


def normalize_loras(loras: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in loras or []:
        name = str(item.get("name") or item.get("lora_name") or "").strip()
        if not name:
            continue
        strength = float(item.get("strength", item.get("strength_model", 0.75)))
        normalized_item = dict(item)
        normalized_item["name"] = name
        normalized_item["lora_name"] = name
        normalized_item["strength"] = strength
        normalized_item["strength_model"] = float(item.get("strength_model", strength))
        normalized_item["strength_clip"] = float(item.get("strength_clip", strength))
        normalized.append(normalized_item)
    return normalized


def build_lora_tokens(loras: list[dict[str, Any]] | None) -> dict[str, Any]:
    normalized = normalize_loras(loras)
    first = normalized[0] if normalized else {}
    tokens: dict[str, Any] = {
        "lora_count": len(normalized),
        "lora_name": first.get("name", ""),
        "lora_strength": first.get("strength", 0),
        "lora_strength_model": first.get("strength_model", 0),
        "lora_strength_clip": first.get("strength_clip", 0),
    }
    for index, lora in enumerate(normalized):
        tokens[f"lora_{index}_name"] = lora["name"]
        tokens[f"lora_{index}_strength"] = lora["strength"]
        tokens[f"lora_{index}_strength_model"] = lora["strength_model"]
        tokens[f"lora_{index}_strength_clip"] = lora["strength_clip"]
    return tokens


def build_checkpoint_txt2img_workflow(
    *,
    checkpoint: str,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    seed: int,
    batch_size: int = 1,
    loras: list[dict[str, Any]] | None = None,
    filename_prefix: str = "openaiserve/image",
) -> dict[str, Any]:
    workflow: dict[str, Any] = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        }
    }

    model_ref: list[Any] = ["1", 0]
    clip_ref: list[Any] = ["1", 1]
    next_id = 2

    for lora in normalize_loras(loras):
        node_id = str(next_id)
        workflow[node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": model_ref,
                "clip": clip_ref,
                "lora_name": lora["name"],
                "strength_model": lora["strength_model"],
                "strength_clip": lora["strength_clip"],
            },
        }
        model_ref = [node_id, 0]
        clip_ref = [node_id, 1]
        next_id += 1

    positive_id = str(next_id)
    negative_id = str(next_id + 1)
    latent_id = str(next_id + 2)
    sampler_id = str(next_id + 3)
    decode_id = str(next_id + 4)
    save_id = str(next_id + 5)

    workflow.update(
        {
            positive_id: {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": clip_ref},
            },
            negative_id: {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt, "clip": clip_ref},
            },
            latent_id: {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": batch_size},
            },
            sampler_id: {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": sampler_name,
                    "scheduler": scheduler,
                    "denoise": 1.0,
                    "model": model_ref,
                    "positive": [positive_id, 0],
                    "negative": [negative_id, 0],
                    "latent_image": [latent_id, 0],
                },
            },
            decode_id: {
                "class_type": "VAEDecode",
                "inputs": {"samples": [sampler_id, 0], "vae": ["1", 2]},
            },
            save_id: {
                "class_type": "SaveImage",
                "inputs": {"images": [decode_id, 0], "filename_prefix": filename_prefix},
            },
        }
    )

    return workflow


def build_wan21_t2v_workflow(
    *,
    diffusion_model: str,
    text_encoder: str,
    vae: str,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    frames: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    seed: int,
    shift: float = 8.0,
    batch_size: int = 1,
    fps: int = 16,
    loras: list[dict[str, Any]] | None = None,
    filename_prefix: str = "openaiserve/wan2_1_t2v",
) -> dict[str, Any]:
    workflow: dict[str, Any] = {
        "37": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": diffusion_model, "weight_dtype": "default"},
        }
    }

    model_ref: list[Any] = ["37", 0]
    next_id = 100
    for lora in normalize_loras(loras):
        node_id = str(next_id)
        workflow[node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": model_ref,
                "lora_name": lora["name"],
                "strength_model": lora["strength_model"],
            },
        }
        model_ref = [node_id, 0]
        next_id += 1

    workflow.update(
        {
            "48": {
                "class_type": "ModelSamplingSD3",
                "inputs": {"model": model_ref, "shift": shift},
            },
            "38": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": text_encoder, "type": "wan", "device": "default"},
            },
            "39": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": vae},
            },
            "40": {
                "class_type": "EmptyHunyuanLatentVideo",
                "inputs": {"width": width, "height": height, "length": frames, "batch_size": batch_size},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["38", 0], "text": prompt},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["38", 0], "text": negative_prompt},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["48", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["40", 0],
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": sampler_name,
                    "scheduler": scheduler,
                    "denoise": 1.0,
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["39", 0]},
            },
            "28": {
                "class_type": "SaveAnimatedWEBP",
                "inputs": {
                    "images": ["8", 0],
                    "filename_prefix": filename_prefix,
                    "fps": fps,
                    "lossless": False,
                    "quality": 90,
                    "method": "default",
                },
            },
        }
    )

    return workflow
