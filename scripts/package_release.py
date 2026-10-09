"""Windows 离线部署包：显式文件清单、锁定 wheel 与文件身份校验。"""

import argparse
import hashlib
import json
import shutil
import subprocess
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collect_application_files(root):
    root = Path(root)
    files = [
        p
        for p in (root / "app").rglob("*")
        if p.is_file() and p.suffix in (".py", ".mako") and "__pycache__" not in p.parts
    ]
    files += [
        p
        for p in (root / "web/dist").rglob("*")
        if p.is_file()
        and p.suffix in (".html", ".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2")
    ]
    return sorted(files)


def verify_delivery(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "release_manifest.json").read_bytes())
    files = manifest["files"]
    if not files:
        raise ValueError("交付清单不得为空")
    for name, expected in files.items():
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or PureWindowsPath(name).is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or ":" in name
        ):
            raise ValueError("交付清单路径越出安装目录")
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file() or sha256(target) != expected:
            raise ValueError("交付文件缺失、越界或哈希改变：" + name)
    return {
        "valid": True,
        "file_count": len(files),
        "manifest_sha256": sha256(root / "release_manifest.json"),
    }


def package(root, output, runtime, wheelhouse, requirements, uv, *, policy_path=None):
    from app.config import AppSettings
    from app.domain.policy import SchedulingPolicy
    from app.knowledge.loader import load_release, read_release_ref

    root, output, runtime, wheelhouse = map(
        lambda p: Path(p).resolve(), (root, output, runtime, wheelhouse)
    )
    output.mkdir(parents=True, exist_ok=False)
    settings = (
        AppSettings(policy_path=Path(policy_path).resolve()) if policy_path else AppSettings()
    )
    reference = read_release_ref(settings.release_root, settings.release_id)
    loaded = load_release(settings.release_root, reference)

    def copy(source, relative):
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    app_files = collect_application_files(root)
    if not (root / "web/dist/index.html").is_file():
        raise ValueError("必须先实际构建前端")
    for source in app_files:
        copy(source, source.relative_to(root))
    for name in ("pyproject.toml", "uv.lock", "web/package.json", "web/package-lock.json"):
        copy(root / name, name)
    copy(root / "scripts/package_release.py", "scripts/package_release.py")
    for source in sorted((root / "deploy/windows").glob("*.ps1")):
        copy(source, Path("windows") / source.name)
    copy(root / "deploy/.env.example", ".env.example")
    copy(root / "docs/P6-Windows部署说明.md", "README.md")
    for source in sorted((root / "docs").glob("*.md")):
        copy(source, source.relative_to(root))
    copy(root / "AGENTS.md", "AGENTS.md")
    copy(requirements, "requirements.txt")
    copy(uv, "tools/uv.exe")
    python = runtime / "python.exe"
    version = subprocess.check_output([str(python), "--version"], text=True).strip()
    if not version.startswith("Python 3.12."):
        raise ValueError("交付运行时必须为 Python 3.12")
    shutil.copytree(
        runtime,
        output / "runtime/python",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log"),
    )
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    allowed = {
        Path(unquote(urlparse(w["url"]).path)).name: w["hash"].removeprefix("sha256:")
        for p in lock["package"]
        for w in p.get("wheels", [])
    }
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        raise ValueError("离线 wheel 集为空")
    for wheel in wheels:
        if allowed.get(wheel.name) != sha256(wheel):
            raise ValueError("wheel 不属于当前锁文件或哈希不一致：" + wheel.name)
        copy(wheel, Path("wheels") / wheel.name)
    directory = settings.release_root / reference.release_id
    for name in ("manifest.json", *(a.path for a in loaded.manifest.artifacts)):
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.suffix in (".sqlite", ".sqlite3", ".db", ".zip")
            or path.name == ".env"
        ):
            raise ValueError("知识发布包含不允许交付的文件：" + name)
        copy(directory / name, Path("knowledge") / reference.release_id / name)
    policy = json.loads(settings.policy_path.read_bytes())
    SchedulingPolicy.model_validate(policy)
    copy(settings.policy_path, "policy.json")
    manifest = {
        "schema_version": "1.0",
        "platform": "Windows AMD64",
        "kind": "P6_WINDOWS_DEVELOPMENT_CANDIDATE"
        if reference.release_kind == "development"
        else "P6_WINDOWS_COMPETITION_CANDIDATE",
        "acceptance": "NOT_FINAL",
        "created_at": datetime.now(UTC).isoformat(),
        "release": reference.model_dump(mode="json"),
        "policy": policy,
        "python_version": version,
        "uv_version": subprocess.check_output([str(uv), "--version"], text=True).strip(),
        "frontend": {
            "kind": "PREBUILT_STATIC_ASSETS",
            "node_runtime_required": False,
            "node_build_baseline": "25.8.1",
            "npm_build_baseline": "11.11.0",
        },
        "wheel_count": len(wheels),
        "runtime_source": "项目锁定的独立 CPython 分发；不是开发虚拟环境",
        "network_required_for_install": False,
        "language_enabled_by_default": False,
        "documentation": {
            "entry_point": "README.md",
            "directory": "docs",
            "historical_test_artifacts_included": False,
            "note": "安装验证与排程性能验收分别报告，当前状态见包内验收说明。",
        },
        "formal_open_items": [
            "P1 full 正式发布与阶段门槛",
            "P6-08 正式最终验收",
            "官方动态语义与公网目标",
        ],
        "files": {
            p.relative_to(output).as_posix(): sha256(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    }
    (output / "release_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    proof = verify_delivery(output)
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for source in sorted(output.rglob("*")):
            if source.is_file():
                info = zipfile.ZipInfo(
                    source.relative_to(output).as_posix(), date_time=(1980, 1, 1, 0, 0, 0)
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, source.read_bytes())
    return {
        **proof,
        "archive": archive.name,
        "archive_sha256": sha256(archive),
        "scope": manifest["kind"],
        "wheel_count": len(wheels),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--runtime", type=Path, default=ROOT / ".python/cpython-3.12-windows-x86_64-none"
    )
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument(
        "--policy", type=Path, help="显式选择交付策略；默认使用 AppSettings 当前默认策略"
    )
    parser.add_argument("--uv", type=Path, default=ROOT / ".tools/bin/uv.exe")
    args = parser.parse_args()
    if args.verify:
        proof = verify_delivery(args.verify)
    else:
        if not all((args.output, args.wheelhouse, args.requirements)):
            parser.error("打包必须指定 output、wheelhouse、requirements")
        proof = package(
            ROOT,
            args.output,
            args.runtime,
            args.wheelhouse,
            args.requirements,
            args.uv,
            policy_path=args.policy,
        )
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == "__main__":
    main()
