"""归档并显式发布新的P3开发热规则；原P2运行指针和准备版本不变。"""

import argparse
import json
import os
from pathlib import Path

from app.knowledge.graph_projection import GraphProjector
from app.knowledge.loader import load_release
from app.pipeline.p3_thermal_release import prepare_thermal_release, stage_thermal_sources
from app.pipeline.publish import ReleaseBundle, publish_release
from app.pipeline.snapshot_export import SnapshotExporter

ROOT = Path(__file__).resolve().parents[1]


def _write_immutable(path: Path, text: str) -> None:
    payload = text.encode("utf-8")
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("不可覆盖不同的热规则发布输入：" + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    output = ROOT / "data/preparations/p3-thermal-v1"
    original_pointer = ROOT / "data/releases/active_release.json"
    original_bytes = original_pointer.read_bytes()
    source, audit = prepare_thermal_release(ROOT)
    stage_thermal_sources(source, ROOT, output / "source_inputs")
    _write_immutable(output / "reviewed_release.json", source.model_dump_json(indent=2))
    _write_immutable(
        output / "source_audit.json", json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    print("热规则开发输入和全部原始证据已归档；工艺修改数为0", flush=True)
    if args.publish:
        from neo4j import GraphDatabase

        settings = dict(
            line.split("=", 1)
            for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )
        settings.update(os.environ)
        with GraphDatabase.driver(
            settings["NEO4J_URI"], auth=(settings["NEO4J_USERNAME"], settings["NEO4J_PASSWORD"])
        ) as driver:
            driver.verify_connectivity()
            projector = GraphProjector(driver)
            graph = projector.project(source)
            print("新的技术规则版本已投影至本地Neo4j", flush=True)
            build = SnapshotExporter(projector).export_snapshot(source)
        ref = publish_release(
            ReleaseBundle(build=build, source_root=output / "source_inputs"), output / "releases"
        )
        loaded = load_release(output / "releases", ref)
        if loaded.snapshot.knowledge != source or original_pointer.read_bytes() != original_bytes:
            raise ValueError("发布重载内容或原运行指针核验失败")
        report = {
            "kind": "P3_THERMAL_REAL_GRAPH_RELEASE",
            "release": ref.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
            "snapshot_validation": build.validation.model_dump(mode="json"),
            "offline_reload_valid": True,
            "p2_active_release_unchanged": True,
            "formal_human_review_complete": False,
            "process_changes": 0,
            "source_mode": "HASH_VERIFIED_OLD_RELEASE_ARCHIVE_PLUS_NEW_AMENDMENT",
        }
        _write_immutable(
            output / "publication_report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        print("本地Neo4j导出与完整文件发布重载通过；正式审核仍待办", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
