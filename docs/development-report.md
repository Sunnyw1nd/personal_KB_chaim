# 开发与测试报告

报告日期：2026-08-12（Asia/Shanghai）

## 仓库与基线

工作区 `E:\personal_KB` 开始时为空，且不是 Git 仓库。因此没有当前分支、基线提交或原有未提交修改。本轮未初始化 Git，未 commit、push 或创建 PR。开发完成后，用户在 `samples/` 中补充了两个不可变 ZIP 样本；回归测试直接校验 ZIP 哈希，并只解压到 pytest 临时目录。

## 实现

原型采用 Python 3.11+，包入口为 `wechat_html_normalizer.normalize_wechat_article`，命令行入口为 `wechat-normalize`。核心模块包括：

- `models.py`：严格数据模型、交叉引用和安全相对路径校验；
- `parser.py`：微信元数据、`#js_content` 与有序正文块解析；
- `media.py`：SHA-256、实际图片类型和尺寸识别；
- `normalizer.py`：输入限制、安全边界、资源清单、资产复制和全部输出编排；
- `renderer.py`：仅从已验证 Document 渲染 Markdown；
- `cli.py`：单文章 CLI 和三份 JSON Schema 导出。

输出为 `output/wechat-<HTML 哈希前缀>/`，包含标准文档、Markdown、处理报告、资源清单与 `assets/`。不执行脚本、不加载 iframe、不请求网络、不修改输入。

## 验证结果

合成样本覆盖微信元数据、原始 URL、全部要求的正文块类型、隐藏节点、内联链接/加粗、无扩展名 WebP、PNG 声明与 WebP 实体不一致、正文外图片、CSS、JavaScript、辅助 HTML、缺图、不支持资源、路径穿越、缺少正文根、schema 悬空引用及重复运行字节稳定性。

最终结果为 11 项测试全部通过，其中 9 项为合成边界/功能测试，2 项为真实 ZIP 回归测试。真实样本结构、正文首尾、标题层级、表格、图片解码、Markdown 引用和输出稳定性另见 `real-sample-validation.md`。

## 已知限制

- 只支持具有静态 `#js_content` 的保存页面，不做低可信整页回退。
- DOM 定位用于稳定回查，不提供浏览器像素级高亮。
- 深度嵌套在单个列表项内部的复杂块结构会降级为列表文本；列表内图片保留为紧随列表的图片块。
- SVG 因潜在主动内容只盘点、不复制；当前复制范围是 Pillow 可解码的安全光栅图片。
- 不从 CSS/JavaScript 恢复懒加载资源，不联网补图。
- 编码规范化只覆盖 BOM、HTML charset、UTF-8 和 charset-normalizer 回退。
- 本轮没有 OCR、图片描述、图片分类、切分、embedding、检索或 RAG。
- 当前 CLI 接收解压后的 HTML 与资源目录；ZIP 安全解压属于测试/摄取前置步骤，尚未作为公开 CLI 子命令提供。

下一轮图片理解模块可围绕稳定 `assetId`、哈希、实际媒体类型、尺寸、正文位置和四个待描述字段设计输入/回填接口；本轮不预先选择 OCR 或视觉模型。

---

## 第二轮：知乎适配与解析内核抽取

报告日期：2026-08-24（Asia/Shanghai）

### 开始前基线

- 工作区：`E:\personal_KB`，仍不是 Git 仓库，因此无分支、HEAD、远程或未提交状态；现场
  全部已有文件视为用户基线。
- Python：3.12.2；beautifulsoup4 4.13.3、charset-normalizer 3.4.1、lxml 6.0.2、
  Pillow 11.2.1、Pydantic 2.12.5、pytest 8.4.2。
- `python -m compileall -q src tests` 通过；`python -m pytest` 为 11 passed；
  `python -m pytest -m real_sample` 为 2 passed、9 deselected。
- 三份正式 JSON Schema 与当时运行时模型一致。环境没有安装 Ruff，基线 lint 命令实际失败为
  `No module named ruff`，未将其记为通过。
- 两个微信 ZIP 和既有输出可访问。`samples/` 中另有 `MCPzhihu.zip`；项目外用户附件
  `E:\html\MCPzhihu.zip` 与仓库样本字节一致，SHA-256 均为
  `15bcd20ae793ab09787ba983d94e4bd0c033c5853291a173e1fcb8e5fc290f62`。
- `.work/real-samples-20260812-161622` 是没有可信完成清单的手工解压目录，旧真实测试在 pytest
  临时目录调用 `extractall()`，仍依赖测试自行猜测顶层 HTML；该目录没有被当作新缓存命中。

