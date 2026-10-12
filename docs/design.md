# 静态文章标准化设计说明

## 边界与数据流

公开入口 `normalize_saved_article(html_path, resource_directory, output_directory)` 表示一个
完整导入单元。它只接受浏览器已静态保存的 HTML 和同名 `_files` 目录，依次完成输入边界
检查、编码识别、静态 DOM 解析、来源识别、正文块转换、资源关联、模型校验、修订判定和原子
发布。

```text
输入与安全检查 ──> 来源适配器 ──> 稳定来源身份 ──> 通用块/资产模型
                                                      ↓
                                               修订指纹与完整性
                                                      ↓
                                  文件式逻辑文档 ──> current + 历史修订
```

输入目录边界是 HTML 文件的父目录。URL 解码后的本地引用必须仍位于该边界及指定资源目录；
符号链接不跟随。HTTP(S)、协议相对 URL、data URL、脚本和 iframe 均不会载入或执行。输出
目录不得与输入重叠。

## 通用内核与适配器

来源无关能力位于 `normalizer.py`、`parser.py`、`media.py`、`models.py`、`revision.py`、
`storage.py` 和 `renderer.py`：

- 输入限制、路径边界、编码检测和页面资源引用盘点；
- 通用 heading、paragraph、list、quote、code、table、image、unknown 块转换；
- 图片真实格式、尺寸和哈希识别；
- Document、Asset、Manifest、Report 契约及交叉引用校验；
- 稳定块/资产 ID、确定性 JSON 和仅由 Document 生成的 Markdown；
- 内容/资产修订指纹、正文图片完整性和 current 决策；
- 不可变修订、原子文档记录和可读目录发布。

`adapters/` 只承担来源识别、稳定来源身份、元数据定位、正文容器定位和来源特殊节点映射：

- 微信适配器要求 `#js_content`，并结合 `mp.weixin.qq.com` URL 或微信标题/账号节点识别；
- 知乎适配器要求知乎专栏 `/p/` URL，且 `.Post-Title`、`.AuthorInfo-name` 和唯一
  `.Post-RichText` 同时存在；
- 零匹配返回 `source_unsupported`，多匹配返回 `source_ambiguous`；不做整页回退。

这里没有设计动态插件发现机制。两个来源共享的行为才进入内核，选择器和特殊映射留在适配器，
避免复制整套解析器，也避免为尚不存在的来源构建复杂框架。

包名 `wechat_html_normalizer` 暂时保留，避免破坏第一轮公开导入路径；新增中性 CLI
`kb-normalize` 和 API `normalize_saved_article`。兼容 API `normalize_wechat_article` 及
`wechat-normalize` 不变。

## 知乎特殊映射

### 元数据与时间

标题和作者优先取明确 DOM 节点，原始 URL 取 `og:url`，首次发布时间和最后编辑时间分别取
`datePublished`、`dateModified`。ISO 时间保留明确偏移；中文/页面显示时间按上海时区解析。
结构化时间优先，页面分钟精度时间只用于一致性检查；忽略秒数后仍不一致会产生 warning，
不会覆盖结构化值。知乎不存在公众号名称，`accountName` 保持 `null`。

### 图片与图注

知乎 `figure` 中的正文图片按位置生成图片块，`figcaption` 作为该图片的可选 `caption` 和
`captionInlines`，不重复生成无关段落。正文图片以解码结果识别格式；SVG 及损坏/不支持文件不复制，
但保留资产占位和 warning。

### 链接卡片

第一版不增加网站专用的 `linkCard` 块。卡片在原位置降级为普通 paragraph/link：`href`
保存确定性解码后的目标 URL，`sourceHref` 保存知乎跳转地址，标题和可见描述成为正文内容。
缩略图只以 `link_card_thumbnail` 进入资源清单，不生成正文图片资产。这个方案保持块契约简单，
代价是暂不表达卡片的复杂布局。

### 嵌入视频

视频按原位置降级为 `unknown`，`originalTag=video`，并产生
`unsupported_embedded_video` warning。远程 MP4 不请求，来源 URL 去除 query/fragment 后
仅用于追溯，避免把临时授权或跟踪参数写入正文。视频封面以 `video_poster` 盘点，不计入
普通正文图片。当前不增加完整视频 schema，因为本轮不支持下载、转码、字幕或内容理解。

### 代码与行内结构

代码块直接读取 `pre/code` 的文本树一次，保留换行和缩进，不重复拼接高亮 span；语言只在
可靠属性存在时保存。Markdown 围栏长度大于代码中最长连续反引号。行内解析保留普通链接、
加粗、强调、行内代码和 `<br>` 语义；SVG 路径及隐藏/主动节点不进入文字。

