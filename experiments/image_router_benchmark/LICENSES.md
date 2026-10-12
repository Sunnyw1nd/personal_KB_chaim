# 实验依赖与模型许可记录

核对日期：2026-09-11。这里只记录本次实际采用入口的许可边界，不用框架许可证替代模型权重
条款。

| 候选 | 推理实现 | 代码许可 | 权重来源与条款 | 集成含义 |
| --- | --- | --- | --- | --- |
| MobileCLIP2-S0 | Apple `ml-mobileclip` 固定提交＋OpenCLIP | Apple 代码 MIT；OpenCLIP MIT | `apple/MobileCLIP2-S0` 固定修订，Apple AMLR，仅限非商业研究 | 可做本隔离研究；企业或商业正式集成前必须另选可用权重或取得授权 |
| OpenAI CLIP ViT-B/32 | Transformers 加载 OpenAI 官方 HF 仓库 | Transformers Apache-2.0；OpenAI 原实现 MIT | `openai/clip-vit-base-patch32` 固定修订；模型卡定位为研究输出并要求具体任务内测试 | 本实验可用；不能把零样本结果直接视为可部署安全结论 |
| SigLIP2 Base Patch16 224 | Transformers | Apache-2.0 | `google/siglip2-base-patch16-224` 固定修订，模型卡标注 Apache-2.0 | 仍需复核训练数据、实际部署要求及项目合规政策 |

实验依赖 `open-clip-torch==3.3.0`、`ftfy==6.3.1`、`timm==1.0.22` 只安装到
`.work/image-router-benchmark/venv`，不进入主项目 `pyproject.toml`。虚拟环境使用
`--system-site-packages` 复用现场已安装的 Torch/Transformers，避免复制数 GiB CUDA 运行时；
manifest 会记录实际解析到的完整依赖版本，因此这不是宣称完全封闭的生产环境。

本机安装元数据记录：`open-clip-torch 3.3.0` 为 MIT，`ftfy 6.3.1` 与 `timm 1.0.22`
为 Apache-2.0。它们的许可不能替代上表中的模型权重条款。

官方来源：

- https://github.com/apple/ml-mobileclip
- https://github.com/apple/ml-mobileclip/blob/main/LICENSE
- https://github.com/apple/ml-mobileclip/blob/main/LICENSE_MODELS
- https://huggingface.co/apple/MobileCLIP2-S0
- https://github.com/openai/CLIP
- https://huggingface.co/openai/clip-vit-base-patch32
- https://github.com/mlfoundations/open_clip
- https://huggingface.co/google/siglip2-base-patch16-224
- https://github.com/huggingface/transformers
