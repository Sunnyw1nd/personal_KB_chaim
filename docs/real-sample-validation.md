# 真实样本验证记录

第一轮验证日期：2026-08-12；第二轮复核日期：2026-08-24（Asia/Shanghai）

## 输入安全检查

| ZIP | SHA-256 | 条目数 | 解压大小 | 压缩倍率 | 路径穿越/加密/符号链接 |
| --- | --- | ---: | ---: | ---: | --- |
| `html1.zip` | `3e915b558b07e272367b30c255afef02460489cce993e5bd0b7152f3f82514b1` | 43 | 8,598,106 B | 4.07 | 均无 |
| `html2.zip` | `2d4768c7021b0cc00a88b2c65d054c564a9d1b44b6ac4dc3c5daa1c9cfee9d5b` | 40 | 8,776,106 B | 3.94 | 均无 |
| `MCPzhihu.zip` | `15bcd20ae793ab09787ba983d94e4bd0c033c5853291a173e1fcb8e5fc290f62` | 86 | 12,011,864 B | 2.76 | 均无 |

表内三个第二轮基线 ZIP 都由 `prepare_sample` 逐项安全解压到 `.work/samples/` 内容寻址缓存，
原 ZIP 未修改。每个压缩包都恰好有一个可唯一确定的文章 HTML 与同名 `_files` 组合；知乎 ZIP
另有一个辅助 HTML，不会被误选为主文章。后续新增 ZIP 的批量结果见文末。

## 样本一

- Document ID：`wechat-04ed2205bb9a70f6`
- 标题：`京东二面：你的Agent跑到一半挂了，你怎么断点续跑？我愣了一下，说应该是从头重跑吧？面试官有点惊讶：'那你昨天跑那137份的时间呢？'`
- 公众号/作者：算法狗 / 算法狗
- 发布时间：`2026年8月10日 17:40` → `2026-08-10T17:40:00+08:00`
- 原始 URL：`https://mp.weixin.qq.com/s/EZ-Hc5Db3YzK1_iGhCrJ5w`
- 正文块：35（段落 22、三级标题 6、图片 7）
- 正文图片：7/7 找到并复制，均为实际 WebP；尺寸分别为 863×511、1003×517、983×497、943×491、1024×347、940×320、983×497。
- 图片异常：7 张均声明 PNG、实际 WebP，产生 7 条 `declared_type_mismatch` warning。
- 资源：正文图 7；样式表 23；正文外页面图片 6；脚本 4；辅助 HTML 1。
- 处理状态：`completed_with_warnings`。
- 正文人工抽查：开头、结尾、6 个三级标题与原始静态 DOM 一致；7 张图片顺序为 `640` 至 `640(6)`，Markdown 中均位于相应正文位置且可解码。
- 输出：[wechat-04ed2205bb9a70f6](../output/real-samples/wechat-04ed2205bb9a70f6/)

## 样本二

- Document ID：`wechat-d5bd23572d184478`
- 标题：`面试官问：5个OK全绿，Agent为何还把USD写成EUR？`
- 公众号/作者：吴师兄学大模型 / 吴师兄
- 发布时间：`2026年8月10日 18:22` → `2026-08-10T18:22:00+08:00`
- 原始 URL：`https://mp.weixin.qq.com/s/lDy-E_xDYtm6JAHoZ-z5ag`
- 正文块：46（段落 36、二级标题 4、图片 4、表格 1、列表 1）
- 正文图片：4/4 找到并复制，均为实际 WebP；尺寸分别为 1080×608、1080×720、1080×647、1080×594。
- 图片异常：无缺失、无法识别或声明类型不一致。
- 资源：正文图 4；样式表 23；正文外页面图片 6；脚本 4；辅助 HTML 1。
- 处理状态：`completed`（存在 3 条正常安全决策 info）。
- 正文人工抽查：开头、结尾、4 个二级标题及一张 4×2 表格与原始静态 DOM 一致；4 张 Markdown 图片均存在且可解码。
- 输出：[wechat-d5bd23572d184478](../output/real-samples/wechat-d5bd23572d184478/)

