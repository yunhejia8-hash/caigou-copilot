# caigou-copilot

面向采购人员的 AI 工作台，联动订单、库存与生产数据，帮助跟进交期、识别缺料风险、比较采购方案并辅助决策。

- **总控主页**：汇总各模块上报和待跟进问题，提出跨模块协调与决策建议。
- **模块工作台**：采购、销售、库存、生产各有独立 Agent 对话和数据看板；全局数据页提供跨模块概览。
- **采购六环节**：物料需求单、询价、比价、审批、采购、入库分别配备专项 Agent、独立会话及进度数据。销售、库存、生产只显示本模块 Agent。
- **便捷交互**：快捷问题点击即发送；Enter 发送，Shift+Enter 换行。

采购人员可直接询问“哪些采购订单延期了？”或“连接器延期会影响哪些交付？”。助手根据当前账号可见的业务数据给出依据和下一步建议；业务取舍及审批由负责人决定。供应商议价为模拟功能。

基于 ERPNext，保留上游 GPL-3.0 许可与版权。技术边界及部署记录见 [DELIVERY.md](DELIVERY.md)，依赖清单见 [focus_manifest.json](apps/erpnext/erpnext/focus_manifest.json)。

## 启动

需要 Python 3、Docker Compose 和足够的 Docker 存储空间。首次构建下载基础运行环境并编译资源。

```powershell
python tools/create_env.py
docker compose up -d --build
docker compose ps -a
```

等待 `initialize` 以状态 0 结束，访问 http://127.0.0.1:8081 。用户名 `Administrator`，本机 `.env` 的 `ADMIN_PASSWORD` 是随机生成的登录口令。初始化创建合成演示公司、主数据和 600 件连接器异常。再次启动不覆盖已有公司设置，已有 `.env` 不会被生成脚本覆盖。

口令、数据库、模型凭据、日志、配置备份不入 Git。真实模型服务通过 `ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`、`ANTHROPIC_AUTH_TOKEN` 环境变量配置；未配置时明确提示不可用，规则计算和手动业务仍可使用。供应商议价仅为模拟。

## 可复现验证

```powershell
python -B -m unittest discover -s tests -v
$env:PYTHONPATH = (Resolve-Path apps/suke_ops).Path
python -B -m unittest discover -s apps/suke_ops/tests -v
docker compose exec -T backend bench --site focus.local execute suke_ops.integration.run
docker compose exec -T backend bench --site focus.local execute suke_ops.simple_integration.run
docker compose exec -T backend bench --site focus.local execute suke_ops.hub_integration.run
python tools/verify_assets.py
python tools/verify_realtime.py
```

已在独立全新数据库通过 21 项核心异常和 7 项普通业务检查，合成记录在 [verification](verification)。另有 8 项源码/部署检查和 25 项规则引擎/Agent 工具测试，以及 20 项 Agent 数据库集成检查通过。静态资源检查逐项请求框架、业务及工作台脚本和样式，防止只编译业务应用造成登录或手动表单资源缺失。全新部署验收修复了队列 YAML 参数与 Global Defaults 原生默认值同步问题。

覆盖销售缺货→采购申请→采购订单→收货→发货、现货交付、发票及收付款、生产领料完工、审批驳回重提、修改后重审、既有预留保护和重复执行保护。序列号/批次只验证基础查询，未覆盖其全部实物流转。业务测试回滚测试变更，保留 MAIN/TEST 合成基础记录。

销售与生产冲突由负责人选择，批准后才执行；供应商“可能先发 200”不计为确定库存。已有单据主动申请协同审批后才强制审批，并非所有原生单据都自动进入审批。

## 使用采购助手

登录后打开 `/desk/buying/operations-hub`。左侧保留采购、销售、库存和生产入口，主页提供总控主页与数据总览；四个业务模块只提供工作台和本模块数据入口，数据页地址为 `/desk/buying/operations-data`。模块入口跳转到独立工作台，分别保存会话；总控按问题协调相关模块。

数据面板按当前账号权限统计；无权限显示“—”，不会混同为零。采购关注待收货和逾期订单，销售关注待交付订单，库存区分现存与实际预留，生产关注未完成工单。

模型可以读取面板/单据、比较异常方案，并提供采购申请→采购单、采购单→收货、销售单→发货、工单→完工入库的原生未保存草稿入口。点击时再次校验权限及源单据版本，由用户补全字段、保存、审批和提交。Agent 不自动保存或提交业务单据。

带序列号/批次的物料使用原生手动创建：上游部分映射会提前生成批次记录，当前 Agent 草稿工具明确拒绝该路径。原手动功能不受此限制。争料方案和模拟议价仍在异常页面执行，原审批规则继续生效。

会话仅本人可见；同账号并发任务通过数据库行锁限制，刷新恢复最近 50 轮。未配置模型或调用失败时明确提示，不把规则结果伪装成模型回答。对话采用后台队列，模型调用有轮数、工具数和超时上限。合法批量查询分批执行，相同参数复用本轮查询结果；子模块失败时标注未核实部分，汇总失败时保留已完成的模块结果。

本次已在用户明确授权后，用 `api.deepseek.com` 对合成数据完成真实总控和四模块协作，记录见 [model-live.json](verification/model-live.json)。工具协议参考 [DeepSeek Anthropic 兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)。部署到其他环境时需自行配置模型服务并明确业务数据的外发授权，仓库不附带凭据。

