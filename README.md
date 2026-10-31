# caigou-copilot

面向采购人员的 AI 工作台，联动订单、库存与生产数据，帮助跟进交期、识别缺料风险、比较采购方案并辅助决策。

- **总控主页**：汇总各模块上报和待跟进问题，提出跨模块协调与决策建议。
- **模块工作台**：采购、销售、库存、生产各有独立 Agent 对话和数据看板；全局数据页提供跨模块概览。
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

已在独立全新数据库通过 21 项核心异常和 7 项普通业务检查，合成记录在 [verification](verification)。另有 8 项源码/部署检查和 25 项规则引擎/Agent 工具测试，以及 15 项 Agent 数据库集成检查通过。静态资源检查逐项请求框架、业务及工作台脚本和样式，防止只编译业务应用造成登录或手动表单资源缺失。全新部署验收修复了队列 YAML 参数与 Global Defaults 原生默认值同步问题。

覆盖销售缺货→采购申请→采购订单→收货→发货、现货交付、发票及收付款、生产领料完工、审批驳回重提、修改后重审、既有预留保护和重复执行保护。序列号/批次只验证基础查询，未覆盖其全部实物流转。业务测试回滚测试变更，保留 MAIN/TEST 合成基础记录。

销售与生产冲突由负责人选择，批准后才执行；供应商“可能先发 200”不计为确定库存。已有单据主动申请协同审批后才强制审批，并非所有原生单据都自动进入审批。

## 使用采购助手

登录后打开 `/desk/buying/operations-hub`。左侧保留采购、销售、库存和生产入口，每个模块提供工作台、总控主页和全局数据入口，数据页地址为 `/desk/buying/operations-data`。模块入口跳转到独立工作台，分别保存会话；总控按问题协调相关模块。

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

## 供应异常单内采购对话

在已保存的 `Supply Case` 表单工具栏点击 **采购对话**，无需跳转即可向现有采购 Agent 提问，例如“现在缺多少料？”、“比较销售优先与生产优先”或“有哪些到货条件还需要确认？”。每次提问带入当前异常单号，由现有服务端工具读取最新数据并校验权限。未保存的修改须先保存。

弹窗复用协同主页的真实模型、工具白名单、后台队列和草稿入口；不引入原型的固定口令解析器或合成订单。会话按当前账号和异常单隔离，关闭后重新打开可继续；未配置模型时发送不可用，不伪造回答。模型只查询、分析及准备现有工具支持的未保存草稿，方案选择、审批与执行仍回原页面进行。部署前沿用上文模型配置及业务数据外发授权要求。

更新后运行 `bench build --app suke_ops` 并清理站点缓存，或按上文 Docker 构建流程部署。前端回归测试：`node --test tests/test_purchase_chat.cjs`。测试无需模型凭据；真实 Frappe 页面与模型联调需部署环境验收。


## 采购订单对话与六类协同演示

已保存的采购订单工具栏新增「到货跟踪与采购对话」，直接带入订单号查询交期、收货、关联需求与费用依据；到货跟踪仍归属采购订单，不新增业务模块。保留 Supply Case 内「采购对话」，兼容独立模块工作台、快捷问题直发和按模块/单据隔离的会话。服务端沿用原有权限与工具限制，界面上下文提示不是权限机制。

在总控或采购工作台右侧点击「采购异常协同演示」，或访问 `/assets/suke_ops/scenario-demo/index.html`。六个案例为连接器延期争料、替代料未批准、客户批次限制、短收及质检冻结、需求减少导致超采、销售与生产需求重复计算。可逐条回放采购/销售/库存/生产反馈与总控建议、注入首例新证据、调整加急量试算、记录模拟待审批草案并导出 JSON。

**数据全部合成，对话是预设回放，不是真实模型输出，不写入 ERP。** 这套案例与上文 `DEMO1031` 实测数据独立。第一例自由库存为 460−180−60=220；加急150件的增量费用为150×1.2+300=480元。采购批准、供应确认、生产改期都是待确认前提；480元是额外支出，不是节省。实际收益和人工节时未验证。其余案例展示固定事实与方案，不支持任意参数求解。草案仅存页面内存，刷新会丢失；导出可保留回放记录。

验证：`node --test tests/test_purchase_chat.cjs tests/test_scenario_demo.cjs`、`node tools/verify_chat_interactions.cjs`。新加前端不需要数据库迁移；首次同步本版 main 时仍须执行上文迁移/导航步骤。

## 真实多 Agent 场景讨论

总控主页点击「真实多 Agent 场景讨论」，选择六个合成场景之一。模型按四部门初核 → 总控追问 → 四部门复核 → 总控建议进行十步真实调用，反馈随轮次显示并保存到本人对话。首例可选择补充供应证据。该入口和静态预设回放独立，真实模型不读取预设台词。

场景证据由服务端 `scenario_facts.json` 选择；只暴露只读证据工具，不读取真实 ERP 单据、不提供执行工具。程序核算首例数量与费用；其他案例的模型推理需人工复核。最多30次模型请求、40次工具读取；部门失败显式标记，不能当作完整方案。对话目标也会发往已配置模型平台，请仅输入合成问题。

比赛平台文档：https://credits.openai-next.com/guide/quickstart 。Anthropic 协议地址为 `https://api.openai-next.com`，本地联调用模型 `claude-sonnet-4-6`。密钥只填写本地被忽略的 `.env`，不随仓库分发。保留其他兼容模型服务配置能力。更新后重建 Docker 并执行 main 要求的迁移和导航更新。

真实模型实测曾出现新旧证据及交期混淆，因此首例补充证据后另列程序核算；新证据优先于旧状态，部门回答不作为新业务事实。程序展示不能保证模型解释完全正确，最终审批仍需逐项核实。
首例含补充证据时，最终结论由确定性程序模板提供，模型原话保留为待核实讨论；已测试模型即使误述09:00与10:00的先后顺序，也不能覆盖该程序结论。其余场景暂没有同等的完整程序方案保障。
