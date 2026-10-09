"""生成可复现的只读来源副本和清单，不生成审核记录或知识发布。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.pipeline.device_import import import_devices
from app.pipeline.import_raw import import_recipes

ROOT = Path(__file__).resolve().parents[1]


def build_source_manifest(root: Path, output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    imports: list[dict[str, object]] = []
    for name, label, target in (
        ("docs/recipes_100.csv", "ORIGINAL_NUMBERED_STEPS", "recipes_original.json"),
        (
            "data/revisions/recipes_v3/recipes_100_调度版.csv",
            "DEVELOPMENT_V3_EXPORTED_OPERATIONS",
            "recipes_development_v3.json",
        ),
    ):
        recipes = import_recipes(root / name)
        payload = [recipe.model_dump(mode="json") for recipe in recipes]
        (output / target).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        imports.append(
            {
                "source_path": name,
                "source_sha256": recipes[0].source_sha256,
                "artifact_path": target,
                "artifact_sha256": hashlib.sha256((output / target).read_bytes()).hexdigest(),
                "recipe_count": len(recipes),
                "unique_name_count": len({r.name for r in recipes}),
                "numbered_step_count": sum(len(r.steps) for r in recipes),
                "step_count_basis": label,
            }
        )
    device_path = "docs/设备参数清单参考.json"
    devices = import_devices(root / device_path)
    (output / "devices.json").write_text(devices.model_dump_json(indent=2) + "\n", encoding="utf-8")
    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "status": "RAW_IMPORTED_NOT_REVIEWED",
        "recipe_sources": imports,
        "device_source": {
            "source_path": device_path,
            "source_sha256": devices.source_sha256,
            "artifact_path": "devices.json",
            "artifact_sha256": hashlib.sha256((output / "devices.json").read_bytes()).hexdigest(),
            "device_count": len(devices.devices),
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    print(json.dumps(build_source_manifest(ROOT, ROOT / "data/raw"), ensure_ascii=False, indent=2))
