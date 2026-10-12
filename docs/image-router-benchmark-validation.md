# 本地图片路由器隔离验证记录

验证日期：2026-09-11。结论适用于当前 45 张图片和评测基准 v1，不代表通用图片分布。

## 结论

本轮不建议进入“通用本地路由器正式集成”。三种候选在冻结安全策略下都没有把中、高价值图片
自动送入 `skip_candidate`，也没有把复杂图片自动送入 `local_ocr`；但真正能够留在本地、从而
减少云端调用的比例只有 2.2%～4.4%。这不足以抵消 300 MB～1.5 GB 权重、约 0.94～1.13 GB
增量峰值工作集和额外维护成本。

两张二维码被 MobileCLIP2 和 CLIP 都正确识别为 `qr_code`，但人工表中的处理建议是“仅归档／
不进入检索”，因此路线评分把 `local_qr` 记为不一致。这恰好证明“看图分类”和“是否进入知识库”
不是同一问题。若继续实验，建议只做独立的确定性二维码检测／解码；其他图片保持
`uncertain → Qwen`。暂不建议开展第五轮补充 B 的通用编码模型接入。

## 现场与隔离边界

- 工作区：`E:\personal_KB`，不是 Git 仓库；没有 commit、push 或 PR。
- Python 3.12.2；Windows 11 build 26200；CPU 为 AMD Ryzen 7 5800H，8 核 16 线程；内存
  17,024,741,376 bytes；本机虽有 RTX 3060 Laptop 6 GiB，但本轮固定 CPU、batch size 1、
  Torch 线程数 8。
- 实验代码在 `experiments/image_router_benchmark`，未放入 `src/`，没有改正式 Document、Asset、
  revision、Enrichment、Markdown 或 CLI。
- 独立环境、官方代码、模型与预测缓存均在 `.work/image-router-benchmark`；机器输出在
  `output/image-router-benchmark`。推理命令没有工作簿参数；评分命令才只读打开工作簿。
- 未读取 `.env`，未调用 Qwen 或其他云端模型，未修改正式图片 enrichment 结果。

## 数据和标注对齐

- 输入文件 45，唯一二进制 45，重复组 0；全部可解码。
- 实际格式：WEBP 35、JPEG 7、PNG 3。
- 现有图片清单按二进制哈希匹配 45/45，包含 87 条正式来源关系；40 张图片存在多个精确关系。
- 评测工作簿有 45 个可按完整 SHA-256 对齐的标注行。主类型、知识价值和处理建议均有值；人工
  参考事实 43/45 有值，但本实验没有读取它们作为推理输入。
- 工作簿“审核状态”仍为 45 行“未审核”，所以所有得分标记为 `provisional_v1`，不能宣称为
  已确认评测真值。
- 推理前后数据集指纹均为
  `4d23996af1d095fc52696823a37700cd03722c582e22d0edac81a9d4b2be7c28`，原图未改变。

## 冻结方法

- 类别描述版本：`image-router-categories-v1`；每类 3 条通用英文描述。
- 模型配置版本：`image-router-models-v1.1`。
- 每条描述先独立归一化，类内取均值后再次归一化；图文使用余弦相似度。
- 归一化比较分数使用固定温度 0.02。它只表示当前候选集合内的相对分数，不是校准概率。
- 主类型 11 类，处理路线 7 类；人工细类和处理建议通过显式映射表对齐。
- 冻结阈值：默认比较分数 0.40、余弦分差 0.015；`local_qr` 与 `local_ocr` 为 0.65/0.03；
  `skip_candidate` 为 0.75/0.04。未达到阈值统一拒判为 `uncertain`。

### 有效运行

| 候选 | 固定模型修订 | 推理实现 |
| --- | --- | --- |
| MobileCLIP2-S0 | `3136ea51c8ed56b9f9abfab04cb816735aaad6cb` | Apple 固定代码提交 `aecfb545…` + OpenCLIP 3.3.0 |
| OpenAI CLIP ViT-B/32 | `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268` | Transformers 4.51.3 |
| SigLIP2 Base Patch16 224 | `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` | Transformers 4.51.3 |

