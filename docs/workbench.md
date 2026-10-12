# 本地工作台使用指南

工作台用于本机文章导入、修订/正文查看、正文图片识别、审核和少量人工修改。只接受保存页ZIP，
不支持URL抓取、TXT/MD/Office/PDF导入或检索。机器结果仍需审核，不能因结构通过自动入库。

## 安装、启动、停止

```powershell
python -m pip install -e ".[workbench]"
kb-workbench
```

打开`http://127.0.0.1:8765/`。未更新console script的旧editable环境也可直接执行：

```powershell
python -m wechat_html_normalizer.workbench.server
```

终端按Ctrl+C停止。没有运行中取消按钮；正常停止等待当前图片在既有截止/恢复预算内结束，
不会继续启动后续图片。强制结束可能留下未知的已发请求；重启标为interrupted，绝不自动重发。
同一工作数据根只能运行一个工作台，另一个实例会拒绝启动。不要同时用CLI写同一文档/缓存根。

只绑定回环地址，不开放`--host 0.0.0.0`、账号或远程访问。端口可用`--port`调整。
数据目录可用`--project-root`、`--data-root`、`--documents-root`、`--enrichment-root`明确配置；
目录必须位于所选项目根内、互不嵌套，不与原始samples/test_pictures或源码重叠。
现有数据无需移动，可把documents-root指向已有逻辑文档根；不要选择含多个根重复ID的混合目录。

## 最短操作

1. 拖入或选择一个/多个文章ZIP。原文件不动，上传副本按完整SHA隔离，同名不同包不覆盖。
2. 从左栏选择文章，切换current/历史修订。正文来自标准Document，不执行原网页；缺图占位
   保留，警告/资源清单可展开查看。历史查看不会改变current。
3. 查看正文图片任务，展开必要上下文，选定范围并勾选外发确认，再点击识别/缓存按钮。
   输入直接来自正式修订，不需要测试清单或Excel。没有Key会阻塞，不把dry-run当识别完成。
4. 点击“查看结果／审核”，核对原图、正式字段、attempt及脱敏原文。可认可、待复核或否决
   **机器结果**；否决立即影响机器缓存资格，但原文与历史不改写。
5. 编辑描述、每行一条的关键事实和代码正文。保存会产生独立**人工草稿**；再选择已保存版本
   点击确认采用。未保存文本不会被采用。采用版本有清楚的人工作者标记，可切回模型原结果。

密钥仍仅由后端从项目`.env`或DASHSCOPE_API_KEY环境变量读取；浏览器只看到已配置/未配置。
新正式图片默认禁止外发，只有明确选择并确认的批次才设置授权；local-only始终拒绝。图注和
相邻正文沿用现有截断/脱敏，人工答案及修改不传给模型。含凭据样式的人工编辑会拒绝保存，
而不是悄悄改写代码后当作逐字内容。

## 任务、恢复与费用

模型配置固定compact-json-v1、现有快照、非思考、4096/8192输出预算及180秒每图总截止。
每图最多3次真实调用（首次＋传输恢复1＋结果恢复1），批次上限为明确选定的图片/上下文任务
数乘3，最多选12个。各组使用独立现有run，避免同SHA不同上下文被runner目录去重误合并。

调度预算独立于结果配置：现有CLI的profile中18次是旧批次默认，新工作台向runner传每组3次的
execution_call_limit。该调度参数不改变缓存身份，未修改旧键或Frozen Prompt。原CLI仍旧默认，
没有将工作台选择强行替换成CLI默认。

刷新/查看仅GET，不发模型请求。双击、响应丢失后同requestId、同范围默认重复操作会返回旧任务；
同缓存身份正在处理时返回已有任务，跨文档绑定和request别名也持久化。重启不自动执行queued/
running任务，已完成及人工版本仍可读取。

“新任务再次运行”是明确新动作，仍优先缓存；不提供同身份强制覆盖缓存。失败未入成功缓存时，
可以在重新确认后创建新run；已否决或当前校验无效的cache不会因新run被绕过。服务已发出但结果
未知时可能已计费，确认框有提示；工具不承诺供应商exactly-once。

离线恢复只读已有attempt并重建清单，不创建Provider、不重新发图；无已存run明确报错。
失败/未知响应仍是失败/待复核，不因恢复按钮变成成功cache。cached显示的原结果usage属于历史，
本次任务的apiCalls与usage单独展示，不能把历史tokens再计为本次费用。

## 数据与契约

| 范围 | 用途 |
| --- | --- |
| `output/workbench/uploads/<完整SHA>/a.zip` | 不可变上传副本；短固定文件名避免Windows长路径；原文件名在upload-events保留 |
| `output/workbench/upload-events/` | 上传原名、完整哈希、大小和时间 |
| `.work/wb/` | 复用既有安全ZIP检查的工作副本；不是原始事实来源 |
| `output/documents/`（可配置） | 既有document-record/current/不可变修订 |
| `output/workbench/tasks/`、`requests/`、`bindings/` | 持久任务、幂等别名及跨文档/位置关系；不是可随意删的缓存 |
| `output/image-enrichment/runs/`、`cache/`、`reviews/` | 原有调用证据、机器缓存、审核覆盖，仍与Document事实分离 |
| `output/workbench/human/<binding hash>/revisions/` | 不可变人工草稿，保留父版本、原结果指纹及文档/修订/asset/位置 |
| `human/.../events/`、`adoption.json` | 每次采用事件及最新指针；采用不等于修改机器审核 |

任务与人工修订使用独立1.0.0契约，见新workbench Schema。原Document/Asset/结果Schema与Prompt
没有改写。人工版本用于工作台展示，不写回原始Markdown，不进入检索，不成为Excel评测真值。
同图同上下文合并执行但保留全部位置；同图不同上下文生成不同cache身份，而非只选第一处。

## 技术与安全

可选UI新增Flask约束`>=3.1,<4`，本机验证3.1.0，许可证
[BSD-3-Clause](https://github.com/pallets/flask/blob/3.1.0/LICENSE.txt)。基础安装不依赖Flask；
旧enrichment/annotation extra和三个命令保留。OpenAI SDK沿用已有可选依赖声明，未升级主环境。
Flask比手写HTTP/multipart少维护解析代码，又不需要前端构建链；FastAPI/Streamlit不是本轮所必需。

上传采用总量100MiB、单ZIP50MiB、最多30ZIP/32form parts及非文件表单内存限制，再执行原ZIP
逐项安全检查。Host必须匹配实际127.0.0.1端口；写操作同时核验Origin和启动时随机CSRF token，
不开放CORS；页面CSP不允许inline/unsafe-eval、iframe或公网资源。文件只能由已知文档/修订/asset
定位，经逐层链接/越界和真实解码检查，不提供任意文件浏览接口。模型/用户文本用textContent，
原HTML和SVG不作为可执行预览。
依据：[Flask上传](https://flask.palletsprojects.com/en/stable/patterns/fileuploads/)与
[安全说明](https://flask.palletsprojects.com/en/stable/web-security/)。本机HTTP不提供加密/多人权限，
不要使用端口转发、反向代理或部署到公网。

验证截图、已知限制和用户验收清单见[第十四轮报告](workbench-round14-validation.md)。
