# 第五轮图片 Enrichment 验证记录

记录日期：2026-09-10。工作区：`E:\personal_KB`，当前未使用 Git。本文只记录本轮实际检查和
执行，不把未发生的云端调用写成已完成基线。

## 修改前基线

- Python 3.12.2；项目版本 0.3.0。
- 修改前全量 pytest：76 passed、1 skipped；跳过项是当前环境不能创建测试符号链接。
- `ruff check .` 通过。
- `ruff format --check .` 会扫描 `output/` 中文章派生 Markdown，把其中两份代码块当作待格式化
  Python；这是现场已有检查范围问题。`ruff format --check src tests` 的 29 个文件全部通过。
- 当前目录不是 Git 仓库，没有分支、提交或未提交状态可报告；没有执行外部版本控制操作。

## 测试集与基准审计

- 输入：`test_pictures/`，45 个文件、45 个唯一完整 SHA-256、0 个二进制重复组。
- 实际格式：WEBP 35、JPEG 7、PNG 3；45 个均可解码。
- 图片内容与 `annotation-002/inventory.json` 中完整哈希和大小逐项一致。
- 人工工作簿：`output/image-annotation/annotation-002/图片辅助标注.xlsx`。
- 工作簿 SHA-256：`66d69402546e6ca1655b13895098975316877c6bec9bb962c9ea04117ae93bf1`。
- 工作簿主表和文件表各 45 行，完整 imageId 唯一并与实际文件一致；43 行有参考答案。
- 图片主类型、知识价值和建议动作已有填写，但审核状态仍为 45 行“未审核”。因此本轮把它称为
  “评测基准 v1（暂定）”：可以报告全部可比较行的原始符合度，不能把差异直接算作已确认错误。

推理数据由 `inventory.json` 和正式 Document 关系构造，推理命令不接受工作簿路径。评测命令在
推理结束后才只读加载工作簿。程序运行后再次核对输入图片哈希；工作簿哈希也保持不变。

## 实际 dry-run

执行：

```powershell
python -m wechat_html_normalizer.enrichment.cli run `
  --dry-run `
  --allow-external `
  --run-name qwen37-flash-baseline-v1-dryrun
```

结果：45 个文件全部进入预演，45 个唯一图片，42 个有正式 Asset 关系和受限正文上下文，3 个
明确 `context.available=false`。安全审计发现 1 张代码截图的人工参考内容含凭据样式字符串，已按
完整图片哈希写入 `config/image-enrichment-policy.json` 并标为 `local-only`。最终状态为 44 个
`dry_run`、1 个 `policy_rejected`，未创建 API 客户端、未发送网络请求。
清单位于：

`output/image-enrichment/runs/qwen37-flash-baseline-v1-preflight/run-manifest.json`

随后实际运行独立评测命令，得到 0 个机器结果、45 个缺失结果，符合 dry-run 事实；没有生成
虚假分数：

`output/image-enrichment/evaluations/qwen37-flash-baseline-v1-preflight-20260910T052440Z/`

评测报告对 1 行凭据样式内容自动替换为 `[REDACTED_CREDENTIAL]`；机器结果和原始响应在持久化
前也执行相同防护。含原文的用户工作簿保持只读。开发中生成的两份未脱敏评测目录已精确删除，
最终输出扫描未发现 `sk-…`、Authorization Bearer 或图片 base64。

另外执行了未显式 `--dry-run`、但要求外发的单图预检。由于当前进程没有
`DASHSCOPE_API_KEY`，CLI 明确提示后自动降级为 dry-run 并返回非零状态；仍没有发出请求。该
清单位于 `output/image-enrichment/runs/qwen37-flash-no-key-final/`。

## Provider 与版本

- processor：`image_enrichment`；provider：`aliyun_dashscope_openai_compatible`。
- 默认请求模型：`qwen3.7-flash-2026-07-15`；可配置别名 `qwen3.7-flash`。
- 默认地域/endpoint：北京 / `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- Prompt：`image-enrichment-v1`，冻结 SHA-256
  `d81daa6b18608d8cc2dae1ff46b83bd69abf0a3e82172015de4c76e5660eb4f9`；结果 Schema：1.0.0；
  运行记录 Schema：1.0.0。
