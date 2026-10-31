# 模块工作台验收

- 左侧采购、销售、库存、生产四入口全部逐一点击通过，各自进入 operations-buying / selling / stock / manufacturing，Agent 身份正确。
- 全局数据库存卡片实际跳转到 /desk/stock/operations-stock，不再只筛选同页。
- 每个工作台同时包含独立模块会话、指标、明细及问题区。模块切换不受分析按钮禁用影响。
- 库存显示真实现存量、预留、预计数量、预留明细、质检和收货记录。质检数据为空时显示暂无记录，入口可打开质检列表。
- 模块分析完成后自动成为当前账号的最新汇报，总控 read_module_reports 工具可读取汇报及四模块当前面板。真实数据库 + 模拟模型链路检查通过；跨用户汇报隔离通过。
- 当前按打开、刷新或发送分析触发，不包含后台定时自主巡检；总控输出协调决策建议，实际审批与执行业务仍由负责人完成。
- 33 项离线检查、15 项数据库集成检查、发送与导航交互检查通过。截图见 stock-workspace-v2.png、control-workspace-v2.png；真实点击记录见 module-navigation-v2.json。

复现：

```powershell
$env:PYTHONPATH = (Resolve-Path apps/suke_ops).Path
python -B -m unittest discover -s apps/suke_ops/tests -v
python -B -m unittest discover -s tests -v
node tools/verify_chat_interactions.cjs
docker compose exec -T backend bench --site focus.local execute suke_ops.hub_integration.run
```

已有站点部署后运行 migrate 导入新增 Page，并执行 suke_ops.setup.customize_navigation 应用四模块导航。前端资源使用明确版本标识，防止旧缓存继续显示过时界面。仓库名称未改，代码尚未推送远端。