### 复用分析与实现

第一轮的模型、媒体识别、安全路径、确定性输出、Markdown 和报告编排可直接复用；绑定点是
元数据选择器、`#js_content`、适配器名称/来源类型、文档前缀及正文 figure 行为。本轮新增
`adapters/base.py`、`adapters/wechat.py`、`adapters/zhihu.py` 和 `html_utils.py`，把这些绑定点
收敛到两个小型适配器；公共解析和资源逻辑保留一份。

包名未改，避免破坏旧导入；新增 `normalize_saved_article`、`normalize_zhihu_article` 和
`kb-normalize`。微信仍使用 schema 1.0.0；知乎使用向后兼容 1.1.0。用第二轮实现重新生成两个
微信样本后，19 个既有输出文件（含全部图片）的 SHA-256 与修改前基线逐项完全一致。

知乎实现包括高可信来源识别、标题/作者/首次发布时间/最后编辑时间、`.Post-RichText` 正文、
通用块、figure 图注、链接卡片降级、视频占位、卡片缩略图/视频封面分类，以及脚本 DOM 语义
识别。视频 URL 会去除 query 和 fragment；没有访问任何远程地址。

`sample_cache.py` 新增真实样本公共准备入口。缓存按 ZIP 完整哈希隔离，清单逐文件记录大小和
SHA-256；命中前全量复核。逐项解压拒绝路径穿越、绝对/盘符路径、特殊文件、加密、Windows
冲突、数量/大小/压缩比异常和 CRC 损坏。初次验证发现 `html1.zip` 的长中文资源路径与过长
临时目录叠加会触发 Windows 路径限制，因此缩短了受控临时目录名；修复后三个 ZIP 均完成
安全准备，连续第二次调用均命中缓存。

根据实际使用反馈，随后新增 `batch.py` 和 `--normalize-samples`：自动扫描 `samples` 顶层
全部 ZIP，逐项调用同一安全缓存和自动来源识别，再直接生成正式输出。单项失败继续后续处理，
汇总写入 `batch-processing-report.json`；CLI 以 0/1/2 区分全部成功、部分失败和批次无法开始。
原单文章 API、CLI 与 `--prepare-sample` 诊断入口均保持兼容。

### 最终验证

- `python -m compileall -q src tests`：通过；
- `python -m pytest -q`：49 passed；
- `python -m pytest -m real_sample`：4 passed、45 deselected；
- `python -m wechat_html_normalizer.cli --write-schemas schemas`：三份 schema 已从模型重新导出；
- 安装项目已声明但环境缺失的 Ruff 0.16.4（未修改依赖清单）后，
  `python -m ruff check .` 通过，`python -m ruff format --check src tests` 显示 19 files already
  formatted。

自动测试覆盖知乎识别、元数据和时间冲突、行内结构、代码、图片图注、卡片、视频、SVG/噪声、
`.js.下载`、缺失/不支持图片、路径/符号链接安全、稳定输出，以及样本首次准备、缓存命中、ZIP
变更、损坏缓存重建、半成品、路径冲突、加密、符号链接、CRC 和资源限制。真实微信和知乎
测试都直接调用新样本准备接口，不再需要 Codex 手工解压。

批量测试额外覆盖两个来源一次扫描、第二次全量 cache hit、ZIP 大小写扩展名、输出稳定、单项
不支持仍继续、无 ZIP、根目录无效、输出与 `samples`/`.work` 重叠、CLI 无需文章文件名，以及
现场全部 ZIP 均进入汇总。最终现场批量运行扫描 5 个 ZIP：4 个成功标准化，1 个以
`source_unsupported` 明确失败，另外 4 个没有受影响；第二次验收时 5 个均命中缓存。

### 范围和操作

没有新增依赖；现有五个运行时依赖及选择理由见设计文档。本轮没有实现知乎问题/回答、通用网页、
GitHub Pages、PDF、TXT/Markdown、OCR、图片或视频理解、Chunk、Embedding、检索、RAG、Web UI
或正式用户 ZIP 导入。没有初始化 Git，也没有 commit、push、PR 或其他外部写操作；原始三个
样本 ZIP 未修改。

---

## 第三轮：稳定文档身份、修订管理与可读输出

报告日期：2026-08-24（Asia/Shanghai）

### 修改前现场基线

- 工作区仍为 `E:\personal_KB`，不是 Git 仓库；没有分支、HEAD、远程或可区分的未提交状态，
  现场文件均按用户基线保留。
