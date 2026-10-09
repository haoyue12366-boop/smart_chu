"""实际故障矩阵、断图状态与真实 TCP；预热和故障耗时分别保存。"""

from tests.p6_evidence_support import current_sources, relative_evidence_path, report_for, sha256


def test_complete_current_fault_matrix_has_no_skips_and_restores_original_graph_state():
    _, report = report_for("faults")
    assert report["status"] == "PASSED"
    assert report["tests"] >= 53 and report["failures"] == report["skips"] == 0
    assert report["test_sources_unchanged"] is True
    current_sources(report, "benchmarks/fault_injection.py")
    for name, digest in report["test_hashes"].items():
        assert sha256(relative_evidence_path(name)) == digest
    graph = report["neo4j"]
    assert not graph["during"]["Running"] and graph["bolt_port_closed"]
    assert graph["after"]["Running"] == graph["before"]["Running"]
    measured = report["measured_faults"]
    stream = measured["p6_fault:sse_disconnect_restart"]
    assert stream["transport"] == "REAL_TCP" and stream["execution_facts_unchanged"]
    assert measured["p6_fault:invalid_llm_stale_event"]["live_llm_calls"] == 0
    for value in measured.values():
        assert value["fault_request_ms"] >= 0 and value["recovery_ms"] >= 0
