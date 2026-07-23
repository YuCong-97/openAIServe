from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "public_base_url": "http://127.0.0.1:8000",
        "output_dir": "outputs",
    },
    "auth": {
        "api_key": "change-me-openaiserve-key",
        "allow_no_key": False,
    },
    "providers": {
        "ollama": {
            "enabled": True,
            "base_url": "http://127.0.0.1:11434",
            "default_model": "qwen3:30b",
            "timeout_seconds": 600,
        },
        "comfyui": {
            "enabled": True,
            "base_url": "http://127.0.0.1:8188",
            "client_id": "openaiserve",
            "timeout_seconds": 900,
            "default_image_model": "flux_schnell_fp8",
            "image_models": {
                "flux_schnell_fp8": {
                    "family": "flux",
                    "workflow": "checkpoint_txt2img",
                    "aliases": ["flux", "comfyui-flux"],
                    "checkpoint": "flux1-schnell-fp8.safetensors",
                    "width": 1024,
                    "height": 1024,
                    "steps": 4,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "negative_prompt": "",
                    "filename_prefix": "openaiserve/flux",
                },
                "sdxl_base": {
                    "family": "sdxl",
                    "workflow": "checkpoint_txt2img",
                    "aliases": ["sdxl", "comfyui-sdxl"],
                    "checkpoint": "sd_xl_base_1.0.safetensors",
                    "width": 1024,
                    "height": 1024,
                    "steps": 28,
                    "cfg": 6.5,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "negative_prompt": "low quality, blurry, deformed, extra fingers, bad anatomy, watermark, text",
                    "filename_prefix": "openaiserve/sdxl",
                },
            },
            "characters": {},
            "video": {
                "default_model": "wan2_1_t2v_1_3b_480p",
                "workflow_template": "",
                "models": {
                    "wan2_1_t2v_1_3b_480p": {
                        "family": "wan21_video",
                        "workflow": "wan21_t2v",
                        "aliases": ["wan2.1-t2v-1.3b", "wan2_1_t2v_1_3b", "comfyui-video"],
                        "diffusion_model": "wan2.1_t2v_1.3B_fp16.safetensors",
                        "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                        "vae": "wan_2.1_vae.safetensors",
                        "width": 832,
                        "height": 480,
                        "frames": 33,
                        "steps": 30,
                        "cfg": 6.0,
                        "shift": 8.0,
                        "sampler_name": "uni_pc",
                        "scheduler": "simple",
                        "fps": 16,
                        "negative_prompt": (
                            "overexposed, static, blurred details, subtitles, text, watermark, "
                            "low quality, jpeg artifacts, bad hands, bad face, deformed limbs"
                        ),
                        "filename_prefix": "openaiserve/wan2_1_t2v",
                    }
                },
            },
        },
        "cosyvoice3": {
            "enabled": False,
            "base_url": "http://127.0.0.1:50000",
            "timeout_seconds": 300,
            "note": "Reserved for a future CosyVoice 3 audio provider.",
        },
    },
    "model_profiles": {
        "rtx3090": {
            "ollama_models": [
                {
                    "name": "qwen3:30b",
                    "repo_id": "Qwen/Qwen3-30B-A3B-GGUF",
                    "filename": "Qwen3-30B-A3B-Q4_K_M.gguf",
                    "source_urls": [
                        "https://modelscope.cn/models/Qwen/Qwen3-30B-A3B-GGUF/resolve/master/Qwen3-30B-A3B-Q4_K_M.gguf"
                    ],
                    "target": "deps/ollama-models",
                    "template": "qwen",
                    "parameters": {
                        "num_ctx": 32768,
                        "temperature": 0.6,
                        "top_p": 0.95,
                    },
                },
                {
                    "name": "qwen2.5-coder:32b",
                    "repo_id": "Qwen/Qwen2.5-Coder-32B-Instruct-GGUF",
                    "filename": "qwen2.5-coder-32b-instruct-q4_k_m.gguf",
                    "source_urls": [
                        (
                            "https://modelscope.cn/models/Qwen/Qwen2.5-Coder-32B-Instruct-GGUF/resolve/master/"
                            "qwen2.5-coder-32b-instruct-q4_k_m.gguf"
                        )
                    ],
                    "target": "deps/ollama-models",
                    "template": "qwen",
                    "parameters": {
                        "num_ctx": 32768,
                        "temperature": 0.3,
                        "top_p": 0.9,
                    },
                },
            ],
            "comfyui_models": [
                {
                    "id": "flux-schnell-fp8",
                    "repo_id": "Comfy-Org/flux1-schnell",
                    "filename": "flux1-schnell-fp8.safetensors",
                    "target": "checkpoints",
                },
                {
                    "id": "sdxl-base-1.0",
                    "repo_id": "stabilityai/stable-diffusion-xl-base-1.0",
                    "filename": "sd_xl_base_1.0.safetensors",
                    "target": "checkpoints",
                },
                {
                    "id": "wan2.1-t2v-1.3b",
                    "repo_id": "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
                    "repo_filename": "split_files/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors",
                    "target": "diffusion_models",
                },
                {
                    "id": "wan2.1-umt5-fp8",
                    "repo_id": "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
                    "repo_filename": "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                    "target": "text_encoders",
                },
                {
                    "id": "wan2.1-vae",
                    "repo_id": "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
                    "repo_filename": "split_files/vae/wan_2.1_vae.safetensors",
                    "target": "vae",
                },
            ],
            "cosyvoice_models": [],
        },
        "minimal": {
            "ollama_models": [
                {
                    "name": "qwen3:8b",
                    "repo_id": "Qwen/Qwen3-8B-GGUF",
                    "filename": "Qwen3-8B-Q4_K_M.gguf",
                    "source_urls": [
                        "https://modelscope.cn/models/Qwen/Qwen3-8B-GGUF/resolve/master/Qwen3-8B-Q4_K_M.gguf"
                    ],
                    "target": "deps/ollama-models",
                    "template": "qwen",
                    "parameters": {
                        "num_ctx": 32768,
                        "temperature": 0.6,
                        "top_p": 0.95,
                    },
                }
            ],
            "comfyui_models": [],
            "cosyvoice_models": [],
        },
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_path(path_value: str | Path, base: Path | None = None) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (base or ROOT_DIR) / path


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    raw_path = config_path or os.getenv("OPENAISERVE_CONFIG") or "config.yaml"
    path = resolve_path(raw_path)

    if not path.exists() and Path(raw_path).name == "config.yaml":
        example = ROOT_DIR / "config.example.yaml"
        if example.exists():
            path = example

    if not path.exists():
        return deepcopy(DEFAULT_CONFIG)

    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}

    return deep_merge(DEFAULT_CONFIG, loaded)


def configured_api_key(config: dict[str, Any]) -> str:
    return (
        os.getenv("OPENAISERVE_API_KEY")
        or str(config.get("auth", {}).get("api_key") or "").strip()
    )
