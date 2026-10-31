# 将 procurement-agent 接入原采购 Agent

复用 ivvli/procurement-agent 的后端能力，在现有采购对话中执行需求提取、基础比价和联网查价。沿用现有界面、队列、会话权限和人工确认流程。

## 已接入

原采购 Agent 及需求/询价/比价专项 Agent 的工具白名单新增：

- `procurement_intake`：原仓库 `chat_service.extract / validate_fields`，从本人对话原文提取需求并提示缺项。
- `procurement_compare`：原仓库 `model_quotes / local_quotes / validate_quote / total / issues`，提取供应商报价、核对规格单位数量税运交期、计算含税到仓金额。同口径完整报价至少两份才输出最低价候选；有效期、付款未知时提示核实。
- `procurement_search`：原仓库 `response` 执行 web_search，要求真实搜索调用和来源引用；仅发送物料、规格、数量、单位，不把网上价格当供应商承诺。

工具运行在现有 Frappe 后台队列中，使用同一 Agent Turn、本人会话、原轮询和原回答区域。原文、提取结果和计算结果保存在本人 Agent Turn.result.procurement 中。报价来自用户最近一条带“供应商：”标记的消息，修改时应提供完整新版；需求取本轮和最近4轮用户描述，缺失或矛盾继续追问。

当前是文字交互接入。原仓库的附件上传/OCR、SQLite历史导入、订单记录和库存流水未挂到ERP；不运行原HTTP服务或初始化SQLite，不复制独立网页，不声称已生成ERP正式单据。ERP原有只读查询、原生草稿、权限和审批继续使用既有流程。

## 源码与更新

`apps/suke_ops/suke_ops/vendor/procurement_agent` 是用户本地发布仓库的后端快照（包括前次修复的搜索代码），仅将内部导入改为包内相对导入。`SOURCE.json` 保存源仓库、基础提交与每个源文件SHA-256。只复制四个Python源码文件，不带.env、密钥、数据库、前端或真实资料。桥接层是 `procurement_bridge.py`；后续仓库更新需核对哈希、重做包内导入并运行测试，不会自动追踪远端版本。

本接入仅提供原仓库的基础比价，不包含质量、履约或综合评分。

## 模型配置

保留原 Anthropic 配置优先。未配置时，可使用已有采购仓库的 `OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL`，通过 Responses 适配器接入原工具循环。凭据仅在本地.env和容器环境，禁止提交Git或输出到日志。没有新模型或新供应商，沿用用户原配置。用户对话及其有权限读取的工具结果会发送给所配置模型服务。

Responses 适配器保留 function_call、call_id、reasoning输出和function_call_output，服务端继续做工具白名单与参数检查。参考：https://developers.openai.com/api/docs/guides/function-calling

## 验证与部署

```sh
PYTHONPATH=apps/suke_ops python3 -m unittest discover -s apps/suke_ops/tests -q
python3 -m unittest discover -s tests -q
docker compose build backend
docker compose up -d --no-deps backend worker
```

本次未修改前端静态文件。镜像包含后端快照，重启容器后接入仍保留。无需新建业务数据表。

自动回归：36项后端/工具测试、8项源码部署检查通过；原静态界面文件与任务开始时提交一致。

原8081对话验收：虚构A厂报价计算为1850元，B厂2000元；实际工具 trace 为 procurement_compare，结果记录 backend=ivvli/procurement-agent。没有创建ERP单据。连续对话按每条用户需求增量解析，重试/查价指令不重新拼成需求。

原8081联网验收：同一会话追问网上价格，procurement_search 返回公开来源，reference_only=true；匹配规格/采购数量不足时明确说明，不当成供应商正式报价。