## 资源排除与安全结论

两个样本各有 4 个浏览器保存为 `.js.下载` 的脚本资源。解析器结合 `<script src>` 引用语义与文件名模式，将其全部标记为 `refused/executable_script`；每篇的 `frame.html` 标记为 `refused/auxiliary_frame`。解析期间没有执行脚本、加载 iframe 或发起远程请求。

自动验证确认：Document、ProcessingReport 和 ResourceManifest 均能再次通过运行时 schema 校验；块序号连续，图片资产引用完整；Markdown 中的全部资产路径均存在；所有输出图片均通过 Pillow 解码验证。

## 第二轮缓存与微信复核

- 三个 ZIP 首次进入新机制时均生成 `status=complete` 的 `.sample-extraction.json`；清单包含
  ZIP 完整哈希、策略版本、条目/总大小、唯一文章对及所有文件的大小和 SHA-256。
- 同一 ZIP 紧接着再次准备均为 cache hit，没有重复解压。
- `html1.zip` 首次实现曾因超长资源名叠加临时目录触发 Windows 路径限制；缩短受控临时目录
  后首次准备成功，第二次命中。失败过程没有完成清单，也没有修改原 ZIP。
- 自动测试验证 ZIP 内容变化产生新缓存；缺失/不完整清单、文件缺失/篡改会精确重建当前缓存；
  上次中断目录不会命中；穿越、绝对/盘符路径、Windows 冲突、符号链接、加密、CRC、数量、
  大小、压缩比及文章对歧义均明确失败。
- 两篇微信通过新准备入口重新回归为 2 passed。另将第二轮代码生成的 19 个微信输出文件与
  修改前基线逐项比较 SHA-256，全部一致，包含标准 JSON、Markdown、报告、清单和 11 张图片。

## 知乎专栏真实样本

- ZIP：`MCPzhihu.zip`；主 HTML SHA-256：
  `ef0808404d2029c7a18840cd853d9e423727d1d543f8e065725079ce73d953c3`。
- 识别：`zhihu_column_saved_html`，依据为知乎专栏 `/p/` 的 `og:url`，以及
  `.Post-Title + .AuthorInfo-name + 唯一 .Post-RichText` 完整结构。
- Document ID：`zhihu-ef0808404d2029c7`；schema：`1.1.0`。
- 标题：`MCP (Model Context Protocol)，一篇就够了。`；作者：`LastWhisper`；公众号字段为
  `null`。
- 原始 URL：`https://zhuanlan.zhihu.com/p/29001189476`。
- 首次发布时间：`2025-03-09T10:18:27.000Z` → `2025-03-09T10:18:27+00:00`；
  最后编辑时间：`2025-04-11T17:54:57.000Z` → `2025-04-11T17:54:57+00:00`。
- 正文块 128：段落 73、标题 14（h2 9、h3 5）、列表 16（无序 12、有序 4）、引用 3、
  代码 11、图片 10、视频 unknown 占位 1。
- 10 张普通正文图片全部找到、复制并由 Pillow 识别为 JPEG；缺失、无法识别、声明格式不一致
  均为 0。图注 2 个，分别与所属图片块关联；不是独立噪声段落。
- 链接卡片 1 个：在原位置降级为带标题/描述的链接段落；目标 URL 解码为 GitHub 地址，同时
  保存知乎跳转地址。卡片缩略图 1 个只盘点，不计正文图。
- 嵌入视频 1 个：在前后正文之间保留明确占位并产生
  `unsupported_embedded_video` warning；远程 MP4 未请求，视频封面 1 个只盘点。
- `_files` 中实际资源 84 个：正文图 10、脚本拒绝 25、样式表 6、页面 UI 40、链接卡片
  缩略图 1、视频封面 1、辅助 iframe HTML 1。页面含 30 个 script 元素、1 个 iframe 和
  178 个远程引用；解析器均未执行、加载或请求。
