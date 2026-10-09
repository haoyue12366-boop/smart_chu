"""合成 XML 反例：补跑必须同一用例真实通过，失败和不足样本不可被覆盖。"""

from xml.etree import ElementTree as ET

from benchmarks.property_profiles import collect


def xml(path, *, outcome=None, identity="graph", sufficient=False):
    suite = ET.Element("testsuite")
    if sufficient:
        properties = ET.SubElement(suite, "properties")
        for index in range(10):
            ET.SubElement(properties, "property", name=f"p6_property:{index}", value="200")
        ET.SubElement(properties, "property", name="p6_full_session:sequences", value="106")
    case = ET.SubElement(suite, "testcase", classname="synthetic.audit", name=identity)
    if outcome:
        ET.SubElement(case, outcome)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def test_missing_explicit_graph_run_remains_failed(tmp_path):
    original = xml(tmp_path / "offline.xml", outcome="skipped", sufficient=True)
    report = collect([original])
    assert report["status"] == "FAILED" and len(report["unresolved_skips"]) == 1


def test_successful_same_test_supplement_preserves_original_skip_evidence(tmp_path):
    original = xml(tmp_path / "offline.xml", outcome="skipped", sufficient=True)
    actual = xml(tmp_path / "online.xml")
    report = collect([original, actual])
    assert report["status"] == "PASSED"
    assert report["observed_skipped_count"] == 1 and not report["unresolved_skips"]
    assert len(report["resolved_by_actual_same_test_rerun"]) == 1
    assert len(report["tests"]) == len(report["evidence_sha256"]) == 2


def test_different_test_cannot_cover_skipped_requirement(tmp_path):
    original = xml(tmp_path / "offline.xml", outcome="skipped", sufficient=True)
    other = xml(tmp_path / "other.xml", identity="another-graph")
    assert collect([original, other])["status"] == "FAILED"


def test_later_pass_does_not_erase_observed_failure(tmp_path):
    original = xml(tmp_path / "failed.xml", outcome="failure", sufficient=True)
    actual = xml(tmp_path / "passed.xml")
    assert collect([original, actual])["status"] == "FAILED"
