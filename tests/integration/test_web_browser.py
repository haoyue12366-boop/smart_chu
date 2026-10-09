"""真实浏览器联调包装，保留逐次报告并检查后台异常。"""

import os
from datetime import UTC, datetime

from tests.web_verification_support import ROOT, checked_cases, record_web, run_web


def browser_checks(scenarios: list[str], record_testsuite_property) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = ROOT / "data/verification/P5-browser" / ("gate-" + stamp)
    evidence = run_web(
        ["run", "test:e2e", "--", *scenarios],
        env={**os.environ, "SMART_COOKING_BROWSER_REPORT_ROOT": str(output)},
    )
    evidence["test_results"] = checked_cases(output / "results.xml")
    evidence["report_root"] = output.relative_to(ROOT).as_posix()
    record_web(record_testsuite_property, "p5_real_browser_commands", [evidence])


def test_real_manual_feedback_replanning_and_simulated_release(record_testsuite_property):
    browser_checks(
        ["tests/e2e/replanning.spec.ts", "tests/e2e/simulation_release.spec.ts"],
        record_testsuite_property,
    )
