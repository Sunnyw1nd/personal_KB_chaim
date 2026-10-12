# 使用指南

本文记录完整的安装、批量转换、单篇转换、输出解释和常见问题。架构、Schema 和技术取舍见
[设计说明](design.md)。

## 1. 安装

要求 Python 3.11 或更高版本。在项目根目录执行：

```powershell
python -m pip install -e .
```

如需运行测试和代码检查：

```powershell
python -m pip install -e ".[dev]"
```

安装后使用 `kb-normalize`。如果当前环境尚未暴露该命令，可在项目根目录临时使用：

```powershell
$env:PYTHONPATH = "src"
python -m wechat_html_normalizer.cli --help
```

## 2. 批量转换 ZIP

目录职责：

```text
samples/              # 原始 ZIP，只读事实来源
.work/samples/        # 自动生成、可重建的解压缓存
output/documents/     # 逻辑文档、修订与批量结果
```

将 ZIP 放入 `samples/` 顶层，然后执行：

```powershell
kb-normalize --normalize-samples `
  --samples-root samples `
  --work-root .work `
  --output-directory output/documents
```

批量入口会按稳定文件名顺序扫描顶层全部 `.zip`，对每个文件依次完成：

1. 安全检查和解压，或复用通过完整性检查的缓存；
2. 自动识别微信公众号或知乎专栏；
3. 生成该文章的标准 JSON、Markdown、报告、资源清单和正文图片；
4. 将成功或失败结果写入批量汇总；
5. 继续处理下一个 ZIP。

单个 ZIP 损坏、文章对歧义或来源不支持，不会中断其他 ZIP。命令退出码：

| 退出码 | 含义 |
| ---: | --- |
| `0` | 所有 ZIP 都成功标准化 |
| `1` | 批次完成，但至少一个 ZIP 失败或发生身份冲突 |
| `2` | 批次无法开始，例如根目录无效、没有 ZIP 或输出目录不安全 |

PowerShell 中可用 `$LASTEXITCODE` 查看退出码。

批量报告保存为 `output/documents/batch-processing-report.json`，每项包含：

- ZIP 文件名和 SHA-256；
- `hit` 或 `extracted` 缓存状态；
- 最终适配器、逻辑文档 ID、修订 ID、current 修订和导入决策；
- 逻辑文档目录、具体修订目录和 warning 数；
- 失败时的稳定错误代码与说明。

批量命令不会自动删除已经从 `samples/` 移走的文章输出，也不会清理其他 ZIP 的缓存。

常见 `importDecision`：`created` 新建逻辑文档；`unchanged` 同包重复；`equivalent` 异包但结果
等价；`resource_improved` 补图改善；`resource_degraded` 退化修订已保留但未替换 current；
`content_updated` 正文更新。完整规则见[修订管理说明](revision-management.md)。

## 3. 只准备一个 ZIP

排查单个 ZIP 的解压或缓存问题时使用：

```powershell
kb-normalize --prepare-sample "samples/MCPzhihu.zip" `
  --samples-root samples `
  --work-root .work
```

该命令只准备工作副本并返回 HTML、资源目录和缓存清单位置，不执行标准化转换。正常使用优先
选择批量命令。

## 4. 转换一篇已解压文章

```powershell
kb-normalize "文章标题.html" "文章标题_files" output
```

默认自动识别来源。调试时可以声明预期适配器：

```powershell
kb-normalize --adapter wechat "文章标题.html" "文章标题_files" output
kb-normalize --adapter zhihu "文章标题.html" "文章标题_files" output
```

兼容命令 `wechat-normalize` 和 Python API `normalize_wechat_article(...)` 仍然保留。

## 5. 输出内容

输出采用逻辑文档和不可变修订两层结构：

```text
output/documents/
└── weixin-可读标题--<documentId短前缀>/
    ├── document-record.json
    └── revisions/
        └── revision-<修订指纹前缀>/
            ├── document.json
            ├── document.md
            ├── processing-report.json
            ├── resource-manifest.json
            └── assets/
```

`document-record.json` 保存稳定来源身份、全部修订和明确的 `currentRevisionId`。标题后来变化时
不会自动移动既有逻辑文档目录。批量报告中的 `outputPath` 直接指向本次采用或匹配的修订。

| 路径 | 用途 |
| --- | --- |
| `document-record.json` | 逻辑文档、current 与修订来源包清单 |
| `revisions/<id>/document.json` | 某一不可变修订的正式标准文档 |
| `revisions/<id>/document.md` | 从同一 Document 渲染的人工预览 |
| `revisions/<id>/processing-report.json` | 适配器、统计、排除区域和分级消息 |
| `revisions/<id>/resource-manifest.json` | `_files` 中每个资源的处理决定 |
| `revisions/<id>/assets/` | 以逻辑资产 ID 命名的可解码光栅图片 |

新导入的微信和知乎文档统一使用 schema `2.0.0`。旧 `1.0.0` / `1.1.0` 输出仍可读取，且不会
自动移动。身份、修订、current 和完整性规则见[修订管理说明](revision-management.md)。

## 6. Python API

批量转换：

```python
from pathlib import Path
from wechat_html_normalizer import normalize_sample_archives

