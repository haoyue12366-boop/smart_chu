"""P4 合成工艺与真实发布读取明确区分。"""

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from app.domain.canonical_recipe import CanonicalRecipeModel
from app.domain.events import RuntimeEvent
from app.domain.policy import SchedulingPolicy
from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def p4_knowledge():
    root = ROOT / "data/preparations/p4-v1/releases"
    ref = read_release_ref(root, "delegated-v3-p4-preparation-v1-all")
    repository = SnapshotKnowledgeRepository(root)
    repository.load(ref)
    with repository.acquire(ref) as lease:
        return lease.select(tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes))


def policy():
    value = json.loads((ROOT / "data/policies/p4-runtime-v1.json").read_text(encoding="utf-8"))
    return SchedulingPolicy.model_validate(value)


ORIGIN = datetime.fromisoformat("2026-09-29T10:00:00+08:00")


def synthetic_knowledge():
    recipes = []
    for index in range(2):
        operations = []
        for ident, duration, human in (
            ("mix", 180, True),
            ("wait", 1200, False),
            ("finish", 60, True),
        ):
            operations.append(
                {
                    "operation_id": ident,
                    "action": "MARINATE" if not human else "MIX",
                    "duration": {"execution_sec": duration},
                    "resource_requirements": [
                        {
                            "resource_type": "HUMAN",
                            "resource_id": "human_1",
                            "conflict_policy": "UNARY",
                        }
                    ]
                    if human
                    else [],
                }
            )
        recipes.append(
            CanonicalRecipeModel(
                schema_version="1.0",
                recipe_id=f"synthetic-{index}",
                name=f"合成腌制{index}",
                recipe_version="1",
                operations=operations,
                ingredient_requirements=(),
                material_specs=(),
                dependencies=[
                    {
                        "predecessor_id": a,
                        "successor_id": b,
                        "reason": "合成依赖",
                        "evidence_refs": ["synthetic"],
                    }
                    for a, b in (("mix", "wait"), ("wait", "finish"))
                ],
                provenance_refs=("synthetic",),
            )
        )
    return p4_knowledge().model_copy(
        update={"recipes": tuple(recipes), "recipe_contexts": (), "rules": ()}
    )


def event(session, identity, kind, payload, at=0):
    state = session.runtime
    return RuntimeEvent(
        event_id=identity,
        session_id=state.session_id,
        event_type=kind,
        occurred_at=state.time_origin.at(at),
        received_at=state.time_origin.at(at),
        source=state.execution_mode,
        expected_state_revision=state.state_revision,
        base_plan_version=state.current_plan_version,
        payload=payload,
    )
