# 第十五轮：本地TXT、Markdown与独立图片验证

日期：2026-10-14。结论：三类输入的本地工程闭环通过；云端新上下文效果和用户内容质量验收未执行。

## 现场与保护

工作区`E:\personal_KB`；main/`948bf838c978365158a1cbf52ed8fd8fddc13813`，origin为原GitHub仓库。
开始时工作区干净，读取本机AGENTS、架构/路线、工作台及第十四轮报告和实际来源/存储/图片代码。
本轮不checkout历史版本、不stage/commit/push/PR，不移动私人资料。修改前185 passed、1 skipped，
编译、Ruff及格式检查通过。Python3.12.2、Pydantic2.12.5、Pillow11.2.1、Flask3.1.0；当前OpenAI
SDK2.54.0，而非第十四轮历史报告的3.14.1；本轮没有修改主环境SDK。

1176份保护文件的原始哈希在`.work/round15/before.json`，包括原始ZIP/图片/工作簿、原Schema、
Frozen Prompt、config和历史output/实验证据。完整性检查`integrity-final.json`：checked1176、
changed空，新增仅4份独立reader Schema。未改人工参考答案、原始文档、审核历史或生产cache。

## 实现与取舍

新增`application/local_imports.py`，集中离线编码、Token适配、受控图片关联、重复候选和显式身份。
Workbench.import_files统一新上传入口；保存页仍复用prepare_sample和normalize_saved_article，
所有来源共用publish_revision、图片任务、compact、审核和人工版本。没有复制模型runner或重试逻辑。
旧import_archives、三个CLI和Python公开入口保留。Markdown资源采用明确主MD的ZIP，未建设无限制
文件浏览器、YAML执行、多格式采集或新UI框架；同名/同字节不是自动合并授权。

模型与存储增量：SourceType增加local_text/local_markdown/local_image，新LocalSource没有假HTML
字段；新文档/报告/资源3.0，Record1.1记录原始输入与快照SHA，local-持久随机ID不依赖输入SHA。
ListItem增量depth/ordered/number及plainText用于本地文字/列表；None不进入旧HTML序列化或指纹。
旧Schema仍原位原字节，新reader输出文件名带版本；runtime读取旧文档/Record，旧外部reader需升级。
迁移预览明确拒绝将未管理local来源误算为知乎；没有执行数据迁移。

修订仍比较内容与资产：重复已知目标unchanged、新导入同快照duplicate_candidate、不同正文
content_updated、MD仅补齐图片resource_improved、退化不提升current。逻辑assetId与二进制SHA分开，
独立图片固定正文位置，MD按引用和同引用出现次序分配，不因补图改变其他逻辑图片身份。
输入、输出根不得重叠；原图只读，复制原字节，不旋转/重编码EXIF。处理后JSON大小有10MiB上限，
正文图片引用/复制总量也受安全预算限制，避免小输入重复引用制造无限输出。

工作台新增混合文件上传、编码选择、MD主路径、文档页“导入新版本”、重复候选按钮。列表显示
来源和短ID以区分同名文档，图片显示实际格式/尺寸/字节数。正文用已有结构化阅读，不以整份JSON
替代TXT/MD阅读。审核JSON美化只登记到roadmap，本轮没有展开重构。

完整支持/降级规则、路径/编码/EXIF与命令见[输入指南](local-inputs.md)。主文件与全部引用状态进入
快照，未关联附件只盘点；远程图不下载、不写进前端img.src。公式/脚注/HTML/front matter可见
内容保留并告警，未宣称支持插件完整结构。示例包主MD始终显式选择，多个候选没有自动猜测。

## 本机浏览器验收（不是用户人工验收）

工具`python tools/local_formats_browser_check.py`，Edge137.0.3296.83＋现有Playwright，无界面。
最新完整证据`.work/round15/browser-cf81ed/validation.json`，TXT/MD缺图/MD补图/review PNG均实际
查看；pageErrors空、externalRequests空、inputsUnchanged=true。服务只监听127.0.0.1:8768，结束停止。
验收数据区单独位于该证据目录，不把fake结果写入生产cache/用户工作台。

| 本地输入 | 首次/重复/显式更新结果 | 读取与图片闭环 |
| --- | --- | --- |
| 合成中文UTF-8 BOM TXT | created→duplicate_candidate→目标unchanged→content_updated | #不变标题、缩进/换行可见，2历史修订 |
| 合成MD资源ZIP | created→duplicate_candidate→unchanged→resource_improved→resource_degraded | 标题/加粗/嵌套列表/引用/代码/表格可读；1缺图补至2可用，退化仍保留完整current |
| 原45图中asset-ce52ad0983de.webp的原字节副本 | created→duplicate_candidate→unchanged→明确更换PNG新版 | 无工作簿准备任务；fake结果、待复核、独立草稿/采用可见；新二进制无旧人工采用版本 |

