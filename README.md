# 专项资金分摊与舍入尾差复核

        面向一线复核人员的单服务数据工作台。样例数据随仓库提供，结论保留行号、原因码和原值。

        ## 业务模块

        - M1 预算采信：`POST /api/budgets/import`，版本列表 `GET /api/budgets/versions`
- M2 资格校验：`POST /api/eligibility/evaluate`
- M3 权重测算：`POST /api/weights/calculate`
- M4 金额分摊：`POST /api/allocations/calculate`，批次列表 `GET /api/allocations/batches`，恢复 `POST /api/allocations/batches/{batch_id}/restore`
- M5 尾差裁决：`POST /api/remainders/resolve`，按批次回看 `GET /api/remainders/{review_id}`
- M6 批次复核：`GET /api/export/allocations.csv?batch_id=...`，批次对账 `GET /api/runs`

        ## 核心规则

        先迭代封顶再按有效权重分配，最后用最大余额法落到分；输入顺序变化不得改变结果。

        ### 单位与总额

        - 导入同时接受`元`与`万元`；`万元`按 1 万元 = 10000 元折算，一律以元入账。
        - 来源单位与来源金额原文保留在快照里，页面与导出按单位分列合计（元两位、万元六位）。
        - 分摊基数 = 当前批次绑定采信版本的预算合计；封顶后未分配余额单独列出。

        ### 采信版本与批次

        - 每次导入追加一个不可变采信版本；同一摘要重试复用原版本，不重复入账。
        - 上游修正后旧版本保留并可回看，`parent_version_id` 串起修正链。
        - 分摊批次绑定生成时的采信版本；重算保留旧批次，新批次 `parent_batch_id` 指向它。
        - 过期版本（非当前最新采信版本）拒绝发布新批次与导入，返回 `STALE_VERSION`。
        - 批次保存失败整体回滚，指针回到上一已保存批次；同幂等键重试不新增批次。
        - 切回旧批次用 `restore`，只移动当前指针，不新增行、不改写历史结果。

        ### 尾差复核

        - 尾差复核始终按批次绑定的采信版本回看当时采信值，最新来源只纠正新批次分摊。
        - 每行给出舍入尾差（分摊到分 − 精确分摊），合计恒为 0；`tail_abs_total` 为需调整量。
        - 同一批次重复出具复核复用原 `review_id`，不重复入账。

        ## 启动

        ```bash
        docker compose up --build
        ```

        浏览器访问 `http://localhost:18213`。运行测试：

        ```bash
        docker compose exec -T app pytest -q
        ```

        ## 样例流程

        依次运行六个模块；最后在批次页复核输入、有效、隔离与输出合计。同一输入摘要重复执行时复用原 `run_id`。样例导入后修改金额可观察旧版本、旧批次的回看行为。

        ## 已知不覆盖

        不处理加密文件与需要外部密钥的对象；不连接真实生产存储。
