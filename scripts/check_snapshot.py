"""新的只读进程加载固定发布；可禁用所有网络并记录实际调用数。"""

import argparse
import builtins
import json
import socket
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--deny-network", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    calls = 0
    original_import = builtins.__import__

    def guarded_import(name, *positional, **keyword):
        if name.split(".")[0] in {"neo4j", "httpx", "ortools"}:
            raise AssertionError("在线知识读取不得导入图数据库、HTTP或求解器")
        return original_import(name, *positional, **keyword)

    def deny(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("在线知识读取不得发网络请求")

    if args.deny_network:
        builtins.__import__ = guarded_import
        socket.socket.connect = deny
        socket.socket.connect_ex = deny
        socket.create_connection = deny
    started = time.perf_counter()
    from app.domain.ids import RecipeId
    from app.knowledge.loader import read_release_ref
    from app.knowledge.repository import SnapshotKnowledgeRepository

    ref = read_release_ref(args.root, args.release_id)
    repository = SnapshotKnowledgeRepository(args.root)
    handle = repository.load(ref)
    with repository.acquire(ref) as session:
        ids = tuple(RecipeId(entry.recipe_id.root) for entry in session.loaded.index.recipes)
        view = session.select(ids)
    report = {
        "release": ref.model_dump(mode="json"),
        "snapshot_hash": handle.snapshot_hash,
        "recipe_count": len(view.recipes),
        "operation_count": sum(len(r.operations) for r in view.recipes),
        "network_calls": calls,
        "network_guard": args.deny_network,
        "neo4j_imported": "neo4j" in sys.modules,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
