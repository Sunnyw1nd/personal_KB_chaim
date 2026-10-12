# 个人知识库：静态文章导入工具

把浏览器保存的微信公众号或知乎专栏文章，离线转换为结构化 JSON 和便于阅读的 Markdown。
处理过程不会执行网页脚本，也不会联网下载资源。

## 快速开始

环境要求：Python 3.11 或更高版本。

```powershell
python -m pip install -e .
```

自行创建 `samples/`，将下载后的 ZIP 放入其中，然后一次转换全部样本。
GitHub 仓库不附带真实文章、测试图片或密钥：

```powershell
kb-normalize --normalize-samples `
  --samples-root samples `
  --work-root .work `
  --output-directory output/documents
```

命令会自动解压、复用有效缓存、识别文章来源并继续处理其他文件。某个 ZIP 无法识别时，
它会出现在汇总报告中，不会影响其余文章。

## 查看结果

每篇文章会进入一个可读、稳定的目录，例如：

```text
output/documents/
├── batch-processing-report.json
├── weixin-文章标题--<短ID>/
└── zhihu-文章标题--<短ID>/
```

从批量报告的 `outputPath` 打开当前导入对应的修订，主要查看：

- `document.md`：阅读和人工核对；
- `document.json`：后续知识库处理的正式数据；
- `processing-report.json`：警告和处理统计；
- `resource-manifest.json`：资源采用或排除原因；
- `assets/`：正文图片。

同一文章再次保存或补齐图片后仍归入原目录；历史修订会保留，不会破坏性覆盖。

## 转换单篇已解压文章

```powershell
kb-normalize "文章标题.html" "文章标题_files" output
```

目前支持微信公众号静态文章和知乎静态专栏文章。其他页面会明确报告为不支持，不会使用低
可信的通用网页提取。

## 更多文档

- [完整使用指南](docs/usage.md)
- [源码发布与本机资料范围](docs/github-publication.md)
- [稳定身份与修订管理](docs/revision-management.md)
- [解析器与数据结构设计](docs/design.md)
- [真实样本验证记录](docs/real-sample-validation.md)
- [开发与测试报告](docs/development-report.md)
- [人工验收清单](docs/acceptance-checklist.md)
- [图片 Enrichment 使用指南](docs/image-enrichment.md)
- [图片观察台与存储审计](docs/image-enrichment-observer.md)
- [第五轮图片 Enrichment 验证](docs/image-enrichment-validation.md)
- [第七轮六图校准验证](docs/image-enrichment-round7-validation.md)
- [本地图片路由器隔离验证](docs/image-router-benchmark-validation.md)

## 图片辅助标注

将图片放入 `test_pictures/`，生成带缩略图和下拉选项的 Excel：

```powershell
python -m pip install -e ".[annotation]"
python -m wechat_html_normalizer.image_annotation --input test_pictures --output output/image-annotation
```

打开命令返回目录中的 `图片辅助标注.xlsx` 开始填写。再次生成会创建新目录，保留已有标注。
操作步骤与限制见[图片标注说明](docs/image-annotation.md)。

## 图片云端基线

先安装可选依赖并确认即将外发的范围（dry-run 不联网）：

```powershell
python -m pip install -e ".[enrichment]"
kb-enrich-images run --dry-run --max-images 3
```

确认图片允许发送后，在项目根目录 `.env` 的 `DASHSCOPE_API_KEY=` 后填入密钥，并显式加入
`--allow-external` 才会调用百炼。也可以使用同名环境变量，完整说明见下方文档。
机器结果单独写入 `output/image-enrichment/`，不会改写文章 JSON、Markdown 或人工工作簿。
严格结构化输出校验失败时默认最多受控重试一次，逐次证据和 usage 会分开保留。
完整命令、安全边界和评测方法见[图片 Enrichment 使用指南](docs/image-enrichment.md)。

可选的精简普通 JSON 配置先这样预演：

```powershell
kb-enrich-images run --profile compact-json-v1 --dry-run --max-images 3
```

该配置提供有限次数的错误恢复，生成结果仍需质量审核；默认配置暂不切换。
运行、审核和恢复命令见[精简 JSON 使用说明](docs/image-enrichment-compact.md)。

已有图片运行记录可离线生成观察台，再双击输出的 `index.html` 查看：

```powershell
kb-enrich-images observe build
```

入口是 `output/image-enrichment/observer/index.html`；观察台只读、不调用模型，详情见
[图片观察台与存储审计](docs/image-enrichment-observer.md)。