- 处理状态：`completed_with_warnings`，业务 warning 仅为视频不支持；另有脚本拒绝、iframe
  拒绝和外部引用未加载 3 条 info。
- 正文对照：首块为 `READ⏰: 30min (read) + 30min (practice)`；末块为 Reference 的 5 条
  链接列表。14 个标题、11 个代码块、10 张图片/2 个图注及卡片/视频相对顺序已与原始
  `.Post-RichText` 静态 DOM 逐项核对；评论、导航、作者头像、推荐、SVG 路径均未进入正文。
- Markdown 的 10 个相对图片路径均存在并可解码；JSON 与 Markdown 中的块和资产 ID 一致。
  固定 `importedAt` 后在两个新输出目录重复执行，`document.json` 与 `document.md` 逐字节一致。
- 原 ZIP 处理前后 SHA-256 一致。项目外附件 `E:\html\MCPzhihu.zip` 与 `samples` 副本哈希
  完全相同。
- 输出：[zhihu-ef0808404d2029c7](../output/real-samples/zhihu-ef0808404d2029c7/)

## 批量转换验收

执行：

```powershell
kb-normalize --normalize-samples `
  --samples-root samples --work-root .work `
  --output-directory output/real-samples
```

执行时 `samples` 现场共有 5 个 ZIP。批量入口没有接收任何文章文件名，结果为：

- `html1.zip`：cache hit，微信，`wechat-04ed2205bb9a70f6`，成功；
- `html2.zip`：cache hit，微信，`wechat-d5bd23572d184478`，成功；
- `MCPzhihu.zip`：cache hit，知乎，`zhihu-ef0808404d2029c7`，成功；
- `企业内Agent工具落地实践：统一Harness、Skill与虚拟文件系统.zip`：cache hit，微信，
  `wechat-ffd9d415d880b663`，成功；
- `模型后训练 - AI Agents in Depth_files.zip`：cache hit，明确失败为
  `source_unsupported`；其失败没有阻断前述 4 篇输出。

汇总统计为 5 个归档、4 个标准化成功、1 个失败、5 个 cache hit、0 个重复解压；报告位于
[batch-processing-report.json](../output/real-samples/batch-processing-report.json)。真实批量回归
会固定校验前三个基线 ZIP，同时要求现场所有新增 ZIP 都进入逐项汇总，任何失败必须带稳定错误码。
批量处理前后对现场全部 ZIP 重新计算 SHA-256，均保持不变。

### 人工验收说明

自动验收覆盖静态 DOM、标准 JSON、Markdown、资源文件、图片解码和批量编排。桌面浏览器视觉
对照按用户要求由用户自行完成，不作为本次批量功能自动验收的阻塞项。

---

## 第三轮：稳定身份与修订存储

验证日期：2026-08-24（Asia/Shanghai）。旧 `output/real-samples/` 未移动、覆盖或删除；第三轮
结果写入独立的 [managed-samples](../output/managed-samples/) 目录。

### 三个基线样本

| ZIP | 新逻辑文档 ID | 来源身份方法 | 修订 ID | 正文图片完整性 |
| --- | --- | --- | --- | ---: |
| `html1.zip` | `wechat-0d15bf414f2f1f89` | `wechat_s_path_token` | `revision-f8d8d065009ba9eb` | 7/7 |
| `html2.zip` | `wechat-8cd143556ee72855` | `wechat_s_path_token` | `revision-60c58705c16c7a82` | 4/4 |
| `MCPzhihu.zip` | `zhihu-e889fcd682a4c668` | `zhihu_column_url_path_id` | `revision-662ebaa150a9f6eb` | 10/10 |

三个 ID 均由原始 URL 的稳定来源身份计算，不再等于 HTML 或 ZIP 哈希前缀。标准文档使用
Schema 2.0.0，并保留原始 URL、规范来源身份、完整 ZIP/HTML SHA-256、修订三类指纹和正文图
完整性。标题、作者、时间、块类型/顺序、正文图片二进制、知乎卡片/视频降级和资源排除统计继续
通过既有真实回归断言。

