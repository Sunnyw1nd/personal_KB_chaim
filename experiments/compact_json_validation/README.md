# 第十二轮离线质量评审

状态：已完成的阶段评审工具，不是正式推理模块或通用评测框架。结论见
[验证报告](../../docs/image-enrichment-round12-validation.md)。

使用主项目现有enrichment/annotation可选环境；只读取原六图、已有compact run、v1/v2历史结果
及`output/image-annotation/annotation-002/图片辅助标注.xlsx`。人工答案只用于运行后的对照。
脚本明确固定这一次实验，不能把其中的GRADE/参考代码当作对任意新图片的预测。

```powershell
python -m experiments.compact_json_validation.review_round12
```

脚本不创建Provider、不请求模型、不修改工作簿；产生`quality-review.json`、逐图Markdown和
评审完整性记录，位于该run对应evaluation中。已有评审会拒绝覆盖，因此当前本机再次执行预期
报“preserve existing assessment”，不建议为重跑删除原证据。用户质量决定应另存正式review，
不能改写历史Codex评审来伪装用户已批准。

本轮只补README与索引，Python入口和运行数据原位保留；真实6图没有在第十三轮重新调用。
