"""P3真实准备版本测试入口；不替换P2夹具或伪造人工批准。"""

from functools import lru_cache

from app.knowledge.loader import read_release_ref
from app.knowledge.repository import SnapshotKnowledgeRepository
from tests.compiler_support import ROOT, menu_for, runtime


@lru_cache(maxsize=1)
def prepared_knowledge():
    root = ROOT / "data/preparations/p3-v1/releases"
    ref = read_release_ref(root, "development-v3-p3-preparation-v1-all")
    repository = SnapshotKnowledgeRepository(root)
    repository.load(ref)
    with repository.acquire(ref) as lease:
        return lease.select(tuple(r.recipe_id for r in lease.loaded.snapshot.knowledge.recipes))


def shared_menu():
    knowledge = prepared_knowledge()
    ids = {"5c8200d96dc6e123a037a202", "5fe197175f8f38795ea6fe77"}
    menu = menu_for(*(r for r in knowledge.recipes if r.recipe_id.root in ids))
    return knowledge, menu, runtime(knowledge)
