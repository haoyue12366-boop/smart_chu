"""一次请求内各次候选计算共用的算法时间余额，不写入不可变策略。"""

from dataclasses import dataclass

from app.domain.policy import BudgetSpec


@dataclass
class ComputationBudget:
    greedy_remaining_ns: int
    solver_remaining_ns: int
    attempts_remaining: int = 1

    @classmethod
    def from_spec(cls, specification: BudgetSpec) -> "ComputationBudget":
        return cls(specification.greedy_ms * 1_000_000, specification.solver_ms * 1_000_000)

    def charge_greedy(self, elapsed_ns: int) -> None:
        self.greedy_remaining_ns = max(0, self.greedy_remaining_ns - max(0, elapsed_ns))

    @property
    def greedy_allowance_ns(self) -> int:
        """有条件发布的首轮保留下一轮构造时间，单次计算使用全部剩余额度。"""
        if self.attempts_remaining <= 1:
            return self.greedy_remaining_ns
        return self.greedy_remaining_ns * 3 // 4

    def charge_solver(self, elapsed_ns: int) -> None:
        self.solver_remaining_ns = max(0, self.solver_remaining_ns - max(0, elapsed_ns))