- 默认非流式、`enable_thinking=false`、严格 JSON Schema、本地 Pydantic 二次校验。
- 新增可选依赖声明：`openai>=2.14,<3`（Apache-2.0）。现场已有 openai 2.14.0，没有下载安装
  或升级。评测继续复用 openpyxl 3.1.5（MIT）和既有 Pillow。

2026-09-10 核对的官方模型页列出北京地域短上下文原价输入 0.2 元/百万 token、输出 0.8 元/
百万 token；代码没有固化价格。由于没有真实 API 请求，本轮 usage、耗时分布和实际成本均无
云端数据，费用为“未计算”，不是零成本实测。

## 自动测试

实际执行结果：

- `python -m compileall -q src tests`：通过；
- `python -m pytest -ra`：105 passed、1 skipped；跳过原因仍为测试环境不允许创建符号链接；
- `python -m pytest -m real_sample -ra`：5 passed、100 deselected；
- `python -m pytest tests/test_sample_cache.py -ra`：23 passed；
- enrichment 专项：29 passed；
- `python -m ruff check .`：通过；
- `python -m ruff format --check src tests`：40 files already formatted；
- 正式文章四份 Schema 与 enrichment 四份 Schema 均成功导出；
- 本地 editable 包刷新为 0.4.0，`kb-enrich-images --help` 和三个子命令可用。

新增专项覆盖请求与结果严格校验、action 条件、endpoint 白名单、服务端结构化参数和本地校验、
fake 正常与错误、重试上限、dry-run/外发拒绝、缓存命中、配置变化、损坏缓存、安全记录、批次
中断恢复、清单哈希、路径穿越、local-only 策略、凭据样式脱敏、基准隔离和只读评测。最终输出
含 45 份请求记录且均通过正式请求模型校验；请求键中没有人工答案、人工标签或 benchmark 字段。

## 未完成与人工项

- 当前环境缺少 `DASHSCOPE_API_KEY`，没有完成 1～3 图连通性、45 图云端基线、真实 usage/
  成本和重复调用全缓存命中验证。
- 在当前安全策略下，即使提供密钥也只会外发 44 张；`asset-b3f47abf650b.webp` 必须先确认截图中
  的凭据样式字符串已经失效或替换为脱敏图片，再更新策略和清单。若该字符串可能仍有效，应先
  在百炼控制台撤销/轮换。
- 因没有机器结果，未进行结构图、图表、代码、表格、二维码、装饰图和界面截图的结果质量人工
  对照，也没有“无需修改/轻微修改/实质修改/不可用”工作量等级。
- 45 行工作簿审核状态尚未改为“已确认”；是否批量确认应由用户审查后决定，本轮没有代填。
- 没有启用 Function Calling、Batch Inference、二维码工具、本地模型、OCR、正式文档回写、
  Chunk、Embedding 或 RAG。

下一步应先完成云端小批量和 44 张安全图片的基线，再决定是否清理第 45 张。之后本地对照的最小
候选是：确定性二维码解码器；一个本地 OCR/文档结构候选用于文字、表格和代码；一个小型本地
视觉语言候选用于结构图、流程图、图表和混合截图。三者继续使用同一结果 Schema。持续 Schema
无效、关键事实臆造、箭头方向错误、表格结构不可恢复，或在用户硬件上延迟/显存不可接受时，应
回退到 `manual_review` 或云端基线，不把低质量结果自动写入检索。

## 2026-09-11 连通性与无效响应留存补充

用户使用正式快照完成三图连通性运行 `qwen37-flash-connectivity-v1`：2 个 success、1 个
`invalid_structured_output`。成功响应汇总为 5,409 tokens；旧实现没有保留失败响应 usage，因此
该数字不能代表三次调用的完整用量。失败图片随后通过仅指定完整 SHA-256 的新 run
`qwen37-flash-connectivity-v1-retry-001` 重试成功，服务实际返回模型仍为
`qwen3.7-flash-2026-07-15`，该次记录 2,632 tokens。两次 run 均保留，原失败记录未修改。

