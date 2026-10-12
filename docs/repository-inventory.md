# 第十三轮本机盘点与数据布局

日期：2026-10-12，工作区`E:\personal_KB`。开始前main，HEAD
`a17e913c10ce4c6dca126443feb9c8c57c534787`，origin为Sunnyw1nd/personal_KB_chaim，工作区干净。
盘点同时检查Git跟踪文件和本机忽略内容；不以GitHub可见目录代替本机事实。

## 本机分类（整理前快照）

文件数含该范围的字节码/附件；数值属于本轮快照，不写入长期AGENTS。

| 路径 | 用途/权威性 | 文件/字节 | 跟踪/主要引用 | 建议去向/本轮移动/理由 |
| --- | --- | ---: | --- | --- |
| `src/` | 正式运行源码及可重建字节码 | 81 / 1,018,658 | 40跟踪；CLI、测试、实验 | 原位；仅批量实现移到包内application，保留旧接口 |
| `tests/` | 合成测试、5项本机真实回归 | 39 / 1,134,061 | 13跟踪；pytest | 原位；不把真实资料变成公开fixture |
| `schemas/` | 10份正式契约 | 10 / 97,382 | 全跟踪；模型导出、测试 | 原位，字节不变 |
| `config/` | 哈希级local-only策略 | 1 / 225 | 跟踪；dataset/observer | 原位；无Key，不能当图片标签真值 |
| 包内`enrichment/prompts/` | 3版冻结Prompt | 3 | 跟踪；加载器与缓存 | 原位；不格式化、不重算版本 |
| 顶层`prompts/` | 空目录，无已发现消费者 | 0 / 0 | 未跟踪 | 留原位；不用它再建一套Prompt体系 |
| `docs/` | 当前指南与阶段证据 | 16 / 163,933 | 全跟踪；README/观察台证据引用 | 原位加分类导航；不搬迁历史报告 |
| `experiments/` | 4个独立实验及未上传证据 | 196 / 1,533,157 | 23跟踪，其余忽略；详见实验索引 | 原位；状态不等于删除依据 |
| `samples/` | 不可变输入，含真实缺图/补图配对 | 13 ZIP / 31,581,518 | 忽略；prepare_sample/真实回归 | 原位，不重新导入未参与回归的所有文章 |
| `test_pictures/` | 45张唯一图片，8JPEG/2PNG/35WEBP | 45 / 2,239,441 | 忽略；inventory/工作簿/实验 | 原位，无重命名或重压缩 |
| `output/image-annotation/` | 2工作簿、清单、预览 | 95文件 | 忽略；图片任务与评测 | 人工答案权威，预览派生；都不移动 |
| `output/managed-samples/` | 文档修订与正式Asset | 146文件 | 忽略；inventory来源关系 | 原位保留current/历史 |
| `output/real-samples/` | 真实验证输出与旧输出 | 172文件 | 忽略；回归/迁移/图片关系 | 原位；不认定全是无用副本 |
| `output/real-completion-pair/` | 缺图/补图真实验证 | 42文件 | 忽略；配对证据 | 原位保留 |
| `output/image-enrichment/` | 模型结果、原文、attempt、run、评测、观察台 | 525文件 | 忽略；observer/缓存/审核 | 原位；13个run、20个cache、4个evaluation；没有持久化用户审核决定 |
| `output/image-router-benchmark/` | 预测、评分、混淆矩阵、资源报告 | 57文件 | 忽略；本地路由实验 | 原位保留路线证据 |
| `output/`合计 | 事实/人工/证据/派生混合，不能整目录清理 | 1,038 / 27,491,266 | 忽略；含23文档记录、26份document.json | 未来按用途配置根，本轮不迁移 |
| `.work/` | ZIP副本、独立环境、模型、第三方代码、临时核验 | 3,521 / 3,873,372,501 | 忽略；缓存和实验 | 原位；不做长期清理服务 |
| `.env` | 凭据配置，正文未读 | 1 | 忽略；凭据加载器 | 留本机；只确认位置及规则 |
| `.env.example` | 空凭据模板 | 1 | 跟踪；安装指南 | 根模板可公开；填写后绝不提交 |

