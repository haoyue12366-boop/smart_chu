"""新解释器真实首次加载及后续复用，不用模拟模块替代运行证据。"""

import json
import os
import subprocess
import sys
from pathlib import Path

from app.config import AppSettings


def test_lazy_modules_keep_warm_worker_validation_and_history(tmp_path):
    root = Path(__file__).resolve().parents[2]
    release_root = Path(
        os.getenv("SMART_COOKING_LAZY_TEST_RELEASE_ROOT", AppSettings().release_root)
    )
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(root / "tests/lazy_planning_app.py"),
            str(tmp_path / "lazy.sqlite3"),
            str(release_root.resolve()),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert all(evidence.values()), evidence
