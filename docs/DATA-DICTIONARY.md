# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |
| `version_id` | string | 否 | 采信版本编号（不可变，派生自快照摘要） |
| `version_seq` | integer | 否 | 采信版本追加序号，越大越新 |
| `parent_version_id` | string | 是 | 上游修正前的采信版本；首批为空 |
| `digest` | string | 否 | 采信快照摘要，用于幂等重试 |
| `batch_id` | string | 否 | 分摊批次编号（不可变） |
| `batch_seq` | integer | 否 | 批次追加序号 |
| `parent_batch_id` | string | 是 | 本次重算所替代的上一批次 |
| `idempotency_key` | string | 否 | 批次幂等键（版本 + 参数 + 规则版本） |
| `batch_status` | string | 否 | `active` 当前批次 / `superseded` 已被重算替代 |
| `version_status` | string | 否 | `current` 最新采信版本 / `superseded` 已被修正 |
| `review_id` | string | 否 | 尾差复核编号，按批次唯一 |
| `reviewed_version_id` | string | 否 | 复核所绑定的采信版本（当时采信值） |
| `source_unit` | string | 否 | 来源单位：`元` 或 `万元` |
| `source_amount` | string | 是 | 来源金额原文（保留千分位等原始写法） |
| `amount_yuan` | string | 否 | 折算到元后的金额，两位小数 |
| `unit_totals` | object | 否 | 按来源单位分列合计：元两位小数，万元六位小数 |
| `base_yuan` | string | 否 | 分摊基数：当前批次绑定版本的预算合计（元） |
| `allocated_total` | string | 否 | 分摊到分后的合计（元） |
| `unallocated` | string | 否 | 封顶后未能分配的余额（元） |
| `remainder` / `tail` | string | 否 | 行级舍入尾差：舍入后分摊 − 精确分摊 |
| `remainder_total` | string | 否 | 尾差合计，最大余额法下为 0 |
| `tail_abs_total` | string | 否 | 行级尾差绝对值合计，用于复核调整量 |

金额与计量值用 `Decimal`；万元与元的折算固定为 1 万元 = 10000 元，采信后一律以元存储，页面与导出同时显示来源单位。字符串编号逐字保留。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。