- Python 3.12.2；运行时依赖版本未变化。现场已经安装 pytest 8.4.2、Ruff 0.16.4。
- `compileall` 通过；全量 pytest 为 49 passed；真实样本为 4 passed、45 deselected；Ruff 检查
  和格式检查通过；三份正式 Schema 与运行时模型一致。
- 批量接口严格串行，扫描顶层 ZIP；当时 `documentId = 来源前缀 + HTML SHA-256 前 16 位`，
  资产按正文顺序 `asset-0001...`，输出没有索引、current 或历史修订。
- ZIP SHA-256 用作解压缓存键和来源审计；Document 记录 HTML、ZIP 与资源来源，但没有区分
  逻辑文档和修订。`output/real-samples` 有 10 个成功旧目录；按原始 URL 扫描没有重复组，
  其中一篇为 5/7 正文图片。
- `samples/` 现场为 11 个 ZIP；`.work/samples` 由上一轮可信清单管理。旧输出和全部原始 ZIP
  在本轮未修改。

### 实现

包版本提升为 0.3.0，未改包名、既有函数签名和两个 CLI 名称。新增：

- `identity.py`：微信/知乎来源身份规范化、未解析隔离身份和稳定 `documentId`；
- `revision.py`：逻辑资产 ID、正文/资产/组合修订指纹和正文图完整性；
- `storage.py`：可读目录、不可变修订、原子文档记录、current 和重新导入决策；
- `migration.py`：旧输出只读扫描、候选分组和迁移预览；
- `document-record.schema.json` 及 Schema 2.0 的 Document/Report/Manifest；
- 身份、修订、命名、失败保护、迁移和混合批量测试。

适配器现在提供来源身份。知乎采用 `/p/<id>`；微信优先 `/s/<token>`，查询 URL 只采用已确认
的 `__biz`、`mid`、`idx`、`sn` 组合。无可靠身份时按 HTML 完整哈希隔离并 warning，不按标题
合并。ZIP 哈希继续保留为包证据和缓存键，不再决定可靠来源文档 ID。

资产 ID 优先由远程原图地址、其次本地引用、最后来源定位计算，与二进制 `sha256` 分离。修订
指纹排除 ZIP 压缩元数据、导入时间和本地路径；异包等价只追加包追溯。文件式存储按逻辑文档
保存 `document-record.json` 和 `revisions/`；修订先原子发布，记录最后原子替换。批量仍严格
串行并复用单篇服务。

current 策略为：同正文补图改善采用；同正文资源退化保留历史但不采用；同完整度二进制变化
采用；正文更新采用。旧目录不自动迁移、移动或删除，只新增 `--preview-migration`。

### 开发中发现并修复的问题

现场首次向 `output/managed-samples` 发布时，10 个受支持文档已经正确写入，但 CLI 向 GBK
控制台打印含不间断空格的 ZIP 名时发生 `UnicodeEncodeError`。CLI 随后改为仅对真实
`TextIOWrapper` 显式配置 UTF-8，并增加专用测试。修复后同一批次稳定返回 10 个
`unchanged`、1 个 `source_unsupported`，没有重复修订。

本地 editable 安装第一次因受控环境的用户目录权限失败；获得明确工具批准后以相同本地源码
完成 0.3.0 安装。没有访问文章远程资源，也没有新增或下载项目依赖。

### 最终验证

- `python -m compileall -q src tests`：通过；
- `python -m pytest`：74 passed；
- `python -m pytest -m real_sample`：5 passed、69 deselected；
- `python -m pytest tests/test_sample_cache.py`：23 passed；
- `python -m pytest tests/test_batch.py tests/test_identity_revision.py tests/test_migration.py`：
  31 passed；
- `python -m ruff check .`：通过；
- `python -m ruff format --check src tests`：27 files already formatted；
- 四份正式 Schema 与运行时模型逐项一致；`kb-normalize --help` 通过；
- 现场 11 个 ZIP 与批量报告记录的完整 SHA-256 逐项一致；
- `output/managed-samples` 有 10 份有效 DocumentRecord、10 个修订、10 个有效 current，30 份
  修订 JSON 均可解析；
- 旧输出迁移预览扫描 10 个旧文档：10 个可靠组、0 个重复组、0 unresolved、0 invalid，且
  `migrationExecuted=false`。

