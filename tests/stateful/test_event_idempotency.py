"""独立菜单/版本参考模型；生成重复、乱序、错误来源和结束后的真实 SQLite 事件。"""

import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from app.domain.events import EventSource
from app.runtime.clock import SimulationClock
from app.runtime.service import RuntimeService
from app.storage.unit_of_work import UnitOfWork
from tests.runtime_support import ORIGIN, event, policy, synthetic_knowledge


def test_event_sequences_match_independent_reference(tmp_path, record_testsuite_property):
    statistics = Counter()

    class EventMachine(RuleBasedStateMachine):
        def __init__(self):
            super().__init__()
            self.directory = TemporaryDirectory(dir=tmp_path)
            self.store = UnitOfWork(Path(self.directory.name) / "events.sqlite")
            self.store.migrate()
            self.runtime = RuntimeService(
                self.store, synthetic_knowledge(), SimulationClock(ORIGIN)
            )
            self.runtime.create_session("stateful-events", "SIMULATED", ORIGIN, policy())
            self.revision = 0
            self.phase = "CREATED"
            self.offset = 0
            self.recipes = []
            self.cancelled = set()
            self.starts = {}
            self.applied = []
            self.saved = []
            self.trace = []
            self.sequence = 0
            statistics["sequences"] += 1

        def teardown(self):
            self.store.close()
            self.directory.cleanup()

        def new_event(self, kind, payload):
            self.sequence += 1
            current = self.runtime.get("stateful-events")
            return event(current, f"event-{self.sequence}", kind, payload, at=self.offset)

        def submit(self, request, expected):
            before = self.runtime.get("stateful-events")
            result = self.runtime.apply_event(request)
            self.trace.append(
                {
                    "event": request.model_dump(mode="json"),
                    "expected": expected,
                    "result": result.model_dump(mode="json"),
                }
            )
            statistics["steps"] += 1
            statistics[expected] += 1
            if result.status != expected:
                (tmp_path / "event-minimal-failure.json").write_text(
                    json.dumps(self.trace, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            assert result.status == expected, result
            if expected == "APPLIED":
                self.revision += 1
                self.applied.append(request.event_id)
            else:
                assert self.runtime.get("stateful-events") == before
            self.saved.append((request, result))

        @rule(index=st.integers(0, 1), start=st.booleans())
        def menu_event(self, index, start):
            kind = "START_SESSION" if start else "ADD_RECIPE"
            accepted = self.phase == ("CREATED" if start else "ACTIVE")
            request = self.new_event(
                kind, {"recipes": [{"id": f"synthetic-{index}", "name": f"合成腌制{index}"}]}
            )
            self.submit(request, "APPLIED" if accepted else "REJECTED")
            if accepted:
                self.phase = "ACTIVE"
                self.recipes.append(f"synthetic-{index}")

        @precondition(lambda self: bool(self.recipes))
        @rule(index=st.integers(0, 100), cancel=st.booleans(), earliest=st.integers(0, 2000))
        def change_menu(self, index, cancel, earliest):
            current = self.runtime.get("stateful-events")
            target = current.menu[index % len(self.recipes)].recipe_instance_id
            payload = {"recipe_instance_id": target}
            if not cancel:
                payload["earliest_start_sec"] = earliest
            self.submit(
                self.new_event("CANCEL_RECIPE" if cancel else "DELAY_RECIPE", payload),
                "APPLIED" if self.phase == "ACTIVE" else "REJECTED",
            )
            if self.phase == "ACTIVE" and target.root not in self.cancelled:
                if cancel:
                    self.cancelled.add(target.root)
                else:
                    self.starts[target.root] = earliest

        @rule(seconds=st.integers(0, 100))
        def advance(self, seconds):
            accepted = self.phase == "ACTIVE"
            self.submit(
                self.new_event("ADVANCE_SIMULATION", {"advance_sec": seconds}),
                "APPLIED" if accepted else "REJECTED",
            )
            if accepted:
                self.offset += seconds

        @rule()
        def end(self):
            accepted = self.phase == "ACTIVE"
            self.submit(
                self.new_event("RESET_SESSION", {"reason": "synthetic:stateful reset"}),
                "APPLIED" if accepted else "REJECTED",
            )
            if accepted:
                self.phase = "ENDED"

        @precondition(lambda self: bool(self.saved))
        @rule(index=st.integers(0, 100), changed=st.booleans())
        def replay(self, index, changed):
            request, original = self.saved[index % len(self.saved)]
            before = self.runtime.get("stateful-events")
            if changed:
                request = request.model_copy(
                    update={
                        "source": EventSource.MANUAL_CONFIRM
                        if request.source == "SIMULATED"
                        else EventSource.SIMULATED
                    }
                )
            result = self.runtime.apply_event(request)
            assert result.status == "CONFLICT" if changed else result == original
            assert self.runtime.get("stateful-events") == before
            statistics["steps"] += 1
            statistics["content_conflicts" if changed else "replays"] += 1

        @precondition(lambda self: self.revision > 0)
        @rule()
        def stale(self):
            request = self.new_event("ADVANCE_SIMULATION", {"advance_sec": 1})
            request = request.model_copy(update={"expected_state_revision": self.revision - 1})
            self.submit(request, "CONFLICT")

        @rule()
        def invalid_source(self):
            request = self.new_event("ADVANCE_SIMULATION", {"advance_sec": 1})
            self.submit(
                request.model_copy(update={"source": EventSource.MANUAL_CONFIRM}), "REJECTED"
            )

        @invariant()
        def reference_state(self):
            current = self.runtime.get("stateful-events")
            assert current.runtime.state_revision == self.revision
            assert current.runtime.current_plan_version == 0
            assert current.status == self.phase
            assert current.runtime.now_offset_sec == self.offset
            assert [i.recipe_id.root for i in current.menu] == self.recipes
            assert len({i.recipe_instance_id for i in current.menu}) == len(self.recipes)
            assert set(current.runtime.details.cancelled_instance_ids) == self.cancelled
            assert dict(current.runtime.details.earliest_starts) == self.starts
            assert list(current.runtime.event_refs) == self.applied
            assert not current.runtime.executions and not current.ledger

    run_state_machine_as_test(
        EventMachine,
        settings=settings(
            max_examples=200, stateful_step_count=50, deadline=None, derandomize=True, database=None
        ),
    )
    assert statistics["sequences"] >= 200
    for key, value in statistics.items():
        record_testsuite_property("event_stateful_" + key, value)
    (tmp_path / "event-stateful-statistics.json").write_text(
        json.dumps(dict(statistics), indent=2), encoding="utf-8"
    )
