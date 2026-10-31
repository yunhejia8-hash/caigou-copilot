# 本地采购需求功能合并验收

基线：GitHub main `881d6c47482385a71ec72fa24c39e9a58f200be4`，已包含 PR #1、#2。

目标：将本地最新采购需求登记、行动报告及弹窗交互改动整合到上述主线，保留并行讨论、查询来源与失败历史、独立 intake 功能。验收在独立工作副本和端口 8083 的隔离数据库完成，不更新正在运行的应用。

## 实现与边界

- 明确采购需求且资料齐全时，总控委派需求 Agent 或采购 Agent 受控保存真实 Material Request 草稿，返回可打开的单号。校验数量、日期、公司、仓库、单位及当前账号权限；不自动提交、批准或选择供应商。
- 需求单提交后可准备询价草稿，选择有效供应商报价后准备采购草稿，审批与提交保持人工处理。
- 行动报告区分分析、阻塞、待打开草稿和已保存单据。模型失败后仍保留实际保存的单据及审计。
- 并行仅用于可独立执行的模块委派，建单与行动报告仍遵循依赖顺序。
- 修复已保存单据但任务中断、过期转失败后重试重复建单：同账号、同会话从各状态的持久化审计复用真实单据。
- 按实际工具白名单拼装提示词，合成讨论不要求未提供的行动或建单工具。

## 可复现验证

| 检查 | 结果 |
| --- | --- |
| `python -B -m unittest discover -s tests -q` | 8 通过 |
| `PYTHONPATH=apps/suke_ops python -B -m unittest discover -s apps/suke_ops/tests -q` | 51 通过 |
| `node --test tests/test_purchase_chat.cjs tests/test_scenario_demo.cjs` | 17 通过 |
| `node tools/verify_chat_interactions.cjs` | 通过，含采购请求路由、输入法、页面切换和会话保留 |
| `bench --site focus.local execute suke_ops.demand_integration.run` | 9 通过 |
| `bench --site focus.local execute suke_ops.hub_integration.run` | 25 通过 |
| `bench --site focus.local execute suke_ops.pr_integration.run` | 5 通过 |
| `bench --site focus.local execute suke_ops.compat_integration.run` | 4 通过，使用真实 commit，含中断后重试 |
| `bench --site focus.local execute suke_ops.integration.run` | 21 通过 |
| `bench --site focus.local execute suke_ops.simple_integration.run` | 7 通过 |
| `bench build --app suke_ops` + `python tools/verify_assets.py --url http://127.0.0.1:8083` | 构建成功，56 项资源检查通过 |
| 真实模型合成讨论 `discuss(evidence(0, True), ...)` | 10 步全部成功、40 事件、10 次模型调用、四部门并行，约 10.5 秒 |
| 规范轴与需求轴只读复核 | 未发现剩余阻塞项 |

提示词回归和中断后重试回归均先在修复前复现失败，再修复并通过。数据库回归使用真实 Frappe/ERPNext 和隔离数据库，业务模型响应为测试桩；另一次真实模型检查仅使用合成证据，不读写业务库。没有对生产部署或所有可能输入作零风险保证。

本次证据见同目录 `local-release-*-20261031.json`。另保留原本地的四份需求/行动验收记录，作为历史证据。原项目 9 个相关文件哈希仍与开始验收时一致，用户未提交改动未被覆盖。仅发布源码和验收材料，不包含模型凭据、运行配置或数据库。
