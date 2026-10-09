"""实际 CLI 读取本地发布、预热工作进程并导出可复核秒/小数分钟计划。"""

import json
import subprocess
import sys

from tests.compiler_support import ROOT, published_knowledge


def test_real_cli_emits_decimal_minutes_and_bound_validation(tmp_path):
    recipe = next(r for r in published_knowledge().recipes if r.name == "韩式泡菜鸦片鱼头")
    output = tmp_path / "fish-plan.json"
    command = [
        sys.executable,
        "-m",
        "scripts.run_sample_plan",
        "--recipe-id",
        recipe.recipe_id.root,
        "--output",
        str(output),
    ]
    process = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["result"]["status"] == "VALIDATED"
    assert report["result"]["validation"]["valid"]
    assert report["release_kind"] == "development"
    assert not report["formal_review_complete"]
    assert any(row["duration_minute"] == "0.25" for row in report["minute_projection"])
    assert output.with_suffix(".problem.json").is_file()
    assert output.with_suffix(".builds.json").is_file()
