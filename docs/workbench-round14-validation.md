# 第十四轮：本地工作台最小闭环验证

日期：2026-10-12。结论：工程最小闭环完成，图片质量继续按“可用但需审核”处理；用户桌面验收待完成。

## 基线与工作区

main/`a17e913c10ce4c6dca126443feb9c8c57c534787`，origin仍为原仓库。第十三轮所有未提交整理
成果在本机保留，未checkout旧HEAD，没有add/commit/push/PR/远程修改。
开始前全量173 passed、1 skipped；Python3.12.2。读取本机AGENTS、第十三轮报告/盘点/架构/路线、
compact与观察台说明、实际资产/修订/Provider/runner和审核接口后实施。

UI选Flask＋原生JS，不引入前端构建、数据库或任务平台。可选workbench extra新增Flask约束，
本机3.1.0、BSD-3-Clause；沿用OpenAI extra声明，没有升级主环境SDK或新增模型依赖。
既有SDK3.14.1与声明`<3`的差异仍保留，wheel验证复用当前依赖，不冒充全新依赖解析验证。

## 实现与契约

新增`application/image_tasks.py`、`workbench.py`、`workbench_models.py`，`workbench/server.py`及
包内HTML/CSS/JS；独立任务/人工修订Schema、12项workbench测试、浏览器核查工具、使用指南。
pyproject增加可选extra、第四个`kb-workbench`入口及静态资源打包，三个旧命令与公开接口保留。

现有核心仅小幅扩展：dataset策略读取可忽略不属于本修订的策略键（测试集默认仍严格）；
compact runner接受单独执行预算；原子JSON临时文件名缩短，以修复实际Windows路径问题。
没有重写Prompt、原10份Schema、身份/修订/current算法或旧缓存键，没有替换默认CLI profile。

### 正式图片桥接

直接读取已知Document修订与assets，校验来源ID、修订、真实解码/格式/大小/尺寸/二进制SHA，
只处理正文图。标题、图注、前后正文沿用现有裁剪/脱敏，不读取inventory、工作簿或评测答案。
按二进制SHA＋上下文指纹分组：相同上下文合并执行、位置全部保留；不同上下文独立处理。
request保留AssetReference，task bindings保留block/order/sourceLocator。缺图、损坏、未复制/
不支持及越界项目列原因，不构造假请求。查看历史不改变current。

### 状态、幂等与编辑

单个工作进程、ThreadPoolExecutor单worker。任务在入队前持久化；OS文件锁防第二个同根实例。
scope与request ID/别名持久化，运行中跨文档同cache身份返回旧任务并保留新位置绑定；同请求在
完成或重启后仍指向原任务。GET只读取状态，重启queued/running标记interrupted，不自动发图。
离线恢复复用runner已有attempt，不发模型；无已存run明确拒绝恢复。永久配置错误停止后续组。

每组独立run，既有每图3次/180秒恢复限制不变；任务预算为明确选择组数×3（最多12组）。
execution_call_limit属于调度参数，不改语义缓存身份；旧CLI默认18保持。缓存否决/无效不被
新run绕过；重新任务仍优先缓存，失败重发需明确新动作和授权，不承诺供应商exactly-once。

机器审核复用reviews，认可/待复核/否决独立于结构状态。人工描述、关键事实、代码作为不可变
draft保存，关联原结果指纹和文档/修订/asset/位置；每次编辑新版本，采用另存event＋指针。
没有覆盖机器cache、原文、Document、Markdown或Excel，不进入检索。代码换行/缩进/特殊字符
通过模拟保存与重启测试；含凭据样式的人工提交明确拒绝，不静默改写成“准确代码”。

## 本地安全

只监听127.0.0.1。Host严格匹配端口；POST同时验证Origin与启动时随机CSRF header，没有CORS。
上传限总100MiB、单ZIP50MiB、最多30文件及32parts，继续调用原ZIP安全检查。上传副本隔离
`uploads/<完整SHA>/a.zip`，原名记录upload-events，短basename和`.work/wb`避免浏览器长文件名
叠加；原ZIP保持不变。同名不同包不覆盖，同内容副本校验后复用。
浏览器没有Key或任意路径读取入口；资产按已知ID解析，拒绝越界/链接/SVG/HTML，仅真实光栅图。
模型/用户文本用textContent/textarea，CSP不含unsafe-eval、inline或公网资源。

开发中实际发现并修复：multipart内存上限过小导致合法ZIP413；Windows长SHA包名导致新解压
缓存路径过长；并发轮询时任务JSON在Windows无法替换；原子临时文件名过长。修复分别为合理
表单限制、短受控副本/工作路径、任务读写同锁、短同目录临时名。历史结果不删除；早期失败
任务与本轮工作副本残留仍在本机，不能把开发失败记录改写成成功。

## 一篇真实文章的浏览器闭环

使用获准`html1.zip`，原包SHA为`3e915b558b07e272367b30c255afef02460489cce993e5bd0b7152f3f82514b1`。
Microsoft Edge137.0.3296.83＋现有Playwright无界面运行，真实通过网页文件控件上传，不以直接
调用Python导入冒充界面验证：

1. 导入得到`wechat-0d15bf414f2f1f89`、修订`revision-f8d8d065009ba9eb`；7正文图可读，警告与
   资源清单可查看。再次上传显示unchanged，没有第二个逻辑文档。