首轮 MobileCLIP2 运行遗漏官方要求的输入均值 0／方差 1；首轮 SigLIP2 运行遗漏官方要求的文本
`padding=max_length, max_length=64`。对应原始 run 保留但排除出基线。适配器版本加入缓存键后，
使用正确官方预处理生成 `*-zero-shot-v1b` 三个有效 run；没有依据评分修改类别描述。

## 零样本结果

| 模型 | 主类型 Top-1 | 原始路线 Top-1 | 冻结覆盖 | 接收项精度 | 预计云端减少 | 危险跳过 | 复杂图误送 OCR |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| MobileCLIP2-S0 | 53.3% | 40.0% | 33.3%（15） | 73.3% | 4.4%（2） | 0 | 0 |
| OpenAI CLIP ViT-B/32 | 66.7% | 53.3% | 28.9%（13） | 69.2% | 4.4%（2） | 0 | 0 |
| SigLIP2 Base P16 224 | 66.7% | 42.2% | 15.6%（7） | 85.7% | 2.2%（1） | 0 | 0 |

“接收项精度”按工作簿处理建议严格比对。二维码被预测为 `local_qr`、而人工建议为 `skip_candidate`
时计为不一致；这不是图片类型识别错误，也不意味着自动跳过是安全的。

### 各主类型召回率

| 主类型（支持数） | MobileCLIP2 | CLIP | SigLIP2 |
| --- | ---: | ---: | ---: |
| diagram_or_flowchart（21） | 42.9% | 61.9% | 52.4% |
| code_screenshot（7） | 100% | 100% | 100% |
| chart_or_plot（5） | 40.0% | 60.0% | 80.0% |
| plain_text_or_decorative_text（4） | 0% | 25.0% | 50.0% |
| qr_code（2） | 100% | 100% | 100% |
| logo_or_icon（2） | 50.0% | 50.0% | 50.0% |
| ui_screenshot（2） | 100% | 100% | 100% |
| table（1） | 100% | 0% | 100% |
| decorative_image（1） | 0% | 100% | 0% |

少数类的百分比波动很大；例如“表格 100%”只代表 1 张，不能据此宣称泛化。

### 统一阈值观察（评分后探索，不属于冻结基线）

| 模型 | 分数/分差 | 覆盖 | 接收精度 | 云端减少 | 危险跳过 | 复杂图误送 OCR |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| MobileCLIP2 | 0/0 | 84.4% | 44.7% | 20.0% | 0 | 3 |
| MobileCLIP2 | 0.7/0.04 | 20.0% | 77.8% | 4.4% | 0 | 0 |
| CLIP | 0/0 | 91.1% | 58.5% | 33.3% | 0 | 3 |
| CLIP | 0.4/0.02 | 24.4% | 63.6% | 4.4% | 0 | 0 |
| SigLIP2 | 0/0 | 68.9% | 54.8% | 26.7% | 0 | 3 |
| SigLIP2 | 0.4/0.02 | 11.1% | 60.0% | 4.4% | 0 | 0 |

放宽阈值会提高表面覆盖和预计云端节省，但三个模型都把 3 张高价值复杂图片原始预测为
`local_ocr`。冻结的按路线阈值成功拒判了这些项。

### 放宽阈值时的复杂图误送 OCR 清单

- MobileCLIP2：
  - `asset-0007.webp` — `50933b2e0795615983dbae141f9c641af658c01428fc74634edd8e9742edad86`
  - `asset-367af9dd1487.webp` — `af5e261205ecfed7a484d7c731c7b22f52a2a847aa208f2b60a18cb6c65d5910`
  - `asset-8441b3f81fc0.webp` — `dd8871c96dac55ea1512c268f0334197a94d1729f4de94c3f7448f7de1f9e3ae`
- CLIP 与 SigLIP2：
  - `asset-367af9dd1487.webp` — `af5e261205ecfed7a484d7c731c7b22f52a2a847aa208f2b60a18cb6c65d5910`
  - `asset-8441b3f81fc0.webp` — `dd8871c96dac55ea1512c268f0334197a94d1729f4de94c3f7448f7de1f9e3ae`
  - `asset-a49ad725cf46.webp` — `9bb09d34635a2d03ba82c4bc6dd3e732c4ba458912bc222dbf8313c156fb4411`

