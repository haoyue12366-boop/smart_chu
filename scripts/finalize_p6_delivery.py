"""从真实通过的阶段报告生成技术交付清单；仅允许验收后的文档登记变化。"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.dataset import ROOT
from benchmarks.runner import write_json
from scripts.package_release import sha256, verify_delivery
from scripts.verify import fingerprint

DOCUMENTATION = {"docs/task.md", "docs/P6实施与验收记录.md"}


def artifact(path):
    path = path.resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError("交付证据缺失或越出项目目录")
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)}


def finalize(gate, output):
    supervisor_path, phase_path = gate / "report.json", gate / "phase-report.json"
    supervisor = json.loads(supervisor_path.read_bytes())
    phase = json.loads(phase_path.read_bytes())
    if (
        supervisor["status"] != "PASSED"
        or supervisor["exit_code"] != 0
        or sha256(phase_path) != supervisor["phase_report"]["sha256"]
        or phase["status"] != "DEVELOPMENT_VERIFIED"
        or not supervisor["source_unchanged"]
        or not supervisor["verification_unchanged"]
    ):
        raise ValueError("完整开发阶段未真实通过或报告身份改变")
    for task in (f"P6-0{i}" for i in range(1, 8)):
        if phase["tasks"][task]["status"] != "DEVELOPMENT_VERIFIED":
            raise ValueError("技术任务未通过：" + task)
    if any(not command["passed"] for command in phase["commands"]):
        raise ValueError("存在失败、空集或跳过的必需检查")
    prior = phase["artifact_hashes"]
    current_fingerprint, current = fingerprint(ROOT, tuple(prior))
    if set(current) != set(prior):
        raise ValueError("阶段后文件集合发生变化，必须重新验收")
    changed = {name for name in prior if prior[name] != current[name]}
    if changed - DOCUMENTATION:
        raise ValueError("阶段后存在非登记文档变化：" + ", ".join(sorted(changed)))
    task_document = (ROOT / "docs/task.md").read_text(encoding="utf-8")
    for index in range(1, 8):
        section = task_document.split(f'<a id="p6-0{index}"></a>', 1)[1].split('<a id="', 1)[0]
        if "**状态：DEVELOPMENT_VERIFIED" not in section:
            raise ValueError("task.md 尚未如实登记技术验收结果")
    final_section = task_document.split('<a id="p6-08"></a>', 1)[1]
    if "**状态：BLOCKED" not in final_section:
        raise ValueError("正式外部验收未通过时不得改为完成")
    inputs = json.loads((ROOT / "data/preparations/p6-v1/acceptance_inputs.json").read_bytes())
    required = inputs["windows_delivery"]
    directory, archive = ROOT / required["directory"], ROOT / required["archive"]
    identity = verify_delivery(directory)
    package = json.loads((directory / "release_manifest.json").read_bytes())
    smoke_path = ROOT / required["report"]
    smoke = json.loads(smoke_path.read_bytes())
    if smoke["status"] != "PASSED" or sha256(archive) != smoke["delivery"]["archive_sha256"]:
        raise ValueError("当前 Windows 包不对应真实通过的独立安装报告")
    if identity["manifest_sha256"] != smoke["delivery"]["manifest_sha256"]:
        raise ValueError("包清单身份与独立安装报告不符")
    conditions = inputs["external_conditions"]
    if (
        conditions["competition_release_verified"]
        or conditions["official_dynamic_confirmed"]
        or conditions["public_endpoint"]
        or conditions["public_publication_authorized"]
    ):
        raise ValueError("此入口只交付已授权的本地技术范围，正式条件另行真实验收")
    output.mkdir(parents=True, exist_ok=False)
    audit_path = output / "post-gate-documentation-audit.json"
    audit = {
        "status": "PASSED",
        "scope": "POST_GATE_DOCUMENTATION_REGISTRATION_ONLY",
        "created_at": datetime.now(UTC).isoformat(),
        "phase_report": artifact(phase_path),
        "tested_fingerprint": phase["code_fingerprint"],
        "current_tree_fingerprint": current_fingerprint,
        "unchanged_file_count": len(prior) - len(changed),
        "file_set_unchanged": True,
        "non_documentation_sources_and_artifacts_unchanged": True,
        "changes": {
            name: {"tested_sha256": prior[name], "current_sha256": current[name]}
            for name in sorted(changed)
        },
        "note": "仅追加真实结果和更新任务状态；原阶段报告及其文档指纹不改写。",
    }
    write_json(audit_path, audit)
    reports = {}
    for name, relative in inputs["reports"].items():
        path = ROOT / relative
        report = json.loads(path.read_bytes())
        reports[name] = {**artifact(path), "status": report["status"], "scope": report.get("scope")}
    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "DEVELOPMENT_VERIFIED",
        "scope": "WINDOWS_DEVELOPMENT_TECHNICAL_ACCEPTANCE",
        "formal_acceptance": False,
        "verified_tasks": [f"P6-0{i}" for i in range(1, 8)],
        "blocked_task": "P6-08",
        "release": package["release"],
        "policy_hash": inputs["policy_hash"],
        "suite_hash": inputs["suite_hash"],
        "technical_gate": {"supervisor": artifact(supervisor_path), "phase": artifact(phase_path)},
        "tested_code_fingerprint": phase["code_fingerprint"],
        "current_tree_fingerprint": current_fingerprint,
        "post_gate_documentation_audit": artifact(audit_path),
        "reports": reports,
        "robustness_inventory": artifact(ROOT / inputs["robustness_inventory"]),
        "robustness_source_scope_audit": artifact(ROOT / inputs["robustness_scope_audit"]),
        "windows_delivery": {
            "archive": artifact(archive),
            "manifest": artifact(directory / "release_manifest.json"),
            "installation_report": artifact(smoke_path),
            "file_count": identity["file_count"],
            "wheel_count": package["wheel_count"],
            "python_version": package["python_version"],
            "frontend": package["frontend"],
            "network_required_for_install": package["network_required_for_install"],
        },
        "external_conditions": conditions,
        "external_preconditions": artifact(
            ROOT / "data/verification/P6-external-preconditions-run1.xml"
        ),
        "formal_open_items": package["formal_open_items"],
        "live_llm_calls_during_p6": 0,
    }
    destination = ROOT / "deploy/release_manifest.json"
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "status": manifest["status"],
        "manifest": artifact(destination),
        "audit": artifact(audit_path),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = finalize(args.gate.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
