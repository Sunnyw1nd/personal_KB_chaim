# 第十二轮：精简普通JSON与受控错误恢复验证

日期：2026-10-12。结论：`engineering_passed_quality_limited`。

新的可选配置 `compact-json-v1` 已完成错误出口、调用/时间预算、严格映射、审核隔离、缓存和离线
恢复测试；原六图完成一次真实运行及零调用缓存复跑。六图结构全部通过，内容仍需用户审核，
没有宣称自动无审核入库可用。正式CLI默认仍是legacy/v1，不运行44/45图、不继续调Prompt。

## 现场与开始前证据

- 工作区 `E:\personal_KB` 未使用Git；没有分支/HEAD、commit、push或PR。
- Python3.12.2；OpenAI SDK3.14.1、httpx0.28.1及SDK使用的httpx2、Pydantic2.12.5、Pillow11.2.1、
  openpyxl3.1.5。没有新增或升级依赖。SDK环境版本与pyproject中的 `<3` 声明仍不一致，未擅自调整。
- 第十一轮报告/实际plan/C响应已读取；确认C完全省略response_format。A/B/C/D矩阵没有重跑。
- 当前正式结果已有architecture_flow、code、table、chart、other、manual_review，足以表达本轮
  结果；旧运行契约缺独立审核状态及恢复信息，故仅对运行/记录做1.1.0扩展，结果仍1.0.0。
- 修改前启动了全量回归，现场源码与上一轮133 passed、1 skipped基线一致；该次工具输出未完整
  收取，因此不另声称它产生了已留存的完整基线报告。修改后所有回归结果独立列在下文。
- 开始修改前保存272份原图/工作簿/inventory/历史run/cache/evaluation哈希和源码/Schema清单。
  用户既有文件未被覆盖；非Git环境无法区分其他未提交改动，因此采用精确文件范围和哈希核验。

## 实现与契约

新增：`enrichment/compact.py`、`compact_provider.py`、`compact_runner.py`、`reviews.py`、
`prompts/compact-json-v1.md`、`tests/test_compact_enrichment.py`、精简输出/审核Schema及使用文档。

修改：正式CLI、运行模型、缓存检查、Provider错误元数据、观察台索引和少量既有页面字段；
README增加可选配置预演入口。Document/Asset、HTML/ZIP/文档身份处理和默认Prompt未改。

精简模型JSON采用imageType判别的互斥分支，只要求当前分支字段：

| 类型 | 核心字段 | 正式映射 |
| --- | --- | --- |
| 代码 | codeText、language、uncertainties及少量类型/价值字段 | 唯一codeText原样映射code.text，不填重复OCR正文 |
| 结构图/UI/照片 | description、keyFacts、uncertainties | describe及同名内容字段，结构图不塞入chart |
| 表格 | headers、rows、mergedCells | 同名单元格；textRepresentation仅按已得内容确定性串联 |
| 量化图表 | axes、legends、series、trends、keyValues等 | chart字段；保留模型输出，不补造数值 |
| 文字/装饰/Logo | extractedText、description、retrievalDecision | 文字或排除/复核动作；high+exclude被业务校验拒绝 |
| uncertain/other/二维码待支持 | reason、非空uncertainties | other/qr_code与manual_review，不伪装识别成功 |

模型仍要判断类型，所以本轮配置不是第十一轮已知类型C的原样重复：增加类型选择与六类任务支持。
不能把本轮提升单独归因为普通JSON。服务端省略response_format、tools、tool_choice；本地严格
类型/多余字段/互斥/业务校验没有放宽。

Prompt版本为 `compact-json-v1`，完整SHA-256：
`7db1cca91f4131ff32ea18b367603ab6147d2a1bc9fc2df9ddcc8905da0621f2`。
映射、校验和恢复分别是compact-mapping-v1、compact-validation-v1、compact-recovery-v1。
首次真实运行后未改Prompt、未通过反复试验选更好答案；随后仅收口安全、展示和模拟故障边界。

