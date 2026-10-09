"""明确的诊断插桩：记录困难追加菜单的真实执行开销，不作为性能验收。"""

import cProfile
import os
import pstats
from pathlib import Path


def create_app():
    import app.api.events as events
    from benchmarks.performance import create_benchmark_app

    original = events.execute_request

    def profiled(services, record, limit):
        profile = cProfile.Profile()
        try:
            return profile.runcall(original, services, record, limit)
        finally:
            path = Path(os.environ["SMART_COOKING_P6_PROFILE_DIRECTORY"])
            path.mkdir(parents=True, exist_ok=True)
            profile.dump_stats(str(path / (record.request_id + ".prof")))

    events.execute_request = profiled
    return create_benchmark_app()


def main():
    import benchmarks.performance as performance
    from tests.fault_injection.stream_support import WindowsApiProcess

    output = Path("benchmarks/reports/P6-performance-profile1").resolve()
    os.environ["SMART_COOKING_P6_PROFILE_DIRECTORY"] = str(output / "profiles")

    def process(directory, **kwargs):
        return WindowsApiProcess(directory, factory="scripts.profile_p6_http:create_app")

    performance.WindowsApiProcess = process
    report = performance.run(output, case_ids=["replan-016"])
    for path in sorted((output / "profiles").glob("*.prof")):
        print(path.name)
        pstats.Stats(str(path)).strip_dirs().sort_stats("cumulative").print_stats(28)
    print("INSTRUMENTED_DIAGNOSTIC_ONLY", report["status"])


if __name__ == "__main__":
    main()
