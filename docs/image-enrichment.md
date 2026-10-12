# 图片 Enrichment 使用指南

图片 enrichment 是可重建的机器解释层。它读取图片、正式资产关系和少量正文上下文，结果独立
保存；不会覆盖原始图片、Document/Asset、文章 Markdown 或人工评测工作簿。本轮唯一真实后端
是阿里云百炼 OpenAI-compatible API，自动测试使用 fake provider。

已有运行结果的离线查看、缓存校验和只读清理预览见[图片观察台与存储审计](image-enrichment-observer.md)。

## 安装与最短路径

```powershell
python -m pip install -e ".[enrichment]"
kb-enrich-images run --dry-run --allow-external --run-name qwen37-flash-baseline-v1-dryrun
```

`--dry-run` 会完成清单、路径、哈希、上下文、策略和缓存键预检，但绝不创建客户端或发送请求。
`--allow-external` 在 dry-run 中只表示预演正式外发策略，不会改变“不联网”的行为。

默认还读取 `config/image-enrichment-policy.json`。其中 `localOnly=true` 的图片即使同时提供
`--allow-external` 也会得到 `policy_rejected`，不会发给云端。可以用 `--policy` 指定另一份经过
审核的策略；不要为了让批次数字好看而绕过凭据或敏感内容隔离。

真实调用前，可以直接编辑项目根目录下已被 `.gitignore` 排除的 `.env`：

```dotenv
DASHSCOPE_API_KEY=在这里填写实际密钥
```

程序只读取 UTF-8 的简单 `名称=值`，不会执行文件内容、shell 语法或变量展开。不要提交、分享或
复制带真实密钥的 `.env`；仓库中的 `.env.example` 始终保持空值。也可以不使用文件，改在当前
PowerShell 会话设置环境变量：

```powershell
$env:DASHSCOPE_API_KEY = Read-Host 'DASHSCOPE_API_KEY'
kb-enrich-images run `
  --allow-external `
  --max-images 3 `
  --run-name qwen37-flash-connectivity-v1
```

同名环境变量的非空值优先于 `.env`。从其他工作目录运行时，可用
`--env-file E:\path\to\private.env` 显式指定文件；符号链接、非 UTF-8、超过 64 KiB、重复键或
不符合简单格式的文件会被拒绝。CLI 不会打印、写入运行记录或缓存密钥，只会说明它来自环境变量
还是哪个凭据文件。

连通性、Schema 和输出检查通过后，才运行完整基线：

```powershell
kb-enrich-images run `
  --allow-external `
  --run-name qwen37-flash-baseline-v1
```

默认请求模型是固定快照 `qwen3.7-flash-2026-07-15`，地域是北京，endpoint 是
`https://dashscope.aliyuncs.com/compatible-mode/v1`。可用 `--model qwen3.7-flash` 显式改为别名；
这会生成不同缓存键。当前只允许经过核对的北京地域官方域名，避免任意 endpoint 接收图片。

如果希望从清单中选取不同类型的连通性样本，可以重复传入完整 SHA-256：

```powershell
kb-enrich-images run `
  --dry-run `
  --allow-external `
  --image-id <完整imageId-1> `
  --image-id <完整imageId-2> `
  --run-name selected-preflight-v1
```

`--max-images` 是额外上限。真实调用默认串行、每分钟 12 次。超时、429 和临时性服务错误默认
最多额外重试两次，由 `--max-retries` 调整；严格结构化输出或本地 Schema 校验失败默认最多额外
重试一次，由 `--max-structured-retries` 调整。两类计数彼此独立但不会相乘，单图实际调用上限为
`1 + max-retries + max-structured-retries`。结构化重试仍使用冻结的原请求，不做宽松 JSON 修补；
两次均失败时保持失败且不进入成功缓存。可用 `--requests-per-minute` 调整限速。默认关闭思考模式；启用
`--enable-thinking` 会进入新的缓存键和独立 run，不能与非思考基线混合。

## 缓存、恢复与强制重跑

成功结果按以下语义生成缓存键：

- 图片完整二进制 SHA-256；
- provider、请求模型和地域；
- Prompt 版本及文件 SHA-256；
- 结果 Schema 版本；
- 规范化上下文指纹；
- `enable_thinking`、输出 token 上限、温度和结构化输出模式。

成功记录先写临时目录再发布到 `output/image-enrichment/cache/<cacheKey>/`。单图失败只进入
当前 run；中断前已经成功发布的图片在下一次命令中直接命中缓存。因此不要删除缓存来恢复任务，
只需使用新的 run 名再次执行：

```powershell
kb-enrich-images run --allow-external --run-name qwen37-flash-baseline-v1-resume
```

每个 run 名对应不可覆盖的批次目录。已存在的名称会被拒绝，防止覆盖证据。若确实需要忽略
缓存，必须同时给出 `--force` 和新的 run 名；强制结果保存在该 run 的 `forced-records/`，不会
覆盖原缓存：

```powershell
kb-enrich-images run --allow-external --force --run-name qwen37-flash-baseline-v1-rerun-001
```

## 输出

```text
output/image-enrichment/
├── cache/<cacheKey>/
│   ├── record.json
│   ├── raw-response.json
│   └── attempts/            # 成功前发生过结构化失败时的脱敏响应证据
├── runs/<runId>/
│   ├── run-manifest.json
│   ├── failed-records/<imageSha256>/
│   │   ├── record.json
│   │   └── attempts/        # 每次有响应的失败尝试分别留存，不互相覆盖
│   └── forced-records/       # 仅 --force
└── evaluations/<runId>-<时间>/
    ├── baseline-evaluation.json
    └── baseline-evaluation.md