2. 选择正文order29的结构信息图，binary SHA
   `50933b2e0795615983dbae141f9c641af658c01428fc74634edd8e9742edad86`。
3. 桥接产生与历史compact相同的上下文/配置键
   `bf98df682760717e63c215037e4d2b3eb41240e1a6b6eaf3ba44727e7293b10b`。
   用户式选择/确认后复用缓存，run `wb-d8063325d2ab-00`，apiCalls0、cacheHits1。
4. 浏览器登记待复核，reason明确为Codex功能测试，非用户认可；没有登记approved。
5. 保存一份人工description草稿并确认采用，actor=`codex_test`、修订
   `396e4bc0808e4103b696c294e9acb827`；模型原文件不变，人工版本有独立标记。
6. 刷新后重新打开可读；真实停止并重启服务后仍可读。明确创建第二缓存任务，run
   `wb-c185f5d70a21-00`，仍apiCalls0、cacheHits1。两份浏览器运行记录pageErrors均空。

**本轮真实模型调用0**，没有新的响应usage或模型费用；界面可显示历史记录的usage，但它不是
本轮用量。没有用剩余3次授权预算追加模型验证或图像质量实验；真实恢复仅沿用历史能力，
本轮新的错误恢复场景在fake测试验证。结构图箭头关系仍有质量限制，未重新宣称内容正确。

证据在`.work/round14/browser/first-check.json`、`restart-check.json`、`inspect-check.json`，
以及document/review/restart-cache/review-panel等PNG。实际查看截图确认原图/JSON/审核按钮/
草稿/采用标识对应；不是用户本人的桌面验收，也不是仅后端单测通过。

## 真实缺图/补图界面回归

按既有完整包哈希选择两份ZIP，分别通过网页上传。初次记录从5/7、缺2图到7/7、缺0图，
`resource_improved`成为current；documentId始终`wechat-4a498ea1280780fd`，两修订仍为
`revision-257aa1197ecc9f28`和`revision-88eabcd4f0bd163d`。重复核验再次导入缺图版为
resource_degraded，不替换完整current；补图版为unchanged。切回历史修订，网页显示2个
缺图占位。证据`pair-check.json`、`pair-history.png`；没有对配对图片调用模型。

## 自动化、安装和完整性

- 修改前：173 passed、1 skipped；本轮全量：185 passed、1 skipped；真实回归5 passed。
- `python -m pytest -ra tests/test_workbench.py`：12 passed，覆盖桥接/上下文分组、越界/坏图、
  重复/正在处理/重启/跨文档别名、缺Key/未授权/local-only/否决、人工版本与代码、HTTP安全、
  大multipart/坏ZIP隔离、独占进程、失败原文及离线恢复。
- 既有compact/legacy相关fake回归通过；全部隔离实验50 passed，无真实模型请求。
- 编译及Ruff检查通过，格式检查85文件；没有格式化Prompt或重序列化原Schema。
- 普通wheel在`.work/round14/wheel-env`非editable安装；UI资源/入口验证，安装后workbench
  测试12 passed。复用现有依赖，未替换主环境；旧三个入口仍在wheel元数据。
- 957份原始/权威/历史保护文件哈希未变，`.work/round14/integrity-after.json`中changed为空；
  新文件75项包括独立Schema、上传/文档副本、任务/审核及人工版本，不伪装为原有附件。
- Key不在浏览器、普通日志或JSON中，截图/数据仅本地；全局源资料、历史cache/run/evaluation/
  人工工作簿未移动。运行数据及私有资料仍被.gitignore排除，没有新增暂存内容。

最终新Schema导出/一致性核对通过；25份入口/指南的67个相对链接无缺失。23份工作台JSON
扫描未发现Key/Bearer或图片data URL；最终wheel再次在Edge进行只读页面核查，pageErrors为空，
API仍0。当前静态资源、审核覆盖和人工版本可读。验证服务在结束时停止，避免占用用户端口。

## 已知限制与用户验收短清单

没有运行中取消、表格结构编辑、同身份强制覆写识别、跨进程CLI并发写、多用户/远程服务；
未知已发请求只恢复证据，不自动重发。极长自定义项目根仍有Windows路径风险，建议短根目录。
人工采用仅用于工作台展示，没有检索、Markdown回写或评测真值自动同步。
测试审核原因有明确标记，但既有机器审核契约仍使用reviewer=user字面值；本次仅needs_review、
从未approved。独立人工记录明确actor=codex_test，不应视为用户已验收。

用户可用[使用指南](workbench.md)启动后完成：

1. 导入一篇自己的保存页ZIP，查看标题、正文、current/历史与缺图；
2. 检查图片范围和上下文，再决定是否允许外发；本轮不替用户追加模型调用；
3. 原图与结果对照，认可/待复核/否决；检查机器状态与人工版本没有混淆；
4. 修改描述/事实或代码，先保存再采用，刷新与重启确认；
5. 相同配置再次任务确认缓存0调用，失败/否决项不被当作成功；
6. 检查现有Codex测试版本标记，按自己的判断新存/采用用户版本，不改写历史测试证据。

下一轮扩TXT/MD/独立图片；本轮不继续Prompt选型、44图基线、LAN、URL/PDF或RAG。
