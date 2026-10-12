# 第十一轮：Qwen API 输出格式与契约复杂度对照

日期：2026-10-12。状态：八格首测和 Codex 质量对照完成；用户人工验收待完成。

## 结论

这两张难例中，省略服务端 `response_format` 的 C 比严格精简 JSON 的 B 保留了更多有效内容。
代码 B 的唯一代码字段只有首行，C 保留16行；结构图 B 更接近文章摘要，C 记录了各面板的图形、
进度归属、真实向右箭头，以及没有跨面板箭头这一边界。B/C 的图片、messages 和非格式参数完全
相同，因此当前证据值得继续小规模复核严格模式开关。

但代码 C/D 仍有确定的缩进变化，不能作为逐字转录已验收结果；D 相对 C 没有清楚的质量增益。
本轮不能宣布 JSON 必然降低能力、服务端某种解码算法造成错误、普通输出普遍更稳定，或确定
生产通过率。每格只做了一次首测，图片数仅2，服务随机性和模式实现细节都未被排除。

建议下一步另行授权少量 B/C 配对复核，并针对代码保真核查分辨率/detail及上下文因素；暂不改动
正式 Provider/default Prompt，不扩到原六图或44图。没有实施“再调用模型把自由文本转JSON”。

## 现场与源码核查

- 工作区 `E:\personal_KB` 未使用 Git；无法提供分支/HEAD或区分既有未提交修改，只在新的独立
  `experiments/qwen_output_format_ablation/` 内实施。没有修改用户现有代码、正式缓存或人工资料。
- Python 3.12.2，项目0.4.0；OpenAI SDK3.14.1、Pydantic2.12.5、Pillow11.2.1、openpyxl3.1.5。
  环境SDK版本已超出 `pyproject.toml` 的 `openai>=2.14,<3` 声明；本轮未升级或改依赖。
- 默认快照 `qwen3.7-flash-2026-07-15`，北京 endpoint
  `https://dashscope.aliyuncs.com/compatible-mode/v1`，默认 Prompt `image-enrichment-v1`。
- 默认 Prompt 完整SHA-256：`d81daa6b18608d8cc2dae1ff46b83bd69abf0a3e82172015de4c76e5660eb4f9`。
- 正式 Provider 固定添加严格 `json_schema`；实验复用其 endpoint白名单、正式 Schema生成器、错误
  分类、数据准备、凭据读取和脱敏函数，通过同一已安装OpenAI SDK的小适配层切换格式。
- 正式 runner默认传输重试2、结构化重试1；Provider SDK默认自身重试也未在构造时关闭。本实验
  不使用该重试编排，SDK显式 `max_retries=0`，传输和结构化重试均0。
- 当前结果Pydantic可读旧Schema1.0.0；`enforce_current_policy=True` 检查专用字段与动作/类型及
  代码字段明显缺行。A运行该规则，但本次混有解释的单行代码未触发启发式，仍需内容评审。
- 正式 Provider直接发送原图，无本地缩放、转换或显式detail。有限上下文由现有
  `prepare_dataset` 取得，通常前后正文各最多500字符；两样本均有上下文及正式Asset引用。
- 正式缓存身份包含图片SHA、模型、Prompt版本/哈希、Schema、上下文和关键推理参数；本轮没有
  查询/写入生产成功缓存，首测全部取得新响应。
- 历史补充证据：`docs/image-enrichment-round7-validation.md`、
  `output/image-enrichment/runs/qwen37-flash-reliability6-v1/`、
  `output/image-enrichment/runs/qwen37-flash-quality6-v2/` 及对应人工evaluation。

修改前实际全量测试：133 passed、1 skipped。跳过原因是测试环境不能创建符号链接。

## 样本、授权和参考答案

