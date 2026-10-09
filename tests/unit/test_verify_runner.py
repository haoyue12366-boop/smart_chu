import json
import sys

from scripts.verify import run_command, verify


def test_command_failure_and_unicode_path(tmp_path):
    root = tmp_path / "中文目录 with spaces"
    root.mkdir()
    result = run_command([sys.executable, "-c", "raise SystemExit(7)"], root)
    assert result["exit_code"] == 7
    assert not result["passed"]
    assert result["elapsed_ms"] >= 0


def test_empty_and_skipped_tests_fail(tmp_path):
    for body in ("", "import pytest\n@pytest.mark.skip(reason='缺服务')\ndef test_x(): pass\n"):
        (tmp_path / "test_empty.py").write_text(body, encoding="utf-8")
        result = run_command([sys.executable, "-m", "pytest", "test_empty.py", "-q"], tmp_path)
        assert not result["passed"]


def test_phase_gate_cannot_silently_deselect_required_review_test(tmp_path):
    (tmp_path / "pytest.ini").write_text(
        '[pytest]\naddopts = -m "not review_gate"\nmarkers = review_gate: required\n',
        encoding="utf-8",
    )
    (tmp_path / "test_required.py").write_text(
        "import pytest\ndef test_code(): pass\n"
        "@pytest.mark.review_gate\ndef test_review(): assert False\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "phases": {"P0": {"tasks": ["P0-01"]}},
                "tasks": {"P0-01": {"tests": ["test_required.py"], "artifacts": []}},
            }
        ),
        encoding="utf-8",
    )
    assert verify("P0", None, manifest, tmp_path)["status"] == "FAILED"


def test_unknown_and_missing_phase_fail(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"phases": {}}), encoding="utf-8")
    assert verify("P9", None, manifest, tmp_path)["status"] == "FAILED"
    manifest.write_text(json.dumps({"phases": {"P0": {"tasks": []}}}), encoding="utf-8")
    assert verify("P0", None, manifest, tmp_path)["status"] == "FAILED"


def test_missing_artifact_fails(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "phases": {"P0": {"tasks": ["P0-01"]}},
                "tasks": {"P0-01": {"artifacts": ["missing.json"], "tests": ["test_missing.py"]}},
            }
        ),
        encoding="utf-8",
    )
    assert verify("P0", None, manifest, tmp_path)["status"] == "FAILED"


def test_quality_failure_prevents_task_verification(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "phases": {"P0": {"tasks": ["P0-01"]}},
                "quality_commands": [["{python}", "-c", "raise SystemExit(1)"]],
                "tasks": {"P0-01": {"tests": ["test_ok.py"], "artifacts": []}},
            }
        ),
        encoding="utf-8",
    )
    report = verify("P0", None, manifest, tmp_path)
    assert report["status"] == "FAILED"
    assert report["tasks"]["P0-01"]["status"] == "FAILED"