同一输出根再次导入三个 ZIP 均为 `unchanged`，`currentRevisionId` 未变化，未创建重复修订。
测试临时输出根中的首次导入均为 `created`。处理前后重新计算现场全部 ZIP SHA-256，11 个均
未变化。

### 现场批量

现场 `samples/` 共 11 个 ZIP。第三轮首次发布形成 10 个逻辑文档；此前已知的
`模型后训练 - AI Agents in Depth_files.zip` 仍以 `source_unsupported` 失败，未污染其他文档。
修复 Windows 控制台 UTF-8 输出后再次执行，报告为：

- 扫描 11、标准化 10、失败 1、冲突 0；
- 11 个缓存全部命中；
- 10 个成功项全部为 `unchanged`；
- 每项同时给出可读逻辑文档路径、修订路径、修订 ID 和 current ID。

报告：[batch-processing-report.json](../output/managed-samples/batch-processing-report.json)。

### 旧输出迁移预览

只读扫描旧 [real-samples](../output/real-samples/) 的 10 个标准文档，结果为 10 个可靠来源组、
0 个疑似重复组、0 个 unresolved、0 个 invalid、0 个已管理目录。每个候选都给出旧路径、建议
逻辑文档/修订和 current 理由，报告明确 `migrationExecuted=false`。没有执行实际迁移。

预览报告：[migration-preview.json](../output/migration-preview.json)。该预览是在补图版 ZIP 加入前
生成，仍是只读历史结果，没有执行实际迁移。

### 真实缺图版与补图版配对验证

用户随后补充了同一篇微信文章的完整保存包。解析器没有按文件名或标题合并，而是根据
`https://mp.weixin.qq.com/s/QpVbbZE3-tUj7ViOBU-fGg` 得到相同规范来源身份
`wechat:article:s:QpVbbZE3-tUj7ViOBU-fGg` 和逻辑文档 ID `wechat-4a498ea1280780fd`。

| 版本 | ZIP SHA-256 | HTML SHA-256 | 修订 ID | 正文图完整性 |
| --- | --- | --- | --- | ---: |
| 缺图版 `_files.zip` | `615ce9a58ac224feda6213278c66fbdc2d9fb91a37aa8e1a75b8877c4db9df44` | `54ba1414c78f0f3b14129f3c6a1482b3c078a55c6bea1e81b97463d875a47d4a` | `revision-257aa1197ecc9f28` | 5/7 |
| 补图版 `.zip` | `87b835f38a58bb2d28d6d655617e088ac027a625d639220d2ca42fb8eef44803` | `8ff1173b6649a954a56f56f1b3aafdf6f6cb495933a6335379dfe05636ec0722` | `revision-88eabcd4f0bd163d` | 7/7 |

两版的 ZIP、HTML 和资产指纹不同，但规范正文指纹相同：
`db828a353110f4f31f6df2915dc64e77650b0077bbbb142c75fd5b3132db4103`。两版 7 个逻辑
`assetId` 一一对应；缺图版缺少 `asset-cc4178391b48` 和 `asset-65f0ccaa683a`，补图版均能
关联并验证。

按“缺图 → 补图 → 缺图 → 补图”顺序导入时，结果依次为 `created`、
`resource_improved`、`resource_degraded`、`unchanged`。补图修订成为 current；再次导入缺图版
不替换 current，也不产生第三份重复修订。反向按“补图 → 缺图”导入时，结果为 `created`、
`resource_degraded`，current 同样保持完整修订。批量入口扫描现场全部 12 个 ZIP 时也将两项归入
同一逻辑文档，完整包为 `created`，随后处理的缺图包为 `resource_degraded`。

验证输出保存在 [real-completion-pair](../output/real-completion-pair/)；其中正反两个隔离输出根都
保留两份修订和可校验的 `document-record.json`。两个原 ZIP 处理前后完整 SHA-256 均未变化。
桌面浏览器视觉对照仍由用户自行完成，不属于本次自动验收。