| 样本 | 完整二进制SHA-256 | 格式/尺寸/大小 | 外发 |
| --- | --- | --- | --- |
| `asset-44fdbea6544d.webp` | `b3d854ff82aee737216c582c6014e075e6c01f7e3a80d387c39a42eafe76debc` | WEBP / 808×438 / 40,748 bytes | 允许，非local-only |
| `asset-0007.webp` | `50933b2e0795615983dbae141f9c641af658c01428fc74634edd8e9742edad86` | WEBP / 983×497 / 27,514 bytes | 允许，非local-only |

已查看原图：代码图无可见真实凭据、私人地址或账号，使用原始字节，没有制作脱敏副本。安全政策
中的local-only是另一个哈希 `d0c476...`，没有进入本轮请求。

推理结束后才读取人工工作簿 `output/image-annotation/annotation-002/图片辅助标注.xlsx`，SHA-256
`ff905e06a455fe044c7d584decd7d29fae96a46bc47572cf230b8c39b42c2880`。45行、45唯一图片，41行
已确认、4行未审核；两张难例均已确认。人工类型、参考答案、评审和备注未出现在推理messages。

代码参考存在一处需要说明的基准差异：工作簿18行，含两行空行；原图按左侧行号实际为16行。
本轮同时保留逐字工作簿比较和独立对照原图的16行参考，后者没有偷偷改写工作簿。只规范化
CR/LF与最终文件换行，不删主体空格、缩进、标点或反斜杠。第6行逗号后的空格还可由用户放大
复核；C/D新增注释缩进这一确定差异已经足以阻止严格逐字验收，等级不依赖该空格争议。

Codex评审知道路径，因此不宣称盲评；用户可用匿名完整阅读稿独立评分，当前用户等级全部为空。

## 冻结条件与请求差异

- 模型：`qwen3.7-flash-2026-07-15`；8个响应实际模型都相同。
- 非流式，temperature0，`enable_thinking=false`，所有路径 `max_tokens=4096`。
- 原始WEBP，通过内存data URL输入；不设置detail，不做图片重编码。
- 同一图片四条路径使用同一份有限正文上下文。
- B/C仅response_format不同：B严格json_schema；C完全不传该参数，没有json_object或工具调用。
  两者都在同一user消息内包含同样精简Schema及相同字段职责说明。
- D同样事实目标，去掉JSON要求，代码只返回一个围栏，结构图使用自然语言/Markdown。
- A使用现场正式v1完整Prompt与13个顶层字段的完整Schema；额外包含自动类型/动作判断。
  B/C已知专项类型、仅3字段，所以A/B是方案整体比较，不能单独归因于字段数。
- 单次实验不能排除随机性；C/D也不是纯解码因果对照。

实际顺序：代码A → 结构图B → 代码C → 结构图D → 代码B → 结构图A → 代码D → 结构图C。
顺序在读取本轮结果和参考评分前冻结，未按结果补测或重排。

可核查文件：run下 `plan.json`（参数/无图messages）、`request-differences.json`、`schemas/`，以及
每格的 `request-metadata.json`。身份覆盖模型/endpoint、图片SHA、A/B/C/D、Prompt/Schema指纹、
格式开关、上下文、生成参数和校验版本。

## 八格结果

所有8个响应 `finish_reason=stop`，0截断、0传输失败、0结构化重试。A/B/C的6个响应JSON均可解析
且通过本地结构校验；A当前业务校验也通过。D的JSON/Schema状态是“不适用”，不是失败。
这些格式结果与下面的内容等级分开。

