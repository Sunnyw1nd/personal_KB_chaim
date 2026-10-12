# 第八轮：图片观察台与存储生命周期审计验证

日期：2026-09-18。工作区 `E:\personal_KB`，当前不是 Git 仓库；未执行 commit、push、PR。
开始前 Python 3.12.2，项目版本 0.4.0；修改前全量测试为 123 passed、1 skipped，
真实文章样本为 5 passed，编译、Ruff 检查及格式检查通过。原有文档、图片、工作簿和
历史输出均按现场保留。本轮没有新增依赖、真实模型调用或实际清理。

## 实际实现

新增 `observer.py` 的来源索引与生命周期审计，`observer_site.py` 的静态页面生成、
本地 CSS/JavaScript、`kb-enrich-images observe` 子命令和最小单元测试。观察台只写
`output/image-enrichment/observer/`；缩略图以完整图片哈希命名，限制为 180×140 的
等比例 PNG，原图未重压或覆盖。页面的可见模型文本均转义；运行对照由外部本地 JS 数据文件
载入，JavaScript 使用 `textContent` 写入文本。页面有 CSP，不依赖公网资源。

为了让第七轮六图的已有质量记录进入统一索引，依据
`docs/image-enrichment-round7-validation.md` 增加了一份**暂定** `quality-review.json` sidecar。
这不是用户新确认的人工真值，也没有修改已有 v1/v2 run、cache 或评测文件。

缓存读取新增当前业务校验：旧 v1 成功记录若只通过历史结构校验、却违反现行跨字段规则，
会保留为诊断证据但不再作为成功缓存返回。新 Provider 输出也经当前规则校验。
发现的两个实际旧缓存分别为结构信息图错入 chart，以及完整 OCR 与第一行正式代码不一致。
正式结果 Schema 未更改；四份重新导出的正式 Schema 与仓库当前文件哈希逐一相等。

## 真实数据盘点

| 类别 | 文件 | 目录 | 字节 |
| --- | ---: | ---: | ---: |
| cache | 28 | 14 | 91,609 |
| runs | 99 | 24 | 201,268 |
| evaluations | 6 | 3 | 147,525 |
| 合计 | 133 | 41 | 440,402 |

45 张测试图片均按清单哈希验证可用，全部有正式 Asset 关系，共 87 条；1 张为
`local-only`。10 个 run 可分为 4 个 dry-run、4 个真实调用和 2 个缓存检查；
14 个 cache 均可映射到 run，孤立项 0。完整字节重复组 15 组，主要为不同 run
重复保存的同一请求摘要，并非删除依据。当前解析/引用问题只有 2 条旧缓存业务校验失败；
无图片哈希错配或缺失。两份早期连通性 run 缺少后期 `metrics.apiCalls`，界面改由其
逐图记录显示可观察的调用次数，不假定缺失的 usage 为 0。

最大的三个来源文件：v1 六图基准评测 JSON 77,530 字节；预检评测 JSON 62,842 字节；
预检 run manifest 20,075 字节。审计清单列出完整前十名和重复内容路径。

清理预览：保留 26 项、待确认 0 项、可清理候选 1 项，候选为第六轮无调用
dry-run（完整路径见清理预览 JSON），占 13,808 字节。它没有真实模型调用、
人工评测或项目文档引用；即便如此，本轮仅给建议，不执行删除。模型成功 cache、失败证据、
v1/v2 对照及人工评测均不自动列为“可安全删除”。未知项若出现会进入待确认。

## 验证证据

- `kb-enrich-images observe build`：45 张缩略图成功、0 张失败、78 个静态页面。
- 本机 Edge 无界面打开 `file://` 首页和对照页：导航、CSS 与脚本加载；对照页显示
  第六轮 v1／第七轮 v2 共同 6 张图。不是用户桌面浏览器逐项验收。
- 抽查结构图、代码图、装饰文字的详情页：分别关联 7/6/7 个 run、2/1/2 个 cache、
  各 2 份评测、各 2 个正式位置。两个当前无效 cache 均有反向图片和 run 关联。
- `kb-enrich-images observe strict` 返回 1，并准确列出 2 个
  `cache_business_validation_failed`；这是预期审计结果，不是程序崩溃。
- `kb-enrich-images observe audit` 和 `cleanup-preview` 生成机器可读 JSON 及 Markdown。
- 在最终重建前后，45 张原图、cache、runs、evaluations 和工作簿共 179 个文件的
  按路径+文件 SHA-256 聚合摘要均为
  `1100bf7b4f413869d669770087a96b9ecafb962e060a279f4bd75d9b14dde8a4`。
  这个比较发生在新增暂定评价 sidecar 之后；不能据此声称 sidecar 在本轮开始前已存在。
- 页面文件扫描未发现外部 URL、API Key 格式、Authorization 或图片 Base64。
  构建路径不会创建 Provider，实际 API 调用数为 0。

### 失败原文可见性补充（2026-09-20）

观察台初版只显示 attempt 证据路径，用户仍需手工打开 JSON。现已让失败项默认展开，并直接
展示每次调用中已脱敏的 `content` 原文和 `localValidation.errors`。真实 v2 代码图的两次
失败均可看到完整模型回答，以及“`code.text` 遗漏 `extractedText` 中多行代码”的拒绝原因。
最早的连通性失败发生在逐次原文留存之前，页面明确说明原文不可恢复。新增测试覆盖原文并非
合法结果 JSON、HTML 注入、凭据样式脱敏、失败证据和旧记录无原文等情形。

- `python -m pytest -ra`：133 passed、1 skipped；跳过原因是当前环境不允许创建测试符号链接。
- `python -m pytest -q tests/test_real_sample.py`：5 passed。
- `python -m compileall -q src tests`、`python -m ruff check src tests`、
  `python -m ruff format --check src tests` 均通过；格式检查覆盖 45 个 Python 文件。
- `kb-enrich-images write-schemas --output .work/observer-schema-check` 后，四份导出 Schema
  与 `schemas/` 中的正式文件逐项 SHA-256 相等。

## 未做与限制

没有删除、移动或压缩任何历史数据；没有跑 44 图基线、改 Prompt、改 Document/Asset，
也没有外发 `local-only`。观察台当前面向项目已有工作簿和清单格式，原始图片链接依赖当前
本机项目位置，迁移目录后需要重建。文件式静态观察台不提供编辑、权限系统或并行写入。
清理预览只是候选，实际清理必须另立任务并获得用户批准。