def test_phase_core_dependencies_do_not_pull_in_full_gate(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    phases = {
        "P0": {"tasks": ["P0-01"]},
        "P1:core": {"tasks": ["P1-01"], "required_phase_gates": ["P0"]},
        "P1:full": {"tasks": ["unimplemented-full"]},
        "P2": {"tasks": ["P2-01"], "required_phase_gates": ["P1:core"]},
    }
    tasks = {
        name: {"artifacts": [], "tests": ["test_ok.py"]} for name in ["P0-01", "P1-01", "P2-01"]
    }
    manifest.write_text(json.dumps({"phases": phases, "tasks": tasks}), encoding="utf-8")
    report = verify("P2", None, manifest, tmp_path)
    assert report["status"] == "VERIFIED"
    assert set(report["tasks"]) == {"P0-01", "P1-01", "P2-01"}
    from app.domain.reports import VerificationReport

    assert VerificationReport.model_validate(report).status == "VERIFIED"


def test_explicit_development_gate_never_reports_formal_verification(tmp_path):
    import hashlib

    (tmp_path / "test_code.py").write_text("def test_code(): pass\n", encoding="utf-8")
    (tmp_path / "test_formal.py").write_text("def test_review(): assert False\n", encoding="utf-8")
    auth = tmp_path / "authorization.json"
    auth.write_text(
        json.dumps({"source": "USER_MESSAGE", "authorization_id": "synthetic-test-authorization"}),
        encoding="utf-8",
    )
    artifact = {
        "path": auth.name,
        "sha256": hashlib.sha256(auth.read_bytes()).hexdigest(),
        "media_type": "application/json",
    }
    document = {
        "phases": {
            "P3": {"tasks": ["P3-01"]},
            "P3:development": {
                "tasks": ["P3-01"],
                "development_authorization_artifact": artifact,
                "deferred_formal_requirements": ["Synthetic formal review remains pending"],
                "development_dependency_overrides": {"P3-01": ["DEV-INPUTS"]},
            },
        },
        "tasks": {
            "FORMAL": {"tests": ["test_formal.py"]},
            "DEV-INPUTS": {"tests": ["test_code.py"]},
            "P3-01": {"tests": ["test_code.py"], "dependencies": ["FORMAL"]},
        },
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    development = verify("P3", "development", manifest, tmp_path)
    assert development["status"] == "DEVELOPMENT_VERIFIED"
    assert development["deferred_formal_requirements"]
    assert "FORMAL" not in development["tasks"]
    assert verify("P3", None, manifest, tmp_path)["status"] == "FAILED"
    from app.domain.reports import VerificationReport

    assert VerificationReport.model_validate(development).status == "DEVELOPMENT_VERIFIED"
    auth.write_text("{}", encoding="utf-8")
    assert verify("P3", "development", manifest, tmp_path)["status"] == "FAILED"


def test_fingerprint_binds_benchmark_code_and_cases_without_report_recursion(tmp_path):
    from scripts.verify import fingerprint

    benchmark = tmp_path / "benchmarks"
    (benchmark / "scenarios").mkdir(parents=True)
    (benchmark / "reports").mkdir()
    (benchmark / "shared_ablation.py").write_text("# synthetic test code\n", encoding="utf-8")
    (benchmark / "scenarios/cases.json").write_text("{}", encoding="utf-8")
    report = benchmark / "reports/report.json"
    report.write_text("{}", encoding="utf-8")
    first, hashes = fingerprint(tmp_path)
    assert "benchmarks/shared_ablation.py" in hashes
    assert "benchmarks/scenarios/cases.json" in hashes
    assert "benchmarks/reports/report.json" not in hashes
    report.write_text('{"result":"changed"}', encoding="utf-8")
    assert fingerprint(tmp_path)[0] == first


def test_fingerprint_binds_selected_release_without_recursing_installed_or_old_copies(tmp_path):
    from scripts.verify import fingerprint

    source = tmp_path / "data/raw/source.json"
    release = tmp_path / "data/preparations/p6/release/snapshot.json"
    installed = tmp_path / "data/delivery/install/python/Lib/copied.py"
    report = tmp_path / "data/verification/report.json"
    nested_code = tmp_path / "benchmarks/robustness/simulator.py"
    for path in (source, release, installed, report, nested_code):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
    bound = (release.relative_to(tmp_path).as_posix(),)
    first, hashes = fingerprint(tmp_path, bound)
    assert bound[0] in hashes and "data/raw/source.json" in hashes
    assert "benchmarks/robustness/simulator.py" in hashes
    assert "data/delivery/install/python/Lib/copied.py" not in hashes
    assert "data/verification/report.json" not in hashes
    installed.write_text("new installed copy", encoding="utf-8")
    report.write_text("new result", encoding="utf-8")
    assert fingerprint(tmp_path, bound)[0] == first
    release.write_text("changed pinned snapshot", encoding="utf-8")
    assert fingerprint(tmp_path, bound)[0] != first