## 资源分类

页面引用盘点同时记录标签语义和本地路径。`script[src]` 优先级最高，因此 `.js.下载` 或无
常规扩展名的脚本一律 `refused/executable_script`；`link[rel=stylesheet]` 同理识别 CSS。
随后结合真实媒体类型和正文/页面引用关系，区分正文图片、卡片缩略图、视频封面、页面 UI、
样式、字体、辅助 HTML 和不支持资源。资源目录中的每个实际文件都有一条清单记录。

## Schema、身份与确定性

新导入统一使用 Schema `2.0.0`。运行时模型仍接受旧 `1.0.0` / `1.1.0`，但不会用新字段重新
解释旧输出。2.0 增加：

- `sourceIdentity`：原始值、规范值、来源原生 ID、方法、版本、可信度；
- `revision`：内容指纹、资产指纹、组合指纹、修订 ID 和本次包哈希；
- `assetCompleteness`：正文图片的预期、可用、缺失、异常和稳定缺失 ID；
- Asset 的 `identityKey` / `identityMethod`；现有 `sha256` 明确表示修订内图片二进制；
- `document-record.schema.json`：逻辑文档、current、历史修订和等价包追溯。

`documentId` 由来源类型和适配器规范身份计算，不使用 ZIP 或 HTML 哈希。可靠微信 `/s/` token、
微信身份查询字段和知乎 `/p/` ID 的规则均为显式版本化逻辑；无法可靠识别时使用带 warning 的
HTML 哈希隔离 ID，不按标题合并。

修订内容指纹忽略运行时间、DOM 定位和本地路径，资产指纹保留逻辑资产、可用状态和二进制
SHA-256。组合指纹还包含 Schema、适配器及规则版本。资源清单按路径排序；JSON 键排序、固定
缩进。相同修订重复导入直接读取已发布的不可变结果，因此 `importedAt` 和输出字节保持首次值。
完整算法、current 策略和目录规则见[稳定文档身份与修订管理](revision-management.md)。

## 文件式修订存储

没有引入 SQLite。当前规模和严格串行批量入口适合每个逻辑文档一份
`document-record.json` 与 `revisions/<revisionId>/`：修订先在同目录临时区域完整写入并验证，
再原子移动；文档记录最后原子替换。失败修订不会改变旧 current，也不依赖符号链接。

同正文下更完整资源成为 current；同正文资源退化保留历史但不替换 current；正文更新形成新
修订并成为 current。包不同但标准化正文和资产等价时不创建新修订，只把包哈希追加到该修订
来源清单。批量严格串行，不为本轮引入跨进程锁或全局索引。

目录名由来源标签、首次标题安全摘要和稳定文档 ID 短前缀组成。已有记录优先于新标题，因此
标题修改不会移动目录。Windows 非法字符、控制字符、保留名、尾随点/空格、长标题和输出根
深度均经过确定性处理。

## 真实样本安全缓存

`sample_cache.py` 只服务真实回归和人工验收，不改变正式 HTML+目录输入接口。`samples/`
保存不可变 ZIP，`.work/samples/` 保存内容寻址缓存。缓存目录由安全样本名和 ZIP 完整 SHA-256
前缀构成；完成清单最后写入，记录策略版本、状态、ZIP 身份、条目/总大小、主 HTML 对、每个
文件大小和 SHA-256。

每次命中前会复核清单及实际文件集合。缺清单、非完成状态、策略或 ZIP 不符、文件缺失/变化/
新增均触发该缓存的精确重建。解压先写入 `.work/samples/.tmp-*`，全部验证后再原子发布；失败
不产生完成标记。Windows 临时目录名刻意保持较短，以容纳浏览器生成的长中文资源路径。

解压不使用 `extractall()`，逐项拒绝：绝对和盘符路径、`..`、符号链接/特殊文件、加密条目、
控制字符、Windows 保留名、大小写或尾随点/空格冲突、文件兼目录冲突、异常数量、单文件/
总大小、异常压缩比以及 CRC 损坏。主 HTML 与同名 `_files` 组合必须唯一。

### 批量编排

`batch.py` 的 `normalize_sample_archives` 复用上述准备接口和正式
`normalize_saved_article`，按大小写无关的稳定文件名顺序扫描 `samples` 顶层全部 ZIP。
每项独立完成准备、自动来源识别和标准输出；一个 ZIP 的准备或来源识别失败只形成该项失败，
不会回滚或阻断其他文章。批量层不实现新的网页回退，也不重新解释适配器结果。

