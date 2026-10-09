# 真实样本待审状态

本目录现有12道真实菜谱的V3开发样本，均为NEEDS_REVIEW；目录名称不表示审核已完成。

样本直接承接data/revisions/recipes_v3/scheduling_dataset.json，包含214个原子操作、228条依赖、数值执行时间、物料流、设备预约和原文证据。原始菜谱ID不变。AI补全已完成，但不等于人工批准。

- 审核范围与缺口：../../../data/issues/sample_review.md
- 逐工序审核工作单：../../../data/issues/p0_review_packet.md
- 逐菜修订对照：../../../data/revisions/recipes_v3/修订对照.md

审核者须核对当前版本，留下身份、日期、实际依据、结论及工艺内容哈希。修改工艺后须重新核对新哈希。不能仅改APPROVED字符串、使用AI输出代替人工记录，或把合成用例计入真实样本门槛。

已有人工审核记录的样本受导入脚本覆盖保护。当前0个APPROVED，尚不能通过P0-06和P1 core审核门。
