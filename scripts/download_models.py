from __future__ import annotations

import argparse
import atexit
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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


def ollama_base_url() -> str:
    raw = os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if "://" not in raw:
        raw = "http://" + raw
    return raw.rstrip("/")


def ollama_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{ollama_base_url()}/api/tags", timeout=2) as response:
            return response.status < 500
    except (urllib.error.URLError, TimeoutError):
        return False


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def ensure_ollama_server() -> subprocess.Popen[bytes] | None:
    if ollama_ready():
        return None

    logs_dir = ROOT_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "ollama-download.log"
    log_file = log_path.open("ab")
    env = os.environ.copy()
    env.setdefault("OLLAMA_HOST", "127.0.0.1:11434")

    print(f"[ollama] starting temporary server for model pulls; logs: {log_path}")
    process = subprocess.Popen(["ollama", "serve"], stdout=log_file, stderr=subprocess.STDOUT, env=env)
    atexit.register(log_file.close)
    atexit.register(terminate_process, process)

    for _ in range(60):
        if process.poll() is not None:
            raise SystemExit(f"ollama serve exited early. See {log_path}")
        if ollama_ready():
            return process
        time.sleep(1)

    terminate_process(process)
    raise SystemExit(f"ollama serve did not become ready within 60s. See {log_path}")


def run_ollama_pulls(models: list[str]) -> None:
    if not models:
        return
    if shutil.which("ollama") is None:
        raise SystemExit("ollama is not on PATH. Install/start Ollama first, then rerun model download.")
    process = ensure_ollama_server()
    try:
        for model in models:
            print(f"[ollama] pulling {model}")
            subprocess.run(["ollama", "pull", model], check=True)
    finally:
        if process is not None:
            terminate_process(process)


def hf_endpoints() -> list[str]:
    configured = os.getenv("HF_ENDPOINTS") or os.getenv("HF_ENDPOINT")
    if configured:
        return [item.strip().rstrip("/") for item in configured.split() if item.strip()]
    return ["https://huggingface.co", "https://hf-mirror.com"]


def direct_url_templates() -> list[str]:
    if "MODEL_DIRECT_URL_TEMPLATES" in os.environ:
        return [item.strip() for item in os.environ["MODEL_DIRECT_URL_TEMPLATES"].split() if item.strip()]
    return ["https://modelscope.cn/models/{repo_id}/resolve/master/{filename}"]


def as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict) and isinstance(item.get("url"), str):
                result.append(item["url"])
        return result
    return []


def render_direct_url(template: str, repo_id: str, filename: str) -> str:
    values = {
        "repo_id": urllib.parse.quote(repo_id, safe="/"),
        "filename": urllib.parse.quote(filename, safe="/"),
        "basename": urllib.parse.quote(Path(filename).name, safe=""),
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise SystemExit(f"Unknown placeholder in MODEL_DIRECT_URL_TEMPLATES entry {template!r}: {exc}") from exc


def direct_urls(item: dict[str, Any], repo_filename: str | None) -> list[str]:
    repo_id = item["repo_id"]
    urls = as_string_list(item.get("source_urls") or item.get("sources") or item.get("urls"))
    if repo_filename:
        urls.extend(render_direct_url(template, repo_id, repo_filename) for template in direct_url_templates())

    deduped = []
    seen = set()
    for url in urls:
        if url and url not in seen:
            deduped.append(url)
            seen.add(url)
    return deduped


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def download_direct_file(url: str, target: Path) -> None:
    tmp_path = target.with_name(target.name + ".part")
    if shutil.which("curl"):
        command = [
            "curl",
            "-fL",
            "--retry",
            os.getenv("MODEL_DOWNLOAD_RETRIES", "5"),
            "--retry-delay",
            os.getenv("MODEL_DOWNLOAD_RETRY_DELAY", "5"),
            "--connect-timeout",
            os.getenv("MODEL_DOWNLOAD_CONNECT_TIMEOUT", "20"),
            "--max-time",
            os.getenv("MODEL_DOWNLOAD_MAX_TIME", "0"),
            "-C",
            "-",
            url,
            "-o",
            str(tmp_path),
        ]
        subprocess.run(command, check=True)
        tmp_path.replace(target)
        return

    request = urllib.request.Request(url, headers={"User-Agent": "openAIServe-model-downloader/1.0"})
    try:
        if tmp_path.exists():
            tmp_path.unlink()
        with urllib.request.urlopen(request, timeout=60) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")

            downloaded = 0
            last_report = time.monotonic()
            with tmp_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 2:
                        print(f"[direct] downloading {target.name}: {format_bytes(downloaded)}")
                        last_report = now

        tmp_path.replace(target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


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
        for url in direct_urls(item, repo_filename):
            try:
                print(f"[direct] downloading {url} -> {expected}")
                download_direct_file(url, expected)
                return
            except Exception as exc:
                print(f"[direct] failed from {url}: {exc}")
        for endpoint in hf_endpoints():
            try:
                print(f"[hf] downloading {repo_id}/{repo_filename} from {endpoint} -> {expected}")
                cached_path = hf_hub_download(repo_id=repo_id, filename=repo_filename, endpoint=endpoint)
                shutil.copy2(cached_path, expected)
                return
            except Exception as exc:
                print(f"[hf] failed from {endpoint}: {exc}")
        raise SystemExit(
            f"Failed to download {repo_id}/{repo_filename}. Set source_urls in config.yaml, "
            "MODEL_DIRECT_URL_TEMPLATES, HF_ENDPOINTS, or HF_ENDPOINT to reachable mirror(s)."
        )

    for endpoint in hf_endpoints():
        try:
            print(f"[hf] snapshot {repo_id} from {endpoint} -> {target_dir}")
            snapshot_download(
                repo_id=repo_id,
                local_dir=str(target_dir),
                allow_patterns=allow_patterns,
                endpoint=endpoint,
            )
            return
        except Exception as exc:
            print(f"[hf] failed from {endpoint}: {exc}")
    raise SystemExit(f"Failed to download {repo_id}. Set HF_ENDPOINTS or HF_ENDPOINT to reachable mirror(s).")


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
