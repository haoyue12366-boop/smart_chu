"""当前 Windows 包与真实离线安装、业务持久化和重启证据一致。"""

import hashlib
import json
import zipfile

from benchmarks.dataset import ROOT
from scripts.package_release import collect_application_files, verify_delivery
from tests.p6_evidence_support import current_sources, inputs, relative_evidence_path, sha256


def test_current_windows_delivery_matches_real_offline_install_and_restart():
    required = inputs()["windows_delivery"]
    report = json.loads((ROOT / required["report"]).read_bytes())
    assert report["status"] == "PASSED" and report["formal_acceptance"] is False
    assert report["tests"] == 1 and report["failures"] == report["skips"] == 0
    assert report["live_llm_calls"] == 0 and report["verification_unchanged"]
    assert not report["neo4j"]["during"]["Running"] and report["neo4j"]["bolt_port_closed"]
    assert report["neo4j"]["after"]["Running"] == report["neo4j"]["before"]["Running"]
    current_sources(report)
    for name, digest in report["verification_hashes"].items():
        assert sha256(ROOT / name) == digest
    assert sha256(ROOT / required["archive"]) == report["delivery"]["archive_sha256"]
    directory = ROOT / required["directory"]
    identity = verify_delivery(directory)
    assert identity["manifest_sha256"] == report["delivery"]["manifest_sha256"]
    manifest = json.loads((directory / "release_manifest.json").read_bytes())
    assert manifest["platform"] == "Windows AMD64"
    assert manifest["frontend"]["node_runtime_required"] is False
    assert manifest["network_required_for_install"] is False
    assert manifest["release"]["release_id"] == inputs()["release_id"]
    for source in collect_application_files(ROOT):
        relative = source.relative_to(ROOT).as_posix()
        assert manifest["files"][relative] == sha256(source)
    with zipfile.ZipFile(ROOT / required["archive"]) as bundle:
        assert set(bundle.namelist()) == {*manifest["files"], "release_manifest.json"}
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == digest
            assert not name.endswith((".sqlite", ".sqlite3", ".db", ".log"))
            assert name.rsplit("/", 1)[-1] != ".env"
    xml = ROOT / required["report"]
    assert sha256(xml.parent / "tests.xml") == report["xml_hash"]
    assert len(report["installation_evidence"]) == 1
    frozen = report["installation_evidence"][0]
    path = relative_evidence_path(frozen["path"])
    assert sha256(path) == frozen["sha256"]
    actual = json.loads(path.read_bytes())
    assert actual["identity"] == identity
    commands = actual["commands"]
    assert all(command["exit_code"] == 0 for command in commands)
    assert [command["script"] for command in commands] == [
        "Install.ps1",
        "Start.ps1",
        "Verify.ps1",
        "Stop.ps1",
        "Start.ps1",
        "Stop.ps1",
    ]
