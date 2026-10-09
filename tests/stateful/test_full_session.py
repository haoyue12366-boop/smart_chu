"""两个明确合成会话的跨模块序列；真实规划、SQLite、通知和重启。"""

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
from app.runtime.notifications import NotificationService
from app.storage.repositories import RuntimeRepository
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_device_and_material_failures import device_service
from tests.integration.test_material_ledger import material_service
from tests.runtime_support import event


def test_full_session_sequences_keep_reference_facts_and_publication(
    tmp_path, record_testsuite_property
):
    counts = Counter()

    class FullSession(RuleBasedStateMachine):
        def __init__(self):
            super().__init__()
            self.directory = TemporaryDirectory(dir=tmp_path)
            root = Path(self.directory.name)
            (root / "material").mkdir()
            (root / "device").mkdir()
            self.material, self.material_planner = material_service(root / "material")
            self.device, self.device_planner = device_service(root / "device", choice=False)
            self.services = (
                (self.material, self.material_planner, "material"),
                (self.device, self.device_planner, "flow"),
            )
            current = self.material.get("material")
            first = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
            self.task = first.assignment.task_ids[0]
            self.output_spec = first.carrier.material_outputs[0].spec_id
            self.raw = current.runtime.details.lots[0]
            self.amount = self.consumed = self.output = 0
            self.phase = "PENDING"
            self.history = None
            self.menu_count = 1
            self.device_available = True
            self.saved = []
            self.trace = []
            self.sequence = 0
            self.sent = {}
            self.revisions = {"material": 1, "flow": 1}
            self.plan_versions = {"material": 1, "flow": 1}
            self.poll_notices()
            counts["sequences"] += 1

        def teardown(self):
            self.material.store.close()
            self.device.store.close()
            self.directory.cleanup()

        def submit(self, runtime, sid, kind, payload, expected="APPLIED", *, at=None):
            current = runtime.get(sid)
            self.sequence += 1
            now = (
                current.runtime.now_offset_sec
                if at is None
                else max(at, current.runtime.now_offset_sec)
            )
            request = event(current, f"p6-sequence-{sid}-{self.sequence}", kind, payload, at=now)
            result = runtime.apply_event(request)
            self.trace.append(
                {
                    "request": request.model_dump(mode="json"),
                    "expected": expected,
                    "result": result.model_dump(mode="json"),
                }
            )
            if result.status != expected:
                (tmp_path / "full-session-minimal-failure.json").write_text(
                    json.dumps(self.trace, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            assert result.status == expected, result
            if expected == "APPLIED":
                self.revisions[sid] += 1
                runtime.clock.advance(now)
            else:
                assert runtime.get(sid) == current
            self.saved.append((sid, request, result))
            counts[kind + ":" + expected] += 1
            counts["steps"] += 1
            return result

        def movement(self, amount):
            return {
                "lot_id": self.raw.lot_id,
                "spec_id": self.raw.spec_id,
                "quantity": {"value": amount, "unit": "g", "scale": 1},
            }

        @precondition(lambda self: self.phase == "PENDING")
        @rule()
        def begin_material(self):
            current = self.material.get("material")
            if current.dispatch_blocked:
                self.drain_one(self.material, self.material_planner, "material")
                current = self.material.get("material")
            if current.dispatch_blocked:
                return
            self.submit(
                self.material,
                "material",
                "OPERATION_STARTED",
                {
                    "task_id": self.task,
                    "execution_id": "p6-material",
                    "consumed": [self.movement(0)],
                },
            )
            self.phase = "RUNNING"

        @precondition(lambda self: self.phase == "RUNNING")
        @rule(amount=st.integers(0, 11), remaining=st.integers(1, 180))
        def report_duration_and_quantity(self, amount, remaining):
            valid = self.consumed <= amount <= 10
            self.submit(
                self.material,
                "material",
                "DURATION_UPDATED",
                {
                    "task_id": self.task,
                    "execution_id": "p6-material",
                    "remaining_sec": remaining,
                    "consumed": [self.movement(amount)],
                },
                "APPLIED" if valid else "REJECTED",
            )
            if valid:
                self.consumed = amount

        @precondition(lambda self: self.phase == "RUNNING")
        @rule(fail=st.booleans())
        def finish_or_fail_material(self, fail):
            current = self.material.get("material")
            record = next(
                e for e in current.runtime.executions if e.execution_id.root == "p6-material"
            )
            at = current.runtime.time_origin.offset(record.started_at) + 60
            amount = self.consumed if fail else 10
            produced = (
                []
                if fail
                else [
                    {
                        "lot_id": "p6-output",
                        "spec_id": self.output_spec,
                        "quantity": {"value": 10, "unit": "g", "scale": 1},
                    }
                ]
            )
            self.submit(
                self.material,
                "material",
                "OPERATION_FAILED" if fail else "OPERATION_COMPLETED",
                {
                    "task_id": self.task,
                    "execution_id": "p6-material",
                    "consumed": [self.movement(amount)],
                    "produced": produced,
                    "output_status": "WASTE" if fail else "QUALIFIED",
                    "reason": "synthetic:p6 terminal feedback",
                },
                at=at,
            )
            self.consumed = amount
            self.output = 0 if fail else 10
            self.phase = "FAILED" if fail else "COMPLETED"
            self.history = next(
                e
                for e in self.material.get("material").runtime.executions
                if e.execution_id.root == "p6-material"
            )

        @precondition(lambda self: self.menu_count < 4)
        @rule()
        def add_recipe(self):
            recipe = self.material.knowledge.recipes[0]
            self.submit(
                self.material,
                "material",
                "ADD_RECIPE",
                {"recipes": [{"id": recipe.recipe_id, "name": recipe.name}]},
            )
            self.menu_count += 1

        @precondition(lambda self: self.menu_count > 1)
        @rule(cancel=st.booleans(), earliest=st.integers(0, 1000))
        def change_added_recipe(self, cancel, earliest):
            current = self.material.get("material")
            target = current.menu[-1].recipe_instance_id
            payload = {"recipe_instance_id": target}
            if not cancel:
                payload["earliest_start_sec"] = earliest
            self.submit(
                self.material, "material", "CANCEL_RECIPE" if cancel else "DELAY_RECIPE", payload
            )

        @rule(available=st.booleans())
        def device_status(self, available):
            self.submit(
                self.device,
                "flow",
                "DEVICE_RECOVERED" if available else "DEVICE_UNAVAILABLE",
                {"device_id": "burner_1", "reason": "synthetic:p6 availability"},
            )
            self.device_available = available

        @rule()
        def invalid_device_release(self):
            self.submit(
                self.device,
                "flow",
                "DEVICE_RELEASE_CONFIRMED",
                {"device_id": "burner_1", "execution_id": "nonexistent"},
                "REJECTED",
            )

        @rule(after=st.integers(0, 1000))
        def manual_preference(self, after):
            current = self.device.get("flow")
            target = current.menu[0].recipe_instance_id.root
            before = dict(current.runtime.details.earliest_starts).get(target, 0)
            self.submit(
                self.device,
                "flow",
                "MANUAL_OVERRIDE",
                {
                    "correction_type": "PLAN_PREFERENCE",
                    "target_id": target,
                    "before_value": before,
                    "after_value": after,
                    "reason": "synthetic:p6",
                    "operator": "synthetic:test",
                    "evidence_refs": ["synthetic:p6"],
                },
            )

        @rule()
        def invalid_material_before_value(self):
            current = self.material.get("material")
            raw = next(lot for lot in current.runtime.details.lots if lot.lot_id == self.raw.lot_id)
            self.submit(
                self.material,
                "material",
                "MATERIAL_SHORTAGE",
                {
                    "lot_id": raw.lot_id,
                    "before": {"value": int(raw.available.fraction()) + 1, "unit": "g", "scale": 1},
                    "after": {"value": 0, "unit": "g", "scale": 1},
                    "reason": "synthetic:invalid scale",
                    "evidence_refs": ["synthetic:p6"],
                },
                "REJECTED",
            )

        def drain_one(self, runtime, planner, sid):
            before = runtime.get(sid)
            outcome = planner.drain(sid)
            current = runtime.get(sid)
            if outcome.status == "PUBLISHED":
                self.plan_versions[sid] += 1
                assert outcome.plan.validated.validation.valid
                assert current.runtime.executions == before.runtime.executions
                assert current.runtime.details.lots == before.runtime.details.lots or all(
                    next(
                        lot for lot in current.runtime.details.lots if lot.lot_id == old.lot_id
                    ).available
                    == old.available
                    for old in before.runtime.details.lots
                )
            else:
                assert outcome.status in {"FAILED", "NO_REPLAN", "PENDING"}
                assert current.runtime.current_plan_version == before.runtime.current_plan_version
                assert current.runtime.executions == before.runtime.executions
            counts["drain:" + outcome.status] += 1

        @rule()
        def replan(self):
            self.drain_one(self.material, self.material_planner, "material")
            self.drain_one(self.device, self.device_planner, "flow")
            counts["steps"] += 1

        @precondition(lambda self: bool(self.saved))
        @rule(index=st.integers(0, 10000), changed=st.booleans())
        def replay(self, index, changed):
            sid, request, original = self.saved[index % len(self.saved)]
            runtime = self.material if sid == "material" else self.device
            before = runtime.get(sid)
            if changed:
                request = request.model_copy(update={"source": EventSource.MANUAL_CONFIRM})
            result = runtime.apply_event(request)
            assert result.status == "CONFLICT" if changed else result == original
            assert runtime.get(sid) == before
            counts["changed_replay" if changed else "replay"] += 1
            counts["steps"] += 1

        @rule()
        def stale(self):
            current = self.material.get("material")
            request = event(
                current, f"p6-stale-{self.sequence}", "ADVANCE_SIMULATION", {"advance_sec": 1}
            )
            request = request.model_copy(
                update={"expected_state_revision": current.runtime.state_revision - 1}
            )
            before = current
            assert self.material.apply_event(request).status == "CONFLICT"
            assert self.material.get("material") == before
            counts["stale"] += 1
            counts["steps"] += 1

        @rule()
        def restart(self):
            for runtime, planner, sid in self.services:
                before = runtime.get(sid)
                runtime.store.close()
                runtime.store = UnitOfWork(runtime.store.path)
                assert runtime.get(sid) == before
                assert planner.runtime is runtime
            counts["restart"] += 1
            counts["steps"] += 1

        @rule()
        def poll_notices(self):
            for runtime, _, sid in self.services:
                with runtime.store.transaction() as tx:
                    service = NotificationService()
                    for notice in service.due(tx, sid, runtime.clock.now()):
                        sent = service.mark_sent(tx, notice.notification_id, runtime.clock.now())
                        assert (
                            service.mark_sent(tx, notice.notification_id, runtime.clock.now())
                            == sent
                        )
                        self.sent[sid, notice.notification_id] = sent
            counts["notices"] += 1
            counts["steps"] += 1

        @invariant()
        def independent_balances_and_versions(self):
            current = self.material.get("material")
            raw = next(lot for lot in current.runtime.details.lots if lot.lot_id == self.raw.lot_id)
            assert raw.available.fraction() == 10 - self.consumed
            consumed = [e for e in current.ledger if e.lot_id == raw.lot_id and e.kind == "CONSUME"]
            assert sum(e.before.fraction() - e.after.fraction() for e in consumed) == self.consumed
            assert len({e.entry_id for e in current.ledger}) == len(current.ledger)
            assert len(current.menu) == self.menu_count
            if self.history is not None:
                assert (
                    next(
                        e
                        for e in current.runtime.executions
                        if e.execution_id == self.history.execution_id
                    )
                    == self.history
                )
            for runtime, _, sid in self.services:
                state = runtime.get(sid)
                assert state.runtime.state_revision == self.revisions[sid]
                assert state.runtime.current_plan_version == self.plan_versions[sid]
                with runtime.store.engine.connect() as tx:
                    repo = RuntimeRepository(tx)
                    records = repo.notification_records(sid)
                    assert len({r.deduplication_key for r in records}) == len(records)
                    assert all(repo.plan(sid, r.plan_version) is not None for r in records)
                    assert all(
                        r.status in {"CANCELLED", "SENT"}
                        for r in records
                        if r.plan_version < state.runtime.current_plan_version
                    )
                    for notice in records:
                        if (sid, notice.notification_id) in self.sent:
                            assert notice == self.sent[sid, notice.notification_id]
            device = self.device.get("flow")
            observations = [
                d for d in device.runtime.device_states if d.device_instance_id == "burner_1"
            ]
            if observations:
                assert observations[0].availability_status == (
                    "AVAILABLE" if self.device_available else "UNAVAILABLE"
                )

    run_state_machine_as_test(
        FullSession,
        settings=settings(
            max_examples=100, stateful_step_count=50, deadline=None, derandomize=True, database=None
        ),
    )
    assert counts["sequences"] >= 100
    for key in (
        "DURATION_UPDATED:APPLIED",
        "OPERATION_COMPLETED:APPLIED",
        "OPERATION_FAILED:APPLIED",
        "DEVICE_UNAVAILABLE:APPLIED",
        "DEVICE_RECOVERED:APPLIED",
        "MANUAL_OVERRIDE:APPLIED",
        "ADD_RECIPE:APPLIED",
        "CANCEL_RECIPE:APPLIED",
        "DELAY_RECIPE:APPLIED",
        "replay",
        "changed_replay",
        "restart",
        "notices",
        "drain:PUBLISHED",
    ):
        assert counts[key] > 0, "状态机没有实际覆盖动作：" + key
    for key, value in counts.items():
        record_testsuite_property("p6_full_session:" + key, value)
    (tmp_path / "full-session-statistics.json").write_text(
        json.dumps(dict(counts), indent=2), encoding="utf-8"
    )
