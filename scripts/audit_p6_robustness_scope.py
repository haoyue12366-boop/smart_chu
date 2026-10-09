"""核对 SQL/HTTP 局部优化与已归档纯扰动模型的源码边界，不重写旧报告。"""

import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "benchmarks/reports/P6-robustness-run2/report.json"
OUTPUT = ROOT / "data/verification/P6-robustness-source-scope-audit-run7.json"
BASELINE = ROOT / "data/delivery/P6-Windows-candidate-run1"
EXCLUSIONS = {
    "app/api/events.py": (None, "apply", "fastapi.responses"),
    "app/runtime/notifications.py": ("NotificationService", "persist", "sqlalchemy"),
    "app/services/planning.py": ("PlanningService", "_compute_latest", None),
    "app/services/container.py": (
        "ServiceContainer",
        ("__init__", "pending_sessions", "recover_pending"),
        ("sqlalchemy", "app.scheduling.worker", "app.scheduling.json_worker"),
    ),
    "app/storage/competition_tasks.py": ("HttpRequestRepository", "pending", "sqlalchemy"),
    "app/main.py": (None, "create_app", None),
    "benchmarks/runner.py": (
        "BenchmarkRunner",
        "run",
        ("app.scheduling.worker", "app.scheduling.json_worker"),
    ),
}
NEW_MIGRATION = "app/storage/migrations/versions/0009_pending_recovery_indexes.py"
NEW_JSON_ADAPTER = "app/scheduling/json_worker.py"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remaining_tree(path, owner, name, import_module):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = import_module if isinstance(import_module, tuple) else (import_module,)
    tree.body = [
        node
        for node in tree.body
        if not (
            import_module is not None
            and isinstance(node, ast.ImportFrom)
            and node.module in imports
        )
    ]
    body = tree.body
    if owner is not None:
        body = next(
            node.body for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner
        )
    names = name if isinstance(name, tuple) else (name,)
    removed = [
        node
        for node in body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    if len(removed) != len(names):
        raise ValueError("排除边界未定位每一个声明函数")
    for node in removed:
        body.remove(node)
    return ast.dump(tree, include_attributes=False)


def run():
    report = json.loads(REPORT.read_bytes())
    if report["status"] != "EXPERIMENT_COMPLETED" or not report["source_unchanged"]:
        raise ValueError("只核对已实际完成且运行期间源码不变的扰动实验")
    changes = {}
    for name, recorded in report["source_hashes"].items():
        if (name.startswith("app/") or name == "benchmarks/runner.py") and digest(
            ROOT / name
        ) != recorded:
            changes[name] = {"historical_sha256": recorded, "current_sha256": digest(ROOT / name)}
    if set(changes) != set(EXCLUSIONS):
        raise ValueError("实际变更超出已定位的 HTTP/SQL/生命周期函数")
    for name, (owner, function, import_module) in EXCLUSIONS.items():
        historical = (
            BASELINE / name
            if name.startswith("app/")
            else ROOT / "data/verification/P6-source-baselines" / name
        )
        current = ROOT / name
        if digest(historical) != report["source_hashes"][name]:
            raise ValueError("原始源码副本不属于此扰动实验")
        if remaining_tree(historical, owner, function, import_module) != remaining_tree(
            current, owner, function, import_module
        ):
            raise ValueError("定位函数以外存在语义变化")
        changes[name].update(
            baseline_path=historical.relative_to(ROOT).as_posix(),
            unexecuted_symbols=[
                (owner + "." if owner else "") + name
                for name in (function if isinstance(function, tuple) else (function,))
            ],
            unchanged_ast_outside_symbol_and_import=True,
        )
    old_app_files = {name for name in report["source_hashes"] if name.startswith("app/")}
    current_app_files = {path.relative_to(ROOT).as_posix() for path in (ROOT / "app").rglob("*.py")}
    if (
        current_app_files - old_app_files != {NEW_MIGRATION, NEW_JSON_ADAPTER}
        or old_app_files - current_app_files
    ):
        raise ValueError("新增/删除应用源码超出纯 SQL 索引迁移边界")
    migration = ast.parse((ROOT / NEW_MIGRATION).read_text(encoding="utf-8"))
    for node in ast.walk(migration):
        if isinstance(node, ast.Call):
            if not (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "op"
                and node.func.attr in {"execute", "drop_index"}
                and all(
                    isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    for arg in node.args
                )
            ):
                raise ValueError("新增迁移包含索引 DDL 以外的可执行逻辑")
    evidence = {
        "status": "PASSED",
        "scope": "PURE_ROBUSTNESS_MODEL_SOURCE_COMPATIBILITY_ONLY",
        "formal_acceptance": False,
        "report_path": REPORT.relative_to(ROOT).as_posix(),
        "report_sha256": digest(REPORT),
        "created_at": datetime.now(UTC).isoformat(),
        "producer_sha256": digest(Path(__file__)),
        "exclusions": changes,
        "new_unexecuted_sources": {
            name: digest(ROOT / name) for name in (NEW_MIGRATION, NEW_JSON_ADAPTER)
        },
        "reason": (
            "Observed session uses pure bind_plan/apply_observation and "
            "PlanningCore/ReplanningService; "
            "it never executes the HTTP events route, SQL NotificationService.persist, "
            "PlanningService._compute_latest, ServiceContainer construction/recovery, "
            "the HTTP application factory/lifecycle, or either SQL recovery queue. The new "
            "migration only adds partial SQL indexes and is not imported or executed by the "
            "pure experiment. The SQL wrapper now ends optional "
            "optimization earlier. JsonSolverWorker is only constructed by the online "
            "ServiceContainer; the pure experiment constructs the original, byte-identical "
            "SolverWorker and never registers JSON message reducers. The pure experiment's "
            "own fixed budgets and original IPC are unchanged. "
            "BenchmarkRunner.run is not executed by either the pure robustness or real HTTP "
            "performance experiment; its source_hashes/write_json helpers are unchanged "
            "by the AST check. The full-suite runner now uses the online JSON adapter. "
            "All other recorded app sources remain byte-identical. HTTP and SQL changes require "
            "their own current contract, property, fault and performance checks."
        ),
        "limitation": (
            "Original whole-app fingerprint is preserved; "
            "this is not a claim that the whole application is unchanged."
        ),
    }
    if OUTPUT.exists():
        raise FileExistsError("源码范围核对不得覆盖既有证据")
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({"status": evidence["status"], "changed_functions": changes}, ensure_ascii=False)
    )


if __name__ == "__main__":
    run()