针对这次可诊断性缺口，后续无效结构化响应会额外保存脱敏后的模型原始 content、模型/request
ID、usage 和不含输入值的本地校验问题；非法结果不会进入成功缓存。新增测试同时覆盖 JSON 语法
错误和“JSON 合法但违反 action 条件”两种情况，以及凭据样式脱敏、失败 usage 汇总与失败记录
关联。修改后实际执行：全量 `114 passed, 1 skipped`，enrichment 专项 33 passed，编译、Ruff
检查和格式检查均通过。本次代码验证没有再次请求云端，因此新失败证据文件格式由 fake provider
自动测试验证，仍需在将来真实出现结构化输出失败时取得现场实例。

## 2026-09-11 第六轮：六图可靠性收口

### 修改前基线与缺口

- 工作区仍为 `E:\personal_KB`，不是 Git 仓库；Python 3.12.2，项目版本 0.4.0。
- 修改前实际执行：全量 `114 passed, 1 skipped`，真实样本 `5 passed`，编译、Ruff 检查与格式
  检查通过。跳过项仍是当前环境不允许创建测试符号链接。
- 原 `--max-retries` 只覆盖超时、429 和临时性服务错误；`invalid_structured_output` 不自动重试。
- 图片任务只保存最终记录。若第一次结构化失败、第二次成功，第一次的原始响应、request ID 和
  usage 会丢失，run usage 也无法完整覆盖两次调用。
- 既有缓存键不包含 run name 或时间戳，具备跨 run 复用基础；成功缓存和失败记录已经分离。

### 最小实现

- 新增 `--max-structured-retries`，默认 1；`--max-retries` 继续只表示传输类重试。两类重试共用
  一个顺序调用循环且不相乘，单图上限为 `1 + max-retries + max-structured-retries`。
- 每次真实 provider 调用生成独立 `attemptRecords`，记录调用序号、两类重试序号、状态、耗时、
  request ID、实际模型、usage 和诊断响应路径。
- 结构化失败响应按 `attempts/attempt-NNN.raw-response.json` 脱敏后分别保留；后续重试成功也不
  删除前次证据。两次均失败时不会发布成功缓存。
- 单图 record 保留原 `usage`（最终尝试）语义，新增 `taskUsage` 汇总该任务所有已返回 usage 的
  调用；manifest 汇总全部本次实际调用。新增 metrics 明确 API 调用数、首次成功、重试恢复、
  结构化失败、最终失败、缓存命中及 usage 已知/未知调用数。未知 usage 不写成 0。
- Prompt、结果 Schema、缓存键和正式 Document/Asset 契约均未改变；仍未加入宽松 JSON 修补或
  二次纠错 Prompt。

### 代表样本与 dry-run

选择 6 张此前未用于三图连通性测试的图片，覆盖结构信息图、图表、表格、非敏感代码、UI 截图
和装饰文字。策略文件中的 `local-only` 图片 `asset-b3f47abf650b.webp` 未被选择。dry-run 结果为
6 个 `dry_run`、0 个 `policy_rejected`、0 次 API 调用；6 张均有正式 Asset 关系和受限上下文。

实际 run：`qwen37-flash-reliability6-v1`。显式设置 `--max-retries 0` 和
`--max-structured-retries 1`，因此硬上限为 12 次调用。实际结果：

