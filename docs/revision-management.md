# 稳定文档身份与修订管理

本文说明第三轮引入的逻辑文档、输入包、修订、资产完整性和文件式存储规则。日常命令见
[使用指南](usage.md)。

## 1. 五个不同概念

| 概念 | 回答的问题 | 稳定依据 |
| --- | --- | --- |
| 逻辑文档 | 是否为同一来源中的同一篇文章 | 适配器提供的规范来源身份 |
| 输入包 | 本次处理的是哪个原始 ZIP | ZIP 完整 SHA-256 |
| 修订 | 本次形成了什么标准正文与正文资产 | 内容指纹和资产指纹 |
| 逻辑资产 | 正文中的哪一个图片引用 | 原始图片 URL、本地引用或来源定位 |
| 展示目录 | 人如何快速找到文档 | 来源标签、首次标题摘要和短文档 ID |

ZIP SHA-256 仍用于 `.work` 缓存、输入不变性和审计，但不参与可靠来源文档的 `documentId`。
因此重新压缩 ZIP、补齐本地图片或重新保存同一文章不会创建另一个平级逻辑文档。

## 2. 来源身份

### 知乎专栏

知乎适配器从可靠的 `og:url` 中提取 `/p/<article-id>`，规范值为
`zhihu:column:<article-id>`。主机名、分享查询参数和 fragment 不参与身份。原始 URL、原生 ID、
提取方法和规范化版本同时保留。

### 微信公众号

微信适配器按保守优先级处理：

1. `/s/<token>` 使用路径 token；
2. `/s?...` 只采用 `__biz`、`mid`、`idx`、`sn`，要求存在 `sn`，或同时存在
   `__biz + mid + idx`；
3. `scene`、`from` 等跟踪参数不进入身份。

微信查询参数不会被通用函数全部删除。规范值记录实际采用的身份字段及固定顺序，原始 URL
仍完整保留。

### 身份不可靠

缺少上述可靠身份时，系统产生 `source_identity_unresolved` warning，并用 HTML 完整 SHA-256
生成隔离的 fallback ID。该策略不按标题、作者或发布时间合并；两个标题相同但 HTML 不同的
输入保持独立。它可保证相同直接输入稳定，但不能承诺重新保存后的跨包归并，需后续人工确认
或显式身份覆盖能力。

### `documentId`

可靠身份的 ID 输入为：

```text
source-identity-v1 + sourceType + normalizedIdentity
```

对 UTF-8 输入计算 SHA-256，取前 16 个十六进制字符，并保留 `wechat-` / `zhihu-` 来源前缀。
完整规范身份保存在 `sourceIdentity`，因此短 ID 不是唯一审计证据。来源类型参与输入，防止不同
适配器之间碰撞。

## 3. 修订指纹

Schema 2.0 的 Document 增加 `revision`：

- `contentFingerprintSha256`：标题、作者、账号、发布时间、编辑时间以及去除运行时 ID 和 DOM
  定位后的有序标准块；图片块保留逻辑 `assetId`；
- `assetFingerprintSha256`：每个正文资产的逻辑 ID、可用状态、真实媒体类型、二进制 SHA-256、
  宽高和是否可复制；
- `fingerprintSha256`：指纹规则版本、Schema、适配器及版本、上述两个指纹的组合；
- `revisionId`：完整修订指纹前 16 位，加 `revision-` 前缀；
- `packageSha256`：形成该 Document 的本次 ZIP；直接 HTML 输入可以为 `null`。

`importedAt`、ZIP 文件名、ZIP 压缩元数据、HTML 文件名、输出路径和 `.work` 路径不进入修订
指纹。不同 ZIP 只要标准正文和正文资产结果等价，就映射到同一修订；所有等价包哈希追加到
`document-record.json` 的该修订来源清单。

解析规则或 Schema 变化会改变组合指纹。这会保留一份新修订，避免新旧标准化语义在同一 ID
下被误认为字节等价。

## 4. 图片逻辑身份与完整性

新资产 ID 不依赖图片二进制，也不再依赖全文图片顺序编号。优先依据：

1. `data-original`、`data-actualsrc`、`data-src` 中的远程原图地址，去除 query 和 fragment；
2. 本地 `src` 相对引用；
3. 无其他依据时使用稳定来源定位。

