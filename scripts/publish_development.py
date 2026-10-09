"""从真实 V3 开发版本完成图谱、快照、原子发布；不授予正式审核资格。"""

import json
import os
import time
from pathlib import Path

from neo4j import GraphDatabase

from app.knowledge.graph_projection import GraphProjector
from app.pipeline.development import load_development_knowledge
from app.pipeline.publish import ReleaseBundle, publish_release
from app.pipeline.release_input import prepare_release_input
from app.pipeline.snapshot_export import SnapshotExporter

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    started = time.perf_counter()
    knowledge = load_development_knowledge(ROOT)
    source = prepare_release_input(
        knowledge,
        release_id=knowledge.knowledge_version + "-all",
        recipe_ids=tuple(r.recipe_id.root for r in knowledge.recipes),
    )
    settings = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text("utf-8").splitlines()
        if line and not line.startswith("#")
    )
    settings.update(os.environ)
    with GraphDatabase.driver(
        settings["NEO4J_URI"], auth=(settings["NEO4J_USERNAME"], settings["NEO4J_PASSWORD"])
    ) as driver:
        projector = GraphProjector(driver)
        graph = projector.project(source)
        build = SnapshotExporter(projector).export_snapshot(source)
    ref = publish_release(ReleaseBundle(build=build, source_root=ROOT), ROOT / "data/releases")
    report = {
        "kind": "REAL_DEVELOPMENT_RELEASE",
        "release": ref.model_dump(mode="json"),
        "graph": graph.model_dump(mode="json"),
        "snapshot_validation": build.validation.model_dump(mode="json"),
        "recipe_count": len(source.recipes),
        "operation_count": sum(len(r.operations) for r in source.recipes),
        "approved_count": 0,
        "formal_release_eligible": False,
        "single_recipe_solve_status": "NOT_RUN",
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    output = ROOT / "benchmarks/reports/verification/P1-development-release.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("kind", "release", "recipe_count", "operation_count", "elapsed_sec")
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