三个固定真实样本的新稳定 ID、修订和完整性见[真实样本验证记录](real-sample-validation.md)。
用户随后加入同一真实微信文章的缺图版与补图版，现场 ZIP 增至 12 个。新增真实回归确认两包
归入 `wechat-4a498ea1280780fd`：5/7 缺图修订补全为 7/7 时判为 `resource_improved` 并成为
current；反向导入缺图版为 `resource_degraded`，不会替换完整 current；重复完整包为
`unchanged`。批量入口对同一配对得到相同逻辑文档和 current 决策，原 ZIP 哈希未变化。隔离验证
输出见 [real-completion-pair](../output/real-completion-pair/)。用户已说明桌面浏览器对照自行进行，
本轮没有执行桌面浏览器验收。

### 范围、依赖和外部操作

没有新增依赖。未实现联网补图、OCR、图片理解、PDF、TXT/Markdown、Chunk、检索、RAG、UI、
跨进程并发写、current 人工切换或真实旧输出迁移。未初始化 Git，未 commit、push、创建 PR。
唯一外部环境写入是经批准重新安装本地 editable 包 0.3.0；没有进行网络资料上传或远程仓库
操作。

## 第五轮：云端多模态基线工程（2026-09-10）

新增独立 `enrichment` 包、四份 JSON Schema、版本化 Prompt、百炼 OpenAI-compatible provider、
fake provider、可 dry-run/限量/恢复/强制新 run 的 CLI，以及工作簿只读评测。正式 Document、
Asset、文章 Markdown、ZIP 解析和修订 current 逻辑均未修改。项目版本提升为 0.4.0；新增可选
依赖组声明 `openai>=2.14,<3` 和既有 openpyxl，实际环境已存在 openai 2.14.0（Apache-2.0），
本轮没有下载模型、上传图片或引入数据库。

开始前基线为 76 passed、1 skipped；Ruff 源码检查通过。完整 45 图 dry-run 已实际运行：45 个
唯一图片全部通过哈希和路径预检，42 个有正式资产关系和受限上下文，3 个明确无上下文。安全审计
将 1 张含凭据样式内容的截图按哈希标为 local-only，最终为 44 dry-run、1 policy_rejected。
评测工作簿 45 行中有 43 行参考答案，但审核状态仍全部为“未审核”；dry-run 评测正确得到 0 个
机器结果和 45 个缺失结果，没有生成虚假分数。报告、上下文及机器输出增加凭据样式脱敏，原图
仍依赖显式 local-only 策略，不能依赖 OCR 前的文本规则保证安全。

当前进程没有 `DASHSCOPE_API_KEY`。未显式 dry-run 的单图调用在 provider 构造和网络请求之前
自动退回预检并返回非零状态。因此真实 1～3 图连通性、45 图云端基线、usage、成本、质量人工
分级和二次运行全缓存命中仍待凭据/服务条件验证，不能标为完成。最终自动测试数字和产物位置见
[第五轮验证记录](image-enrichment-validation.md)。

修改后全量结果为 105 passed、1 skipped；5 个真实样本回归、23 个 ZIP 缓存测试和 29 个
enrichment 专项均通过。Ruff 静态检查和 `src tests` 格式检查通过，八份 Schema 成功导出。
没有执行真实 API 请求，usage 和成本没有数据。刷新本地 editable 安装时使用了已有依赖且禁止
索引访问；没有 commit、push、PR 或远程资料上传。

### 第五轮补充：本地 `.env` 凭据入口

在保持 `DASHSCOPE_API_KEY` 环境变量最高优先级的前提下，enrichment CLI 新增项目根目录
`.env` 的默认读取和 `--env-file` 显式路径。读取器只接受 UTF-8 的简单 `名称=值`，不执行 shell
语法或变量展开，并拒绝符号链接、异常大小、重复密钥和歧义格式；日志只记录凭据来源，不记录
值。`.env` 与 `.env.*` 已加入忽略规则，空值 `.env.example` 可作为模板。本次没有新增依赖，
也没有写入或使用真实 API Key。

### 第五轮补充：无效结构化响应证据

首个三图真实连通性运行得到 2 个 success、1 个 `invalid_structured_output`；失败请求已获得服务
响应，但旧实现只留下通用错误，丢失模型原文、具体校验路径及该次 usage。用户随后仅重试该图片
并成功，确认输入图片、鉴权和 endpoint 可用，问题符合偶发结构化输出违规的表现。

此后 provider 会将无效响应的原始 `content`、模型/request ID、usage 和去除输入值的本地校验
错误交给 runner；runner 经过现有凭据样式脱敏后，将它与失败记录并排保存。非法结果仍不进入
成功缓存。run 汇总现在会计入已经取得 usage 的无效响应，避免把已发生的调用低报为零成本。
历史失败 run 不被追写或修改；该能力从后续调用开始生效。本补充未发起新的云端请求。