`.work`最大项是SigLIP2权重约1.50GB，其次CLIP的TF/PyTorch约0.606/0.605GB。未下载新模型，
未重装原实验环境。现有临时脚本只发现包内评审脚本和工作副本，没有因未跟踪而自动删除任何项。
完整本机数量/扩展名/最大项与957份保护哈希在`.work/round13/before.json`，不上传该私人清单。

图片inventory有45文件、87关系，指向20份已存在的Document路径；所有指向均在本项目output，
缺失0。关系一对多，不将20个来源路径等同于20篇唯一文章。工作簿及Codex评审与用户质量决定
分别保留；“用户说可以接受”不自动写成已有approved事实。

## 已引用的外部位置与未查看范围

当前Python为`E:\Environments\Python\Python312\python.exe`，现有实验venv的pyvenv.cfg引用同一
安装，路径可访问；只确认解释器与配置，不扫描其他磁盘。局域网实验的私有服务地址可在该
实验配置读取，但本轮没有连网探测或模型调用。Git origin只从本机配置读取，没有远程修改。

历史验证记录明确引用的`E:\html\MCPzhihu.zip`按精确路径检查，当前不存在；项目`samples`中的
知乎ZIP仍可访问并通过回归。不把历史“当时哈希相同”的结论改成当前存在，也不搜索整个下载盘。

未枚举整盘、浏览器下载目录或未被代码/配置/文档引用的项目外资料；早期聊天中提到的其他路径
不代表仍为当前活跃输入。私人数据只核实完整性/已知关系，不借本轮重评模型或逐页人工验收。

## 未来数据根：规划而非当前接口

现有CLI已分别接受samples/work/output/image-root等参数，但没有统一知识库根配置，也没有可迁移
的全局权威数据库。未来工作台可选一个独立KB根，用配置映射以下职责，默认沿用本机路径：

| 配置职责（规划） | 当前实际位置 | 保留/重建规则 |
| --- | --- | --- |
| 输入快照 | `samples/`、`test_pictures/` | 权威原始字节，不覆写；本机路径不当来源身份 |
| 文档修订 | `output/managed-samples/`及各既有输出 | 当前标准事实/current/历史；保留身份与人工修改追溯 |
| 人工答案/审核/修改 | 工作簿、`evaluations/`、未来`reviews/`与修改记录 | 人工信息不可凭模型输出重建 |
| 调用证据 | `runs/`、attempt/raw response/usage、实验reports | 失败证据与历史评审保留，未知usage不补零 |
| 机器处理缓存 | `image-enrichment/cache/` | 可重建但会产生费用和非确定输出，不等于低成本临时项 |
| 派生展示/查询索引 | `observer/`、缩略图；未来检索索引 | 低成本本地重建；删除前仍核实没有人工字段 |
| 工作副本/环境 | `.work/` | 内容寻址副本、可重新准备；不要用宽泛删除替代精确计划 |

只读观察台可以重建；inventory中的documentJson和某些运行证据使用绝对路径，移动整个KB后
不能保证只改一个根就恢复所有引用。下一轮先显式定位/修复派生关系，不静默重写历史请求上下文、
来源身份或cache key。cache与run不合并，人工review不放入可随意清理区。

## 重复核验入口

```powershell
python tools/repository_inventory.py --output .work/round13/local-inventory-new.json
python tools/repository_inventory.py --verify .work/round13/before.json --output .work/round13/integrity-new.json
```

输出已存在会拒绝覆盖；只写`.work`内新报告，不读取凭据正文、不发网络请求、不跟随链接。
后一个命令核验原有保护文件，新增项分开列出；这里的哈希校验不等于确认图片语义或清理授权。
