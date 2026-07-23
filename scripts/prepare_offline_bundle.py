from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

try:
    from openaiserve.config import load_config, resolve_path  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "yaml":
        raise SystemExit("PyYAML is required to read config.yaml. Run: python -m pip install PyYAML") from exc
    raise


DEFAULT_COMFYUI_GIT_URLS = [
    "https://gitcode.com/gh_mirrors/co/ComfyUI.git",
    "https://gitee.com/mirrors/ComfyUI.git",
    "https://gitee.com/mirrors/comfyui.git",
    "https://github.com/Comfy-Org/ComfyUI.git",
]

DEFAULT_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def selected_components(value: str) -> set[str]:
    parts = {item.strip().lower() for item in value.split(",") if item.strip()}
    if "all" in parts:
        return {"ollama", "comfyui", "cosyvoice"}
    return parts


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


def direct_url_templates() -> list[str]:
    configured = os.getenv("MODEL_DIRECT_URL_TEMPLATES")
    if configured:
        return [item.strip() for item in configured.split() if item.strip()]
    return ["https://modelscope.cn/models/{repo_id}/resolve/master/{filename}"]


def render_direct_url(template: str, repo_id: str, filename: str) -> str:
    values = {
        "repo_id": urllib.parse.quote(repo_id, safe="/"),
        "filename": urllib.parse.quote(filename, safe="/"),
        "basename": urllib.parse.quote(Path(filename).name, safe=""),
    }
    return template.format(**values)


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


