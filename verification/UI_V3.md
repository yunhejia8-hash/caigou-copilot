# 第三版：采购专项 Agent 与独立模块界面

已在本机 http://127.0.0.1:8081 应用，仓库名称未变。本次修改尚未提交或上传 GitHub。

## 交付范围

- 采购左侧保留采购概览，新增物料需求单、询价、比价、审批、采购、入库六个入口。每个入口有独立 Agent、会话标识、历史过滤、职责提示和单据工具范围。
- 六环节分别展示需求订购进度、询价与供方回复、逐物料报价及有效期、采购审批、采购收货进度、入库与来料质检。单据名可打开 ERPNext 真实表单。
- 销售、库存、生产分别只展示本模块 Agent；不再显示其他 Agent 页签或其他模块汇报。必需的指标、明细和风险面板保留。
- 分析记录默认折叠。快捷问题点击直接发送、Enter 发送、Shift+Enter 换行和中文输入保护保留。
- 采购概览可读取六环节进度与汇报；总控可读取专项汇报或委派专项。没有新增无人值守巡检；业务批准及执行仍由负责人操作。

## 验证

33 项离线检查（25 项工具与规则测试、8 项源码和部署测试）通过；18 项真实数据库集成检查通过，其中六专项 Agent 使用模拟模型走真实工具链。无权限统计、会话隔离和越权拒绝均通过。

浏览器逐个点击六个采购环节与销售、库存、生产入口，身份、路由和数据匹配。非采购三模块可见的 Agent 页签数均为 0。需求单 MAT-MR-2026-00003 的明细链接成功进入真实单据表单。记录见 stage-navigation-v3.json，数据库检查见 hub-ui-v3.json。

采购专项还通过现有 DeepSeek 连接完成两次真实分析。第一次识别出订单交期 11/01 与物料行交期 11/04 不一致，但把三个不同物料数量合并，并因面板缺少逾期指标给出前后矛盾说法；原文保留在 stage-agent-live-first.txt。随后增加独立的程序逾期指标、交期差异信号，并明确禁止跨物料合计。第二次原文 stage-agent-live-final.txt 正确引用“待收货3单、逾期0单”，分别列示三个物料，提示交期差异待核实。该实测不代表已全面验证所有模型输出。

当前演示库尚无询价、供应商报价和采购审批记录，相关面板显示暂无记录，没有填充虚构状态。原有业务单据未被 Agent 修改。

截图：procurement-stage-v3.png、stock-workspace-v3.png。

## 复现

```powershell
$env:PYTHONPATH = (Resolve-Path apps/suke_ops).Path
python -B -m unittest discover -s apps/suke_ops/tests -v
python -B -m unittest discover -s tests -v
node tools/verify_chat_interactions.cjs
docker compose exec -T backend bench --site focus.local execute suke_ops.hub_integration.run
```

更新已有环境：

```powershell
docker compose up -d --build
docker compose exec -T backend bench --site focus.local migrate
docker compose exec -T backend bench --site focus.local execute suke_ops.setup.customize_navigation
```

然后刷新浏览器。
