# 采购助手第一版验证

仓库名称保留 caigou-copilot，产品文案面向采购提效。

## 已验证

- 25 项规则引擎与 Agent 离线测试通过，另有 8 项源码与部署检查通过。
- 13 项真实 ERP 数据库集成检查通过；模型响应模拟，覆盖四模块委派、同模块换问法重复委派、部分模块失败及汇总失败后的结果保留。
- 56 项静态资源请求通过。
- Node 真实事件处理检查通过：Enter 发送、Shift+Enter 换行、输入法选字保护、快捷问题直接发送、保留输入草稿和防止重复发送。
- 浏览器确认侧栏只有采购助手与决策数据；对话页无指标卡、业务侧栏、工具日志或宣传说明。
- 使用真实模型和合成演示数据完成跨模块摘要，见 live-summary-ui-v1.txt。

## 复现命令

```powershell
$env:PYTHONPATH = (Resolve-Path apps/suke_ops).Path
python -B -m unittest discover -s apps/suke_ops/tests -v
python -B -m unittest discover -s tests -v
node tools/verify_chat_interactions.cjs
docker compose exec -T backend bench --site focus.local execute suke_ops.hub_integration.run
python -B tools/verify_assets.py --output verification/assets-ui-v1.json
```

原始 HEAD 的 agent_runtime.py 在内存中运行同一个七调用回归测试会报“单轮工具调用数量超出限制”，新版通过；未修改原始文件或 Git 历史。

## 证据

- chat-ui-v1.png：简洁对话页。
- data-ui-v1.png：独立决策数据页。
- hub-ui-v1.json：数据库集成验收。
- assets-ui-v1.json：静态资源验收。
- live-summary-ui-v1.txt / .png：真实模型跨模块摘要。

## 部署状态

本地 8081 容器已同步最终代码。基础镜像构建和解包已完成，最终修改通过轻量代码层补齐到同一镜像标签，再以 docker compose up -d --no-build 启动。代码未提交或推送 GitHub。后续按 README 的标准完整构建命令可重建。

真实模型复测中还发现未来交期被称为逾期的表述问题；最终代码在模块面板补充 as_of 业务日期，并要求严格引用后端逾期指标。最终镜像已用真实模型再次运行原问题，返回业务日期 2026-10-31、采购 2 笔、销售 6 笔、生产 2 张，并正确引用采购/销售逾期 0 的程序指标；记录见 live-orders-ui-v1.txt / .png。这属于当前合成场景验收，不代表对所有模型输出事实准确性的保证。

最终服务状态：backend、frontend、worker、scheduler、websocket、db、redis-cache、redis-queue 全部 running，56 项静态资源再次通过。