summary = normalize_sample_archives(
    Path("samples"),
    Path(".work"),
    Path("output/documents"),
)
print(summary.statistics)
```

安全准备并转换单个 ZIP：

```python
from pathlib import Path
from wechat_html_normalizer import normalize_saved_article, prepare_sample

sample = prepare_sample(
    Path("samples/MCPzhihu.zip"),
    Path("samples"),
    Path(".work"),
)
result = normalize_saved_article(
    sample.html_path,
    sample.resource_directory,
    Path("output"),
    source_sample=sample,
)
print(result.document.metadata.title)
print(result.import_decision, result.current_revision_id)
```

传入 `source_sample` 后，标准来源字段追溯到原 ZIP 和 ZIP 内相对路径，不记录 `.work` 绝对路径。

## 7. 缓存和安全边界

缓存以 ZIP 完整 SHA-256 区分版本。只有完成清单、ZIP 身份、策略版本、文件集合、文件大小和
逐文件 SHA-256 全部一致时才会命中；否则只重建对应 ZIP 的缓存。

安全解压逐项拒绝绝对路径、盘符路径、`..`、符号链接和特殊文件、加密条目、Windows 路径
冲突、异常文件数量或大小、异常压缩比及 CRC 损坏。实现不调用未经逐项验证的
`extractall()`。

解析器不执行 JavaScript、不加载 iframe、不访问远程图片、视频、链接卡片目标或其他资源。
批量输出目录不得位于 `samples` 或 `.work` 内。

默认解析限制：HTML 10 MiB、单个资源 50 MiB、资源总量 250 MiB、资源文件 2,000 个。

## 8. 支持范围

当前承诺支持：

- 浏览器完整保存、正文已静态存在的微信公众号文章；
- 浏览器完整保存、具有可靠专栏结构的知乎专栏文章。

不支持知乎问题、回答、想法、动态骨架、MHTML、通用博客、GitHub Pages、PDF、TXT、Markdown、
OCR、图片或视频理解、Chunk、Embedding、检索和 RAG。来源无法可靠识别时返回
`source_unsupported`，不使用任意网页正文回退。

## 9. 常见问题

### 批量命令返回 1

批次已经执行完，但存在单项失败。查看 `batch-processing-report.json` 中对应项的
`errorCode`；已经成功的文章输出仍然有效。

### 出现 `source_unsupported`

ZIP 已能安全准备，但其中主 HTML 不符合当前微信或知乎专栏识别条件。不要通过修改原 HTML
绕过识别；应确认输入页面类型，或在后续轮次增加正式适配器。

### 同一文章重新保存后会怎样

ZIP SHA-256 仍决定解压缓存和输入审计，但不再决定逻辑文档 ID。相同来源文章会进入同一逻辑
文档：完全重复为 `unchanged`，不同包但标准化结果相同为 `equivalent`，补图为
`resource_improved`。同正文的资源退化会保留为历史，但不会替换更完整的 current。

### 预览旧输出迁移

下面的命令只读取旧目录并写迁移建议，不移动、覆盖或删除任何旧结果：

```powershell
kb-normalize --preview-migration output/real-samples `
  --migration-report output/migration-preview.json
```

报告按新的稳定来源身份分组，列出建议逻辑文档、修订和 current。实际迁移不属于当前工具
范围。

## 10. 开发验证

```powershell
python -m compileall -q src tests
python -m pytest
python -m pytest -m real_sample
python -m ruff check .
python -m ruff format --check src tests
kb-normalize --write-schemas schemas
```

实际阶段验证数字记录在[开发报告](development-report.md)和
[真实样本验证记录](real-sample-validation.md)，不在本指南中固化。

## 11. 图片辅助标注

独立图片目录可生成带内嵌缩略图的 Excel。入口、填写步骤、来源匹配范围和本轮验证结果见
[图片标注说明](image-annotation.md)。该工具独立于文章导入，不修改 Document/Asset 契约。

## 12. 图片云端 Enrichment

图片机器解释使用独立入口和存储，不修改标准文章、Markdown 或人工工作簿。先执行不联网的
dry-run；确认外发范围后，只有显式加入 `--allow-external` 且环境变量或项目根目录 `.env`
提供了 `DASHSCOPE_API_KEY` 才会调用百炼。环境变量优先于文件。缓存、恢复、强制新 run、
结果评测和全部参数见
[图片 Enrichment 使用指南](image-enrichment.md)，本轮实测边界见
[第五轮验证记录](image-enrichment-validation.md)。
