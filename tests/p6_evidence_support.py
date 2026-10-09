"""读取真实 P6 产物；缺失、旧源码、越界路径及损坏归档均失败。"""

import gzip
import hashlib
import json
from pathlib import Path

from benchmarks.dataset import ROOT


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs():
    return json.loads((ROOT / "data/preparations/p6-v1/acceptance_inputs.json").read_bytes())


def report_for(name):
    path = ROOT / inputs()["reports"][name]
    assert path.is_file(), "缺少已实际执行的 P6 报告：" + str(path)
    return path, json.loads(path.read_bytes())


def current_sources(report, *producers, not_executed=()):
    assert report.get("source_unchanged") is True, "运行期间源码变化或运行未完成"
    recorded = report["source_hashes"]
    selected = {
        name
        for name in recorded
        if name.startswith("app/") or name in {"pyproject.toml", "uv.lock"}
    }
    selected.update(producers)
    selected.difference_update(not_executed)
    assert selected and any(name.startswith("app/") for name in selected)
    for name in selected:
        assert name in recorded, "报告没有绑定生产者：" + name
        assert sha256(ROOT / name) == recorded[name], "证据不覆盖当前源码：" + name


def read_artifact(directory, relative, digest=None):
    path = (directory / relative).resolve()
    assert path.is_relative_to(directory.resolve()) and path.is_file(), "缺少或越界产物"
    if digest is not None:
        assert sha256(path) == digest, "归档 SHA-256 不一致"
    return json.loads(gzip.decompress(path.read_bytes()))


def same_suite(report):
    expected = inputs()
    assert report["suite_hash"] == expected["suite_hash"]
    if "release" in report:
        for key in ("release_id", "snapshot_id", "manifest_hash"):
            assert report["release"][key] == expected[key]
    if "policy_hash" in report:
        assert report["policy_hash"] == expected["policy_hash"]


def relative_evidence_path(value):
    path = Path(value)
    path = path if path.is_absolute() else ROOT / path
    assert path.resolve().is_relative_to(ROOT.resolve()) and path.is_file()
    return path
