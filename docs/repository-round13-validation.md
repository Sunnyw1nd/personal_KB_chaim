# 第十三轮：仓库整理与本地知识库规划验证

日期：2026-10-12。本轮完成本机盘点、最小代码整理、文档分层与产品交接规划；没有实现新格式、
可编辑工作台或检索，没有模型请求，没有Git提交/推送或远程修改。

## 现场与基线

- 工作区`E:\personal_KB`，main，HEAD`a17e913c10ce4c6dca126443feb9c8c57c534787`；
  origin为Sunnyw1nd/personal_KB_chaim；开始前工作区干净，无用户未提交源码改动。
- Python3.12.2、BeautifulSoup4.13.3、lxml6.0.2、charset-normalizer3.4.1、Pillow11.2.1、
  Pydantic2.12.5、OpenAI SDK3.14.1、openpyxl3.1.5、pytest8.4.2、Ruff0.16.4。
- SDK3.14.1仍超出pyproject的`openai>=2.14,<3`范围；本轮没有借整理升级/改依赖。
  wheel验证复用现有依赖，不等于重新按声明全新解析依赖成功。
- 修改前：全量167 passed、1 failed、1 skipped；全部独立实验50 passed。
  原有失败是凭据模板测试只接受`!.env.example`，但发布后使用根限定`!/.env.example`，
  忽略规则本身正确。本轮测试允许两种等价模板规则，未放宽实际.gitignore策略。
- 本机包括13个真实ZIP、45唯一图片、2工作簿、4实验，LAN实验完整留本机；不是只看公开仓库。
  [盘点与范围](repository-inventory.md)区分看到的引用和未扫描的项目外位置。

## 选择与实际变化

采用保留旧包、包内逐步整理方案；新增中性包需要维护更多兼容层，当前收益不足。
展示名称中性化，但发布名、Python包名、三个CLI、extra、项目版本和远程名称均不变。

| 文件/范围 | 本轮变化 |
| --- | --- |
| `application/importing.py` | 原batch实现迁入，导入改为父包相对路径，业务主体不改 |
| `batch.py`、文章`cli.py` | 明确兼容门面、CLI使用应用模块；包根公开函数/类仍是同一实现 |
| `enrichment/settings.py`、图片CLI、observer | 提取同一冻结配置，保留CLI常量名称，去除observer→CLI反向依赖 |
| `tests/test_repository_structure.py` | 同一接口别名、无实验反向依赖、配置定位及盘点安全测试 |
| `tests/test_enrichment.py` | 修正既有模板忽略断言 |
| `tools/repository_inventory.py` | 只读盘点/保护哈希；拒绝报告越界、链接及覆盖，不读密钥内容 |
| README、AGENTS、usage/design | README简化；当前能力与历史范围分开；修正“导入不支持图片理解”等全项目误读 |
| 新architecture/index/inventory/roadmap及本报告 | 实际模块、迁移、数据层、未来接缝及下一轮验收 |
| `experiments/README.md`及各README | 已完成/暂停/替代状态与证据索引，含未发布LAN；路径与命令保持 |

LAN README未跟踪，先备份到`.work/round13/lan-readme-before.md`再仅增状态导航；不能依赖Git恢复
它。其他旧实验代码、环境、Prompt、Schema、模型权重、run/cache/report都不修改。
没有拆分runner/normalizer/observer的大块业务，没有新增透传manager或空retrieval/rag模块。
完整逐路径回退和兼容限制（包括未承诺的pickle）见[架构迁移表](architecture.md)。

## 测试与安装

普通wheel构建成功：

```powershell
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir .work/round13/wheel
python -m venv --system-site-packages .work/round13/wheel-env
& '.work/round13/wheel-env/Scripts/python.exe' -m pip install --no-index --no-deps .work/round13/wheel/wechat_html_normalizer-0.4.0-py3-none-any.whl
```

包实际从wheel-env的site-packages导入，不是editable。三个`.exe --help`均成功；包内三个Prompt
与CSS/JS可用。现有安装环境没有被卸载或替换，没有新增依赖或许可证选择。

已实际执行的结果：

- 核心相关专项：98 passed（首次整理后）；结构/盘点专项：5 passed；
- 全量：`python -m pytest -ra`，173 passed、1 skipped（无法创建符号链接）；
- 真实样本：`python -m pytest -ra -m real_sample`，5 passed、169 deselected；
- 包安装后的相关验证：57 passed，包含微信/知乎、ZIP、批量、真实缺图/补图、修订/current、
  compact恢复/审核/缓存、只读观察台；命令为
  `wheel-env/Scripts/python.exe -m pytest -ra -o pythonpath= tests/test_batch.py tests/test_real_sample.py tests/test_enrichment_observer.py tests/test_compact_enrichment.py`；
- 全部本机实验（含未上传LAN）50 passed，测试默认离线；没有探测LAN或真实云端调用；
- `python -m compileall -q src tests experiments tools`，通过；
  `python -m ruff check src tests experiments tools`和
  `python -m ruff format --check src tests experiments tools`通过（76文件）；
- 普通wheel通过两个CLI导出10份Schema至`.work/round13/schema-export`，与正式文件逐份字节
  完全相等，不只比较JSON语义；正式文件没有被导出覆盖。

这里不以帮助命令替代业务测试，也不因已有wheel安装成功宣称未运行的新格式可用。

## 真实观察与完整性

wheel的`kb-enrich-images observe build --site-root output/image-enrichment/observer/round13`成功：
45缩略图、0失败、88页；未覆盖旧观察台。程序核对图片/run/cache/evaluation反向映射、静态资源
和本地链接；旧的两个无效缓存仍显示为原有问题。本轮没有桌面浏览器逐项视觉验收。

28份入口/指南/实验README的80个本地相对链接核对通过。发现原报告中3个早期SHA输出目录已
不可访问，保留为明确标注的历史路径而非失效超链接，并链接当前稳定输出报告；没有改变旧ID、
实测统计或历史事实。项目外已引用的`E:\html\MCPzhihu.zip`当前缺失，samples副本通过回归。

整理前957份保护文件哈希清单位于`.work/round13/before.json`；核验原图/ZIP/工作簿、标准文档与
资产、历史模型结果/原文/评审、正式Schema、冻结Prompt及策略没有变化。索引/缩略图不纳入
权威快照，新观察台单独生成；完整核验输出见`.work/round13/integrity-after.json`。
本轮新增文件不是旧资料移动，也没有清理或重写历史模型结果。

没有敏感文件纳入暂存/跟踪；原`.gitignore`继续排除资料、模型、环境和LAN实验。新本机盘点
清单只在.work，不公开私人路径清单。Git HEAD不变；未执行add、commit、push、merge、reset或clean。

## 未做与下一轮

- 不搬迁私人数据，不执行清理计划或变更旧缓存身份；
- 不重做模型能力、Prompt、默认配置及真实六图；用户反馈“修改后可接受”记录为反馈，不伪造
  持久化approved sidecar；现有机器审核状态保持；
- 不实现正式Document→图片任务、同图多位置上下文政策、后台任务状态或内容人工修改契约；
- 不实现多格式Source泛化、URL获取、Office/PDF、检索/RAG；
- 不全盘扫描项目外资料，不核查LAN当前部署，不重新安装大模型；
- wheel复用现有依赖，严格全新依赖解析/SDK版本声明协调另行处理。

下一轮可直接从[roadmap的工作台交接表](roadmap.md)开始：现有导入/存储/图片恢复/审核模块已可
复用，最关键缺口是**正式修订到图片任务**，不要让工作台依赖测试工作簿才能识图。
