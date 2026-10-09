"""批次状态的纯投影；只声明有明确边界的停止状态，不把计划当观测。"""

from app.domain.compatibility import GroupRuleSpec
from app.domain.scheduling_problem import CandidateCarrier, SchedulingProblem
from app.domain.transitions import ThermalProfile, ThermalState


def initial_thermal_state(problem: SchedulingProblem, key: tuple[str, str]) -> ThermalState:
    observations = [
        d for d in problem.runtime.device_states if (d.physical_resource_id, d.component_id) == key
    ]
    if len(observations) > 1:
        raise ValueError("同物理设备存在重复热状态")
    if observations and observations[0].thermal_state is not None:
        device = observations[0]
        state = device.thermal_state
        assert state is not None
        if (
            (state.physical_resource_id, state.component_id) != key
            or state.origin != "OBSERVED"
            or state.at_sec != problem.runtime.time_origin.offset(device.observed_at)
            or state.at_sec > problem.runtime.now_offset_sec
        ):
            raise ValueError("热观测身份或时刻与运行快照不一致")
        return state
    return ThermalState(
        physical_resource_id=key[0],
        component_id=key[1],
        condition="UNKNOWN",
        at_sec=problem.runtime.now_offset_sec,
        origin="OBSERVED",
        source_ref="no-thermal-observation",
    )


def planned_exit_state(
    problem: SchedulingProblem,
    carrier: CandidateCarrier | None,
    key: tuple[str, str],
    profile: ThermalProfile | None,
    at_sec: int,
    source_ref: str,
) -> ThermalState:
    condition = "UNKNOWN"
    if carrier is not None and carrier.kind == "THERMAL_BATCH" and len(carrier.rule_refs) == 1:
        rule = next(
            (r for r in problem.transition_rules if r.rule_id == carrier.rule_refs[0]), None
        )
        if rule is not None:
            try:
                spec = GroupRuleSpec.model_validate_json(rule.group_compatibility_predicate)
                if spec.thermal_model == "COLD_LOAD_SETUP_PREHEAT_HEAT_STOP_UNLOAD":
                    condition = "OFF"
            except ValueError:
                pass
    return ThermalState(
        physical_resource_id=key[0],
        component_id=key[1],
        condition=condition,
        profile=profile,
        at_sec=at_sec,
        origin="PLANNED",
        source_ref=source_ref,
    )
