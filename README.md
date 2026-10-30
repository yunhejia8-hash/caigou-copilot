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
```

已在独立全新数据库通过 21 项核心异常和 7 项普通业务检查，合成记录在 [verification](verification)。另有 8 项源码/部署检查和 12 项规则引擎测试通过。前端资源构建成功，新站点 HTTP 返回 200。全新部署验收修复了队列 YAML 参数与 Global Defaults 原生默认值同步问题。

覆盖销售缺货→采购申请→采购订单→收货→发货、现货交付、发票及收付款、生产领料完工、审批驳回重提、修改后重审、既有预留保护和重复执行保护。序列号/批次只验证基础查询，未覆盖其全部实物流转。业务测试回滚测试变更，保留 MAIN/TEST 合成基础记录。

销售与生产冲突由负责人选择，批准后才执行；供应商“可能先发 200”不计为确定库存。已有单据主动申请协同审批后才强制审批，并非所有原生单据都自动进入审批。

总控对话主页、四模块 Agent 和面板在此基线上传后单独实施。初始裁剪审计脚本 `tools/build_focus.py`、`finalize_focus.py`、`prune_optional_paths.py` 不应重新运行覆盖当前源码；`apps` 是后续维护对象。详见 [DELIVERY.md](DELIVERY.md)。
