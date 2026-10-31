# 采购、销售、库存、生产协同

基于 ERPNext 16.53.0 的四模块源码发行版，保留上游 GPL-3.0 许可与版权。`apps/suke_ops` 提供跨模块审批、供需分析、模拟供应商议价和异常决策 Agent。

实际移除 CRM、项目、客服、固定资产、维保、独立质量管理、电话、门户等业务目录及 171 种单据，保留 360 种业务与支撑单据。库存估值、会计科目、采购销售结算、税费、主数据和生产委外是必要依赖。完整清单见 [focus_manifest.json](apps/erpnext/erpnext/focus_manifest.json)。服务端拒绝被禁用的业务字段及模块 API。

Docker 最终镜像从空镜像复制裁剪后的文件系统，分发层不携带完整 ERPNext 旧源码。原工作目录和 8080 演示站点不变，新实例使用独立数据库、卷、站点 `focus.local` 和 8081 端口。

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

已在独立全新数据库通过 21 项核心异常和 7 项普通业务检查，合成记录在 [verification](verification)。另有 8 项源码/部署检查和 20 项规则引擎/Agent 工具测试，以及 11 项 Agent 数据库集成检查通过。静态资源检查逐项请求框架、业务及工作台脚本和样式，防止只编译业务应用造成登录或手动表单资源缺失。全新部署验收修复了队列 YAML 参数与 Global Defaults 原生默认值同步问题。

覆盖销售缺货→采购申请→采购订单→收货→发货、现货交付、发票及收付款、生产领料完工、审批驳回重提、修改后重审、既有预留保护和重复执行保护。序列号/批次只验证基础查询，未覆盖其全部实物流转。业务测试回滚测试变更，保留 MAIN/TEST 合成基础记录。

销售与生产冲突由负责人选择，批准后才执行；供应商“可能先发 200”不计为确定库存。已有单据主动申请协同审批后才强制审批，并非所有原生单据都自动进入审批。

第一阶段基线已作为 `60bdaf8` 上传 GitHub；第二阶段已加入总控对话主页、四模块 Agent 和真实业务面板。初始裁剪审计脚本 `tools/build_focus.py`、`finalize_focus.py`、`prune_optional_paths.py` 不应重新运行覆盖当前源码；`apps` 是后续维护对象。详见 [DELIVERY.md](DELIVERY.md)。


## 对话主页与模块 Agent

登录后打开 `/desk/buying/operations-hub`。总控使用 `ask_module` 委派，采购、销售、库存、生产有独立提示词及工具白名单。可以直接切换到某个模块 Agent，查看实时指标、最近业务单据和原手动入口。面板按当前账号权限统计；空权限显示“—”，不会混同为零。

- 采购：待收货、逾期及草稿采购单，采购申请与询报价入口。
- 销售：待交付、逾期及草稿销售单，客户、报价和发货入口。
- 库存：现存仓位、实际预留、未分交期的预计短缺、库存明细和收货入口。
- 生产：未完成、生产中、待启动工单，BOM、计划及工序入口。

模型可以读取面板/单据、比较异常方案，并提供采购申请→采购单、采购单→收货、销售单→发货、工单→完工入库的原生未保存草稿入口。点击时再次校验权限及源单据版本，由用户补全字段、保存、审批和提交。Agent 不自动保存或提交业务单据。

带序列号/批次的物料使用原生手动创建：上游部分映射会提前生成批次记录，当前 Agent 草稿工具明确拒绝该路径。原手动功能不受此限制。争料方案和模拟议价仍在异常页面执行，原审批规则继续生效。

会话仅本人可见；同账号并发任务通过数据库行锁限制，刷新恢复最近 50 轮。未配置模型或调用失败时明确提示，不把规则结果伪装成模型回答。对话采用后台队列，模型调用有轮数、工具数和超时上限。

本次已在用户明确授权后，用 `api.deepseek.com` 对合成数据完成真实总控和四模块协作，记录见 [model-live.json](verification/model-live.json)。工具协议参考 [DeepSeek Anthropic 兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)。部署到其他环境时需自行配置模型服务并明确业务数据的外发授权，仓库不附带凭据。

启动前在 PowerShell 环境中配置上述三个 `ANTHROPIC_*` 变量，或写入仅本地的 `.env`；服务读取后端环境变量。更新代码后执行 `docker compose up -d --build`，如有单据结构变更再运行 `docker compose exec -T backend bench --site focus.local migrate`。


## 供应异常单内采购对话

在已保存的 `Supply Case` 表单工具栏点击 **采购对话**，无需跳转即可向现有采购 Agent 提问，例如“现在缺多少料？”、“比较销售优先与生产优先”或“有哪些到货条件还需要确认？”。每次提问带入当前异常单号，由现有服务端工具读取最新数据并校验权限。未保存的修改须先保存。

弹窗复用协同主页的真实模型、工具白名单、后台队列和草稿入口；不引入原型的固定口令解析器或合成订单。会话按当前账号和异常单隔离，关闭后重新打开可继续；未配置模型时发送不可用，不伪造回答。模型只查询、分析及准备现有工具支持的未保存草稿，方案选择、审批与执行仍回原页面进行。部署前沿用上文模型配置及业务数据外发授权要求。

更新后运行 `bench build --app suke_ops` 并清理站点缓存，或按上文 Docker 构建流程部署。前端回归测试：`node --test tests/test_purchase_chat.cjs`。测试无需模型凭据；真实 Frappe 页面与模型联调需部署环境验收。
