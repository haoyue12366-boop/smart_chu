"""报告不混用计时、删失败或放宽在线预算；真实 TCP 采样由固定运行器执行。"""

import json
from copy import deepcopy

import pytest

from benchmarks.dataset import ROOT, load_inputs
from benchmarks.performance import statistics, timing_header, validate_profile


@pytest.fixture(scope="module")
def profile():
    _, policy = load_inputs()
    suite = json.loads((ROOT / "benchmarks/scenarios/full_suite.json").read_bytes())
    config = json.loads((ROOT / "benchmarks/scenarios/performance_profiles.json").read_bytes())
    return config, suite, policy


def test_fixed_profile_preserves_all_replans_and_previous_difficult_menus(profile):
    config, suite, policy = profile
    cases = validate_profile(config, suite, policy)
    assert len(cases) == 31 and sum(bool(c.get("event_script")) for c in cases) == 20
    assert config["rounds"] * sum(1 + bool(c.get("event_script")) for c in cases) == 153


@pytest.mark.parametrize("fault", ["budget", "rounds", "concurrency", "difficult", "version"])
def test_less_strict_or_incomplete_profile_is_rejected(profile, fault):
    config, suite, policy = profile
    changed = deepcopy(config)
    if fault == "budget":
        changed["budgets_ms"]["REPLAN"] = 3000
    elif fault == "rounds":
        changed["rounds"] = 2
    elif fault == "concurrency":
        changed["concurrent_jobs"] = 2
    elif fault == "difficult":
        changed["case_ids"].remove("replan-016")
    else:
        changed["suite_hash"] = "0" * 64
    with pytest.raises(ValueError):
        validate_profile(changed, suite, policy)


def test_failures_timeouts_and_missing_server_timings_remain_in_denominator():
    rows = [
        {
            "passed_correctness": passed,
            "service_elapsed_ms": server,
            "client_elapsed_ms": client,
            "budget_ms": 2400,
            "excellent_client_ms": 3000,
            "solver_fallback": fallback,
            "failure_reason": reason,
        }
        for passed, server, client, fallback, reason in (
            (True, 2300, 3100, True, None),
            (False, 2700, 2800, False, "NO_SOLUTION_WITHIN_BUDGET"),
            (False, None, 500, False, "MISSING_SERVER_TIMING"),
        )
    ]
    result = statistics(rows)
    assert result["sample_count"] == 3
    assert result["failure_count"] == result["deadline_exceeded_count"] == 2
    assert result["solver_fallback_count"] == 1
    assert result["official_excellent_exceeded_count"] == 1
    assert result["service"]["measured_count"] == 2
    assert result["service"]["max_ms"] == 2700
    assert result["client"]["max_ms"] == 3100


def test_client_latency_cannot_replace_missing_server_timing():
    assert timing_header("application;dur=2412.123") == 2412.123
    for invalid in ("", "2412.123", "application;dur=-1", "application;dur=nan"):
        with pytest.raises(ValueError):
            timing_header(invalid)
    with pytest.raises(ValueError):
        statistics([])