| 图片 | 代表类型 | 最终状态 | 调用/结构化重试 | total tokens | 耗时 | 人工等级 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `asset-0007.webp` | 三步结构信息图 | success | 1 / 0 | 2,651 | 13,922 ms | 实质修改 |
| `asset-367af9dd1487.webp` | 表格 | success | 1 / 0 | 2,608 | 21,219 ms | 无需修改 |
| `asset-44fdbea6544d.webp` | 非敏感代码 | success | 1 / 0 | 2,221 | 69,453 ms | 实质修改 |
| `asset-57ae62ca4266.webp` | 涌现曲线图 | success | 1 / 0 | 2,895 | 15,250 ms | 轻微修改 |
| `asset-ce52ad0983de.webp` | 装饰文字 | success | 1 / 0 | 1,626 | 51,703 ms | 无需修改；基准动作待复核 |
| `asset-dba5466c7c5e.jpg` | MCP 工具 UI | success | 1 / 0 | 2,655 | 66,656 ms | 轻微修改 |

6 张均为首次成功，没有产生新的真实结构化失败证据。总 usage：input 11,260、output 3,396、
total 14,656、image 3,104 tokens；6 次调用均返回 usage。逐图调用耗时合计 238,203 ms，平均
39,700.5 ms。费用未核算，不能把未计算写成零。

### 质量结论

- 表格的三列表头和七行内容与暂定参考逐项一致，可以直接使用。
- 装饰文字正确识别“精品·知识推荐”，降低为无知识价值并排除检索，同时保留提取文字。人工表
  暂定为 OCR/include，属于值得复核的基准差异，不直接视为模型错误。
- 图表正确提取坐标轴、临界点与趋势；“0% 等于随机表现”不是图中事实，需要轻微删除或弱化。
- UI 的工具名和功能正确；把右上角数字 2 扩展为“服务器或工具组”缺少依据，需要轻微修正。
- 三步信息图的文字和顺序基本正确，但被错误路由为 `chart/understand_chart`，而不是
  `architecture_flow/ocr_and_vision`，结构化字段不适合后续消费者。
- 代码完整转写实际出现在 `extractedText`，但 action 所要求的 `code.text` 只有第一行。结果虽通过
  当前条件 Schema，按契约消费会丢掉绝大部分代码，属于实质修改。

独立人工记录位于
`output/image-enrichment/evaluations/qwen37-flash-reliability6-v1-20260911T092134Z/quality-review.json`。
工作簿全部行仍为“未审核”，以上等级是本轮轻量人工核对，不会改写用户工作簿或提升为绝对真值。

### 缓存、安全与决定

使用完全相同的图片、模型、Prompt、Schema、上下文和参数，以新 run 名
`qwen37-flash-reliability6-v1-cache-check` 复跑：6 个 `cached`，`apiCalls=0`，没有新增 usage 或
费用。一次手工复制 SHA-256 截断的命令在数据准备阶段被明确拒绝，未创建 run、未发出请求；随后
使用完整清单值成功完成缓存检查。

自动测试使用 fake provider 验证了“首次结构化失败后成功”“两次结构化失败”“每次证据不覆盖”
“成功与失败 usage 汇总”“未知 usage 显式计数”“失败不入缓存”和“两类重试采用相加上限”。
请求记录没有人工标签或参考答案，外发仍须显式授权；`local-only` 策略保持有效。

最终回归于 2026-09-14 接续完成：Enrichment 专项 `41 passed`；全量 `117 passed, 1 skipped`；
真实样本 `5 passed`；Python 编译、Ruff 检查与格式检查均通过。跳过项仍为运行环境不允许创建
测试符号链接。正式 Schema 已由运行时模型重新导出并在全量测试中逐项比对。

本轮暂不进入其余 44 图完整基线。原因不是结构化稳定性失败，而是 6 张中有 2 张结果需要实质
修改，已经达到任务卡的暂停条件。下一步应先决定如何强化 action 对字段的条件约束，以及如何
避免把结构信息图误放入 chart 结构；这会影响 Prompt 或结果契约，应作为独立变更验证，不能在
同一冻结 run 内暗改。

第七轮的 v2 Prompt 与本地跨字段校验实验、同组六图逐图对照、失败响应及恢复 v1 默认的依据，
另见[第七轮验证记录](image-enrichment-round7-validation.md)。该记录不改变以上各阶段的历史结果。
