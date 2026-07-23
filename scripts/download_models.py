from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402

from openaiserve.config import load_config, resolve_path  # noqa: E402


def selected_components(value: str) -> set[str]:
    parts = {item.strip().lower() for item in value.split(",") if item.strip()}
    if "all" in parts:
        return {"ollama", "comfyui", "cosyvoice"}
    return parts


def run_ollama_pulls(models: list[str]) -> None:
    if not models:
        return
    if shutil.which("ollama") is None:
        raise SystemExit("ollama is not on PATH. Install/start Ollama first, then rerun model download.")
    for model in models:
        print(f"[ollama] pulling {model}")
        subprocess.run(["ollama", "pull", model], check=True)


def download_hf_item(item: dict[str, Any], target_dir: Path) -> None:
    repo_id = item["repo_id"]
    repo_filename = item.get("repo_filename") or item.get("filename")
    local_filename = item.get("local_filename") or (Path(repo_filename).name if repo_filename else None)
    allow_patterns = item.get("allow_patterns")
    target_dir.mkdir(parents=True, exist_ok=True)

    if repo_filename and local_filename:
        expected = target_dir / local_filename
        if expected.exists():
            print(f"[hf] exists {expected}")
            return
        print(f"[hf] downloading {repo_id}/{repo_filename} -> {expected}")
        cached_path = hf_hub_download(repo_id=repo_id, filename=repo_filename)
        shutil.copy2(cached_path, expected)
        return

    print(f"[hf] snapshot {repo_id} -> {target_dir}")
    snapshot_download(repo_id=repo_id, local_dir=str(target_dir), allow_patterns=allow_patterns)


def download_comfy_models(config: dict[str, Any], items: list[dict[str, Any]], include_optional: bool) -> None:
    comfy_root = resolve_path(config.get("paths", {}).get("comfyui_dir", "deps/ComfyUI"), ROOT_DIR)
    models_root = comfy_root / "models"
    for item in items:
        if item.get("optional") and not include_optional:
            print(f"[comfyui] skipping optional model {item.get('id')}")
            continue
        target = item.get("target", "checkpoints")
        target_dir = resolve_path(target, models_root)
        download_hf_item(item, target_dir)


def download_cosyvoice_models(items: list[dict[str, Any]], include_optional: bool) -> None:
    for item in items:
        if item.get("optional") and not include_optional:
            print(f"[cosyvoice3] skipping optional model {item.get('id')}")
            continue
        target_dir = resolve_path(item.get("target", "deps/CosyVoice/pretrained_models"), ROOT_DIR)
        download_hf_item(item, target_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Ollama and Hugging Face models for OpenAI Supplier Server.")
    parser.add_argument("--config", default=None, help="Path to config.yaml. Defaults to OPENAISERVE_CONFIG/config.yaml.")
    parser.add_argument("--profile", default="rtx3090", help="Model profile name from config.model_profiles.")
    parser.add_argument("--components", default="all", help="Comma list: all,ollama,comfyui,cosyvoice.")
    parser.add_argument("--include-optional", action="store_true", help="Also download optional heavy models.")
    args = parser.parse_args()

    config = load_config(args.config)
    profile = (config.get("model_profiles") or {}).get(args.profile)
    if not profile:
        raise SystemExit(f"Unknown model profile: {args.profile}")

    components = selected_components(args.components)
    if "ollama" in components:
        run_ollama_pulls(profile.get("ollama_models") or [])
    if "comfyui" in components:
        download_comfy_models(config, profile.get("comfyui_models") or [], args.include_optional)
    if "cosyvoice" in components:
        download_cosyvoice_models(profile.get("cosyvoice_models") or [], args.include_optional)

    print("Model download step complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
