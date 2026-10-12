from __future__ import annotations

import json
import os
from pathlib import Path

from experiments.qwen_output_format_ablation.review import (
    IMAGE_CODE_REFERENCE,
    code_comparison,
    fence,
)
from wechat_html_normalizer.enrichment.evaluation import read_benchmark
from wechat_html_normalizer.enrichment.runner import _atomic_json
from wechat_html_normalizer.enrichment.security import redact_sensitive_object

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT / "output/image-enrichment"
RUN = "qwen37-compact6-v1-20261012"
GRADE = {
    "asset-0007.webp": (
        "实质修改",
        "主类型已修为architecture_flow，三步骤、136/200和向右箭头齐全；但把独立箭头说成指向下方方框，图中没有这样的端点连接。需要修正关系；被红线划掉的灰线是否为书本也不确定。",
    ),
    "asset-367af9dd1487.webp": (
        "无需修改",
        "三列表头和七行内容、对应关系准确；换行只是表格排版，未形成额外行。",
    ),
    "asset-44fdbea6544d.webp": (
        "基准待复核",
        "完整16行已进入code.text，TEXT、引号、双反斜杠、for/with缩进保留，上一轮注释缩进问题消失。与人工参考仅剩第6行逗号后的空格差异（工作簿还多两行空行）；该处原图间距需用户放大确认，不能宣称逐字通过。",
    ),
    "asset-57ae62ca4266.webp": (
        "轻微修改",
        "轴名、刻度、100B附近跃升和平台保留；50B‘能力未显现’及‘临界点’应标为示意文字解释，不作为实测结论。原图实际近似平滑S型，旧评审称阶跃图的说法需复核，本次不因S型措辞单独扣分。",
    ),
    "asset-ce52ad0983de.webp": (
        "轻微修改",
        "识别装饰文字并保留可见文字、低价值排除候选；原文件有透明通道，白色背景是渲染假定，不是原图事实。工作簿OCR/include与当前exclude的政策选择仍待用户确认。",
    ),
    "asset-dba5466c7c5e.jpg": (
        "无需修改",
        "两工具、txt_counter及标注数字2准确；没有再扩展成两个活跃服务，主要界面事实与可见文本对应。",
    ),
}


def write(path: Path, value) -> None:
    safe, _ = redact_sensitive_object(value)
    _atomic_json(path, safe)


def main() -> None:
    manifest = json.loads((ROOT / "runs" / RUN / "run-manifest.json").read_text(encoding="utf-8"))
    candidates = [
        p
        for p in (ROOT / "evaluations").glob(RUN + "-*")
        if (p / "baseline-evaluation.json").is_file()
    ]
    if len(candidates) != 1:
        raise ValueError("expected the unique completed Round 12 baseline evaluation")
    target = candidates[0]
    if (target / "quality-review.json").exists():
        raise FileExistsError("preserve existing assessment")
    rows, summary = read_benchmark(
        PROJECT / "output/image-annotation/annotation-002/图片辅助标注.xlsx",
        PROJECT / "test_pictures",
    )
    benchmark = {r["imageSha256"]: r["benchmark"] for r in rows}
    old_runs = {
        name: json.loads((ROOT / "runs" / name / "run-manifest.json").read_text(encoding="utf-8"))
        for name in ["qwen37-flash-reliability6-v1", "qwen37-flash-quality6-v2"]
    }
    items = []
    text = [
        "# 第十二轮六图逐图评审",
        "",
        "评审者：Codex（已知配置）；用户验收：未完成。",
        "",
        "结构通过与事实正确分开；以下代码展示使用text围栏，不能用格式器重排。",
        "",
    ]
    for item in manifest["items"]:
        record = json.loads((ROOT / item["recordPath"]).read_text(encoding="utf-8"))
        result = record["result"]
        grade, note = GRADE[item["relativePath"]]
        comparisons = {}
        for name, old in old_runs.items():
            old_item = next(i for i in old["items"] if i["imageSha256"] == item["imageSha256"])
            old_record = json.loads((ROOT / old_item["recordPath"]).read_text(encoding="utf-8"))
            old_result = old_record.get("result") or {}
            comparisons[name] = {
                "status": old_item["status"],
                "imageType": old_result.get("imageType"),
                "action": old_result.get("action"),
                "recordPath": old_item["recordPath"],
                "codeLines": len((old_result.get("code") or {}).get("text", "").splitlines()),
            }
        reviewed = {
            "imageSha256": item["imageSha256"],
            "relativePath": item["relativePath"],
            "grade": grade,
            "note": note,
            "reviewer": "Codex",
            "userReviewStatus": "pending",
            "machineReviewStatus": record["reviewStatus"],
            "recordPath": item["recordPath"],
            "cacheKey": item["cacheKey"],
            "history": comparisons,
        }
        if result.get("code"):
            reviewed["imageReferenceComparison"] = code_comparison(
                result["code"]["text"], IMAGE_CODE_REFERENCE
            )
            reviewed["workbookComparison"] = code_comparison(
                result["code"]["text"], benchmark[item["imageSha256"]]["referenceAnswer"]
            )
        items.append(reviewed)
        image = PROJECT / "test_pictures" / item["relativePath"]
        href = Path(os.path.relpath(image, target)).as_posix()
        record_href = Path(os.path.relpath(ROOT / item["recordPath"], target)).as_posix()
        text.extend(
            [
                f"## {item['relativePath']}",
                "",
                f"![原图]({href})",
                "",
                f"Codex等级：{grade}；用户：未审核。",
                "",
                note,
                "",
                f"[完整record与attempt关联]({record_href})",
                "",
                "### 首次映射结果",
                "",
                fence(json.dumps(result, ensure_ascii=False, indent=2), "text"),
            ]
        )
        if result.get("code"):
            text.extend(
                [
                    "### 唯一代码字段（原样）",
                    "",
                    fence(result["code"]["text"], "text"),
                    "### 对照差异（不做空格宽松比较）",
                    "",
                    fence("\n".join(reviewed["imageReferenceComparison"]["lineDiff"]), "diff"),
                ]
            )
        text.extend(
            [
                "### 历史同期依据",
                "",
                fence(json.dumps(comparisons, ensure_ascii=False, indent=2), "text"),
                "本次只有首次调用，没有实际恢复结果；恢复机制仅fake验证。",
                "",
                "用户等级：________；用户备注：________",
                "",
            ]
        )
    review = {
        "schemaVersion": "1.0.0",
        "runId": RUN,
        "reviewer": "Codex",
        "userAcceptance": "pending",
        "reviewBasis": "Codex visual comparison of original images, provisional workbook v1 and historical runs; no user approval claimed",
        "benchmarkSha256": summary["workbookSha256"],
        "items": items,
    }
    write(target / "quality-review.json", review)
    (target / "round12-quality-review.md").write_text("\n".join(text), encoding="utf-8")
    verify_text = (target / "round12-quality-review.md").read_text(encoding="utf-8")
    for item in manifest["items"]:
        record = json.loads((ROOT / item["recordPath"]).read_text(encoding="utf-8"))
        code = (record["result"].get("code") or {}).get("text")
        if code and code not in verify_text:
            raise ValueError("code content changed during rendering")
    write(
        target / "review-integrity.json",
        {"codeTextVerbatim": True, "userAcceptance": "pending", "items": len(items)},
    )
    print(target / "round12-quality-review.md")


if __name__ == "__main__":
    main()
