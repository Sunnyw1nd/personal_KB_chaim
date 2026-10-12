# 第七轮：图片路由、代码字段与事实边界校准记录

记录日期：2026-09-17。本轮只处理第六轮的同一组 6 张图片；没有运行 44 图基线，也没有修改正式 Document/Asset 或人工工作簿。

## 现场基线与实现

- 工作区 `E:\personal_KB` 未使用 Git；Python 3.12.2，项目版本 0.4.0。修改前全量测试 `117 passed, 1 skipped`，真实样本 `5 passed`；编译、Ruff 检查及格式检查通过。跳过项仍是当前环境不允许创建符号链接。
- 旧结果模型已有 `architecture_flow`、`description`、`keyFacts`、`code.text` 和 `chart`；代码缺失是字段填充问题，不是缺少 Schema 字段。正式结果 Schema 仍为 1.0.0，旧结果不迁移。
- 新实验 Prompt 为 `image-enrichment-v2`，完整 SHA-256 为 `4096d161eeb0f364523619e3d48de73e802d78a8da1e90643b251feede0267a9`。它要求完整代码进入 `code.text`，区分量化图表与节点/步骤结构图，并收紧图片事实、文章上下文和推断的边界。
- v2 本地校验拒绝明显的代码字段截断、类型/动作/专用字段矛盾，以及无知识价值的排除结果仍含高置信事实。对于代码，只有当 `extractedText` 至少比 `code.text` 多出 3 行可识别的代码式行、且正式代码行是 OCR 行子集时才拒绝；普通 UI 文字和行号不参与比较。这是防漏检的最小启发式，不证明代码转写准确，也可能漏过其他语言或语法形态。
- 新校验仅对带 v2 `promptVersion` 的 Provider 响应启用；旧输出仍可按 Schema 1.0.0 读取。v2 Prompt 哈希与版本进入缓存身份，不会复用 v1 成功缓存。

## 真实运行与逐图比较

预演 `qwen37-flash-quality6-v2-dryrun`：6 个 dry-run、0 次 API 调用，所选图片均不属于 `local-only`。主验证 `qwen37-flash-quality6-v2` 显式使用 Qwen 正式快照 `qwen3.7-flash-2026-07-15`、非思考模式、传输重试 0、结构化重试最多 1。v2 run manifest：

`output/image-enrichment/runs/qwen37-flash-quality6-v2/run-manifest.json`

| 图片 | v1 类型/动作/等级 | v2 类型/动作/状态 | v2 人工等级与结论 | 调用；tokens；耗时 |
| --- | --- | --- | --- | --- |
| `asset-0007.webp` | chart / understand_chart / 实质修改 | chart / understand_chart / success | **实质修改**：三步文字顺序基本正确，仍把无坐标轴、无数据系列的结构信息图塞入 chart；未修复原问题。 | 1；2,516；13,954 ms |
| `asset-367af9dd1487.webp` | table / extract_table / 无需修改 | table / extract_table / success | **无需修改**：三列表头、七行及对应关系仍正确。 | 1；2,561；22,547 ms |
| `asset-44fdbea6544d.webp` | code / transcribe_code / 实质修改 | code / transcribe_code / invalid_output | **不可用**：两次都将多行代码放在 `extractedText`，正式 `code.text` 仅 `import os.path`；v2 校验正确拒收，未产生成功缓存，但模型行为未修复。 | 2；4,366；74,594 ms |
| `asset-57ae62ca4266.webp` | chart / understand_chart / 轻微修改 | chart / understand_chart / success | **轻微修改**：轴和约 100B 的阶跃位置基本正确；“S 型”“指数级”“模型越大越好观点”等说法并非图形直接证明，需要删减。 | 1；2,637；12,282 ms |
| `asset-ce52ad0983de.webp` | decorative_text / exclude / 无需修改 | decorative_text / exclude / success | **无需修改**：保留“精品·知识推荐”可见文字，继续降低知识价值并排除检索。人工工作簿的 OCR/include 暂定动作仍需用户复核。 | 1；1,627；4,906 ms |
| `asset-dba5466c7c5e.jpg` | ui_screenshot / describe / 轻微修改 | ui_screenshot / describe / success | **轻微修改**：两个工具及唯一可见 `txt_counter` server 读对；结尾仍将可见的数字 2 扩展成“两个活跃的 MCP 服务或工具”，应只说两个工具。 | 1；2,656；44,375 ms |