```

`record.json` 保存 provider、精确模型、Prompt/Schema 版本、上下文指纹、最终请求 ID、任务耗时、
两类重试计数、逐次 `attemptRecords`、最终尝试的 `usage`、任务累计 `taskUsage` 和已校验结果。
新增 `taskUsage` 而不重新解释旧 `usage` 字段，以保持既有记录语义。每条 attempt 继续记录自己的
调用序号、状态、耗时、request ID、实际模型、usage 和诊断文件位置。`raw-response.json` 只保存
最终成功的模型返回内容及安全元数据，不保存 API Key、请求头、
图片 base64 或完整请求体。模型输出仍可能包含从图片读取的内容，因此该目录按本地知识资料保护。
常见 `sk-…` 和 Authorization Bearer 字符串会在持久化前替换为
`[REDACTED_CREDENTIAL]`；请求的少量文本上下文也使用相同处理。像素中的秘密无法靠文本规则可靠
发现，所以敏感截图仍必须通过本地策略排除，不能把脱敏器当成外发授权。

如果服务已经返回内容、但本地严格 Schema 校验失败，该次响应会写入当前任务目录的
`attempts/attempt-NNN.raw-response.json`。后者保留经过同样凭据脱敏的模型原始 `content`、模型和
request ID、usage，以及不包含输入值的本地校验错误位置。若下一次结构化重试成功，失败证据随
成功记录一起保留；若最终仍失败，则全部尝试进入 run 的失败任务目录。非法内容不会进入共享成功
缓存，也不会被当作有效 enrichment。超时、连接失败等没有服务响应的错误不会伪造原始响应文件。

run manifest 的 usage 累计本次所有返回 usage 的实际调用，包括随后未通过本地 Schema 校验的
响应；`metrics.apiCallsWithUsage` 和 `apiCallsWithoutUsage` 明确说明覆盖情况，未知 usage 不按零
处理。`metrics` 还记录实际 API 调用、首次成功、重试恢复、结构化失败、最终失败和缓存命中数量。
缓存命中不重复计为新增用量。没有可靠价格时不计算成本；价格核算应结合调用当天、地域、上下文
档位和官方计费说明。

## 评测

推理命令没有评测工作簿参数，也不会读取人工标签。推理完成后再独立对齐：

```powershell
kb-enrich-images evaluate `
  --run-manifest output/image-enrichment/runs/qwen37-flash-baseline-v1/run-manifest.json `
  --workbook output/image-annotation/annotation-002/图片辅助标注.xlsx
```

对齐只使用完整图片 SHA-256。报告比较图片类型、知识价值、处理动作和派生的检索决定；人工
关键事实与机器结果并列保存，开放式描述不会用字符串相似度冒充质量评分。工作簿中审核状态不是
“已确认”的行只进入“暂定原始符合度”，不能直接算作已裁定模型错误。逐图的无需修改、轻微修改、
实质修改、不可用或基准待复核等级仍需人工填写。

## 契约与安全边界

- 请求 Schema：`schemas/image-enrichment-request.schema.json`；
- 结果 Schema：`schemas/image-enrichment-result.schema.json`；
- 单次记录 Schema：`schemas/image-enrichment-record.schema.json`；
- 批次 Schema：`schemas/image-enrichment-run.schema.json`；
- Prompt：`src/wechat_html_normalizer/enrichment/prompts/image-enrichment-v1.md`。

CLI 默认仍为 v1。第七轮曾以显式 `--prompt` 指向 `image-enrichment-v2.md`，并设置
`--prompt-version image-enrichment-v2` 验证字段与路由校准，但未通过质量门槛；v2 不应被当作
已验收的生产默认。逐图证据见[第七轮验证记录](image-enrichment-round7-validation.md)。

结果按 action 做运行时条件校验，例如代码转写必须含 `code`，表格和图表动作必须含对应结构；
高知识价值内容不能同时选择排除。服务端使用 JSON Schema 结构化输出，本地再由 Pydantic 严格
校验。Prompt 明确把图片中的指令当作待分析数据，并要求看不清的内容进入不确定性。v1 文件以
完整 SHA-256 冻结；修改内容必须建立新的文件和 `--prompt-version`，不能在同一基线内暗改。

只有同时满足以下条件才会外发：命令显式包含 `--allow-external`、环境变量存在、图片位于指定
输入根且哈希与完整清单一致、请求策略允许 cloud、endpoint 在北京地域白名单中。程序不遍历或
上传其他文章、ZIP、输出目录和工作簿，不调用网页、搜索、Function Calling 或内置工具。

## 当前供应商依据与限制

阿里云官方页面列出 Qwen3.7-Flash 的图像输入、结构化输出和上下文缓存能力；OpenAI-compatible
文档给出 `image_url` 的 base64 Data URL 形式；结构化输出文档列出该系列支持 JSON Schema，且
建议结构化输出时关闭思考。参考：

- [Qwen3.7-Flash 模型说明](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)
- [OpenAI Chat Completions 兼容接口](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)
- [结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
- [地域与 endpoint](https://help.aliyun.com/zh/model-studio/base-url)

同步路径稳定后再考虑 Batch Inference；本轮没有同时维护两套执行方式。二维码没有调用确定性
解码器，Function Calling、联网搜索、本地模型、OCR、Markdown 回写、Chunk、Embedding 与 RAG
均未启用。
