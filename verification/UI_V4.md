# 第四版：导航去重与按模块显示数据

## 交互方案与实现

- 左侧只保留工作台、总控主页、数据入口。采购六环节统一在工作台顶部，增加概览入口，保留独立专项会话和工具。
- 采购数据总览呈现四模块概况；销售、库存、生产数据页分别限定本模块。数据页标题、入口名、数据范围一致，不显示跳回各Agent的页签。
- 总览指标卡进入对应模块的数据页，模块数据页提供回到对应Agent讨论的入口。
- 概览按“指标—待关注事项/审批—按需展开明细”排列。专项页保留完整明细，包括库存预留、质检、收货及生产工序。
- 后端 dashboard(view='data', module=...) 只调用指定模块面板，审批按源单据类型过滤；不返回其他模块汇报、全局案例和决策。
- 兼容旧的非采购 operations-data 地址，自动跳转对应模块独立数据页。

## 验证证据

- 25项Agent/规则检查、8项源码/部署检查、19项数据库集成检查通过。发送、输入法、模块身份、数据页指标下钻和明细渲染检查通过。
- 浏览器确认采购询价、物料需求单链接各仅1个，均在工作台顶部。
- 采购数据总览卡片成功进入销售数据页，显示销售订单与发货记录。
- 分别点击销售、库存、生产数据入口，均只显示对应数据；可见Agent切换页签数为0。
- 旧 /desk/selling/operations-data 已实际验证跳转至 operations-data-selling；销售数据与销售Agent可往返。
- 总览4个模块明细默认折叠。
- 记录：hub-ui-v4.json、data-navigation-v4.json。截图：workspace-v4.png、data-overview-v4.png、selling-data-v4.png。

本次只修改导航和数据呈现，没有新增真实模型调用；沿用上一版已验证的Agent行为。已应用到本机8081演示环境，未提交或上传GitHub。

## 复现

```powershell
$env:PYTHONPATH = (Resolve-Path apps/suke_ops).Path
python -B -m unittest discover -s apps/suke_ops/tests
python -B -m unittest discover -s tests
node tools/verify_chat_interactions.cjs
docker compose exec -T backend bench --site focus.local execute suke_ops.hub_integration.run
```

部署更新：构建并启动镜像，运行 bench --site focus.local migrate 与 bench --site focus.local execute suke_ops.setup.customize_navigation，然后刷新浏览器。
