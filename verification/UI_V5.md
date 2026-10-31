# 第五版：独立主页与原地对话

- 新增独立 Supply Home 模块和“主页”侧栏，位于采购前。仅显示总控主页、数据总览。
- 采购/销售/库存/生产只保留各自工作台和数据入口，不再包含总控主页。采购入口改为采购数据。
- 主页顶部总控/采购/销售/库存/生产及右侧模块卡片均为页内对话按钮，URL和主页侧栏不变。
- 每个Agent使用独立组件实例，保留各自历史、草稿和分析状态；切换不会修改正在运行的请求目标或将回复写到另一对话。输入框ID独立，避免多个对话的标签冲突。
- 旧总控路径重定向到 /desk/主页/operations-hub；采购旧数据总览路径重定向到 /desk/主页/operations-data。业务专属数据页保留。

## 验证

33项离线测试、20项真实数据库集成检查及交互检查通过。新增检查覆盖独立模块注册、主页导航首位、四业务模块仅两个入口、页内切换保留分析状态、采购消息发送到 buying Agent。数据库模型链路采用模拟响应，本次无新增真实模型请求。

浏览器实际点击验证：

- 四业务模块可见总控主页链接数均为0，采购数据页面仅显示采购相关数据。
- 主页切换采购、销售、库存、生产和总控，地址始终为 /desk/主页/operations-hub。
- 采购输入测试草稿后切到销售，再回采购，草稿仍在；验收后已清空测试草稿，没有发送。
- 右侧采购卡片也在主页切到采购Agent。
- 主页数据总览包含四模块概况，并与总控对话共用主页侧栏。

证据：hub-ui-v5.json、home-navigation-v5.json、home-chat-v5.png、home-data-v5.png。

部署在本机8081，尚未提交或推送GitHub。原业务数据和历史对话保留。

## 复现

```powershell
node tools/verify_chat_interactions.cjs
$env:PYTHONPATH = (Resolve-Path apps/suke_ops).Path
python -B -m unittest discover -s apps/suke_ops/tests
python -B -m unittest discover -s tests
docker compose exec -T backend bench --site focus.local execute suke_ops.hub_integration.run
```

更新已有站点后运行 `bench --site focus.local migrate` 和 `bench --site focus.local execute suke_ops.setup.customize_navigation`，刷新浏览器。
