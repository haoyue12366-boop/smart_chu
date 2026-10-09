"""P5 开发协议和验收场景设计；不实现 HTTP，不把设计标成测试结果。"""

from typing import Any

TASK_IDS = tuple(f"P5-{index:02d}" for index in range(1, 9))
SUCCESS_FIELDS = (
    "overview",
    "cookingTimeline",
    "ingredientsSummary",
    "detailTimeline",
    "recipeDetail",
)
DEVELOPMENT_PROFILE = "QA0930_TASK_INSERT_DEVELOPMENT"
STATIC_PROFILE = "STATIC_ARRAY_V1"


def competition_profiles() -> dict[str, Any]:
    return {
        "schema_version": "p5-preparation-1",
        "status": "DESIGN_ONLY",
        "implementation_status": "NOT_STARTED",
        "route": "/api/competition/plan",
        "route_authority": "PROJECT_CHOICE",
        "request_body": "DIRECT_RECIPE_ARRAY",
        "success_top_level_fields": list(SUCCESS_FIELDS),
        "profile_selection": {
            "explicit_task_id": DEVELOPMENT_PROFILE,
            "no_task_id": STATIC_PROFILE,
        },
        "official_facts": {
            "source": "docs/答疑9.30.txt",
            "dataset_format_unchanged": True,
            "task_id_suggested": True,
            "single_recipe_after_initial_is_insertion": True,
            "insertion_time_required": False,
            "uses_existing_recipes": True,
            "independent_ingredient_branches_may_reorder": True,
            "same_ingredient_dependencies_preserved": True,
        },
        "profiles": {
            STATIC_PROFILE: {
                "authority": "EXISTING_INTERFACE_DOCUMENT",
                "live_verified": False,
                "task_mode": "INDEPENDENT_REQUEST",
                "response_scope": "REQUEST_ONLY",
                "recipe_count_basis": "REQUEST_LENGTH",
                "time_origin": "REQUEST_RECEIVED",
                "minute_mode": "INTEGER",
                "unrepresentable_seconds": "REJECT_WITHOUT_CHANGING_PROCESS",
            },
            DEVELOPMENT_PROFILE: {
                "authority": "PROJECT_DRAFT_BASED_ON_QA0930",
                "official_dynamic_protocol_confirmed": False,
                "live_verified": False,
                "task_id_transport": "QUERY_PARAMETER",
                "task_id_parameter": "task_id",
                "internal_business_id": "competition_task_id",
                "operation_task_id_is_business_task_id": False,
                "idempotency_transport": "Idempotency-Key",
                "mapping_storage": "PERSISTENT_SQLITE_NEW_MIGRATION_REQUIRED",
                "new_task": "START_SESSION_INITIAL_NONEMPTY_ARRAY",
                "new_task_single_recipe": "INITIAL",
                "existing_active_task_single_recipe": "ADD_RECIPE_REPLAN",
                "existing_task_multiple_recipes": "REJECT_AMBIGUOUS_BATCH",
                "closed_task_reuse": "REJECT_PRESERVE_HISTORY",
                "duplicate_recipe_without_request_key": "REJECT_SECOND_SERVING_UNDEFINED",
                "same_key_same_content": "REUSE_PERSISTED_REQUEST_RESULT",
                "same_key_different_content": "CONFLICT_BEFORE_EVENT",
                "request_digest_includes_new_server_timestamp": False,
                "internal_planning_scope": "ALL_CUMULATIVE_UNFINISHED_REQUIREMENTS",
                "progress_mode": "PLAN_ONLY_MANUAL_FEEDBACK",
                "infer_progress_from_elapsed_time": False,
                "response_scope": "CUMULATIVE_ACTIVE_MENU",
                "recipe_count_basis": "CUMULATIVE_ACTIVE_MENU",
                "time_origin": "SESSION_ORIGIN",
                "minute_mode": "DECIMAL_VERSIONED_CONTRACT_REQUIRED",
                "history_projection": "RETAIN_ACTUAL_HISTORY_AT_SESSION_ORIGIN",
                "serial_baseline": "SESSION_MENU_WITH_HISTORY_CONTEXT",
                "unresolved_protocol_refs": [
                    "U01",
                    "U02",
                    "U03",
                    "U04",
                    "U05",
                    "U06",
                    "U07",
                    "U11",
                    "U12",
                ],
            },
        },
    }


