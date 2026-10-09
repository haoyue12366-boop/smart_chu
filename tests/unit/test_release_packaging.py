"""Windows 部署包文件边界与不可变指纹。"""

import json

import pytest

from scripts.package_release import collect_application_files, verify_delivery


def test_application_allowlist_excludes_secrets_sessions_and_original_zip(tmp_path):
    fixtures = [
        "app/main.py",
        "app/storage/migrations/script.py.mako",
        "app/__pycache__/secret.pyc",
        "web/dist/index.html",
        "web/dist/assets/app.js",
        ".env",
        "data/runtime/user.sqlite3",
        "data/runtime/raw-intent.json",
        "原始项目.zip",
        "web/node_modules/index.js",
    ]
    for name in fixtures:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture", encoding="utf-8")
    result = {p.relative_to(tmp_path).as_posix() for p in collect_application_files(tmp_path)}
    assert result == {
        "app/main.py",
        "app/storage/migrations/script.py.mako",
        "web/dist/index.html",
        "web/dist/assets/app.js",
    }


@pytest.mark.parametrize("path", ["../outside.txt", "C:/outside.txt", "app/../../outside.txt"])
def test_manifest_cannot_escape_install_directory(tmp_path, path):
    (tmp_path / "release_manifest.json").write_text(json.dumps({"files": {path: "0" * 64}}))
    with pytest.raises(ValueError):
        verify_delivery(tmp_path)


def test_changed_delivery_file_is_rejected(tmp_path):
    import hashlib

    file = tmp_path / "app.py"
    file.write_bytes(b"approved")
    (tmp_path / "release_manifest.json").write_text(
        json.dumps({"files": {"app.py": hashlib.sha256(b"approved").hexdigest()}})
    )
    assert verify_delivery(tmp_path)["file_count"] == 1
    file.write_bytes(b"changed")
    with pytest.raises(ValueError):
        verify_delivery(tmp_path)


def test_missing_delivery_file_is_rejected(tmp_path):
    (tmp_path / "release_manifest.json").write_text(json.dumps({"files": {"missing.py": "0" * 64}}))
    with pytest.raises(ValueError):
        verify_delivery(tmp_path)
