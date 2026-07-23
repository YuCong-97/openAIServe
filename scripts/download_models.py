from __future__ import annotations

import argparse
import atexit
import hashlib
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
os.environ.setdefault("OLLAMA_MODELS", str(ROOT_DIR / "deps" / "ollama-store"))

from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402

from openaiserve.config import load_config, resolve_path  # noqa: E402


class OllamaCreateError(RuntimeError):
    pass


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
    env.setdefault("OLLAMA_MODELS", str(ollama_store_root()))
    ollama_store_root().mkdir(parents=True, exist_ok=True)

    print(f"[ollama] starting temporary server for model pulls; store: {env['OLLAMA_MODELS']}; logs: {log_path}")
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


def run_ollama_pulls(models: list[str | dict[str, Any]]) -> None:
    if not models:
        return
    if shutil.which("ollama") is None:
        raise SystemExit("ollama is not on PATH. Install/start Ollama first, then rerun model download.")
    process = ensure_ollama_server()
    try:
        for model in models:
            ensure_ollama_model(model)
    finally:
        if process is not None:
            terminate_process(process)


def hf_endpoints() -> list[str]:
    configured = os.getenv("HF_ENDPOINTS") or os.getenv("HF_ENDPOINT")
    if configured:
        return [item.strip().rstrip("/") for item in configured.split() if item.strip()]
    return ["https://hf-mirror.com", "https://huggingface.co"]


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
    except (KeyError, ValueError) as exc:
        raise SystemExit(
            f"Invalid MODEL_DIRECT_URL_TEMPLATES entry {template!r}: {exc}. "
            "Use placeholders {repo_id}, {filename}, and {basename} only."
        ) from exc


def direct_urls(item: dict[str, Any], repo_filename: str | None) -> list[str]:
    repo_id = str(item.get("repo_id") or "")
    urls = as_string_list(item.get("source_urls") or item.get("sources") or item.get("urls"))
    if repo_id and repo_filename:
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
    marker_path = target.with_name(target.name + ".part.url")
    if marker_path.exists() and marker_path.read_text(encoding="utf-8").strip() != url:
        tmp_path.unlink(missing_ok=True)
        marker_path.unlink(missing_ok=True)
    marker_path.write_text(url, encoding="utf-8")

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
        marker_path.unlink(missing_ok=True)
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
        marker_path.unlink(missing_ok=True)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        marker_path.unlink(missing_ok=True)
        raise


def copy_or_link_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if source.resolve() == target.resolve():
            return
    except OSError:
        pass
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def download_file_item(
    item: dict[str, Any],
    target_dir: Path,
    label: str = "hf",
    local_candidates: list[Path] | None = None,
) -> Path:
    repo_id = str(item.get("repo_id") or "")
    repo_filename = item.get("repo_filename") or item.get("filename")
    local_filename = item.get("local_filename") or (Path(repo_filename).name if repo_filename else None)
    allow_patterns = item.get("allow_patterns")
    target_dir.mkdir(parents=True, exist_ok=True)

    if repo_filename and local_filename:
        expected = target_dir / local_filename
        if expected.exists():
            print(f"[{label}] exists {expected}")
            return expected
        for candidate in local_candidates or []:
            if candidate.exists():
                print(f"[{label}] using local file {candidate} -> {expected}")
                copy_or_link_file(candidate, expected)
                return expected
        for url in direct_urls(item, repo_filename):
            try:
                print(f"[direct] downloading {url} -> {expected}")
                download_direct_file(url, expected)
                return expected
            except Exception as exc:
                print(f"[direct] failed from {url}: {exc}")
        if repo_id:
            for endpoint in hf_endpoints():
                try:
                    print(f"[hf] downloading {repo_id}/{repo_filename} from {endpoint} -> {expected}")
                    cached_path = hf_hub_download(repo_id=repo_id, filename=repo_filename, endpoint=endpoint)
                    shutil.copy2(cached_path, expected)
                    return expected
                except Exception as exc:
                    print(f"[hf] failed from {endpoint}: {exc}")
        raise SystemExit(
            f"Failed to download {repo_id + '/' if repo_id else ''}{repo_filename}. Set source_urls in config.yaml, "
            "MODEL_DIRECT_URL_TEMPLATES, HF_ENDPOINTS, or HF_ENDPOINT to reachable mirror(s)."
        )

    if not repo_id:
        raise SystemExit(f"Model item must set repo_id for snapshot downloads: {item}")
    for endpoint in hf_endpoints():
        try:
            print(f"[hf] snapshot {repo_id} from {endpoint} -> {target_dir}")
            snapshot_download(
                repo_id=repo_id,
                local_dir=str(target_dir),
                allow_patterns=allow_patterns,
                endpoint=endpoint,
            )
            return target_dir
        except Exception as exc:
            print(f"[hf] failed from {endpoint}: {exc}")
    raise SystemExit(f"Failed to download {repo_id}. Set HF_ENDPOINTS or HF_ENDPOINT to reachable mirror(s).")