启动前在 PowerShell 环境中配置上述三个 `ANTHROPIC_*` 变量，或写入仅本地的 `.env`；服务读取后端环境变量。更新代码后执行 `docker compose up -d --build`，如有单据结构变更再运行 `docker compose exec -T backend bench --site focus.local migrate`。


更新本版后执行 `docker compose up -d --build`，再运行 `docker compose exec -T backend bench --site focus.local migrate` 导入决策数据页面，并运行 `docker compose exec -T backend bench --site focus.local execute suke_ops.setup.customize_navigation` 应用精简导航。导航调整只收起入口，业务数据和权限继续保留。


## 模块工作台（第二版）

总控主页：`/desk/buying/operations-hub`。采购、销售、库存、生产均有独立工作台，例如库存为 `/desk/stock/operations-stock`，采购为 `/desk/buying/operations-buying`。左侧四模块入口保留，模块页同时显示独立会话和对应数据看板；模块导航与指标卡均为真实页面链接，分析期间也可以切换页面。

模块分析完成后，其结果自动作为当前账号的最新模块汇报保存。总控主页展示各模块程序预警和最新汇报，可点击“汇总上报并研判”读取汇报、核对当前数据并提出协调决策建议。该版本在打开/刷新页面或发起分析时读取数据，不提供无人值守定时巡检；实际批准和业务单据执行仍需负责人确认。

库存看板包含现存量、实际预留、预计数量、预留记录、质检和收货记录。没有记录或无权限时分别明确显示，不生成虚构质检数据。


## 连接器延期场景实测

在独立演示站点执行以下命令，创建带 `DEMO1031` 标识的合成单据并运行真实模型。首次运行前须完成模型服务配置；合成单据会保留在演示数据库中。

```powershell
docker compose exec -T backend bench --site focus.local execute suke_ops.scenario_demo.seed
$env:PYTHONUTF8 = '1'
python -u -B tools/run_scenario_demo.py
```

场景包含现存320件、已锁定120件、重点客户需求300件、生产用料需求300件，以及延期的600件采购和未确认的200件提前批。程序核算两种优先方案均缺400件；模块分析和总控复核的原始回复见 [场景实测记录](verification/connector-scenario-replay.txt)，完整依据见 [场景数据](verification/connector-scenario-demo.json)。记录保留了模型连接失败、日期误述和批次单位歧义等问题；分析完成不等于建议可以自动执行。


## 采购专项工作台（第三版）

采购工作台顶部提供概览和六个专项入口，左侧不再重复展示环节。六个入口地址依次为 `/desk/buying/operations-buying-demand`、`operations-buying-inquiry`、`operations-buying-comparison`、`operations-buying-approval`、`operations-buying-order`、`operations-buying-receipt`（后三段同属 `/desk/buying/`）。

每个专项使用独立职责提示、单据工具范围、会话和最新上报。需求看待采购量与订购进度；询价看询价状态和供应商回复；比价看逐物料单价、币种、单位和有效期；审批只显示采购订单关联审批；采购看交期和收货比例；入库看收货、仓库和来料质检。进度来自业务单据，不把环节单数当成转化率。没有记录时显示暂无记录；普通模块只显示本模块对话及数据。

专项 Agent 给出建议，审批和业务写入仍由人工执行。采购概览 Agent 可读取六环节进度和汇报，总控可读取专项上报或直接委派专项。新增页面需要运行上述 migrate 和 customize_navigation。验收记录见 [第三版验收](verification/UI_V3.md)。


## 导航与数据范围（第四版）

采购左侧只保留“采购工作台、总控主页、数据总览”，六个专项在工作台顶部切换，新增“概览”返回入口。工作台先展示指标与待跟进问题，各环节进度按需展开。

采购的“数据总览”显示四模块指标、待关注事项和审批，模块明细默认折叠。点击指标进入对应模块的完整数据页。销售、库存、生产左侧分别显示“销售数据、库存数据、生产数据”，后台只读取本模块面板与对应单据类型的审批。数据页不再出现 Agent 切换页签，可通过“与本模块 Agent 讨论”进入专项对话。

独立数据页为 `/desk/{module}/operations-data-{module}`，module 可为 buying、selling、stock、manufacturing；总览仍为 `/desk/buying/operations-data`。旧的销售、库存、生产 operations-data 地址自动跳转对应数据页。升级仍需 migrate 导入页面、customize_navigation 更新导航后刷新浏览器。验证见 [第四版验收](verification/UI_V4.md)。


## 独立主页与页内 Agent 对话（第五版）

左侧顺序为主页、采购、销售、库存、生产。主页独立归属 Supply Home 模块，仅含“总控主页、数据总览”；业务模块仅含工作台和本模块数据。采购数据入口为 `/desk/buying/operations-data-buying`。

主页地址为 `/desk/主页/operations-hub`，总览为 `/desk/主页/operations-data`。旧总控地址和采购旧总览地址会重定向到主页。总控页顶部与右侧模块卡片都在当前页切换对话对象，不切换侧栏、不跳往业务工作台。每个对话保留独立实例、会话、输入草稿及分析状态；后台回复不会写入另一个Agent会话。主页内选择采购时不显示采购六环节导航，专项操作通过左侧采购工作台进入。

更新后运行 migrate 和 customize_navigation 并刷新浏览器。验收记录见 [第五版验收](verification/UI_V5.md)。