def protocol_questions() -> dict[str, Any]:
    rows = (
        ("U01", "PARTIALLY_CLARIFIED", "建议 task_id 关联多轮任务", "传递位置、必填性、生命周期"),
        (
            "U02",
            "INPUT_CLARIFIED_OUTPUT_OPEN",
            "首轮后单菜输入为追加",
            "多项输入、重复菜和响应范围",
        ),
        ("U03", "OPEN", "不要求插入时刻", "评测的执行反馈或模拟推进方式"),
        ("U04", "OPEN", "独立初排以接收时刻开始", "重排原点、历史区间和跨日表达"),
        ("U05", "OPEN", "旧静态规范按请求长度计数", "累计菜单计数及串行基线、timeSave"),
        ("U06", "OPEN", "存在两个同名菜的真实 ID", "响应仅用名称时如何区分实例"),
        ("U07", "OPEN", "用户批准秒级工艺的小数分钟投影", "设备整数分钟类型的官方兼容方式"),
        ("U11", "OPEN", "优秀初排小于5秒、重排小于3秒", "预热、网络、排队及模型计时口径"),
        ("U12", "OPEN", "成功 JSON 仅五个顶层字段", "HTTP 错误、通知和动态版本传输"),
    )
    return {
        "status": "OFFICIAL_CONFIRMATION_PENDING",
        "development_profile": DEVELOPMENT_PROFILE,
        "items": [
            {"id": key, "status": status, "known": known, "open": question}
            for key, status, known, question in rows
        ],
        "does_not_block": ["P5-01", "P5-03", "EXPLICIT_DEVELOPMENT_PROFILE"],
        "cannot_claim": ["OFFICIAL_DYNAMIC_INTEGRATION", "REAL_HARDWARE_VERIFICATION"],
    }


