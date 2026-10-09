"""合成proc/cgroup文件只供诊断；缺失数据保持未知，不冒充零内存或无OOM。"""

import os

import pytest

from app.services.resource_monitor import ResourceMonitor


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def test_reads_only_fixed_numeric_resource_fields_and_public_deploy_identity(tmp_path, monkeypatch):
    proc, group = tmp_path / "proc", tmp_path / "group"
    write(proc, f"{os.getpid()}/statm", "100 25 0 0 0 0 0\n")
    write(proc, "7/statm", "200 50 0 0 0 0 0\n")
    write(group, "memory.current", "471859200\n")
    write(group, "memory.peak", "524288000\n")
    write(group, "memory.max", "536870912\n")
    write(group, "memory.events", "max 7\noom 1\noom_kill 1\n")
    write(group, "cpu.max", "10000 100000\n")
    write(group, "cpu.stat", "usage_usec 4000000\nnr_periods 1000\nnr_throttled 800\n")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("RESOURCE_DIAGNOSTIC_SECRET", "do-not-expose")
    tick = [0]
    monitor = ResourceMonitor(
        proc_root=proc, cgroup_root=group, page_bytes=4096, clock_ns=lambda: tick[0]
    )
    tick[0] = 5_000_000
    result = monitor.read(worker_process_id=7)
    assert result["commit_sha"] == "a" * 40
    assert result["uptime_ms"] == 5
    assert result["api_rss_bytes"] == 25 * 4096
    assert result["solver_rss_bytes"] == 50 * 4096
    assert result["cgroup_memory_current_bytes"] == 471859200
    assert result["cgroup_memory_peak_bytes"] == 524288000
    assert result["cgroup_memory_max_bytes"] == 536870912
    assert result["cgroup_memory_max_unlimited"] is False
    assert result["cgroup_memory_events"] == {"max": 7, "oom": 1, "oom_kill": 1}
    assert result["cgroup_cpu_quota_usec"] == 10000
    assert result["cgroup_cpu_period_usec"] == 100000
    assert result["cgroup_cpu_stat"]["nr_throttled"] == 800
    assert "do-not-expose" not in repr(result)


def test_missing_kernel_fields_are_unknown_and_boot_identity_is_distinct(tmp_path, monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    first = ResourceMonitor(proc_root=tmp_path, cgroup_root=tmp_path)
    second = ResourceMonitor(proc_root=tmp_path, cgroup_root=tmp_path)
    a, b = first.read(), second.read()
    assert a["boot_id"] != b["boot_id"]
    assert a["commit_sha"] is None
    for name, value in a.items():
        if name.startswith(("cgroup_", "api_rss", "solver_rss")):
            assert value is None, name


def test_malformed_counters_do_not_become_zero_or_disclose_unknown_fields(tmp_path, monkeypatch):
    write(tmp_path, "memory.current", "invalid\n")
    write(tmp_path, "memory.peak", "-3\n")
    write(tmp_path, "memory.max", "max\n")
    write(tmp_path, "memory.events", "oom invalid\nsecret_token 12345\n")
    write(tmp_path, "cpu.max", "max 100000\n")
    write(tmp_path, "cpu.stat", "unknown_secret 12345\n")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "not-a-valid-public-sha")
    result = ResourceMonitor(proc_root=tmp_path, cgroup_root=tmp_path).read()
    assert result["commit_sha"] is None
    assert result["cgroup_memory_current_bytes"] is None
    assert result["cgroup_memory_peak_bytes"] is None
    assert result["cgroup_memory_events"] is None
    assert result["cgroup_memory_max_bytes"] is None
    assert result["cgroup_memory_max_unlimited"] is True
    assert result["cgroup_cpu_quota_usec"] is None
    assert result["cgroup_cpu_period_usec"] == 100000
    assert result["cgroup_cpu_stat"] is None
    assert "secret" not in repr(result)


@pytest.mark.parametrize("limit", ["invalid", "-1"])
def test_invalid_memory_limit_stays_unknown(tmp_path, limit):
    write(tmp_path, "memory.max", limit)
    result = ResourceMonitor(proc_root=tmp_path, cgroup_root=tmp_path).read()
    assert result["cgroup_memory_max_bytes"] is None
    assert result["cgroup_memory_max_unlimited"] is None


def test_nested_cgroup_uses_current_process_binding_instead_of_mount_root(tmp_path):
    proc, group = tmp_path / "proc", tmp_path / "group"
    write(proc, f"{os.getpid()}/cgroup", "0::/service\n")
    write(group, "memory.current", "9999\n")
    write(group, "service/memory.current", "111\n")
    result = ResourceMonitor(proc_root=proc, cgroup_root=group).read()
    assert result["cgroup_memory_current_bytes"] == 111