独立图片测试副本保留原文件名，没有向请求添加人工类型/价值/参考答案。fake模型固定输出明确
标注“只验证界面及持久化”的文本，不能据此评价装饰图识别质量。唯一首次fake调用1次；刷新与
真实关闭/重启后明确再运行，命中fake机器缓存、额外fake调用0。人工草稿采用是codex_test，
机器只登记needs_review（旧审核契约reviewer字面值仍是user，reason明确Codex功能测试），未登记
用户认可。原图、原文、工作簿不修改，机器与人工记录仍分离。

**真实云端调用0，真实usage/费用无新增。** 3次真实调用授权未使用；不同上下文没有冒用历史缓存。
云端新输入效果/恢复率未实测，只复用现有可靠性工程和fake验证，用户质量验收另列待执行。
早期浏览器脚本发生等待旧状态/未初始化selection的竞态，以及fake漏finishReason导致缓存重校验
拒绝；修正验收脚本/fake协议后完整通过，早期临时证据仍保留，不伪装为成功或更改生产校验。

## 自动化、安装与安全

实际命令和范围：

- `python -m pytest -o addopts='' -q`：最终212 passed、1 skipped；原185＋27个新参数化测试。
- `python -m pytest -o addopts='' -q -m real_sample`：5 passed，微信/知乎与缺图补图真实回归；
  自动ZIP准备，不手工解压真实样本。
- `python -m pytest -o addopts='' -q tests/test_local_imports.py tests/test_workbench.py`：39 passed。
  编码/BOM/无效输入、字面文本、Token/代码/表格/图片顺序、远程零网络、路径/ZIP限制、重复/
  新建/明确更新、EXIF/真格式、输出预算、写入失败保护、fake审核重启与混合失败隔离。
- Source/新ID贯通observer通过合成inventory关系验证，位置与ID准确；观察台入口仍按指定inventory
  范围收录，不自动扫描所有私人工作台资料。工作台查看新来源不需要inventory/Excel。
- `python tools/export_local_schemas.py`：4新reader Schema与模型一致；旧契约没有重序列化。
  JSON Schema验证在本机已有jsonschema完成，测试在未安装该可选验证库时明确跳过该项。
- `python -m compileall -q src tests tools`、`python -m ruff check src tests tools`、
  `python -m ruff format --check src tests tools`以及`git diff --check`通过。
- `python -m pip wheel . --no-deps --no-build-isolation --wheel-dir .work/round15/final-wheel`，
  独立wheel-env非editable安装。对应新格式/工作台测试39通过，3静态资源存在，4个console script
  help可用。依赖复用system-site-packages，未冒充全新依赖求解。具体wheel为本地可重建产物。
- 只读审计工具检查README/docs相对链接及新浏览器data/enrichment/documents JSON；无Bearer、
  API Key样式、Authorization或图片base64。仅扫描明确证据根，不读取.env或整个磁盘。

新增依赖声明markdown-it-py>=3,<4（已有3.0.0）及现有传递mdurl0.1.2；MIT许可、版本依据与
替代方案见输入指南。没有新增模型/UI依赖、没有升级主环境。代码库设计技能用于集中适配和存储
接缝；代理文档技能用于精简AGENTS能力导航，阶段数据只进本报告。

## 未执行与下一步

未进行新的真实识图、模型质量评分、用户本人桌面验收、完整44/45图、LAN/URL/Office/PDF/OCR/
检索。没有改Prompt、清理、移动历史或发布。复杂容器/表格富格式/插件有明确降级，编码歧义需
人工选择与核对；只有本轮三类格式，不是通用文档解析器。

用户可重启工作台后：导入自己的TXT→检查编码；用主MD路径导入资料包→查看缺图→明确更新补图；
导入独立图→核对格式/大小→按自己的数据许可选择是否外发→用户审核/保存修改；重启再查看。
下一步优先微信/知乎URL获取独立小验证；近期登记可读图片结果和折叠诊断，不在本轮继续扩展。

本轮一次性产物仅保留清理建议：早期browser-*失败脚本证据、合成输入ZIP、临时wheel-env与wheel、
`.work/wb/md-*`副本可在用户另行确认后重建；最终validation/截图/fake人工历史宜先保留验收证据。
没有实际删除路径或删除授权；生产run/cache/人工资料不列入临时清理范围。