def download_hf_item(item: dict[str, Any], target_dir: Path) -> None:
    download_file_item(item, target_dir)


def comfy_local_model_candidates(target: str, filename: str | None) -> list[Path]:
    if not filename:
        return []

    target = target.strip("/\\")
    candidates = []
    for base in (ROOT_DIR / "packages" / "comfyui-models", ROOT_DIR / "downloads" / "comfyui-models"):
        if target:
            candidates.append(base / target / filename)
        candidates.append(base / filename)
    return candidates


def normalize_ollama_model(item: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, str):
        return {"name": item, "pull": item}
    return dict(item)


def ollama_model_name(item: dict[str, Any]) -> str:
    name = item.get("name") or item.get("model") or item.get("id")
    if not name:
        raise SystemExit(f"Ollama model entry is missing name/model/id: {item}")
    return str(name)


def ollama_model_exists(name: str) -> bool:
    return subprocess.run(["ollama", "show", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def ollama_model_root() -> Path:
    return resolve_path(os.getenv("OLLAMA_MODEL_DIR", "deps/ollama-models"), ROOT_DIR)


def ollama_store_root() -> Path:
    return resolve_path(os.getenv("OLLAMA_MODELS", "deps/ollama-store"), ROOT_DIR)


def ollama_local_model_candidates(item: dict[str, Any], filename: str | None) -> list[Path]:
    candidates = []
    for key in ("local_path", "model_path", "gguf_path"):
        if item.get(key):
            candidates.append(resolve_path(str(item[key]), ROOT_DIR))
    if filename:
        for base in (
            os.getenv("OLLAMA_MODEL_DIR"),
            "deps/ollama-models",
            "packages/ollama-models",
            "downloads/ollama-models",
        ):
            if base:
                candidates.append(resolve_path(base, ROOT_DIR) / filename)
    return candidates


def qwen_modelfile(model_path: Path, item: dict[str, Any]) -> str:
    parameters = {
        "num_ctx": 32768,
        "temperature": 0.6,
        "top_p": 0.95,
    }
    parameters.update(item.get("parameters") or {})
    lines = [f"FROM {model_path.as_posix()}"]
    for key, value in parameters.items():
        lines.append(f"PARAMETER {key} {value}")
    lines.extend(
        [
            'TEMPLATE """{{ if .System }}<|im_start|>system',
            "{{ .System }}<|im_end|>",
            "{{ end }}{{ range .Messages }}<|im_start|>{{ .Role }}",
            "{{ .Content }}<|im_end|>",
            "{{ end }}<|im_start|>assistant",
            '"""',
            'PARAMETER stop "<|im_start|>"',
            'PARAMETER stop "<|im_end|>"',
        ]
    )
    return "\n".join(lines) + "\n"


def ollama_modelfile_text(model_path: Path, item: dict[str, Any]) -> str:
    if item.get("modelfile"):
        return str(item["modelfile"]).replace("{{model_path}}", model_path.as_posix())
    template = str(item.get("template", "qwen")).lower()
    if template in {"", "none", "raw"}:
        return f"FROM {model_path.as_posix()}\n"
    if template in {"qwen", "chatml"}:
        return qwen_modelfile(model_path, item)
    raise SystemExit(f"Unknown Ollama Modelfile template for {ollama_model_name(item)}: {template}")


def run_ollama_create(name: str, modelfile_text: str) -> None:
    modelfile_dir = ollama_model_root() / "modelfiles"
    modelfile_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(char if char.isalnum() else "-" for char in name).strip("-")
    modelfile_path = modelfile_dir / f"{safe_name}.Modelfile"
    modelfile_path.write_text(modelfile_text, encoding="utf-8")
    subprocess.run(["ollama", "create", name, "-f", str(modelfile_path)], check=True)


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    read_bytes = 0
    last_report = time.monotonic()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024 * 8)
            if not chunk:
                break
            digest.update(chunk)
            read_bytes += len(chunk)
            now = time.monotonic()
            if now - last_report >= 5:
                print(f"[ollama] hashing {path.name}: {format_bytes(read_bytes)} / {format_bytes(total)}")
                last_report = now
    return digest.hexdigest()


def preseed_ollama_blob(model_path: Path) -> Path | None:
    if not bool_env("OLLAMA_PRESEED_BLOBS", True):
        return None

    source = model_path.resolve()
    blob_dir = ollama_store_root() / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)

    digest = sha256_file(source)
    blob_path = blob_dir / f"sha256-{digest}"
    if blob_path.exists():
        if blob_path.stat().st_size == source.stat().st_size:
            print(f"[ollama] blob already exists {blob_path}")
            return blob_path
        blob_path.unlink()

    try:
        os.link(source, blob_path)
        print(f"[ollama] hardlinked GGUF into blob store {blob_path}")
        return blob_path
    except OSError as exc:
        print(
            f"[ollama] could not hardlink GGUF into {blob_dir}: {exc}. "
            "Ollama may need enough free space to import another full copy."
        )
        return None


