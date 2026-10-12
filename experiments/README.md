# 实验与阶段证据索引

正式包不导入实验。实验可以只读复用正式模型和工具；推理与评分分开，人工标签/参考答案不进
请求。所有路径保持原位，下面的命令入口未因第十三轮整理改变；本轮不重跑任何模型实验。

| 目录 | 类型/状态 | 结论与证据 | 环境/输入/产物 |
| --- | --- | --- | --- |
| [image_router_benchmark](image_router_benchmark/README.md) | 持续评测工具；第五轮补充A完成，正式集成暂停 | 三个零样本候选安全节省的云端调用少，未集成；[验证](../docs/image-router-benchmark-validation.md) | 独立requirements/venv、冻结类别；test_pictures＋inventory；结果output/image-router-benchmark |
| [qwen_output_format_ablation](qwen_output_format_ablation/README.md) | 第十一轮已完成诊断；被compact可选配置后续验证 | 8格首测C较B保留更多内容，不是普遍结论；[报告](qwen_output_format_ablation/reports/round11-validation.md) | 主环境可选enrichment；两图与有限上下文；独立runs，私有附件不上传 |
| [compact_json_validation](compact_json_validation/README.md) | 第十二轮已完成的离线评审工具 | 工程通过、质量有限，用户反馈需修改后可接受；[报告](../docs/image-enrichment-round12-validation.md) | 读取已有6图run/工作簿，输出evaluation；已有评审拒绝覆盖，不发送请求 |
| `lan_vlm_qwen25_3b/` | 第九/十轮已完成，专项路线停止；仅路由候选，当前暂停 | 本机README及reports/round9-validation.md、round10-validation.md、round10-specialist-v2-retry-validation.md | 本机100文件，整个目录未跟踪；私有部署配置，独立runs/cache；不要为读索引而探测服务 |

LAN条目只是导航，不把停止专项误称为路由失败；也不根据历史部署宣称服务现在在线。
本机可打开其README及报告，公开仓库不附带私有LAN实现。其他实验的已忽略raw、cache和运行
附件仍保留本机；没有模型图片、工作簿或结果时，代码可测试不代表真实实验已复现。

## 第五至十三轮入口

- 第五/六轮：[Enrichment工程与可靠性](../docs/image-enrichment-validation.md)；
- 第七轮：[v2质量未通过](../docs/image-enrichment-round7-validation.md)，v1仍为默认；
- 第八轮：[观察台与旧缓存再校验](../docs/image-enrichment-round8-validation.md)；
- 第九/十轮：本机LAN reports（见上表），未发布；
- 第十一轮：上表格式对照；第十二轮：[受控恢复](../docs/image-enrichment-round12-validation.md)；
- 第十三轮：[结构整理](../docs/repository-round13-validation.md)。

历史“待继续”是当时建议，不构成当前追加模型调用授权。最新产品路线见[roadmap](../docs/roadmap.md)。
