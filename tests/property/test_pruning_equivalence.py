"""合成候选穷举：只删完全等价表达，完整时间方案集合保持。"""

from itertools import product

from hypothesis import given
from hypothesis import strategies as st

from app.compiler.pruning import deduplicate
from app.domain.scheduling_problem import CandidateCarrier


@given(st.lists(st.integers(min_value=1, max_value=10), min_size=1, max_size=8))
def test_equivalent_dedup_keeps_all_feasible_duration_vectors(durations):
    candidates = tuple(
        CandidateCarrier(carrier_id=f"a-{i}", kind="STANDALONE", covers=("a",), duration_sec=d)
        for i, d in enumerate(durations)
    )
    candidates += (
        CandidateCarrier(carrier_id="b", kind="STANDALONE", covers=("b",), duration_sec=7),
    )
    retained = deduplicate(candidates).candidates

    def enumerate_plans(values):
        # 独立穷举a/b两个需求所有单独执行方式；无等待后继在a完成时开始。
        options = [[c for c in values if c.covers[0].root == name] for name in ("a", "b")]
        return {(a.duration_sec, a.duration_sec + b.duration_sec) for a, b in product(*options)}

    assert enumerate_plans(candidates) == enumerate_plans(retained)