def api_scenarios(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    by_name: dict[str, list[dict[str, str]]] = {}
    selections = [{"id": row["id"], "name": row["name"]} for row in catalog]
    for row in selections:
        by_name.setdefault(row["name"], []).append(row)
    same_name = by_name["麻辣对虾"]
    fixtures = {
        "initial_three": {"valid_request": True, "body": selections[:3]},
        "initial_single": {"valid_request": True, "body": selections[:1]},
        "add_fourth": {"valid_request": True, "body": selections[3:4]},
        "add_fifth": {"valid_request": True, "body": selections[4:5]},
        "same_name_two_ids": {"valid_request": True, "body": same_name},
        "integer_without_mapping": {
            "valid_request": False,
            "body": [{"id": 1001, "name": selections[0]["name"]}],
        },
        "unknown_id": {
            "valid_request": False,
            "body": [{"id": "unknown-p5-recipe", "name": selections[0]["name"]}],
        },
        "wrong_name": {
            "valid_request": False,
            "body": [{"id": selections[0]["id"], "name": "错误名称"}],
        },
    }
    # 这些是必须转成真实后端/浏览器测试的验收说明，当前不执行场景。
    specifications = (
        ("P5-02", "首轮三菜", "initial_three", "新任务初排，五字段，完整三菜覆盖"),
        ("P5-02", "第零秒追加", "add_fourth", "同任务三菜后加一菜，覆盖四菜，2400ms重排预算"),
        ("P5-02", "连续追加", "add_fifth", "四菜后再加一菜，覆盖五菜，保留全部请求身份"),
        ("P5-02", "首轮单菜", "initial_single", "新task_id的单菜仍为初排，4200ms预算"),
        ("P5-02", "不同任务隔离", "initial_three", "两个task_id的菜单、事实和计划分别持久化"),
        ("P5-02", "缺少任务身份", "initial_single", "STATIC_ARRAY_V1独立初排，不猜测旧任务"),
        ("P5-02", "已有任务多菜", "initial_three", "已有task_id多项输入明确拒绝，不改变菜单"),
        ("P5-02", "结束任务复用", "add_fourth", "结束任务拒绝追加，新一轮使用新task_id"),
        ("P5-02", "同键同内容", "add_fourth", "返回同一请求结果，不重复事件或加菜"),
        ("P5-02", "同键异内容", "add_fifth", "冲突且不落新事件，去重先于版本判断"),
        ("P5-02", "无键重复菜", "initial_single", "已在菜单的同ID拒绝，不推定第二份"),
        ("P5-02", "并发首次调用", "initial_three", "事务只创建一个任务会话及一个请求效果"),
        ("P5-02", "提交后响应丢失", "add_fourth", "重试查询持久请求/发布身份，返回已提交结果"),
        ("P5-02", "任务重启恢复", "add_fourth", "重启保持任务映射、请求结果和固定知识版本"),
        ("P5-02", "输出范围冲突", "add_fourth", "开发累计count=4，静态请求count=1，校验显式分开"),
        (
            "P5-02",
            "秒到小数分钟",
            "initial_three",
            "15秒→0.25分钟、20秒→0.333333，全部字段回读一致",
        ),
        ("P5-02", "会话原点跨日", "add_fourth", "日期/数值时间保存，实际历史不负投影或重复加工"),
        ("P5-02", "同名不同身份", "same_name_two_ids", "两个麻辣对虾按真实ID保留并完整覆盖"),
        ("P5-02", "未知整数映射", "integer_without_mapping", "拒绝未知整数映射，不按CSV行号转换"),
        ("P5-02", "未知菜谱ID", "unknown_id", "拒绝未知ID，不调用实时模型编造菜谱"),
        ("P5-02", "名称不符", "wrong_name", "精确名称校验失败，不落账或发布"),
        ("P5-03", "无反馈不推进", "add_fourth", "只调用排程未提交反馈，旧需求均待执行"),
        (
            "P5-03",
            "人工完成冻结",
            "add_fourth",
            "经内部事件完成后追加，冻结实际时间并释放对应主动占用",
        ),
        ("P5-03", "腌制和热批次冻结", "add_fourth", "实际起点扣等待，保留运行资源、成员和内部阶段"),
        ("P5-03", "设备恢复未释放", "add_fourth", "恢复不清腔体，释放匹配execution_id"),
        ("P5-03", "求解中收到事件", "add_fourth", "事件可落账，旧版本发布拒绝，最新状态有限重算"),
        ("P5-03", "重排失败", "add_fourth", "已接受加菜事实保留，不能返回失效旧计划作为成功"),
        ("P5-01", "停库冷启动", "initial_three", "Neo4j关闭时依靠固定快照、SQLite、工作进程启动"),
        ("P5-01", "缺失固定知识", "initial_three", "ready失败，不切换最新版本或伪造就绪"),
        ("P5-01", "求解期间响应", "initial_three", "健康、读历史、事件及SSE可响应，不阻塞事件循环"),
        ("P5-05", "通知撤销与重连", "add_fourth", "旧未发提醒撤销，发送历史保留，游标重放去重"),
        ("P5-04", "语言模糊", "initial_three", "模糊文本只请求澄清，模型不直接排程或落假定事件"),
        ("P5-04", "语言超时与禁用", "add_fourth", "语言入口诚实失败；普通API/比赛请求继续运行"),
        (
            "P5-06",
            "真实甘特图",
            "same_name_two_ids",
            "Vue展示已发布数值计划，资源/菜品切换和ID选择正确",
        ),
        ("P5-07", "反馈重复点击", "add_fourth", "浏览器复用幂等身份，确认后更新，显示真实重排差异"),
        ("P5-08", "浏览器全链路", "add_fourth", "真实后端初排→反馈→加菜→通知→重启，不用mock成功"),
        ("P5-08", "独立食材依赖", "initial_three", "独立切配可换序，同食材先切后焯保留，人工互斥"),
        (
            "P5-08",
            "入口统一预算",
            "add_fourth",
            "HTTP接收至序列化共用截止时间，排队/重试不重置预算",
        ),
    )
    cases = [
        {
            "id": f"P5-S{index:02d}",
            "task": task,
            "title": title,
            "status": "DESIGN_ONLY_NOT_EXECUTED",
            "input_source": "FIXED_DEVELOPMENT_RELEASE",
            "fixture": fixture,
            "profile": STATIC_PROFILE if index == 6 else DEVELOPMENT_PROFILE,
            "preconditions": (
                "没有既有业务任务，使用新的task_id；无task_id案例使用静态profile"
                if index in {1, 4, 5, 6, 12, 18, 19, 20, 21, 28, 29, 34, 36, 37}
                else "先以initial_three建立活动任务meal-001；按标题注入明确反馈/异常/网络故障"
            ),
            "expected_invariant": assertion,
            "requires_real_backend": True,
            "requires_browser": task in {"P5-06", "P5-07"} or index == 36,
            "execution_evidence": None,
        }
        for index, (task, title, fixture, assertion) in enumerate(specifications, start=1)
    ]
    return {
        "status": "DESIGN_ONLY_NOT_EXECUTED",
        "fixtures": fixtures,
        "cases": cases,
        "progress_feedback_sources": ["MANUAL_CONFIRM", "SIMULATED"],
        "future_simulator_disturbances_visible_to_solver": False,
        "official_dynamic_test_passed": False,
    }


def gate_design(formal_p5_entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "PLANNED_NOT_REGISTERED",
        "name": "P5:development",
        "implementation_status": "NOT_STARTED",
        "required_tasks": list(TASK_IDS),
        "required_prerequisite": "P4:development_ACTUAL_PASSED_REPORT_AND_AUTHORIZATION",
        "required_preparation": "HASH_BOUND_P5_PACKAGE",
        "preserved_formal_p5_entry": formal_p5_entry,
        "browser_requires_real_backend": True,
        "missing_required_test_is_failure": True,
        "skipped_required_test_is_failure": True,
        "live_llm_status": "NOT_EXECUTED_SEPARATE_FROM_FIXED_PROVIDER_TESTS",
        "official_dynamic_status": "NOT_EXECUTED_PROTOCOL_OPEN_ITEMS",
    }
