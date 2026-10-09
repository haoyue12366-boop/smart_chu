"""新进程零网络加载；真实服务停止的检查由显式维护步骤启用。"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.ids import RecipeId
from app.knowledge.loader import LoadedRelease, load_release, read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from app.knowledge.snapshot import SchedulingKnowledgeSnapshot
from app.pipeline.publish import publish_release
from tests.integration.test_release_activation import bundle

ROOT = Path(__file__).resolve().parents[2]


def test_loaded_envelope_keeps_the_fully_validated_snapshot_without_copying(tmp_path, monkeypatch):
    releases = tmp_path / "releases"
    ref = publish_release(bundle(tmp_path / "input"), releases)
    decoded = []
    original = SchedulingKnowledgeSnapshot.model_validate_json

    def observed(cls, body, **kwargs):
        result = original(body, **kwargs)
        decoded.append(result)
        return result

    monkeypatch.setattr(SchedulingKnowledgeSnapshot, "model_validate_json", classmethod(observed))
    loaded = load_release(releases, ref)
    assert loaded.snapshot is decoded[-1]
    assert LoadedRelease.model_validate_json(loaded.model_dump_json()) == loaded


def test_new_process_reads_with_database_imports_and_network_blocked(tmp_path):
    releases = tmp_path / "releases"
    ref = publish_release(bundle(tmp_path / "input"), releases)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.check_snapshot",
            "--root",
            str(releases),
            "--release-id",
            ref.release_id,
            "--deny-network",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    report = json.loads(result.stdout)
    assert report["recipe_count"] == 1
    assert report["network_calls"] == 0
    assert report["neo4j_imported"] is False


def test_pinned_old_snapshot_survives_switch_and_cannot_be_evicted(tmp_path):
    releases = tmp_path / "releases"
    first = publish_release(bundle(tmp_path / "input", "old"), releases)
    second = publish_release(bundle(tmp_path / "input-2", "new"), releases)
    repository = SnapshotKnowledgeRepository(releases)
    repository.load(first)
    with repository.acquire(first) as session:
        repository.load(second)
        assert session.select((RecipeId("synthetic"),)).release == first
        assert repository.select((RecipeId("synthetic"),)).release == second
        with pytest.raises(ValueError, match="引用"):
            repository.unload(first)
    repository.unload(first)
    with pytest.raises(ValueError):
        repository.load(first.model_copy(update={"release_id": "missing"}))
    assert repository.select((RecipeId("synthetic"),)).release == second
    with pytest.raises(ValueError, match="菜谱"):
        repository.select((RecipeId("missing"),))


@pytest.mark.offline_neo4j
def test_real_release_in_new_process_with_neo4j_stopped():
    if os.environ.get("SMART_COOKING_OFFLINE_TEST") != "1":
        pytest.skip("需显式停止本地 Neo4j 后设置 SMART_COOKING_OFFLINE_TEST=1")
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", 7687)) != 0, "必须真正停止 Neo4j"
    releases = ROOT / "data/releases"
    ref = read_release_ref(releases, "development-v3-rebased-v2-all")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.check_snapshot",
            "--root",
            str(releases),
            "--release-id",
            ref.release_id,
            "--deny-network",
            "--output",
            str(ROOT / "benchmarks/reports/verification/P1-08-offline-snapshot.json"),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    report = json.loads(result.stdout)
    assert report["recipe_count"] == 100
    assert report["network_calls"] == 0
    assert report["neo4j_imported"] is False
