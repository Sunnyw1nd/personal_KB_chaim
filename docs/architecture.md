# 本地知识库：当前结构与可执行整理方案

定位：个人本地知识资料的导入、查看、组织、审核与检索工作台；RAG是未来使用方式之一，
不是唯一产品形态。本文区分实际模块与规划，不创建空框架来假装能力已实现。

第十四轮增量：`application/image_tasks.py`已直接由正式修订准备图片/上下文组；
`application/workbench.py`提供持久单线程任务、导入、缓存、审核与独立人工版本；
`workbench/server.py`和包内静态资源提供回环地址Flask界面。下方早期职责表/后续接缝保留为
第十三轮规划背景，其中“工作台/正式图片桥接尚缺”已由本轮实现；具体当前契约见
[工作台指南](workbench.md)。同图多位置按上下文分组，完整位置保存在任务绑定，不依赖工作簿。

## 命名选择

本轮选择**保留现有包，按职责逐步整理**。展示名使用“个人知识库”，发布包名
`wechat-html-normalizer`、导入名`wechat_html_normalizer`、三个CLI及GitHub仓库名保持不变。

| 方案 | 收益 | 当前代价 | 决定 |
| --- | --- | --- | --- |
| 现有包内划分职责 | 小步改善导航，旧调用和资源定位保持 | 包名暂不够中性 | 本轮采用 |
| 新中性包＋旧包门面 | 长期名称准确 | 35个既有Python模块、相对导入、资源及多处实验引用要迁移；易出现两套接口 | 等多格式契约明确后再单独实施 |

不因名称改动documentId、revisionId、assetId、处理器版本或缓存指纹。中性包的未来选择需检查
名称冲突，并逐项提供兼容门面；本轮不预定尚未发布的新包名。

## 已实现的职责地图

| 职责 | 实际模块 | 接口与限制 |
| --- | --- | --- |
| 文档事实与报告 | `models.py` | Pydantic模型不依赖界面、DOM或模型SDK；但Source/SourceIdentity/sourceType仍是HTML与微信/知乎专用，尚非多格式契约 |
| 身份与修订比较 | `identity.py`、`revision.py` | 规范来源身份、内容/资产指纹、完整率；不改已有规则 |
| 本地输入准备 | `sample_cache.py` | ZIP安全解压、完成清单和有效缓存；不是通用上传产品 |
| 格式/来源转换 | `normalizer.py`、`adapters/`、`parser.py`、`media.py` | 静态微信/知乎HTML，真实媒体识别；不执行脚本/联网补齐 |
| 文档持久化 | `storage.py`、`migration.py` | 不可变修订、current、原子发布、只读迁移预览；单机串行，无跨进程事务 |
| 用户操作编排 | `application/importing.py` | 实际批量准备/转换/失败隔离与汇总，不复制身份及发布规则 |
| 图片任务准备 | `enrichment/dataset.py` | 当前从inventory和Document关系生成PreparedImage；不是从正式修订直接生成任务 |
| 图片生成与恢复 | `enrichment/providers.py`、`runner.py`、`compact_provider.py`、`compact_runner.py`、`compact.py` | 可选配置、策略、调用预算、attempt、映射和可校验缓存；业务校验不是视觉正确性证明 |
| 人工决定与评测 | `enrichment/reviews.py`、`evaluation.py` | 审核覆盖独立于模型原文；评测答案只在评分阶段读取 |
| 展示 | 两个`cli.py`、`enrichment/observer.py`/`observer_site.py`及静态资源 | CLI与离线只读观察台；没有可编辑工作台 |
| 冻结图片配置 | `enrichment/settings.py` | Prompt版本、字节哈希和包内路径；CLI保留旧常量导入名 |
| 隔离实验 | `experiments/` | 可读取正式模块，正式包不反向导入实验 |
| 维护盘点 | `tools/repository_inventory.py` | 无网络、只读计数和保护范围哈希，报告写`.work`；不读取密钥正文 |

```text
当前CLI ──> application.importing ──> ZIP准备 / 静态标准化 ──> 文档修订存储
当前图片CLI ──> inventory任务准备 ──> legacy或compact runner ──> 独立模型缓存/证据
当前观察台 ──> 只读索引 + 当前缓存校验 + 人工评测 ──> 静态展示
实验 ──> 可以读取正式模块；正式模块 ──X──> 实验

规划：本地工作台 ──> 用户操作模块 ──> 上述已实现模块
规划：多格式适配 ──> 经版本化的统一文档契约
规划：索引/检索/问答 ──> 权威修订与已审核派生信息的可重建索引
```

`normalizer.py`仍承担较多HTML编排，runner/observer也较大。本轮不为行数拆分它们：目前这些
深模块把安全、身份、恢复和引用约束集中在一个接口后面。下一步只在真实新调用方需要时抽取
接缝，避免新增仅透传参数的manager/utils或循环导入。

## 本轮路径迁移与回退

| 旧位置 | 新位置 | 修复引用/兼容 | 回退方式 |
| --- | --- | --- | --- |
| `batch.py`实现 | `application/importing.py` | `batch.py`显式重导出四个旧名称；包根公开接口仍是同一个函数/类；文章CLI直接调用新模块 | 仅将实现移回旧文件并还原本轮CLI导入；不操作数据或reset工作区 |
| 图片CLI中Prompt常量 | `enrichment/settings.py` | CLI重导入旧名称；observer从settings/providers取配置，不反向依赖CLI | 将同一常量块移回，恢复observer导入；原Prompt字节不动 |
| 文档与实验 | 原位 | 只加导航与状态，不破坏历史命令、相对路径及运行记录 | 撤回本轮新增导航/修改块，历史文档不动 |

旧`python -m wechat_html_normalizer.cli`、Python包根接口、`batch`公开名称、CLI和安装extra保持。
`batch`中的私有辅助函数不是承诺接口；类的Python `__module__`随实现迁移，JSON契约不变，
项目没有发现pickle消费者。不要对未提供的外部pickle兼容作保证。

## 后续接缝与未实现项

1. **正式修订→图片任务**：读取Document和资产文件，校验二进制与修订，生成现有请求；不要
   通过测试工作簿中转。当前`dataset._relations`选择第一份可用上下文而保留多资产引用，不能
   直接等同于同图多正文位置的语义；先决定每位置任务还是明确上下文选择，保持缓存可解释。
2. **工作台动作**：批量导入复用application.importing；单篇复用normalize_saved_article；图片
   执行复用runner；审核复用reviews；观察复用collect_index。任务状态、后台串行调度、取消、
   人工修改后的独立修订尚缺，不在CLI和UI各写一套。
3. **多格式事实**：Source的htmlFileName/resourceDirectoryName/htmlSha256和来源枚举须以
   新契约扩展，不能让TXT的内容冒充HTML。来源ID与定位规则需各格式可测试；旧ID不回写。
4. **获取与检索**：URL获取独立验证快照、安全和敏感信息；PDF先验证布局。检索索引独立于
   Document，不覆盖修订或人工决定，删除索引应只影响重建成本。

近期执行顺序与下一轮验收见[路线与交接](roadmap.md)，数据布局见[本机盘点](repository-inventory.md)。