在已生成的 49 组统一阈值网格中，三个模型的中、高价值错误 `skip_candidate` 数均为 0；这只
说明当前样本与描述，没有证明未来图片不会发生危险跳过。

## CPU 资源实测

下表为模型已缓存后的独立进程运行，不含首次网络下载时间。峰值工作集包含 Python、Torch 和模型；
“增量”以模型加载前 RSS 为基准。

| 模型 | 冷加载 | 单图 P50 / P95 | 45 图推理 | 推理权重 | 增量峰值工作集 |
| --- | ---: | ---: | ---: | ---: | ---: |
| MobileCLIP2-S0 | 7.887 s | 68.5 / 86.8 ms | 3.188 s | 299,864,852 B | 955,379,712 B |
| CLIP ViT-B/32 | 7.702 s | 84.2 / 100.7 ms | 3.837 s | 605,247,071 B | 939,999,232 B |
| SigLIP2 Base | 10.960 s | 168.5 / 184.5 ms | 7.674 s | 1,500,800,904 B | 1,129,721,856 B |

当前 `.work/image-router-benchmark` 实占 3,703,802,052 bytes：模型缓存 3,658,969,476，独立环境
31,565,336，固定 Apple 仓库约 12,101,515。首次 CLIP 下载包含了未用于本轮推理的 TensorFlow 和
Flax 权重，使其快照达到 1,819,546,255 bytes；代码已增加按后端下载白名单，新的干净缓存只会
取 PyTorch 权重和处理器文件。为避免未经授权的破坏性清理，本轮没有自动删除旧缓存。

## 缓存与失败验证

- MobileCLIP2 有效 run 再次执行时 45/45 命中预测缓存，`freshPredictions=0`，且在加载模型前
  直接返回。去除 `cacheHit` 运行字段后，两次预测哈希同为
  `2fbd55b357b6ca77941896a06d26c29bee0f1529258082c1c0ee48b7a022c990`。
- 缓存键包含图片 SHA-256、模型与权重修订、适配器／预处理版本和类别描述哈希。MobileCLIP 与
  SigLIP 的预处理修正不会复用旧预测。
- 官方仓库首次 clone 遇到连接重置，重试后成功；Hugging Face Xet 分片层出现 416 与默认日志
  目录拒绝写入，下载器回退后三个固定模型均完整加载并完成 45/45。
- 初始预处理错误的两个 run 和安装事件记录在
  `output/image-router-benchmark/failures/experiment-events.json`，没有修改原始预测文件。
- 第一次全目录 Ruff 命令误扫 `.work` 中的 Apple 第三方代码和 `output` 生成文件而失败；随后只在
  项目配置中排除生成物、只读样本和第三方缓存，最终全目录检查通过，未改第三方文件。

## 测试与未执行项

- 主项目 pytest：114 passed，1 skipped。
- 实验测试：14 passed，覆盖 JPG/PNG/WEBP、坏图、重复哈希、冻结枚举、阈值、缓存键、完整
  缓存跳过模型、工作簿隔离、缺失标签和安全运行名。
- Python 编译检查通过。
- Ruff 对正式源码、测试和实验代码通过。`.work`、`output`、`samples`、`test_pictures` 已作为
  生成物、模型代码或只读资料排除；不修改 Apple 第三方源码来迎合项目 lint。
- 没有做 GPU 运行、中文／双语描述对照、DINOv2 或监督原型探索；也没有运行 45 张 Qwen 全量
  基线。没有桌面可视化验收需求。

## 可复查输出

- 汇总：`output/image-router-benchmark/comparisons/zero-shot-v1b/`
- 有效原始机器预测：`output/image-router-benchmark/runs/*-zero-shot-v1b/predictions.jsonl`
- 逐图评分、混淆矩阵和阈值表：`output/image-router-benchmark/scores/*-zero-shot-v1b/`
- 输入清单：`output/image-router-benchmark/input-manifest.json`
- 许可记录：`experiments/image_router_benchmark/LICENSES.md`

若用户确认工作簿审核状态并补充一批完全未参与本轮调整的新图片，可重新验证“二维码专用前置
检测”是否值得正式集成。当前证据不支持用任一候选自动跳过通用图片。