这些等级基于原图、两轮机器结果和暂定人工基准的本轮轻量对照，没有回填或覆盖工作簿。尤其不能把 chart 的结构字段完整性等同于视觉路由正确：错误类型的结果也可能在字段之间自洽。代码失败记录包含两次独立 attempt 的原始响应与本地校验错误，可在 `failed-records/<完整图片 SHA-256>/` 下复核；两次都明确报告 `code.text omits multiple code lines found in extractedText`。没有为了取得不同答案增加第三次付费调用。

## 调用、缓存与安全

- 主验证 6 个图片任务、7 次真实调用：5 张首次成功，1 张经历 1 次结构化重试后仍失败；结构化失败次数 2，重试恢复 0，最终失败 1。7 次调用均取得 usage。
- 汇总 usage：input 13,002、output 3,361、image 3,456、total 16,363 tokens；逐图耗时合计 172,658 ms。服务费用没有核算，不能把它写成零。dry-run 和缓存复跑没有新增 usage。
- 5 张成功项用完全相同 v2 配置、另一 run 名 `qwen37-flash-quality6-v2-cache-check` 复跑：`cached=5`、`apiCalls=0`。失败代码图没有成功缓存；因此本轮**没有**达成“6 张全部成功、6 张零调用复跑”的验收条件。v1 run、Prompt、缓存和质量记录均保留。
- 6 张输入处理后 SHA-256 均与 inventory/imageId 相符；所选图片没有 `local-only` 项。扫描本轮两个 run 的 JSON，未发现 API Key 名、Authorization、图片 data URL/base64 或凭据样式密钥。请求记录不包含人工标签、参考答案或工作簿内容。
- 可选的图表/UI 无上下文 A/B 未执行：主验证已经触发暂停条件，继续外发两张不会解决代码字段和信息图路由问题。本轮实际调用 7 次，未触及 16 次上限。

## 自动验证

- `python -m pytest -ra`：123 passed、1 skipped；跳过原因是环境不允许创建测试符号链接。
- `python -m pytest -m real_sample -ra`：5 passed。
- `python -m compileall -q src tests`：通过。
- `python -m ruff check .`：通过；`python -m ruff format --check src tests experiments`：46 files already formatted。
- `kb-enrich-images write-schemas --output .work/round7-schema-export-20260917`：4 份 Schema 成功导出，逐份 SHA-256 与正式 `schemas/` 文件一致。没有改变正式 Schema。
- 专项测试增加 v2 代码截断、合法短代码、类型/动作/字段一致性、v1 旧结果可读取、Prompt 版本与缓存身份隔离，以及 Provider 实际响应形态的 v2 二次校验。原有重试、attempt、usage、脱敏及真实样本回归仍通过。

## 决定与后续

v2 比第六轮更严格地避免了“错误代码字段进入成功缓存”，但没有让模型填对代码字段，结构信息图仍错路由，图表/UI 仍有无依据扩展。质量门槛未通过，而且 5 成功/1 失败较 v1 的 6 成功更差。因此 CLI 默认 Prompt 已恢复为 v1；v2 文件、哈希、校验规则和本次 run 均保留为可显式选择的失败实验，不覆盖历史。**暂停 44 图基线**。

下一次校准应先在独立新版本中消除代码主体与通用 OCR 字段的竞争，并为无坐标轴/系列的结构信息图设计更有效的路由约束；不能把 OCR 文本未经确认地自动复制进正式代码字段。图表/UI 的措辞边界仍需逐图复核。只有同组 6 图的正式字段与主结构正确、没有关键事实错误且完整缓存复跑零调用后，才考虑扩大到 44 图。