新run/record写Schema1.1.0，读取器同时接受1.0.0；新增profile、审核、机器校验、finish reason、
恢复模式及问题路径。中断导致未知耗时/完成时间可为null，不补造0。旧输出保留；严格只识别1.0.0
的旧消费者需要升级读取器。请求/结果Schema仍1.0.0，二者导出字节与原Schema一致。

## 恢复边界

- 每图最多3次：首次1、传输恢复最多1、结果恢复最多1；整批最多18次。未用预算不可转用。
- SDK显式max_retries0；旧legacy重试参数不叠加。保留旧默认行为，修复没有扩大到legacy重试重构。
- 网络超时/连接、429和明确500/502/503/504可恢复一次；Retry-After被尊重，但超过剩余截止即停止。
- Key/权限、模型不匹配、参数错误等永久错误停止后续生成，保留已完成项目。
- 初始4096 tokens，截断恢复上限8192；每图截止默认180秒，每次SDK网络调用总等待最多90秒，
  使用asyncio可取消等待。恢复/等待都受同一剩余时间约束。
- JSON错误、缺字段、错类型、互斥矛盾、可证明业务问题、截断共用一次结果恢复；不循环修补。
- 只剥离完整外层JSON围栏、外围空白/BOM；重复键拒收，不猜引号/括号，不改代码大小写或缩进。
- 缺失视觉正文/类型不明时带同图重生成；已有正文而仅元数据/空占位结构不合规才可文本纠错。
  纠错原文是无指令权限的数据。共同关键内容、代码、数字变化会保存，改变或无法证明无损则待复核。
- 人工答案不进入首次或纠错请求；代码逐字/虚构箭头无法独立判断时不虚称自动已确认。

每次响应在解析前原子落盘。汇总命令不创建Provider；收到原文后流程中断，恢复已知usage，缺失
时间保留未知，尚未发布的任务标为incomplete/needs_review，不自动补成成功cache。请求超时或
中断可能已被服务计费，记录的是客户端尝试与已知响应，不声称服务端strict exactly-once。

模拟中发现并修复Windows长路径问题：任务目录使用短SHA、完整SHA保存在task.json，短名冲突调用
前拒绝；更长自定义根/run名仍应选短路径，不修改Windows全局设置。

## 缓存与质量

身份覆盖图片、上下文、模型/endpoint、Prompt/精简Schema哈希、普通生成模式、映射/校验/恢复版本、
推理与截止预算。未查询或误用旧v1/v2失败缓存。成功机器缓存读取时重新校验原精简JSON与正式映射。

质量审核独立：pending、needs_review、approved、rejected。用户决定保存在reviews下独立event与
原子latest decision，机器/历史结果不改写。否决会阻止可用缓存复用并保留否决状态；观察台分开展示
机器校验、审核状态和缓存资格。实际六图均未被用户批准，Codex等级仅在evaluation中记录。

## 六图真实运行

使用历史 `qwen37-flash-reliability6-v1` 的六个完整ID，保持原WEBP/JPEG字节和相同受限上下文。
全部允许外发，local-only未进入选择；没有替代/脱敏样本，无LAN调用。模型仍为
qwen3.7-flash-2026-07-15，北京endpoint，temperature0、enable_thinking=false、非流式。

| 文件 | 完整SHA-256 |
| --- | --- |
| asset-0007.webp | 50933b2e0795615983dbae141f9c641af658c01428fc74634edd8e9742edad86 |
| asset-367af9dd1487.webp | af5e261205ecfed7a484d7c731c7b22f52a2a847aa208f2b60a18cb6c65d5910 |
| asset-44fdbea6544d.webp | b3d854ff82aee737216c582c6014e075e6c01f7e3a80d387c39a42eafe76debc |
| asset-57ae62ca4266.webp | 1772a86a25b6befbdb5974230f16b6ca6fbbea4811c5e7d19d6f579270c4343e |
| asset-ce52ad0983de.webp | a9712da9954294f5afcb76cfe14e8aa98dcc9da25aa1247721b24801c0934d91 |
| asset-dba5466c7c5e.jpg | 9e8b94e1ea7b8b37cd7974bcc28dee8698663da98cc01e6c5c5f99af7edcb39a |

