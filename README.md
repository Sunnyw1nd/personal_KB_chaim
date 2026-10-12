# 个人知识库：本地资料处理工具

将保存的微信公众号、知乎专栏文章转成带修订历史的JSON与Markdown，并提供独立图片标注、
可审核的云端图片处理、只读观察台和本地操作工作台。多格式导入和检索仍在规划中。

## 使用本地工作台

```powershell
python -m pip install -e ".[workbench]"
kb-workbench
```

打开 `http://127.0.0.1:8765/`，拖入文章ZIP，查看修订，选择正文图后显式识别，再审核或保存人工
修改。默认不会调用模型；密钥只留后端。终端Ctrl+C停止。[完整操作与数据说明](docs/workbench.md)。

## 快速开始

需要Python 3.11或更高版本。在项目根目录安装：

```powershell
python -m pip install -e .
```

自行创建`samples/`，放入浏览器完整保存的文章ZIP，然后批量转换：

```powershell
kb-normalize --normalize-samples `
  --samples-root samples `
  --work-root .work `
  --output-directory output/documents
```

原始资料不修改，不执行网页脚本，也不联网补图。GitHub不附带真实文章、图片或密钥。
同一文章重新保存/补图仍归入原逻辑文档，旧修订保留。

## 结果在哪里

打开`output/documents/batch-processing-report.json`，按每项`outputPath`找到修订目录：

- `document.md`阅读正文；`document.json`是标准结果；
- `assets/`查看正文图片；两个report/manifest文件解释警告与资源处理。

若某项失败，查看批量报告的错误；其他成功项仍保留。只承诺微信静态文章与知乎静态专栏，
其他页面不使用通用正文回退。单篇命令、current与常见问题见[完整指南](docs/usage.md)。

## 图片功能

图片标注需要自行准备`test_pictures/`：

```powershell
python -m pip install -e ".[annotation]"
python -m wechat_html_normalizer.image_annotation --input test_pictures --output output/image-annotation
```

打开返回目录中的`图片辅助标注.xlsx`填写；再次生成不会覆盖原标注。
云端图片处理需[准备图片清单和凭据](docs/image-enrichment.md)，显式允许外发才会发送图片，
结果仍需审核。已有运行数据可离线生成观察台：

```powershell
kb-enrich-images observe build
```

双击`output/image-enrichment/observer/index.html`查看；没有图片清单或历史运行时，请先按图片
指南准备。观察台仍只读；导入/识别/编辑请用上述工作台。

## 更多文档

- [完整文档导航](docs/index.md)：使用、契约、实验与验证；
- [图片标注](docs/image-annotation.md)、[图片处理](docs/image-enrichment.md)、
  [精简JSON可选配置](docs/image-enrichment-compact.md)、[观察台](docs/image-enrichment-observer.md)；
- [当前架构](docs/architecture.md)、[本地工作台路线](docs/roadmap.md)；
- [源码发布与本机数据](docs/github-publication.md)。