| 样本/路径 | 内容与字段职责 | Codex等级 | input | output | image | cached | total | 延迟ms |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 代码 A | extractedText有16行，code.text却是混有自我解释的单行；正式字段不可消费 | 不可用 | 1,741 | 896 | 352 | 0 | 2,637 | 11,463 |
| 代码 B | codeText只有首行，遗漏15行；uncertainties变成编程建议 | 不可用 | 763 | 171 | 352 | 0 | 934 | 2,489 |
| 代码 C | codeText保留16行、TEXT和双反斜杠；注释增加4空格缩进等 | 实质修改 | 818 | 322 | 352 | 0 | 1,140 | 3,918 |
| 代码 D | 单围栏保留16行；注释增加2空格，print后空格等有变化 | 实质修改 | 691 | 148 | 352 | 0 | 839 | 1,939 |
| 结构图 A | 三步骤、136/200和箭头识别到，但仍chart/understand_chart；上下文混入图中事实 | 实质修改 | 1,967 | 814 | 498 | 0 | 2,781 | 10,878 |
| 结构图 B | 三步骤与136/200保留，遗漏主要局部图形/箭头；偏文章摘要与未来实现讨论 | 实质修改 | 987 | 288 | 498 | 0 | 1,275 | 4,622 |
| 结构图 C | 三步骤、数字归属、向右箭头、无跨面板箭头完整；用途来自上下文需注明 | 轻微修改 | 1,077 | 380 | 498 | 0 | 1,457 | 4,275 |
| 结构图 D | 主要可见事实完整；上下文用途及“头像”等次要描述需删改 | 轻微修改 | 944 | 382 | 498 | 0 | 1,326 | 4,054 |

代码A与以前有所变化，但核心问题仍在：不再简单只有 `import os.path`，而是把代码和解释压成
单行混杂字段。当前代码缺行启发式不能捕获所有语义错误，本轮只记录该限制，没有越界修改正式
业务校验。代码C/D保留完整主体显著优于B，但根据工单，确定的字符/缩进错误不降级成轻微修改。

结构图B/C是已知结构内容提取控制组，Schema不要求自动类型，因此不能将其解释为路由准确率提升。
对B/C图片事实覆盖的差异可以观察；对A的错chart可以作为同期完整契约失败证据。

B/C messages完全相同，但服务返回的prompt tokens不同。这只能说明计数/处理受服务端格式模式
影响，无法从响应推知内部实现。所有模式图片tokens相同，未观察到服务缓存命中。

## 费用、原文和恢复

8次真实生成；input8,988、output3,401、image3,400、cached0、total12,389 tokens；响应计时
合计43,638ms。image tokens已计入prompt，不重复计费。没有调用LAN服务。

2026-10-12核对[官方快照模型页](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)，北京地域
输入不超过32k的公开原价为输入0.2元/百万、输出0.8元/百万、缓存输入0.04元/百万。按本次实际
usage估算为 **0.0045184元**；这是公开原价估算，未查询账户账单、免费额度或优惠，不能称为实付。
完整核价记录保存在 `evaluation/cost-estimate.json`，价格没有固化为长期代码常量。

每格先原子保存脱敏 `response.json`，再保存 `content.txt` 和 `analysis.json`。解析代码按JSON解码
后的字符串比较；D记录仅剥离一个外层围栏。没有宽松JSON修补，没有通过复制OCR补造正式代码。

首次启动在客户端构造阶段遇到环境SOCKS代理缺 `socksio`：当时预约/生成调用均0。通过只读诊断
发现已有合法HTTPS HTTP代理配置，在实验适配层明确采用该代理并避免读取未使用的ALL_PROXY，
SDK保持max_retries0。离线客户端构造和回归测试验证修复，未新增依赖、未修改环境或正式Provider。

实际完整运行未发生manifest异常；测试验证收到响应后若解析/manifest中断，离线恢复仍能保留
原文和usage。对真实run执行了离线recover和同配置run复跑：后者
`last-execution.json` 明确记录新增API调用0，8份原文/响应保留，usage没有增加。

预约记录使已完成、API错误和可能已经发出但未知的格都不会自动重发；强制中断后有lock的run需
先检查进程和证据，不自动再次计费。脚本每run硬上限8；新增真实run需要新的调用授权。

## 产物与评审入口

实验入口：`python -m experiments.qwen_output_format_ablation.runner`。

