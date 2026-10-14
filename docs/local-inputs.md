# TXT、Markdown与独立图片：使用和契约

## 推荐操作

按[工作台指南](workbench.md)安装并运行`kb-workbench`。导入页可混合选择保存页ZIP、TXT、MD、
JPEG/PNG/WebP。导入本身完全离线，不加载原HTML、不请求远程图、不启动模型。

本地文件首次导入新建逻辑文档；重复字节/资源快照提示“重复候选”，可点击明确更新某个候选，
或仍作为新文档。同名异内容不合并。更新请从对应**文档页**使用“导入新版本”，只接受一个
同格式文件/MD资料包；改名也仍归明确指定的文档，标题变化不移动既有目录。
图片格式之间可更新（同属local_image），TXT与MD不能互相覆盖；保存页继续依据来源URL身份。

### Markdown带图片

浏览器不能读取旁边资源目录。将MD及图片保留相对位置打包ZIP，在导入页填写主文件路径，例如
`article.md`或`notes/article.md`。存在多个MD时仍必须明确主文件；留空只尝试原保存页HTML规则，
不会挑第一篇MD。批次中填写的主路径适用于其中每个ZIP；混合不同资料包时分批填写。
单独上传MD可正常阅读，但旁边图片会报告缺失；不要把另选独立图片误认为自动关联附件。
图片补齐时重新打包并从文档页更新；MD字节没变也能形成资源改善修订。缺图版不覆盖更完整current。

可用标题来自MD第一个标题，否则来自原文件名（不含扩展名）。TXT/独立图片使用文件名。
作者、URL、发布时间缺失时为null，没有用本机修改/导入时间冒充发表时间。

## 格式规则

TXT：UTF-8、UTF-8 BOM、UTF-16 BOM；非UTF中文经既有charset-normalizer检查，优先GB18030，
再尝试Big5并报告检测不确定。短文本存在歧义，可在界面/CLI明确选编码。空/全空白、控制字符、
损坏解码、10MiB以上、图片/ZIP/PDF伪装为文本拒绝。空行分段，保留段内换行/缩进；不解释#或
编号。JSON段落带plainText，阅读为pre，派生Markdown使用literal text围栏，避免意外变成标题/列表。
这不是对字体、Tab宽度及复杂版式的逐像素复刻。来源定位使用1起始含端点行范围。

Markdown：markdown-it-py3的CommonMark Token＋table规则，保留标题、段落、必要嵌套列表、引用、
代码、分隔线、strong/emphasis/inline code/安全链接、表格和图片顺序。代码不重排/修语法。
列表用items.depth/ordered保存层级；容器内复杂代码独立成块，多段列表项降为独立项并告警。
表格单元格保留可见文字，富格式降级并告警；表格内图片仅留alt并告警，不承诺复杂嵌套容器完整结构。
YAML front matter原样作为代码保留，**不解析任何元数据键**，不加载YAML构造。
原始HTML/脚本/iframe只保留literal文字；事件属性不会成为执行元素。公式/脚注/插件不执行，
保留可见文本或literal定义并告警；解析器当成引用定义的脚注在正文后保留，原始行位置需查原MD。
原始MD保留在上传证据中，document.md是独立派生文件。

Markdown图片路径：URL百分号解码一次，拒绝残留编码路径语法、盘符/绝对路径/反斜杠/链接或
junction逃逸；`../`仅在最终仍在声明输入根内时允许。主MD路径本身不允许`..`。
HTTP/HTTPS图仅保留原URL和未获取占位；前端不会将其写入img.src。无关联附件仅盘点/排除，
不生成图片任务。ZIP使用既有2000条目、每文件50MiB、总250MiB、压缩比200、Windows冲突、
加密/特殊条目拒绝规则，逐字节/CRC验证；工作副本只写受控`.work/wb/md-*`，失败副本不当成功。

独立图片：每图一文档，真实解码仅JPEG/PNG/WebP，单文件20MiB、4000万像素上限，拒绝动画/
多帧、损坏或其他真实格式。识别格式不靠后缀；PNG字节叫.jpg仍记录真实PNG。原字节复制，SHA
就是实际传输字节；不主动旋转/重编码/改EXIF。浏览器可按EXIF方向显示，但资产宽高是原始尺寸，
Provider仍传原字节且缓存原SHA。不推断多页、OCR文字或凭空生成正文。人工版本不会跨新二进制/
上下文修订沿用为已审核事实。

