"""准备检查必须用真实文件仓储和编译/校验链；不连接Neo4j。"""

from scripts.check_p3_preparation import check_preparation
from tests.compiler_support import ROOT


def test_prepared_release_is_ready_for_shared_implementation(tmp_path):
    report = check_preparation(ROOT / "data/preparations/p3-v1", tmp_path)
    assert report["compiled_recipe_count"] == 100
    assert report["source_recipe_count"] == 100
    assert report["compatible_development_groups"] == 2
    assert report["baseline_plan_validated"] is True
    assert report["shared_planning_enabled"] is False
    assert report["formal_release_eligible"] is False