本次run：`experiments/qwen_output_format_ablation/runs/qwen-format-ablation-v1-20261012/`。

- `run-manifest.json`：八格清单、usage和原始/解析路径；
- `cells/<样本--路径--短SHA>/content.txt`：完整脱敏原文，包括质量错误结果；
- `cells/.../response.json`：逐次usage、模型、ID、耗时和原文；
- `cells/.../analysis.json`：格式校验与解析后的唯一消费代码字段；
- `evaluation/comparison-verbatim.md`：逐图原文、字段、字符diff、Codex等级与具体原因；
- `evaluation/anonymous-review-verbatim.md`：不显示A/B/C/D名称的完整回答和代码消费字段，供用户评分；
- `evaluation/quality-review.json`：Codex等级与用户pending状态分别保存；
- `evaluation/benchmark-snapshot.json`：只保留两张难例基准，其他敏感行不复制；
- `integrity.json`：280个权威文件哈希核验；
- `reports/cleanup-preview.json`：只读清理预览，无实际删除入口。

新增文件均在独立实验目录：runner、精简模型、Prompt、离线review、测试、README、run与本报告。
没有修改正式Document/Asset/Enrichment Schema、Provider、默认Prompt、生产缓存或人工工作簿。

## 工程验证

实际执行：

- 实验专项 `python -m pytest -ra experiments/qwen_output_format_ablation/tests`：13 passed；
- 相关回归 `python -m pytest -ra tests/test_enrichment.py tests/test_enrichment_dataset_evaluation.py`：48 passed；
- 项目全量 `python -m pytest -ra`：133 passed、1 skipped；
- 真实样本 `python -m pytest -m real_sample -ra`：5 passed、129 deselected；
- `compileall`、Ruff检查和格式检查：通过。

首次专项命令误写了一个不存在的测试文件名，该子命令没有运行测试；随后按现场实际路径完成
上述48个相关回归。没有将错误路径尝试记为通过。

必要风险测试覆盖B/C请求相同、C/D无格式和工具、八次上限及已完成格不重发、离线恢复零调用、
原文先落盘、JSON换行/反斜杠解码、单围栏剥离、格式与质量分离、截断与模型不匹配、外发授权、
凭据脱敏、初始化代理兼容、缩进差异不被宽松比较掩盖。

交付检查还发现Ruff会格式化Markdown的Python围栏，使早期派生阅读稿的代码显示发生空格变化。
原始 `response.json`、`content.txt`、`analysis.json` 和字符diff没有变化。现已从原始证据生成新的
`*-verbatim.md`，使用text围栏并验证8份原文和4个代码消费字段逐字存在，验证记录为
`evaluation/verbatim-validation.json`。旧派生稿保留但不作为推荐入口。最终Ruff只检查正式源码、
测试和实验的 `.py` 源码，避免格式器改动证据展示；增加了非格式化围栏回归测试。

调用前后对原图、工作簿、inventory、正式源码/Schema和云端/LAN历史run/cache/evaluation共280个
文件计算SHA-256，核验未变化。计划和持久化产物安全扫描未发现API Key、Authorization/Bearer
或图片data URL/Base64。只读取两张获准图片与对应有限正文发送；没有读取 `.env` 内容到日志。

工作簿用户验收尚未执行；也没有做重复随机试验、额外图样本、正式数据映射回写或下一阶段集成。
本轮八次首测预算已使用完毕，未追加请求。没有commit、push、PR或历史数据删除。

## 下一步判断

目前最合适的路线是 **先少量复核B/C格式开关，保留D作为保真对照，暂缓正式集成**。

C在两图首测中优于B，对代码尤其明显；但C和D都没有达到严格逐字转录目标，D也没有胜过C。
A/B不能证明简化字段就能改善质量。建议后续复核固定同样messages的B/C，并单独研究上下文是否
分散专项任务、图像输入设置是否影响代码细节；新增调用须另行授权，不在本轮无限调Prompt。
