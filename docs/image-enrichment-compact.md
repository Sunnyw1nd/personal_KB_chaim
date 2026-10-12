# 精简普通 JSON 图片处理

`compact-json-v1` 是 `kb-enrich-images run` 的可选配置，默认仍为旧 v1。它让模型只填当前类型的
字段，省略服务端 `response_format`，随后执行严格的本地解析、类型校验、业务校验和正式映射。
结果属于派生 Enrichment，不修改 Document/Asset、原图或文章Markdown，也不直接进入检索。

## 使用

```powershell
kb-enrich-images run --profile compact-json-v1 --dry-run --max-images 3
```

确认允许外发后，用完整SHA选择图片；密钥沿用根目录 `.env` 或 `DASHSCOPE_API_KEY`：

```powershell
kb-enrich-images run --profile compact-json-v1 --allow-external --image-id <完整SHA> --run-name compact-demo-001
```

每个run使用新名字。相同图片、上下文和配置已有可用机器缓存时，新的run会复用它，并保留审核状态。
`local-only` 无法外发，dry-run不会创建API客户端。默认模型保持 `qwen3.7-flash-2026-07-15`，北京
endpoint；不静默换模型，不启用思考或工具。

本轮原六图可由已有明确清单选择，不依赖目录排序：

```powershell
$imageIds = (Get-Content -Raw -Encoding UTF8 -LiteralPath 'output/image-enrichment/runs/qwen37-flash-reliability6-v1/run-manifest.json' | ConvertFrom-Json).items.imageSha256
$runArgs = @('run', '--profile', 'compact-json-v1', '--dry-run', '--allow-external', '--run-name', 'compact-six-preflight-001')
foreach ($imageId in $imageIds) { $runArgs += @('--image-id', $imageId) }
kb-enrich-images @runArgs
```

实际运行去掉 `--dry-run` 并换run名。本轮真实调用已完成；上面的预演不会新增费用，新的真实实验
应单独确认预算，不把本轮18次上限当作无限授权。

## 查看与审核

观察台沿用现有只读入口：

```powershell
kb-enrich-images observe build
```

运行记录包含三个独立概念：

- 运行/结构状态：`success`、`invalid_output`、`provider_error`、`blocked`等；
- 机器校验状态：`passed`、`failed`、`incomplete`；
- 质量审核状态：`pending`、`needs_review`、`approved`、`rejected`。

`success + passed + pending` 只说明结果可读取，尚未证明图片事实正确。代码逐字准确性和箭头真实关系
通常需要人工对照，模型自报不确定或置信也不是独立证明。工作簿的标签审核与单次模型结果的质量
审核分别保存；Codex评审不能自动当作用户认可。

用户确认后可对完整cache key登记决定：

```powershell
kb-enrich-images review --cache-key <完整缓存键> --status rejected --reason "代码字符与原图不一致"
```

支持pending、needs_review、approved、rejected。每次决定保存独立event，并原子更新最新decision；
机器cache和旧证据保持不变。被否决结果会显示“结构校验可通过、缓存资格被否决”，不能作为可用
缓存返回，也不会在同一身份下自动付费重跑。若撤回否决，需用户明确登记新的审核决定；改动图片、
上下文或版本也会形成新身份。

## 错误出口和预算

每图最多3次：首次1次、传输恢复最多1次、结果恢复最多1次；整批最多18次。未使用的类别预算
不转给其他类别。旧 `--max-retries`、`--max-structured-retries` 只管理legacy流程，新配置固定各1次。
SDK `max_retries=0`，新配置不支持旧 `--force` 或覆盖其冻结Prompt。

- 已确认的连接错误、超时、429以及500/502/503/504可延迟恢复一次；其他未知异常不连续重试。
- Key、模型、权限、参数等永久配置错误立即停止后续生成；已有结果保留。
- 只允许剥离完整包围JSON的围栏、外围空白和开头BOM；字符串内部不改变，重复键拒收。
- JSON/Schema/可证明业务矛盾最多纠错一次，携带字段路径或具体解析问题。
- 截断与纠错共享同一个结果恢复预算；截断恢复后仍有结构错误时不追加第二次纠错。
- 原文可解析且只有结构不合规时允许仅文本纠错；否则带同图、同上下文重新生成。可比较的正文、
  数字和代码差异会保存，改变或无法证明无损时标为needs_review，不自动补成参考答案。

默认每图总截止180秒，每次模型请求最多90秒；SDK网络调用还由可取消的asyncio总超时约束。
初始输出4096 tokens，截断恢复最多提高到8192。可用 `--task-timeout`（1–600秒）与
`--recovery-max-tokens`（不高于8192）调整，初始输出仍由 `--max-completion-tokens` 指定；
这些参数参与缓存身份。Retry-After超过剩余截止时间时直接停止，不无限等。

超时可能发生在服务已处理后；本工具不声称服务器严格只计费一次。所有取得的usage累计，未知
usage保留未知。中断时已登记请求可能有未知网络结果，恢复只重建证据，不自动重发。

## 产物与离线恢复

- `runs/<run>/requests/<SHA>.json`：标准请求元数据，不含人工答案或图片Base64；
- `runs/<run>/tasks/image-<短SHA>/task.json`：完整图片身份与请求映射；
- `tasks/.../attempts/attempt-NNN.raw-response.json`：先原子保存的脱敏原文及usage；
- `tasks/.../attempts/attempt-NNN.validation.json`：校验、包装处理、恢复差异及映射；
- `cache/<完整cache key>/record.json`：通过机器校验的可重建结果，审核仍独立；
- `reviews/<cache key>/events/`、`decision.json`：用户审核历史和当前决定；
- `evaluations/`：工作簿对照和Codex评审，均不参与推理请求。

缓存身份包含图片、模型/endpoint、Prompt和精简Schema哈希、上下文、普通JSON模式、映射/校验/
恢复版本、生成及截止参数。成功缓存读取时重新验证原精简JSON与正式映射，并应用审核覆盖。
解析失败、截断或业务失败不会进入成功cache。

manifest发布失败后可以仅读取已存attempt恢复：

```powershell
kb-enrich-images recover --run-manifest output/image-enrichment/runs/<run>/run-manifest.json
```

恢复不创建Provider。只有原文、没有完整任务发布记录的项目会保留为needs_review/incomplete；
不假装成功cache，不补造缺失usage或耗时，不能为了零调用演示隐藏失败。

任务目录缩短以降低Windows长路径风险，完整SHA始终在记录中，短名冲突在调用前拒绝。更深输出
根或极长run名仍可能超过Windows路径限制，应选择短根目录/短run名；本轮不修改全局系统配置。

## 兼容性与验证

正式图片结果保持Schema1.0.0；新配置运行/记录写1.1.0，当前读取器继续接受1.0.0。新增字段主要是
profile、审核/校验状态、恢复类型、finish reason及问题路径；中断导致未知时间可用null。旧输出不迁移。
旧版本消费者若严格限定运行Schema1.0.0，需要升级读取器；回退到legacy即可生成原运行格式。

没有新增依赖，使用现有OpenAI SDK、Pydantic与Python标准库；同步CLI通过异步SDK获得总请求截止。
以后在已有async事件循环的服务中应通过线程或异步适配调用，不能直接嵌套同步 `asyncio.run`。

本轮实测及限制见[第十二轮验证报告](image-enrichment-round12-validation.md)。可继续做支持待审核状态
的小界面和TXT/MD/独立图片输入，但这些不属于本轮实现。
