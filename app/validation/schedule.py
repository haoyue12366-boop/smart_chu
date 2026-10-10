"""独立计划校验入口，绝不依赖 Compiler 或求解器的约束生成函数。"""

from app.domain.base import content_hash
from app.domain.knowledge import MenuKnowledgeView
from app.domain.runtime_snapshot import RuntimeSnapshot
from app.domain.schedule import CandidateSchedule
from app.domain.scheduling_problem import SchedulingProblem
from app.domain.validation_contract import ValidationReport
from app.validation.advance_preparation import check_advance_preparations
from app.validation.cooking_completion import check_cooking_completion_sources
from app.validation.dependencies import check_assignments, check_dependencies
from app.validation.duration_buffers import check_duration_buffers
from app.validation.material_balance import check_material_balance
from app.validation.metrics import check_metrics
from app.validation.resources import check_resources
from app.validation.resumption import check_resumptions
from app.validation.schedule_context import Scan
from app.validation.source_coverage import check_source_coverage
from app.validation.thermal_batches import check_thermal_programs


class ScheduleValidator:
    def validate(
        self,
        knowledge: MenuKnowledgeView,
        runtime: RuntimeSnapshot,
        problem: SchedulingProblem,
        candidate: CandidateSchedule,
    ) -> ValidationReport:
        scan = Scan(knowledge, runtime, problem, candidate)
        check_source_coverage(scan)
        check_advance_preparations(scan)
        check_cooking_completion_sources(scan)
        check_resumptions(scan)
        check_assignments(scan)
        check_dependencies(scan)
        check_duration_buffers(scan)
        check_material_balance(scan)
        check_resources(scan)
        check_thermal_programs(scan)
        check_metrics(scan)
        # 校验报告重新绑定实际正文，不信任调用方的指纹复用。
        candidate_hash = content_hash(candidate)
        return ValidationReport(
            report_id="validation-" + candidate_hash,
            problem_hash=scan.problem_hash,
            candidate_hash=candidate_hash,
            validator_version="schedule-independent-v1",
            valid=not scan.issues,
            violations=tuple(scan.issues),
        )