运行记录：

- `output/image-enrichment/runs/qwen37-compact6-v1-20261012-dry/`：6 dry-run，API0；
- `output/image-enrichment/runs/qwen37-compact6-v1-20261012/`：6首次结构成功，API6；
- `output/image-enrichment/runs/qwen37-compact6-v1-20261012-cache/`：6 cached，API0。

实际首测6、传输恢复0、结果恢复0、最终结构失败0、缓存命中主run0/复跑6、待审核6。所有实际模型
标识正确，finish reason均stop；每次均取得usage，未知usage0。未刻意诱发真实失败，故恢复率只有
模拟验证，没有新的真实恢复证据。没有使用额外调用预算继续优化答案。

| 图 | input | output | image | total | 耗时ms | Codex等级与内容限制 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 结构图 | 1,701 | 342 | 498 | 2,043 | 6,312 | 实质修改：类型正确，三步骤/136/200齐全；但将独立箭头说成指向下方方框，目标关系应修正 |
| 表格 | 1,613 | 310 | 542 | 1,923 | 2,360 | 无需修改：3列、7行和对应内容准确 |
| 代码 | 1,475 | 190 | 352 | 1,665 | 2,110 | 基准待复核：唯一字段完整16行，TEXT/反斜杠/缩进保留；仅逗号后空格与人工参考不同，原图需放大确认 |
| 图表 | 1,744 | 382 | 648 | 2,126 | 3,938 | 轻微修改：轴和趋势正确；临界/能力解释应标为示意文字，不作实测结论 |
| 装饰文字 | 1,189 | 98 | 162 | 1,287 | 1,204 | 轻微修改：文字保留；透明底误写白底，OCR/include与exclude基准策略待用户判断 |
| UI | 1,936 | 270 | 902 | 2,206 | 2,938 | 无需修改：两工具、txt_counter、可见数字2准确，不再扩成两个活跃服务 |

六次合计：input9,658、output1,592、image3,104、cached input0、total11,250 tokens；响应/任务计时
合计18,862ms，不包含批次任务之间限速等待。按2026-10-12已核对的[官方快照北京公开原价](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)，输入<=32k的0.2/0.8元每百万tokens估算
**0.0032052元**，未查询账户实付/优惠；image已计入input，不重复计费。估算不写入永久价格常量。

旧六图v1/v2的主要代码字段缺失和结构图错chart得到改善，但还不能宣称完全事实正确。代码参考
工作簿有18行（多2空行），本轮同时保存工作簿与原图16行的逐字差异，不通过删空格来伪造一致。
查看原图后发现图表确实近似平滑S形，旧评审“阶跃图”说法需复核；没有机械将旧标签当绝对真值。

逐图原图/原文/首次结果/历史对照/Code diff在：
`output/image-enrichment/evaluations/qwen37-compact6-v1-20261012-20261012T040521Z/round12-quality-review.md`。
对应quality-review.json明确reviewer=Codex、userAcceptance=pending。用户尚未完成六图验收。
原评测入口读取全部45行，因此baseline-evaluation中的39 missingResults是未选样本，不是39次失败。

## 观察与安全完整性

现有观察台已重建到 `output/image-enrichment/observer/round12/index.html`，45个缩略图均成功，88页。
可查看profile、机器校验、审核状态、恢复方式、finish reason、attempt原文和evaluation，未建新UI。
本轮只完成程序化映射/页面生成验证，没有桌面浏览器人工验收。

最终映射核对确认：6张图片均可反查本轮run/cache/evaluation，6个cache当前校验valid且审核pending，
6份attempt原文存在；缓存复跑API0。92份本轮run/cache JSON安全扫描未匹配凭据或图片data URL。
汇总证据保存于`.work/round12-baseline/final-verification.json`。

