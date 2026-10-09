"""最终外部确认必须有实际来源；项目设计授权不替代赛方确认。"""

import json

from app.api.competition_profiles import CompetitionProfile
from tests.p6_evidence_support import inputs, relative_evidence_path, sha256


def test_official_dynamic_confirmation_has_hash_bound_source_and_current_profile():
    conditions = inputs()["external_conditions"]
    assert conditions["official_dynamic_confirmed"] is True, "P6-08 缺少官方动态联调/确认材料"
    reference = conditions.get("official_confirmation_artifact")
    assert reference, "官方确认必须绑定原始材料路径与哈希"
    path = relative_evidence_path(reference["path"])
    assert sha256(path) == reference["sha256"]
    confirmation = json.loads(path.read_bytes())
    assert confirmation["source"] == "OFFICIAL"
    assert confirmation["adopted_project_profile"] == CompetitionProfile().model_dump(mode="json")
    assert confirmation["actual_joint_test_result"] == "PASSED"
    assert confirmation["source_artifacts"]
    for artifact in confirmation["source_artifacts"]:
        assert sha256(relative_evidence_path(artifact["path"])) == artifact["sha256"]
