# 图片 Enrichment 观察台与只读存储审计

观察台把本地图片、正式 Asset、cache、run、attempt 和 evaluation 组织为可重建的静态页面。
它不调用模型，不修改已有运行证据，不提供删除按钮；机器结果仍不是人工参考答案。

## 最短使用方法

在项目根目录运行：

```powershell
kb-enrich-images observe build
```

双击 `output/image-enrichment/observer/index.html`，或运行 `kb-enrich-images observe open`。
后者使用系统默认浏览器打开静态文件；如果系统关联失败，命令仍会打印入口路径。
图片详情可从缩略图、文件名、哈希、Asset ID 或 Document ID 查找；运行对照页默认选择
第六轮 v1 和第七轮 v2 的六图真实调用。页面完全使用相对链接、本地 CSS/JS 和缩略图，
无需开发服务器和外网。移动整个项目时重新执行 `build` 可更新原图链接。

只取审计或清理建议，不构建网页：

```powershell
kb-enrich-images observe audit
kb-enrich-images observe cleanup-preview
kb-enrich-images observe strict
```

`strict` 不写文件；遇到损坏记录、缺失引用或当前校验不通过的缓存时以非零状态返回。
其余命令只写 `output/image-enrichment/observer/`。可用 `--input-root`、`--inventory`、
`--workbook`、`--enrichment-root`、`--site-root` 和 `--policy` 改变明确的本地输入；
源目录和观察台输出都必须位于项目内，输出不能与 cache、runs、evaluations 重叠。

如需人工别名，可创建 `config/image-observer-aliases.json`，仅供观察台读取：

```json
{
  "aliases": {
    "<完整图片SHA-256>": "三步结构信息图"
  }
}
```

别名不会修改图片、工作簿或正式 Asset；不提供网页内编辑。别名缺失时使用工作簿图片类型和
原文件名回退。页面对文件名、模型文本和 JSON 字符串做转义，搜索索引不包含人工参考答案或
完整请求体。`local-only` 明确标出；页面不包含 API Key、Authorization、图片 Base64，
失败原文只使用经过脱敏的持久化证据，并始终按纯文本转义，不作为 HTML 执行。

结构化失败时，Provider 返回的文本并不需要本身是合法 JSON 才能保存：程序把它作为普通
字符串放在每次 attempt 的 `*.raw-response.json` 的 `content` 字段中，并同时保存
`localValidation.errors`。观察台会读取这份证据，直接显示“模型原始回答”和“本地校验
为什么拒绝”；失败项默认展开。原文不会重新解析为成功结果。早于逐次原文留存功能的历史
失败只有摘要，页面会明确标为无法恢复，而不是补造内容。

## 数据来源与关联

| 来源 | 职责与关联 | 删除后影响 |
| --- | --- | --- |
| `test_pictures/` | 原始测试图片；用完整二进制 SHA-256 与清单对齐 | 权威输入，不清理 |
| `output/image-annotation/annotation-002/` | 文件清单、87 条 Asset 关系及人工工作簿；人工答案只用于显示“已填写”状态 | 人工资料与来源关系，不清理 |
| `output/documents/` | 仅按清单给出的文档 JSON 路径读取图片块位置 | 正式事实，不清理 |
| `cache/<cacheKey>/` | 成功结果及脱敏响应；key 不是图片哈希 | 可再生成但需要云端费用，且输出不一定相同 |
| `runs/<runId>/` | manifest、逐图 request、attempt、成功或失败记录 | 调用、usage 和失败审计证据，通常保留 |
| `evaluations/<runId>/` | 基准评价及人工质量记录 | 人工判断和基准证据，长期保留 |
| `observer/` | 统一索引、缩略图、静态页、审计和清理预览 | 全部可本地无损重建 |

源图哈希用于图片主键；run manifest 的 `imageSha256` 和 `cacheKey` 建立运行关系；cache
记录自身保存图像 SHA、模型、Prompt、Schema 和上下文指纹；attempt 留在 run 记录里；evaluation
按 run ID 与图片 SHA 回连。允许一图对应多个文档位置、多个 cache 和多个 run。统一索引
`observer-index.json` 是派生视图，不是新的权威数据库，也不会参与下一次模型推理或缓存键。

当前 cache key 由完整图片 SHA、Provider/模型、Prompt 版本与内容 SHA、结果 Schema、
上下文指纹、地域、思考模式、最大输出 Token、温度和严格输出设置共同决定。运行名和时间戳
不参与。页面“当前默认键”只表示在观察台采用当前默认参数时键相同，不等于结果一定通过
当前业务校验。

## 存储生命周期与安全边界

`storage-audit.json`/`.md` 统计 cache、runs、evaluations 的文件、目录、字节、重复内容组和
大文件；测试图片及工作簿另列。`cleanup-preview.json`/`.md` 为每个候选写出完整路径、
大小、引用、理由、重建成本、风险与恢复说明。`cleanup_candidate` 只是供用户确认的提案，
绝不是删除授权。本命令没有清理实现，未知用途项目为 `review`。被文档引用、有评测、
有真实 API 调用、有失败证据的 run 均保留；模型 cache 即使孤立也不会因为可重算而直接
列为无成本可清理。

第七轮跨字段规则曾只绑定 v2 Prompt，因此旧 v1 成功缓存可能绕过新校验。当前读取缓存时
会重新执行完整 Pydantic 与当前跨字段业务校验；不合格记录保留原位、标记无效，不再作为
成功命中，也不被自动覆盖。新 Provider 响应同样受当前规则约束。正式结果 Schema 未改版，
历史运行证据未迁移；因此“历史 success”与“当前可复用”在观察台中是两个不同字段。

## 本次真实目录验证（2026-09-18）

- 45 张图片、45 张均有正式关系、87 条 Asset 关系；1 张 `local-only`。
- 10 个 run（4 个 dry-run、4 个真实调用、2 个缓存检查）、14 个 cache、3 份 evaluation；
  来源区共 133 个文件、440,402 字节。第三份 evaluation 是依据第七轮既有文字验证记录
  新增的暂定质量等级 sidecar，不代表用户确认的真值。
- 当前规则拒绝 2 个历史 v1 cache：一个结构信息图错放图表字段，一个代码结果正式字段不完整。
- 孤立 cache 为 0；清理预览保留 26 项、待确认 0 项、候选 1 项（13,808 字节的无调用 dry-run）。
- 45 张缩略图成功、0 张失败；生成 78 个静态页面。
- 读取的工作簿有 45 行，其中 41 行标为“已确认”、4 行“未审核”，43 行有参考事实；
  工作簿 SHA-256 为 `ff905e06a455fe044c7d584decd7d29fae96a46bc47572cf230b8c39b42c2880`。

这些数字只描述本次本地目录，不是长期契约。工作簿标签仍有未确认项，质量评审侧单独标注
了依据；不能把页面展示当作人工真值已最终冻结。

## 人工复核要点

打开第六轮/第七轮六图对照，检查代码图 v1 的历史 success 与当前校验失败是否同时可见；
再看 v2 对应失败证据、结构图两次误判、表格和装饰文字记录。任取三张图片进入详情、
任取两个 cache 反查图片和 run，并核对 `local-only`。浏览器 `file://` 若禁用本地脚本，
静态正文仍可打开；搜索和运行对照需要允许本地 JS。不要把观察台目录移动到其他盘后继续
依赖原图链接；重新构建即可。
