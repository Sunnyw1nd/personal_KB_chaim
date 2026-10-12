# Qwen 输出格式与契约复杂度对照

状态：第十一轮八格诊断已完成，后续由compact可选配置小规模验证；本轮不再生成收费结果。
历史入口保留，最新状态见[实验索引](../README.md)。

用相同两张图片比较完整严格 JSON、精简严格 JSON、精简普通 JSON 和自由文本。
实验采用独立目录和独立证据，实际结果见 [第十一轮报告](reports/round11-validation.md)。

从 `E:\personal_KB` 运行：

```powershell
python -m experiments.qwen_output_format_ablation.runner dry-run --run-name my-format-run
python -m experiments.qwen_output_format_ablation.runner run --run-name my-format-run --allow-external
```

结果位于 `runs/my-format-run/`：

- `plan.json`：冻结的八格参数、图片指纹与无图片数据的 messages；
- `cells/<样本--路径--短哈希>/content.txt`：脱敏后的完整模型回答；
- `cells/.../response.json`：独立响应、usage、模型与耗时；
- `cells/.../analysis.json`：解析与校验结果，不代表质量合格；
- `run-manifest.json`：八格清单与统计。

中断后可离线重建清单和解析，无模型调用：

```powershell
python -m experiments.qwen_output_format_ablation.runner recover --run-name my-format-run
python -m experiments.qwen_output_format_ablation.runner audit --run-name my-format-run
```

再次执行同一个 `run` 会跳过已经保留预约记录的格子；成功、失败和结果未知的已发格均不自动重发。
若进程被强制终止并留下 `active.lock`，应先确认该进程已停止并核查证据，不绕过锁来并行运行。

本轮真实结果的逐图阅读稿是
[comparison-verbatim.md](runs/qwen-format-ablation-v1-20261012/evaluation/comparison-verbatim.md)，
用户可用 [anonymous-review-verbatim.md](runs/qwen-format-ablation-v1-20261012/evaluation/anonymous-review-verbatim.md)
对照原图评分。Codex 评审已知路径；匿名稿仅供用户独立评审，用户验收仍待完成。

评审脚本只支持本轮冻结 run，评审答案不参与推理：

```powershell
python -m experiments.qwen_output_format_ablation.review --run-name qwen-format-ablation-v1-20261012
```

已有 evaluation 时拒绝覆盖。API Key 沿用根目录 `.env` 或 `DASHSCOPE_API_KEY` 环境变量；SDK、
传输和结构化重试均关闭，每个新 run 最多八次生成。本轮新 run 已用满授权预算，新增真实实验应
单独授权。