## 身份、修订与兼容

新建local-<持久随机16hex>，独立于包SHA、文件名、临时路径。随机值只在明确新建时产生；
之后显式documentId更新，上传重试由持久requestId防重复。不是按标题合并，也不以输入SHA永久
充当文档身份。碰撞由既有目录/来源冲突检查拒绝。重复候选扫描已知记录的inputSetSha256s，
匹配当前/历史快照；同字节改名也提示候选。人工“仍新建”允许多个独立逻辑文档保留相同内容。

快照指纹=主文件实际SHA＋正文引用资源状态/路径/真实格式/尺寸/SHA。独立图片仅原图SHA；
不关联附件不决定修订。输入字节、原名、实际编码/格式、主文件路径、资料包SHA分别可追溯。
资源缺失变可用会改变快照和资产指纹，内容/资源组合通过既有修订规则发布；正文更新成为current，
同正文资源退化保留旧current。逻辑Asset按引用＋出现次数（独立图按固定位置）生成，二进制变化
不重新分配逻辑位置ID。修改引用地址本身会形成新位置身份，暂不做语义相似合并。

LocalSource替代HTML来源字段；Document/Report/Manifest新输出3.0.0，Record1.1.0新增实际输入/
快照证据，htmlSha256s对本地记录为空，未伪造HTML。runtime支持原1.0/1.1/2.0保存页文档与1.0
Record；微信/知乎仍输出2.0/1.0，ID/指纹/默认处理器不变，不重写历史。新reader Schema位于
`schemas/*-v3.schema.json`和`document-record-v1.1.schema.json`，枚举包括可读取的旧版本；
原Schema按原字节保留。旧外部reader不能读新来源，需升级，不保证旧正则理解local-前缀。
Enrichment请求AssetReference原本支持任意非空documentId，无需改其正式Schema或Frozen Prompt。

所有来源共用publish_revision、正式图片桥接、compact、审核与独立人工历史，不依赖Excel。
每个请求仍按图片SHA＋上下文和配置缓存；不同标题/正文可能不能复用旧缓存，不冒用。默认外发
关闭，选定图片/上下文并确认才授权；local-only不绕过。导入/刷新/查看不会调用模型。

## Python与命令

应用接口：`application.local_imports.import_local_file(input_root, main_path, output_root, ...)`；
工作台使用`Workbench.import_files`，旧`import_archives`及三个旧CLI/公开Python入口保留。
CLI读取声明输入根内的资源，建议每份资料放独立目录，不把整个项目/磁盘作为输入根：

```powershell
python -m wechat_html_normalizer.application.local_imports "我的资料/笔记.txt" --output-directory output/documents
python -m wechat_html_normalizer.application.local_imports "我的资料/article.md" --input-root "我的资料" --output-directory output/documents
```

更新加`--update-document local-<完整ID>`；重复候选退出2，明确另建加`--create-new`；编码可加
`--encoding gb18030`或`big5`等。参数中的示例目录需要自行存在；命令形态由自动测试覆盖。
导出新reader契约`python tools/export_local_schemas.py`；它拒绝静默覆盖已存在的不同契约。
旧`kb-normalize --write-schemas <新目录>`导出当前reader模型，不要用它重写保留的旧契约目录。

## 依赖与后续

新增基础依赖声明markdown-it-py>=3.0,<4，本机已安装3.0.0，mdurl0.1.2也已存在，无环境升级。
[Token和行号官方用法](https://markdown-it-py.readthedocs.io/en/latest/using.html)；
[解析器MIT许可](https://raw.githubusercontent.com/executablebooks/markdown-it-py/v3.0.0/LICENSE)、
[mdurl许可](https://raw.githubusercontent.com/executablebooks/mdurl/0.1.2/LICENSE)。替代方案Mistune/
Python-Markdown要另补位置/插件一致性；现有markdown-it满足语法树/源位置要求，避免正则堆叠。
维护风险是版本/扩展语法差异，当前冻结主版本、不开插件/执行HTML，未来升级须单独回归。

当前仍不支持URL获取、Office/表格文件/PDF、通用OCR、其他图片、多用户或检索；不是通用文档
解析器。可读图片审核展示待近期UI改进，原JSON仍是诊断证据。实际验收见
[第十五轮验证](local-inputs-round15-validation.md)。
