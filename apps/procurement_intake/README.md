# 采购需求预审网页（独立本地原型）

提供对话式需求填写、确定性预审、连续澄清、新物料人工复核队列和申请 PDF。独立使用 SQLite，仅监听本机，与原有 ERPNext / suke_ops 并行；不接入其数据库、权限或 Docker 服务。

## 启动

需要 Python 3.10+，在本目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh
```

打开 http://127.0.0.1:8765 ，审核队列位于 `/review.html`。更换端口使用 `./run.sh --port 8766`。Windows 可用虚拟环境 Python 直接运行 `app.py`。

PDF 在 macOS 默认使用系统华文黑体。其他系统将 `PDF_CJK_FONT` 设置为可嵌入中文 TrueType 字体路径；TTC使用第一个字体。字体缺失不会误报生成成功。

修改 Python 文件后必须停止旧进程并重新运行；刷新网页不加载新后端代码。启动将中断任务标为失败，允许重试。

## 数据与模型

仓库不包含运行中的数据库、历史工作簿、用户对话或密钥。首次启动建立空对话库，以及旧版兼容接口所需合成数据；对话流程不读取模拟库存或价格。默认数据库为 `data/demo.db`，可用 `PROCUREMENT_DB` 指定。

先启动一次初始化数据库，再导入自己的获授权工作簿：

```sh
.venv/bin/python scripts/import_history.py /path/to/history.xlsx --price-kind purchase
```

每个工作表首行依次为：`月份、物料编码、实际销量(件)、促销标识、单价(元)、在途量(件)`，月份为 `YYYY-MM`。仅已确认采购进价使用 purchase；sale/unknown 不自动用于采购估价。保留工作表、行号及文件哈希。历史销量不等于库存，历史在途不等于当前按时在途。

默认本地规则提取。启动前设置 `OPENAI_API_KEY` 和 `OPENAI_MODEL` 可使用 Responses 结构化提取，不自动读取 `.env`。仅发送当前描述，不发送历史数据库。真实模型调用尚未联调，测试为本地和模拟调用；模型不决定审批。

不具备登录和正式审批，不直接公网部署；不预留库存或预算，不自动下单，部门预算余额、交期、技术及质量要求仍需人工确认。

## 本次修复

输入以下示例，日期按测试当天调整：

> 我是生产部李明，需要500个五层瓦楞包装箱，规格400×300×250mm，用于500件产品出货，每件一个。预算2000元，要求2026年11月15日前送到一号仓。

500个是申请数量，500件产品、每件一个是用途依据，验证500×1=500，不再误报多个需求数量。历史未匹配时进入新物料人工复核，采购价和供需待确认，不凭空使用演示库存。

改成每件两个会提示1000个用量与500个申请不一致；另加其他物料数量仍需分开申请。

## 状态和处理

- `needs_info`：缺少必填字段。
- `needs_clarification`：数量含糊、无效日期、单位不同、用量依据冲突、价格过期或无法提取。暂不判通过与否，不生成申请；无关补充不会解除歧义。
- `blocked`：已知超预算或疑似重复等条件待调整。
- `review`：新物料或异常用量进入持久化人工复核队列，不表示已批准或已通知员工。
- `no_purchase`：用户声明的库存和按时在途足够。
- `passed`：生成待审批申请 PDF，不是采购订单。

补购量 = max(0,需求数量 − 当前可用库存声明 − 按时在途声明)。程序计算数量及金额，不自动换算单位。历史物料以件计量。重复请求 token 幂等，申请保存数据和规则快照。

## 接口

- `POST /api/chat/start`：text、可选 request_id、幂等 token，返回后台任务。
- `GET /api/chat/jobs/{id}`：轮询结果。
- `GET /api/chat/requests` / `/api/chat/requests/{id}`：摘要与对话。
- `GET /api/chat/config`：来源及模式。
- `GET /api/reviews`：人工复核待办。
- `GET /api/applications/{id}/pdf`：申请快照 PDF。

## 验证

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --check static/review.js
```

57项测试覆盖旧版计算、对话补充、歧义纠正、预算、重复保护、新物料待办、产品用量依据、模型模拟及中文 PDF。PDF测试需要中文字体。数据库测试均使用临时目录，不影响运行中数据库。
