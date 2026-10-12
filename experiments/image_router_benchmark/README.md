# 本地图片路由器隔离实验

该目录只用于第五轮补充 A 的零样本验证，不属于正式 Python 包，不读取 `.env`，也不调用 Qwen
或其他云端服务。推理只读取图片清单和冻结类别描述；评分是单独命令，之后才读取人工工作簿。

## 运行

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File experiments/image_router_benchmark/setup_environment.ps1

& '.work/image-router-benchmark/venv/Scripts/python.exe' `
  experiments/image_router_benchmark/router_benchmark.py inventory

& '.work/image-router-benchmark/venv/Scripts/python.exe' `
  experiments/image_router_benchmark/router_benchmark.py infer `
  --model mobileclip2_s0 --run-name mobileclip2-s0-user-run-001

& '.work/image-router-benchmark/venv/Scripts/python.exe' `
  experiments/image_router_benchmark/router_benchmark.py score `
  --predictions output/image-router-benchmark/runs/mobileclip2-s0-user-run-001/predictions.jsonl
```

将 `--model` 依次替换为 `openai_clip_vit_b32` 和 `siglip2_base_patch16_224`。默认设备固定为
CPU、batch size 1、Torch 线程数 8。模型按固定 Hugging Face revision 下载到
`.work/image-router-benchmark/models`；大体积权重、独立环境、预测缓存和 `output/` 均不进入
正式源码或生产依赖。

首次 `inventory` 只读取 `test_pictures` 和现有 `inventory.json`，不会打开人工工作簿。`infer`
命令没有工作簿参数；`score` 才读取 `annotation-002/图片辅助标注.xlsx`。每个 run 目录不可覆盖，
重复推理使用包含模型、权重修订、预处理、类别描述版本和图片 SHA-256 的缓存。

三组评分完成后可以生成统一比较：

```powershell
& '.work/image-router-benchmark/venv/Scripts/python.exe' `
  experiments/image_router_benchmark/router_benchmark.py compare `
  --comparison-name zero-shot-v1b `
  --scores `
  output/image-router-benchmark/scores/mobileclip2-s0-zero-shot-v1b/score.json `
  output/image-router-benchmark/scores/openai-clip-vit-b32-zero-shot-v1b/score.json `
  output/image-router-benchmark/scores/siglip2-base-p16-224-zero-shot-v1b/score.json
```

运行名只能包含 ASCII 字母、数字、点、下划线和连字符，且每个 run 不覆盖既有目录。模型快照
只下载当前推理后端需要的权重格式；已经存在的旧缓存不会由脚本自动删除。

## 输出

```text
output/image-router-benchmark/
├── input-manifest.json
├── runs/<run-name>/
│   ├── run-manifest.json
│   └── predictions.jsonl
├── scores/<run-name>/
    ├── score.json
    ├── scored-images.jsonl
    ├── type-confusion.csv
    ├── route-confusion.csv
    ├── threshold-analysis.csv
    └── report.md
└── comparisons/<comparison-name>/
    ├── comparison.json
    ├── candidate-summary.csv
    ├── resource-report.csv
    ├── threshold-analysis.csv
    └── report.md
```

`predictions.jsonl` 是冻结的机器原始结果，不含人工标签；`scored-images.jsonl` 和报告才含对齐
结果。工作簿仍为只读事实来源，实验不会回写或覆盖它。

本机 45 张样本的实际验证记录见 [验证报告](../../docs/image-router-benchmark-validation.md)。