当前源产物审计：250文件、733,405字节；cache152,392、runs331,935、evaluations249,078字节，
reviews尚无用户决定。只读清理预览为保留36、候选1、待确认0，候选预计13,808字节；“候选”不是
删除授权。观察台/缩略图不计入这些源产物空间。已有2项旧业务校验问题与本轮新cache区分展示。

272份原图、人工工作簿、inventory和历史云端/LAN/实验run/cache/evaluation SHA-256未改变，证据为
`.work/round12-baseline/authority-after.json`。微信/知乎/ZIP配对真实回归同时验证原ZIP哈希不变。
安全扫描未发现Key、Authorization/Bearer或图片Base64/data URL；仅发送原六图与少量正文。
API错误只保留选定诊断字段，不复制服务可能回显的请求头/完整请求体。

观察台有两项既有v1缓存 `cache_business_validation_failed`（693c29e…、c5877487…），原文件保留。
新增六个compact cache均能重新校验和复用，没有把既有错误记成本轮新增回归。

## 自动测试与检查

实际完成：

- 新配置专项：`python -m pytest -ra tests/test_compact_enrichment.py`，35 passed；
- 相关Enrichment回归：92 passed（包含上述35项）；
- 全量：`python -m pytest -ra`，168 passed、1 skipped；跳过项为当前Windows无法创建符号链接；
- 真实样本：`python -m pytest -ra -m real_sample`，5 passed、164 deselected；
- 编译：`python -m compileall -q src tests experiments/compact_json_validation`，通过；
- Ruff：`python -m ruff check src tests experiments/compact_json_validation`，通过；
- 格式：`python -m ruff format --check src tests experiments/compact_json_validation`，53 files already formatted；
  只检查源码/测试，不用格式器改写模型证据Markdown；
- `kb-enrich-images write-schemas`：运行/记录与新compact/review Schema已同步，request/result不变。

测试覆盖正常/包装/非法JSON、缺字段/错类型/互斥、传输恢复、永久错误停止、Retry-After截止、截断
预算共享、纠错后传输恢复、3次/18次上限、Async SDK总超时、SDK0隐藏重试、精确代码转义、unknown
usage、中断后原文/usage恢复、缓存身份、人工否决、dry-run/策略拒绝、敏感回显过滤、内容差异审核。

开发中的两次Schema匹配失败来自新运行字段尚未导出，导出后通过；中断测试暴露Windows长路径
问题，缩短任务目录后通过。均不是修改前基线失败。最后格式检查的一次无匹配通配符命令已改为
实际源码目录范围，不能把该错误尝试算通过。

## 清理预览和后续

现有只读清理预览已生成到观察台的`cleanup-preview.json`与`cleanup-preview.md`，包含完整路径、
理由、引用、影响和重建方式；不实际删除、移动或压缩任何历史资料。模型原文和审核信息作为
证据保留，重建模型原响应需要费用且不保证相同输出。

补充的一次性产物预览（均未执行清理）：

| 精确范围 | 建议 | 删除影响与恢复 |
| --- | --- | --- |
| `.work/round12-baseline/authority.json`、`source.json`、`authority-after.json`、`final-verification.json` | 保留 | 运行前快照不可事后重建，供不可变性审计 |
| `experiments/compact_json_validation/__pycache__/` | 可清理候选 | 无人工信息；执行Python模块后本地重建，不调用模型 |
| `output/image-enrichment/observer/round12/` | 可重建展示，建议暂保留 | observe build本地重建；丢失当前展示和统计但不影响cache/run/evaluation |
| 本轮三个run、六个成功cache及evaluation目录 | 保留 | 含首次响应、零调用复跑和评审证据；不能假定云端重跑能复现原文 |

新增费用估算独立保存在evaluation的`cost-estimate.json`；actualBilledCny为null，不伪装账户实付。

建议保留新配置为可选，暂不切默认；结构图关系、代码严格字符、图表解释仍要人工审核。工程已
达到本轮结束点，可另开小界面和TXT/MD/独立图片输入任务，沿用这些状态，不追加Prompt实验、
六图重复调用或44图基线。