同一依据在正文中重复出现时增加该依据内部的 occurrence，避免资产 ID 冲突。在正文中间增加
另一张不同图片，不会让前后已有远程图片 ID 整体漂移。`Asset.sha256` 始终表示本修订实际
文件的二进制 SHA-256，与逻辑 `assetId` 分工明确。

`assetCompleteness` 只统计普通正文图片：

- `expected`：正文图片引用；
- `available`：本地存在、可验证解码且已复制；
- `missing`：本地文件缺失；
- `invalid`：文件存在但损坏或不支持；
- `completenessRate`：`available / expected`，无图片时为 1；
- 缺失和异常资产的稳定 ID 列表。

链接卡片缩略图、视频封面、作者头像和页面 UI 不参与完整率。

## 5. 导入决策与 current

| 决策 | 新修订 | 是否成为 current |
| --- | --- | --- |
| `created` | 是 | 是 |
| `unchanged` | 否 | 否；保持原 current |
| `equivalent` | 否，仅追加包追溯 | 否 |
| `resource_improved` | 是或命中已有历史 | 是 |
| `resource_updated` | 是 | 是；完整度不降低但二进制有变化 |
| `resource_degraded` | 首次出现时保留为历史 | 否 |
| `content_updated` | 是 | 是 |

资源改善和退化只在正文内容指纹相同的情况下比较。正文确实更新时自动成为 current，即使新正文
的预期图片结构发生变化；这是为了避免把旧正文长期误当最新版。该选择及完整性仍记录在文档
记录中，未来 UI 可以提供人工切换，但本轮没有实现切换命令。

重新导入一个已经存在的非 current 退化修订，会返回 `resource_degraded`，不会把它恢复为
current。相同 ZIP 在同一处理规则下返回 `unchanged`；相同修订来自不同 ZIP 返回
`equivalent`。

## 6. 文件式存储和一致性

每个逻辑文档目录包含：

```text
<display-directory>/
├── document-record.json
└── revisions/
    ├── revision-aaaaaaaaaaaaaaaa/
    └── revision-bbbbbbbbbbbbbbbb/
```

修订先写入文档目录内的 `.tmp-*`，模型和文件完整性检查通过后用同卷原子移动发布。
`document-record.json` 最后通过临时文件和 `os.replace` 原子更新，内部唯一
`currentRevisionId` 是 current 的正式事实来源，不依赖 Windows 符号链接。

更新记录失败时，旧记录和 current 不变；首次发布失败会精确清理本次新建的空文档目录。已有
文档若在修订发布后、记录更新前中断，可能留下未被记录引用的修订目录，但它不会被当作 current；
再次导入同一结果可完成记录发布。

批量处理严格串行，所有项调用同一个 `normalize_saved_article` 和发布服务，因此同批两个包指向
同一文档时按确定顺序比较，不存在批量层的平行写入逻辑。当前不承诺多个独立进程同时写同一
输出根；若以后需要并行工作进程，应增加跨进程锁或事务索引。

## 7. 可读目录名

新目录格式为：

```text
weixin-<首次标题安全摘要>--<documentId前10位>
zhihu-<首次标题安全摘要>--<documentId前10位>
```

标题经过 Unicode NFKC、空白折叠、Windows 非法字符替换、尾随点/空格清理和保留名处理，
再按输出根深度限制长度。短稳定 ID 消除同标题和清洗后同名冲突。逻辑文档找到已有记录后沿用
`directoryName`，后续标题变化不会静默移动目录；完整新标题仍在修订元数据中。

## 8. 旧输出迁移预览

`--preview-migration` 读取旧目录中的 `document.json`，按新身份计算候选文档和修订，比较正文
图片完整性，并以“完整率、可用数、异常数、文章时间、稳定路径顺序”推荐 current。报告明确
`migrationExecuted=false`。

预览不会移动、重命名、覆盖或删除旧目录，也不会执行真实迁移。旧 Schema 1.0/1.1 仍可由
运行时模型读取。执行迁移涉及冲突确认和回滚策略，应作为后续独立任务。

## 9. 限制

- 微信身份规则只覆盖已确认的 `/s/<token>` 和保守身份查询字段组合；
- 无可靠来源身份时不提供自动跨保存包合并；
- 当前 current 不能由 CLI 人工切换；
- 没有跨进程写锁，也没有全局二进制资产去重；
- 不自动联网补图，不实现 OCR、视觉描述、Chunk、检索或 RAG；
- 不执行旧输出迁移或自动删除旧目录。
