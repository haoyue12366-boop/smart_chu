"""精确 10g 的独立参考账；实际算法发布后随机执行、失败、重放和重启。"""

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

from app.runtime.service import RuntimeService
from app.storage.unit_of_work import UnitOfWork
from tests.integration.test_material_ledger import material_service
from tests.runtime_support import event


def test_execution_and_material_sequences_preserve_reference_ledger(
    tmp_path, record_testsuite_property
):
    statistics = Counter()

    class ExecutionMachine(RuleBasedStateMachine):
        def __init__(self):
            super().__init__()
            self.directory = TemporaryDirectory(dir=tmp_path)
            self.runtime, _ = material_service(Path(self.directory.name))
            current = self.runtime.get("material")
            binding = min(current.bindings, key=lambda b: b.assignment.interval.start_sec)
            self.task = binding.assignment.task_ids[0]
            self.raw = current.runtime.details.lots[0]
            self.output_spec = binding.carrier.material_outputs[0].spec_id
            self.consumed = 0
            self.output = 0
            self.output_available = 0
            self.phase = "RUNNING"
            self.terminal = None
            self.saved = []
            self.trace = []
            self.sequence = 0
            self.send("OPERATION_STARTED", {}, "APPLIED")
            statistics["sequences"] += 1

        def teardown(self):
            self.runtime.store.close()
            self.directory.cleanup()

        def movement(self, amount):
            return {
                "lot_id": self.raw.lot_id,
                "spec_id": self.raw.spec_id,
                "quantity": {"value": amount, "unit": "g", "scale": 1},
            }

        def production(self, amount):
            return {
                "lot_id": "stateful-output",
                "spec_id": self.output_spec,
                "quantity": {"value": amount, "unit": "g", "scale": 1},
            }

        def send(self, kind, payload, expected):
            before = self.runtime.get("material")
            request = event(
                before,
                f"step-{self.sequence}",
                kind,
                {"task_id": self.task, "execution_id": "stateful-execution", **payload},
                at=self.sequence,
            )
            self.sequence += 1
            result = self.runtime.apply_event(request)
            self.trace.append(
                {
                    "event": request.model_dump(mode="json"),
                    "expected": expected,
                    "result": result.model_dump(mode="json"),
                }
            )
            if result.status != expected:
                (tmp_path / "execution-minimal-failure.json").write_text(
                    json.dumps(self.trace, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            assert result.status == expected, result
            if expected == "REJECTED":
                assert self.runtime.get("material") == before
            statistics["steps"] += 1
            statistics[kind + ":" + expected] += 1
            self.saved.append((request, result))

        @precondition(lambda self: self.phase == "RUNNING")
        @rule(amount=st.integers(0, 11))
        def cumulative_report(self, amount):
            valid = self.consumed <= amount <= 10
            self.send(
                "DURATION_UPDATED",
                {"remaining_sec": 60, "consumed": [self.movement(amount)]},
                "APPLIED" if valid else "REJECTED",
            )
            if valid:
                self.consumed = amount

        @precondition(lambda self: self.phase == "RUNNING")
        @rule()
        def complete(self):
            self.send(
                "OPERATION_COMPLETED",
                {
                    "consumed": [self.movement(10)],
                    "produced": [self.production(10)],
                    "output_status": "QUALIFIED",
                },
                "APPLIED",
            )
            self.consumed = 10
            self.output = self.output_available = 10
            self.phase = "COMPLETED"
            self.terminal = self.runtime.get("material").runtime.executions[0]

        @precondition(lambda self: self.phase == "RUNNING")
        @rule(output=st.integers(0, 10), status=st.sampled_from(["UNKNOWN", "WASTE"]))
        def fail(self, output, status):
            output = min(output, self.consumed)
            self.send(
                "OPERATION_FAILED",
                {
                    "reason": "synthetic:stateful failure",
                    "consumed": [self.movement(self.consumed)],
                    "produced": [self.production(output)] if output else (),
                    "output_status": status,
                },
                "APPLIED",
            )
            self.output = output
            self.phase = "FAILED"
            self.terminal = self.runtime.get("material").runtime.executions[0]

        @precondition(lambda self: self.phase != "RUNNING")
        @rule()
        def late_failure(self):
            self.send(
                "OPERATION_FAILED",
                {
                    "reason": "synthetic:late report",
                    "consumed": [self.movement(self.consumed)],
                },
                "CONFLICT",
            )

        @rule(index=st.integers(0, 100), changed=st.booleans())
        def replay(self, index, changed):
            request, original = self.saved[index % len(self.saved)]
            before = self.runtime.get("material")
            if changed:
                request = request.model_copy(
                    update={
                        "payload": request.payload.model_copy(
                            update={"reason": "synthetic:changed content"}
                        )
                    }
                )
            result = self.runtime.apply_event(request)
            assert result.status == "CONFLICT" if changed else result == original
            assert self.runtime.get("material") == before
            statistics["steps"] += 1
            statistics["changed_replays" if changed else "replays"] += 1

        @rule()
        def restart(self):
            before = self.runtime.get("material")
            self.runtime.store.close()
            self.runtime = RuntimeService(
                UnitOfWork(self.runtime.store.path), self.runtime.knowledge, self.runtime.clock
            )
            assert self.runtime.get("material") == before
            statistics["steps"] += 1
            statistics["restarts"] += 1

        @invariant()
        def conserved_actuals(self):
            current = self.runtime.get("material")
            assert len(current.runtime.executions) == 1
            execution = current.runtime.executions[0]
            assert execution.status == self.phase
            if self.terminal:
                assert execution == self.terminal
            raw = next(lot for lot in current.runtime.details.lots if lot.lot_id == self.raw.lot_id)
            assert raw.available.fraction() == 10 - self.consumed
            spent = [e for e in current.ledger if e.kind == "CONSUME" and e.lot_id == raw.lot_id]
            assert sum(e.before.fraction() - e.after.fraction() for e in spent) == self.consumed
            assert all(e.before.fraction() > e.after.fraction() for e in spent)
            assert len({e.entry_id for e in current.ledger}) == len(current.ledger)
            outputs = [lot for lot in current.runtime.details.lots if lot.lot_id != raw.lot_id]
            assert sum(lot.produced.fraction() for lot in outputs) == self.output
            assert sum(lot.available.fraction() for lot in outputs) == self.output_available
            active = [o for o in current.runtime.details.occupancies if o.released_at is None]
            assert len(active) == (1 if self.phase == "RUNNING" else 0)
            assert all(o.resource.resource_id == "human_1" for o in active)

    run_state_machine_as_test(
        ExecutionMachine,
        settings=settings(
            max_examples=200,
            stateful_step_count=50,
            deadline=None,
            derandomize=True,
            database=None,
        ),
    )
    assert statistics["sequences"] >= 200
    for key, value in statistics.items():
        record_testsuite_property("execution_stateful_" + key, value)
    (tmp_path / "execution-stateful-statistics.json").write_text(
        json.dumps(dict(statistics), indent=2), encoding="utf-8"
    )