def create_ollama_model(item: dict[str, Any], model_path: Path) -> None:
    name = ollama_model_name(item)
    preseed_ollama_blob(model_path)
    print(f"[ollama] creating {name} from {model_path}; store: {ollama_store_root()}")
    try:
        run_ollama_create(name, ollama_modelfile_text(model_path.resolve(), item))
    except subprocess.CalledProcessError as exc:
        raise OllamaCreateError(
            f"Failed to create Ollama model {name} from {model_path}. "
            f"Ollama imports GGUF files into OLLAMA_MODELS={ollama_store_root()}; "
            "make sure this path is on a disk with enough free space, then restart Ollama and rerun."
        ) from exc


def create_ollama_from_file(item: dict[str, Any]) -> bool:
    filename = item.get("local_filename") or item.get("repo_filename") or item.get("filename")
    filename = Path(str(filename)).name if filename else None
    for candidate in ollama_local_model_candidates(item, filename):
        if candidate.exists():
            create_ollama_model(item, candidate)
            return True

    if not filename or not (item.get("source_urls") or item.get("sources") or item.get("urls") or item.get("repo_id")):
        return False

    target_dir = resolve_path(item.get("target", ollama_model_root()), ROOT_DIR)
    model_path = download_file_item(item, target_dir, label="ollama")
    create_ollama_model(item, model_path)
    return True


def pull_ollama_model(item: dict[str, Any]) -> None:
    name = ollama_model_name(item)
    pull_name = str(item.get("pull") or item.get("pull_model") or name)
    print(f"[ollama] pulling {pull_name}")
    subprocess.run(["ollama", "pull", pull_name], check=True)
    if pull_name != name and not ollama_model_exists(name):
        print(f"[ollama] creating alias {name} from pulled model {pull_name}")
        run_ollama_create(name, f"FROM {pull_name}\n")


def ensure_ollama_model(raw_item: str | dict[str, Any]) -> None:
    item = normalize_ollama_model(raw_item)
    name = ollama_model_name(item)
    if ollama_model_exists(name):
        print(f"[ollama] exists {name}")
        return

    try:
        if create_ollama_from_file(item):
            return
    except OllamaCreateError as exc:
        raise SystemExit(str(exc)) from exc
    except SystemExit as exc:
        print(f"[ollama] failed to prepare {name} from local/direct GGUF: {exc}")
    except Exception as exc:
        print(f"[ollama] failed to create {name} from local/direct GGUF: {exc}")

    pull_fallback = str(os.getenv("OLLAMA_PULL_FALLBACK", str(item.get("pull_fallback", False)))).lower()
    if pull_fallback not in {"1", "true", "yes", "on"}:
        raise SystemExit(f"Ollama model {name} is not available locally and OLLAMA_PULL_FALLBACK is disabled.")

    try:
        pull_ollama_model(item)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Failed to install Ollama model {name}. The Ollama registry may be unreachable from this host. "
            "Use the configured ModelScope GGUF source, set source_urls to a reachable mirror, place the GGUF under "
            "deps/ollama-models or packages/ollama-models, or set OLLAMA_PULL_FALLBACK=false to skip registry pulls."
        ) from exc


def download_comfy_models(config: dict[str, Any], items: list[dict[str, Any]], include_optional: bool) -> None:
    comfy_root = resolve_path(config.get("paths", {}).get("comfyui_dir", "deps/ComfyUI"), ROOT_DIR)
    models_root = comfy_root / "models"
    for item in items:
        if item.get("optional") and not include_optional:
            print(f"[comfyui] skipping optional model {item.get('id')}")
            continue
        target = str(item.get("target", "checkpoints"))
        target_dir = resolve_path(target, models_root)
        repo_filename = item.get("repo_filename") or item.get("filename")
        local_filename = item.get("local_filename") or (Path(repo_filename).name if repo_filename else None)
        download_file_item(
            item,
            target_dir,
            label="comfyui",
            local_candidates=comfy_local_model_candidates(target, str(local_filename) if local_filename else None),
        )


def download_cosyvoice_models(items: list[dict[str, Any]], include_optional: bool) -> None:
    for item in items:
        if item.get("optional") and not include_optional:
            print(f"[cosyvoice3] skipping optional model {item.get('id')}")
            continue
        target_dir = resolve_path(item.get("target", "deps/CosyVoice/pretrained_models"), ROOT_DIR)
        download_hf_item(item, target_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download direct-mirror, Ollama, and Hugging Face models.")
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
