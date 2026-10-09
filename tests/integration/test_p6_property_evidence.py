"""要求真实计数、实际图补跑与当前来源；保留原跳过和旧失败历史。"""

from tests.p6_evidence_support import relative_evidence_path, report_for, sha256


def test_current_system_properties_cover_all_required_profiles_and_actual_sequences():
    _, report = report_for("properties")
    assert report["status"] == "PASSED"
    counts = report["counts"]
    properties = {k: v for k, v in counts.items() if k.startswith("p6_property:")}
    assert len(properties) >= 10 and all(value >= 200 for value in properties.values())
    assert counts["p6_full_session:sequences"] >= 100
    assert report["sequence_max_steps"] == 50
    assert not report["unresolved_skips"] and not any(t["failed"] for t in report["tests"])
    for name, digest in report["source_hashes"].items():
        if name.startswith("app/"):
            assert sha256(relative_evidence_path(name)) == digest
    for name, digest in report["evidence_sha256"].items():
        assert sha256(relative_evidence_path(name)) == digest
    for name, digest in report["test_hashes"].items():
        assert sha256(relative_evidence_path(name)) == digest