汇总报告记录 ZIP 名称/哈希、cache hit 或 extracted、适配器、逻辑文档/修订/current、导入
决策、逻辑文档与修订相对路径、warning 和稳定错误代码，不记录 `.work` 绝对路径。决策统计
区分 created、updated、unchanged、equivalent、退化未采用、conflict 和 failed。报告写入输出根的
`batch-processing-report.json`。输出根不得位于不可变 `samples` 或生成缓存 `.work` 内。
CLI 对“全部成功 / 存在失败或冲突 / 批次本身无法开始”分别返回 0 / 1 / 2，使人工调用和自动
任务都能可靠判断结果。批量仍严格串行，同批两个包指向同一逻辑文档时复用同一发布服务。

## 技术选择与代价

- BeautifulSoup + lxml：DOM 遍历清晰且对浏览器保存的非严格 HTML 容错好。标准库
  `html.parser` 依赖更少但恢复能力弱；直接 lxml 遍历更快但实现复杂。
- Pydantic v2：运行时约束、交叉引用和 JSON Schema 来自同一模型。dataclass + jsonschema
  职责更分离，但需要维护两套契约。
- Pillow + 文件签名：可靠识别无扩展名图片、尺寸和格式。`python-magic` 依赖系统 libmagic，
  Windows 部署成本更高。
- charset-normalizer：仅在 BOM、HTML 声明和 UTF-8 均失败时回退。只支持 UTF-8 更确定，
  但无法兼容历史保存文件。
- zipfile + 标准库：足以逐项安全解压和 CRC 校验，没有为测试缓存引入归档框架。
- JSON 文件式修订记录 + `os.replace`：无需数据库即可保留 current 和历史，适合当前单机串行
  导入；代价是不支持多个独立进程并发写同一输出根。

本轮没有新增运行时或开发依赖。代价是 BeautifulSoup DOM 路径只保证稳定回查，不提供浏览器
像素级高亮；知乎选择器只覆盖当前声明的静态专栏变体；列表内复杂嵌套仍会降级；不从 CSS 或
脚本恢复懒加载内容，也不联网补全资源。

## 后续接口

图片理解阶段可读取稳定 `assetId`、哈希、真实媒体类型、尺寸、正文位置和四个待描述字段再
回填结果。本轮不选择或实现 OCR、视觉模型、多模态 Embedding、图片正文去重、Chunk、检索
或 RAG。

## 第五轮：图片 Enrichment 边界

图片 enrichment 没有加入 Document 2.0，也不修改 Asset 的二进制事实和 `pending` 字段。
原因是模型解释依赖 provider、模型、Prompt、上下文和参数，可重建且可能存在多个版本；将其
写回标准事实会造成不可解释的最后写入覆盖，并无谓触发 Markdown、Chunk 或索引变化。

实现分为四层：

1. dataset 只读取图片清单和正式 Document 关系，逐项复核二进制 SHA-256，并将标题、图注及
   图片前后各一小段文字规范成可选上下文；没有可靠关系时显式 `available=false`。
2. vendor-neutral 请求/结果模型表达图片和资产身份、外发策略、上下文指纹、处理动作、结构化
   内容及不确定性。请求模型不含人工标签或参考答案。
3. provider 将契约映射到百炼 OpenAI-compatible 多模态消息和 JSON Schema 结构化输出；供应商
   SDK 类型不会进入核心模型。fake provider 只用于自动测试。
4. runner 以图片、模型、Prompt/Schema、上下文和关键参数生成缓存键，逐图原子发布；evaluation
   之后才按完整图片哈希读取人工基准并形成暂定或已确认统计。

输出采用全局不可变成功缓存加独立 run 清单。run 名不可覆盖；失败留在本次 run，成功缓存可供
中断后的新 run 复用。`--force` 写 run-local 历史而不替换原缓存。该文件式方案延续当前单机、
串行和可审计的设计，避免为 45 图基线引入数据库；代价是没有跨进程锁，多个进程同时运行同一
键时只保证首个有效原子发布结果成为共享缓存，建议本轮不要并发运行多个 CLI。

Prompt v1 在一次调用中同时完成路由与所需内容，明确图片文字不能改变任务，并要求代码、表格、
图表、结构关系、二维码和排除决定采用不同约束。`action` 的内容条件由本地模型二次校验，防止
结构化 JSON 形式正确但业务矛盾。完整契约和命令见 [图片 Enrichment 使用指南](image-enrichment.md)。
