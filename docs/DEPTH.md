# 深度登记 专项资金分摊与舍入尾差复核

        - 规模：中型
        - 核心规则：先迭代封顶再按有效权重分配，最后用最大余额法落到分；输入顺序变化不得改变结果。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 预算采信 | 导入元/万元预算，复核采信版本 | `/api/budgets/import` `/api/budgets/versions` / `/budgets` | `app/modules/m1_budgets/core.py` `app/modules/m1_budgets/service.py` | `tests/test_m1_budgets.py` `tests/test_m7_versions.py` |
| M2 资格校验 | 运行样例并复核证据 | `/api/eligibility/evaluate` / `/eligibility` | `app/modules/m2_eligibility/core.py` `app/modules/m2_eligibility/service.py` | `tests/test_m2_eligibility.py` |
| M3 权重测算 | 运行样例并复核证据 | `/api/weights/calculate` / `/weights` | `app/modules/m3_weights/core.py` `app/modules/m3_weights/service.py` | `tests/test_m3_weights.py` |
| M4 金额分摊 | 按采信版本重算分摊，保留旧批次 | `/api/allocations/calculate` `/api/allocations/batches` / `/allocations` | `app/modules/m4_allocations/core.py` `app/modules/m4_allocations/service.py` | `tests/test_m4_allocations.py` `tests/test_m7_versions.py` |
| M5 尾差裁决 | 按批次绑定版本出具尾差复核 | `/api/remainders/resolve` / `/remainders` | `app/modules/m5_remainders/core.py` `app/modules/m5_remainders/service.py` | `tests/test_m5_remainders.py` `tests/test_m7_versions.py` |
| M6 批次复核 | 复核单位/总额/尾差并导出 | `/api/export/allocations.csv` `/api/runs` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` `tests/test_m7_versions.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_budgets/core.py` | `tests/test_m1_budgets.py::test_trap_1` |
| TRAP-06 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_eligibility/core.py` | `tests/test_m2_eligibility.py::test_trap_1` |
| TRAP-07 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_weights/core.py` | `tests/test_m3_weights.py::test_trap_1` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_allocations/core.py` | `tests/test_m4_allocations.py::test_trap_1` |
| TRAP-10 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_remainders/core.py` | `tests/test_m5_remainders.py::test_trap_1` |
| TRAP-14 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-19 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_budgets/core.py` | `tests/test_m1_budgets.py::test_trap_2` |
| TRAP-22 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_eligibility/core.py` | `tests/test_m2_eligibility.py::test_trap_2` |
| TRAP-24 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_weights/core.py` | `tests/test_m3_weights.py::test_trap_2` |
| TRAP-26 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_allocations/core.py` | `tests/test_m4_allocations.py::test_trap_2` |
| TRAP-27 | 元/万元混收，页面对不上总额 | 直接相加或只显示折算值 | 折算入账并保留来源单位，按单位分列合计 | `app/modules/m1_budgets/core.py` `app/templates/m1_budgets.html` | `tests/test_m7_versions.py::test_units_normalized_and_totals_exposed` |
| TRAP-28 | 上游修正后原地替换预算 | 覆盖旧值，审计断链 | 追加不可变采信版本并串接父版本 | `app/store.py` `app/modules/m1_budgets/service.py` | `tests/test_m7_versions.py::test_upstream_correction_appends_version` |
| TRAP-29 | 完全冻结沿用错误基数 | 拒绝一切修正 | 重算生成新批次，旧批次保留绑定版本 | `app/modules/m4_allocations/service.py` | `tests/test_m7_versions.py::test_recalculation_keeps_old_batch_and_creates_new` |
| TRAP-30 | 尾差复核被最新来源改写 | 复核时读最新预算 | 复核按批次绑定版本回看当时采信值 | `app/modules/m5_remainders/service.py` | `tests/test_m7_versions.py::test_remainder_review_uses_bound_version` |
| TRAP-31 | 重试重复入账或丢批次 | 无幂等键或先删后写 | 事务回滚 + 幂等键复用 + 指针恢复 | `app/store.py` | `tests/test_m7_versions.py::test_write_failure_restores_saved_position` |
| TRAP-32 | 过期版本仍可发布 | 只校验版本存在 | 非当前最新采信版本拒绝发布 | `app/modules/m4_allocations/service.py` `app/modules/m1_budgets/service.py` | `tests/test_m7_versions.py::test_stale_version_rejected_for_publish` |

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。
        - 采信版本只追加不覆盖；同一摘要重试复用原版本，`version_seq` 不跳号。
        - 分摊批次绑定采信版本；重算新增批次并保留旧批次，同一幂等键不新增行。
        - 行级尾差合计恒为 0；分摊合计 + 未分配余额 = 分摊基数。
        - 尾差复核按批次唯一，重复出具复用原 `review_id`，内容取自当时采信版本。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。
        - 不提供跨批次合并对账；一次只复核一个绑定了采信版本的批次。
