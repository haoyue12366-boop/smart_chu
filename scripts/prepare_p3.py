"""生成独立P3准备包；--publish显式执行本地Neo4j投影及准备目录发布。"""

import argparse
import json
import os
from pathlib import Path

from app.knowledge.graph_projection import GraphProjector
from app.knowledge.loader import load_release
from app.pipeline.p3_preflight import build_preflight
from app.pipeline.p3_preparation import prepare_p3_release
from app.pipeline.publish import ReleaseBundle, publish_release
from app.pipeline.snapshot_export import SnapshotExporter

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="使用本地Neo4j发布到独立准备目录")
    parser.add_argument("--output", type=Path, default=ROOT / "data/preparations/p3-v1")
    args = parser.parse_args()
    active_path = ROOT / "data/releases/active_release.json"
    active_before = active_path.read_bytes()
    output = args.output.resolve()
    if (
        output == (ROOT / "data/releases").resolve()
        or (ROOT / "data/releases").resolve() in output.parents
    ):
        raise ValueError("准备包不能覆盖当前运行发布目录")
    source, audit = prepare_p3_release(ROOT)
    preflight = build_preflight(source)
    output.mkdir(parents=True, exist_ok=True)
    (output / "reviewed_release.json").write_text(
        source.model_dump_json(indent=2), encoding="utf-8"
    )
    (output / "source_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("P3准备输入和共享实施蓝图已生成；未切换P2运行版本", flush=True)
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
            projector = GraphProjector(driver)
            graph = projector.project(source)
            print("本地Neo4j准备版本投影完成", flush=True)
            build = SnapshotExporter(projector).export_snapshot(source)
        ref = publish_release(ReleaseBundle(build=build, source_root=ROOT), output / "releases")
        # 关闭驱动后通过纯文件加载完整准备包，核对版本与来源。
        restored = load_release(output / "releases", ref)
        if restored.snapshot.knowledge != source:
            raise ValueError("发布后准备包内容不一致")
        if active_path.read_bytes() != active_before:
            raise ValueError("准备期间当前P2发布指针意外变化")
        report = {
            "kind": "P3_PREPARATION_REAL_GRAPH_RELEASE",
            "release": ref.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
            "snapshot_validation": build.validation.model_dump(mode="json"),
            "offline_reload_valid": True,
            "shared_planning_enabled": False,
            "p2_active_release_unchanged": True,
        }
        (output / "publication_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("P3准备包已发布并经离线文件重载；共享排程开关仍关闭", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
