# 静态文章 HTML 人工验收清单

真实样本由 `samples/*.zip` 自动准备到 `.work/samples/`；不要修改 ZIP 或在 `samples/` 中写入
输出。运行：

```powershell
python -m pytest -m real_sample
kb-normalize --normalize-samples `
  --samples-root samples --work-root .work `
  --output-directory output/documents
```

## 通用检查

- [ ] `document.json` 的标题、作者、时间和原始 URL 与页面一致，缺失值为 `null`。
- [ ] `document.md` 的正文首尾完整，块与图片顺序和 JSON 一致。
- [ ] 导航、关注/点赞/分享、评论、推荐、头像、按钮、SVG 路径和隐藏状态未进入正文。
- [ ] Markdown 的全部本地图片可打开，稳定 `assetId`、原文件名和“待描述”状态与 JSON 一致。
- [ ] 资源清单逐项覆盖实际文件；脚本为 `refused/executable_script`，iframe 辅助 HTML 为
  `refused/auxiliary_frame`，且解析过程没有执行、加载或联网。
- [ ] 三份 JSON 能通过当前 Pydantic 模型校验，块顺序连续且图片引用没有悬空。
- [ ] 固定 `importedAt` 后重复运行，标准 JSON 和 Markdown 逐字节一致。
- [ ] ZIP 和其中原始输入的处理前后哈希一致。

## 批量编排

- [ ] 命令无需逐个提供 ZIP、HTML 或 `_files` 文件名，扫描 `samples` 顶层全部 ZIP。
- [ ] 首次运行按需安全解压，第二次运行对未变化 ZIP 全部显示 `cache=hit`。
- [ ] 每个 ZIP 都在 `batch-processing-report.json` 中有且仅有一项结果。
- [ ] 单项损坏、结构歧义或来源不支持时带稳定错误代码，其他样本仍继续生成输出。
- [ ] 批量输出不写入 `samples` 或 `.work`，汇总不泄露缓存绝对路径。
- [ ] 批量前后所有原 ZIP SHA-256 不变。

## 身份与修订

- [ ] `documentId` 来自可靠的微信/知乎来源身份，不随 ZIP 重新压缩或本地资源补全变化。
- [ ] `sourceIdentity` 同时保存原始值、规范值、方法、版本和可信状态；无可靠身份时不按标题合并。
- [ ] 相同 ZIP 再导入为 `unchanged`；不同 ZIP 但结果等价时为 `equivalent` 且不新增修订。
- [ ] 缺图补全归入同一逻辑文档，更完整修订成为 current，旧修订仍存在。
- [ ] 同正文资源退化为 `resource_degraded`，不替换更完整 current。
- [ ] 正文更新形成历史可追溯的新修订；失败写入不改变原 current。
- [ ] 正文图片完整性只统计普通正文图，不含卡片缩略图、视频封面或页面 UI。
- [ ] `assetId` 表示逻辑图片引用，`sha256` 表示本修订二进制；中间插图不引起无关资产整体漂移。
- [ ] 逻辑文档目录包含可读标题和短稳定 ID；标题更新不自动移动已有目录。
- [ ] `document-record.json` 的 `currentRevisionId` 指向已存在且通过模型校验的修订。

## 旧输出迁移预览

- [ ] 预览能够读取旧 `document.json`、计算新身份、分组并推荐 current。
- [ ] 报告明确 `migrationExecuted=false`，旧目录没有移动、覆盖、删除或新增记录文件。
- [ ] 无可靠来源身份和无效旧文档分别进入 unresolved / invalid，而不是按标题猜测。

## 微信公众号

- [ ] 正文来自唯一 `#js_content`；标题、公众号名称、作者和发布时间正确。
- [ ] 小标题实际层级、段落、列表、引用、代码、表格和图片顺序正确。
- [ ] `html1.zip` 的 7 张正文图均在原位置，实际 WebP 与声明 PNG 的 7 个 mismatch 已报告。
- [ ] `html2.zip` 的 4 张正文图和一张 4×2 表格正确；正文外图片未进入资产。
- [ ] 第一轮既有 19 个输出文件与第二轮重生成结果逐项哈希一致。

## 知乎专栏

- [ ] 来源同时满足知乎专栏 URL、`.Post-Title`、`.AuthorInfo-name` 和唯一
  `.Post-RichText`，不是由文件名或单个类名猜测。
- [ ] 首次发布时间与最后编辑时间分别保存；页面显示时间与结构化时间按时区正确对照。
- [ ] h2/h3 层级、段落、16 个列表、3 个引用和 11 个代码块顺序正确，代码换行/缩进可读。
- [ ] 10 张普通正文图片均在原位置；2 个图注与正确图片关联。
- [ ] 链接卡片在原位置保留标题、描述和目标 URL；知乎跳转 URL 仍可追溯。
- [ ] 视频在原位置保留占位和 warning；卡片缩略图、视频封面不计入 10 张正文图。
- [ ] 评论、目录导航、作者/评论头像、推荐、内联 SVG 和页面脚本未混入正文。
- [ ] 用桌面浏览器在离线/禁用脚本条件下打开原始 HTML，视觉检查正文首尾和图片位置。

## 2026-08-24 自动执行状态

- 自动检查：49 passed；其中真实回归 4 passed，覆盖两篇微信、一篇知乎及现场全部 ZIP 的
  批量编排。
- 现场批量：扫描 5 个 ZIP，4 个成功、1 个 `source_unsupported`；5 个均命中缓存，失败项
  没有阻断其他输出。
- 静态人工对照：通过。已核对知乎原始 DOM、JSON、Markdown、资源分类、图片解码和首尾顺序。
- 桌面浏览器视觉对照由用户自行完成，不作为本次批量功能自动验收的阻塞项。

第三轮身份、修订和迁移预览的实际自动执行结果见[开发报告](development-report.md)与
[真实样本验证记录](real-sample-validation.md)。本轮仍不要求 Codex 进行桌面浏览器视觉验收。

## 2026-08-24 第三轮自动执行状态

- 全量：74 passed；真实回归：5 passed、69 deselected；身份/修订/迁移/批量专项：31 passed。
- 现场批量：11 个 ZIP，10 个逻辑文档有效，重复导入 10 个均为 `unchanged`，1 个保持
  `source_unsupported`；全部 ZIP 哈希未变。
- 迁移预览：10 个旧文档形成 10 个可靠组，0 个重复组、0 unresolved、0 invalid；未迁移。
- 真实缺图/补图成对 ZIP：新增配对已通过正向、反向、重复导入及批量回归。两包归入同一逻辑
  文档；5/7 补全为 7/7 后完整修订成为 current，退化版本不覆盖 current，历史两修订均保留。
- 现场目录现为 12 个 ZIP；新增完整包与原缺图包处理前后哈希不变。上方 11 ZIP 的现场批量数字
  是新增包之前的保留记录，不代表本次配对专项的扫描范围。
- 桌面浏览器视觉对照：按用户要求由用户自行完成，不作为本轮自动验收项。