def model_filename(item: dict[str, Any]) -> tuple[str | None, str | None]:
    repo_filename = item.get("repo_filename") or item.get("filename")
    if not repo_filename:
        return None, None
    local_filename = item.get("local_filename") or Path(str(repo_filename)).name
    return str(repo_filename), str(local_filename)


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def download_url(url: str, target: Path, dry_run: bool = False) -> bool:
    if target.exists():
        print(f"[exists] {target} ({format_bytes(target.stat().st_size)})")
        return True
    if dry_run:
        print(f"[dry-run] download {url} -> {target}")
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
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
        try:
            subprocess.run(command, check=True)
            tmp_path.replace(target)
            marker_path.unlink(missing_ok=True)
            return True
        except subprocess.CalledProcessError as exc:
            print(f"[download] failed from {url}: {exc}")
            return False

    request = urllib.request.Request(url, headers={"User-Agent": "openAIServe-offline-preparer/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if getattr(response, "status", 200) >= 400:
                raise RuntimeError(f"HTTP {response.status}")
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
                        print(f"[download] {target.name}: {format_bytes(downloaded)}")
                        last_report = now
        tmp_path.replace(target)
        marker_path.unlink(missing_ok=True)
        return True
    except Exception as exc:
        print(f"[download] failed from {url}: {exc}")
        return False


def download_from_sources(urls: list[str], target: Path, label: str, dry_run: bool, strict: bool) -> bool:
    if not urls:
        message = f"[{label}] no source URLs for {target.name}"
        if strict:
            raise SystemExit(message)
        print(message)
        return False
    for url in urls:
        print(f"[{label}] {url}")
        if download_url(url, target, dry_run=dry_run):
            return True
    message = f"[{label}] failed to download {target}"
    if strict:
        raise SystemExit(message)
    print(message)
    return False


def ollama_archive_urls(archive_name: str) -> list[str]:
    if os.getenv("OLLAMA_ARCHIVE_URLS"):
        return [item.strip() for item in os.environ["OLLAMA_ARCHIVE_URLS"].split() if item.strip()]
    if os.getenv("OLLAMA_INSTALL_URL"):
        return [os.environ["OLLAMA_INSTALL_URL"]]

    modelscope_ref = os.getenv("OLLAMA_MODELSCOPE_REVISION", "master")
    if os.getenv("OLLAMA_VERSION"):
        modelscope_ref = "v" + os.environ["OLLAMA_VERSION"].lstrip("v")
    modelscope_model = os.getenv("OLLAMA_MODELSCOPE_MODEL", "modelscope/ollama-linux")
    urls = [f"https://modelscope.cn/models/{modelscope_model}/resolve/{modelscope_ref}/{archive_name}"]
    if modelscope_ref != "master":
        urls.append(f"https://modelscope.cn/models/{modelscope_model}/resolve/master/{archive_name}")
    urls.extend(
        [
            f"https://ollama.ac.cn/download/{archive_name}",
            f"https://github.com/ollama/ollama/releases/latest/download/{archive_name}",
        ]
    )
    return urls


def prepare_ollama_archive(dest: Path, arch: str, dry_run: bool, strict: bool) -> None:
    archive_name = f"ollama-linux-{arch}.tar.zst"
    download_from_sources(
        ollama_archive_urls(archive_name),
        dest / archive_name,
        "ollama-archive",
        dry_run=dry_run,
        strict=strict,
    )


def git_urls_from_env() -> list[str]:
    if os.getenv("COMFYUI_GIT_URL"):
        return [os.environ["COMFYUI_GIT_URL"]]
    if os.getenv("COMFYUI_GIT_URLS"):
        return [item.strip() for item in os.environ["COMFYUI_GIT_URLS"].split() if item.strip()]
    return DEFAULT_COMFYUI_GIT_URLS


def run_command(command: list[str], cwd: Path | None = None, dry_run: bool = False) -> bool:
    print("[run] " + " ".join(command))
    if dry_run:
        return True
    try:
        subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)
        return True
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[run] failed: {exc}")
        return False


def prepare_comfyui_repo(dest: Path, dry_run: bool, strict: bool) -> Path | None:
    repos_dir = dest / "repos"
    repo_dir = repos_dir / "ComfyUI"
    archive_path = repos_dir / "ComfyUI.tar.gz"

    if repo_dir.exists():
        if (repo_dir / ".git").exists():
            run_command(["git", "-C", str(repo_dir), "pull", "--ff-only"], dry_run=dry_run)
        else:
            print(f"[comfyui] local source exists: {repo_dir}")
    else:
        cloned = False
        if not dry_run:
            repos_dir.mkdir(parents=True, exist_ok=True)
        for url in git_urls_from_env():
            tmp_dir = repos_dir / "ComfyUI.tmp"
            if tmp_dir.exists() and not dry_run:
                shutil.rmtree(tmp_dir)
            if run_command(["git", "clone", "--depth", "1", url, str(tmp_dir)], dry_run=dry_run):
                if not dry_run:
                    tmp_dir.replace(repo_dir)
                cloned = True
                break
        if not cloned:
            message = "[comfyui] failed to prepare ComfyUI source"
            if strict:
                raise SystemExit(message)
            print(message)
            return None

    if dry_run:
        print(f"[dry-run] archive {repo_dir} -> {archive_path}")
        return repo_dir
    if repo_dir.exists():
        if (repo_dir / ".git").exists() and run_command(
            [
                "git",
                "-C",
                str(repo_dir),
                "archive",
                "--format=tar.gz",
                "--prefix=ComfyUI/",
                "-o",
                str(archive_path),
                "HEAD",
            ]
        ):
            return repo_dir
        shutil.make_archive(str(repos_dir / "ComfyUI"), "gztar", root_dir=repos_dir, base_dir="ComfyUI")
        return repo_dir
    return None


def iter_profile_items(raw_items: list[Any], include_optional: bool) -> list[dict[str, Any]]:
    items = []
    for raw_item in raw_items:
        item = {"name": raw_item, "pull": raw_item} if isinstance(raw_item, str) else dict(raw_item)
        if item.get("optional") and not include_optional:
            print(f"[skip] optional model {item.get('id') or item.get('name')}")
            continue
        items.append(item)
    return items


def prepare_ollama_models(profile: dict[str, Any], dest: Path, include_optional: bool, dry_run: bool, strict: bool) -> None:
    for item in iter_profile_items(profile.get("ollama_models") or [], include_optional):
        repo_filename, local_filename = model_filename(item)
        if not repo_filename or not local_filename:
            print(f"[ollama-model] skip registry-only entry {item.get('name') or item}")
            continue
        target = dest / "ollama-models" / local_filename
        download_from_sources(direct_urls(item, repo_filename), target, "ollama-model", dry_run=dry_run, strict=strict)


def prepare_comfyui_models(profile: dict[str, Any], dest: Path, include_optional: bool, dry_run: bool, strict: bool) -> None:
    for item in iter_profile_items(profile.get("comfyui_models") or [], include_optional):
        repo_filename, local_filename = model_filename(item)
        if not repo_filename or not local_filename:
            print(f"[comfyui-model] skip snapshot-only entry {item.get('id') or item}")
            continue
        target_group = str(item.get("target", "checkpoints")).strip("/\\") or "checkpoints"
        target = dest / "comfyui-models" / target_group / local_filename
        download_from_sources(direct_urls(item, repo_filename), target, "comfyui-model", dry_run=dry_run, strict=strict)


def linux_pip_target_args(python_version: str) -> list[str]:
    digits = python_version.replace(".", "")
    return [
        "--only-binary=:all:",
        "--prefer-binary",
        "--platform",
        "manylinux2014_x86_64",
        "--platform",
        "manylinux_2_17_x86_64",
        "--platform",
        "manylinux_2_28_x86_64",
        "--platform",
        "linux_x86_64",
        "--implementation",
        "cp",
        "--python-version",
        digits,
        "--abi",
        f"cp{digits}",
        "--abi",
        "abi3",
        "--abi",
        "none",
    ]


def pip_download(
    args: list[str],
    wheel_dir: Path,
    label: str,
    dry_run: bool,
    strict: bool,
) -> bool:
    command = [sys.executable, "-m", "pip", "download", "--dest", str(wheel_dir), *args]
    print(f"[wheels:{label}] {wheel_dir}")
    if dry_run:
        print("[dry-run] " + " ".join(command))
        return True
    wheel_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(command, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        message = f"[wheels:{label}] failed: {exc}"
        if strict:
            raise SystemExit(message) from exc
        print(message)
        return False


def filtered_comfyui_requirements(repo_dir: Path, dest: Path) -> Path | None:
    source = repo_dir / "requirements.txt"
    if not source.exists():
        print(f"[wheels:comfyui] missing {source}")
        return None

    target = dest / "requirements" / "comfyui-requirements.no-torch.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    torch_line = re.compile(r"^\s*(torch|torchvision|torchaudio)(\s|$|[<>=~!;\[])")
    lines = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if torch_line.match(line):
            continue
        lines.append(line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def torch_index_url(variant: str) -> str:
    if os.getenv("TORCH_INDEX_URL"):
        return os.environ["TORCH_INDEX_URL"]
    if variant == "cpu":
        return "https://mirrors.aliyun.com/pytorch-wheels/cpu"
    return f"https://mirrors.aliyun.com/pytorch-wheels/{variant}"


def prepare_wheels(
    components: set[str],
    dest: Path,
    python_version: str,
    torch_variant: str,
    comfyui_repo: Path | None,
    dry_run: bool,
    strict: bool,
) -> None:
    target_args = linux_pip_target_args(python_version)
    pip_index = os.getenv("PIP_INDEX_URL", DEFAULT_PIP_INDEX)

    server_args = [
        "-r",
        str(ROOT_DIR / "requirements.txt"),
        "--index-url",
        pip_index,
        *target_args,
    ]
    pip_download(server_args, dest / "wheels" / "server", "server", dry_run=dry_run, strict=strict)
    pip_download(
        ["pip", "setuptools", "wheel", "--index-url", pip_index, *target_args],
        dest / "wheels" / "server",
        "pip-tools",
        dry_run=dry_run,
        strict=strict,
    )

    if "comfyui" in components:
        torch_args = [
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            torch_index_url(torch_variant),
            "--extra-index-url",
            pip_index,
            *target_args,
        ]
        pip_download(torch_args, dest / "wheels" / f"torch-{torch_variant}", "torch", dry_run=dry_run, strict=strict)

        if comfyui_repo is None:
            print("[wheels:comfyui] skip because ComfyUI source is unavailable")
            return
        if dry_run and not (comfyui_repo / "requirements.txt").exists():
            print("[dry-run] download ComfyUI requirement wheels after source is cloned")
            return
        requirements = filtered_comfyui_requirements(comfyui_repo, dest)
        if requirements is None:
            return
        comfy_args = [
            "-r",
            str(requirements),
            "--index-url",
            pip_index,
            "--extra-index-url",
            torch_index_url(torch_variant),
            *target_args,
        ]
        pip_download(comfy_args, dest / "wheels" / "comfyui", "comfyui", dry_run=dry_run, strict=strict)


def write_manifest(dest: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] write manifest under {dest}")
        return

    files = sorted(path for path in dest.rglob("*") if path.is_file() and not path.name.endswith(".part"))
    total = sum(path.stat().st_size for path in files)
    lines = [
        "# openAIServe offline package manifest",
        f"generated_at: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        f"total_files: {len(files)}",
        f"total_size: {format_bytes(total)}",
        "",
    ]
    for path in files:
        rel = path.relative_to(dest).as_posix()
        lines.append(f"{rel}\t{format_bytes(path.stat().st_size)}")
    (dest / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[manifest] {dest / 'MANIFEST.txt'} ({format_bytes(total)})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a local offline package bundle for Linux deployment.")
    parser.add_argument("--config", default=None, help="Path to config.yaml. Defaults to OPENAISERVE_CONFIG/config.yaml.")
    parser.add_argument("--profile", default="rtx3090", help="Model profile name from config.model_profiles.")
    parser.add_argument("--components", default="all", help="Comma list: all,ollama,comfyui,cosyvoice.")
    parser.add_argument("--dest", default="packages", help="Output directory for the offline bundle.")
    parser.add_argument("--python-version", default="310", help="Target Linux Python ABI, for example 310.")
    parser.add_argument("--torch-variant", default="cu124", help="PyTorch wheel variant for RTX 3090, for example cu124.")
    parser.add_argument("--ollama-arch", default="amd64", choices=["amd64", "arm64"], help="Linux Ollama archive CPU arch.")
    parser.add_argument("--include-optional", action="store_true", help="Also download optional heavy models.")
    parser.add_argument("--skip-models", action="store_true", help="Do not download model files.")
    parser.add_argument("--skip-wheels", action="store_true", help="Do not download Python wheels.")
    parser.add_argument("--skip-repos", action="store_true", help="Do not clone/package ComfyUI source.")
    parser.add_argument("--skip-ollama-archive", action="store_true", help="Do not download the Ollama Linux archive.")
    parser.add_argument("--strict", action="store_true", help="Exit on the first failed download step.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned downloads without writing large files.")
    args = parser.parse_args()

    config = load_config(args.config)
    profile = (config.get("model_profiles") or {}).get(args.profile)
    if not profile:
        raise SystemExit(f"Unknown model profile: {args.profile}")

    components = selected_components(args.components)
    dest = resolve_path(args.dest, ROOT_DIR)
    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    print(f"[bundle] profile={args.profile} components={','.join(sorted(components))} dest={dest}")

    comfyui_repo = None
    if "comfyui" in components and not args.skip_repos:
        comfyui_repo = prepare_comfyui_repo(dest, dry_run=args.dry_run, strict=args.strict)

    if "ollama" in components and not args.skip_ollama_archive:
        prepare_ollama_archive(dest, args.ollama_arch, dry_run=args.dry_run, strict=args.strict)

    if not args.skip_models:
        if "ollama" in components:
            prepare_ollama_models(profile, dest, args.include_optional, dry_run=args.dry_run, strict=args.strict)
        if "comfyui" in components:
            prepare_comfyui_models(profile, dest, args.include_optional, dry_run=args.dry_run, strict=args.strict)

    if not args.skip_wheels:
        prepare_wheels(
            components,
            dest,
            args.python_version,
            args.torch_variant,
            comfyui_repo,
            dry_run=args.dry_run,
            strict=args.strict,
        )

    write_manifest(dest, dry_run=args.dry_run)
    print("Offline bundle preparation complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
