"""Compiler公开转换接口；纯表解释器亦供内存排程使用，独立Validator另行扫描。"""

from app.domain.transition_rules import resolve_transition as resolve_transition
