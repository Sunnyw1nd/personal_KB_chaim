"""Offline assessment and readable evidence; never used by the inference runner."""

from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path

from wechat_html_normalizer.enrichment.evaluation import read_benchmark

from . import runner

IMAGE_CODE_REFERENCE = (
    "import os.path\n"
    "from whoosh.index import create_in, open_dir\n"
    "from whoosh.fields import Schema, TEXT, ID\n"
    "import pickle\n"
    "#轻量级的elastic search\n"
    'with open("index_data\\\\chunk_index\\\\index_chunk", "rb") as f:\n'
    "    index_chunk=pickle.load(f)\n"
    "# 定义Schema\n"
    "schema = Schema(content=TEXT, path=ID(stored=True))\n"
    'ix = create_in("index_data\\\\tfidf", schema)\n'
    "writer = ix.writer()\n"
    "for chunk in index_chunk.values():\n"
    "    print (chunk)\n"
    "    writer.add_document(content=chunk.content, path=str(chunk.chunk_id))\n"
    "writer.commit()\n"
    'print("索引创建完成")'
)

# A separate Codex visual assessment of the actual frozen run; never sent to the model.
ASSESSMENTS = {
    "code-A": {
        "grade": "不可用",
        "notes": [
            "code.text 混入模型自我解释和重复片段，解析后只有一行，不能作为正式代码消费。",
            "extractedText 有16行代码，但缺第6行逗号后的空格和第13行 print 后的空格。",
            "当前跨字段启发式未拒绝这种一行混杂解释的代码；不能把业务校验通过当内容完整。",
        ],
        "fieldResponsibility": "failed",
    },
    "code-B": {
        "grade": "不可用",
        "notes": [
            "codeText 只有 import os.path，遗漏15行主体。",
            "uncertainties 讨论异常处理、对象类型和路径规范，并非难辨字符定位。",
        ],
        "fieldResponsibility": "failed",
    },
    "code-C": {
        "grade": "实质修改",
        "notes": [
            "16行主体全部进入 codeText；TEXT、双引号和双反斜杠保持。",
            "第8行 # 定义Schema 被增加4空格缩进；第6行逗号后的可见空格丢失。",
            "uncertainties 混有代码语义说明；代码确定字符/缩进差异按工单不能降为轻微修改。",
        ],
        "fieldResponsibility": "correct_but_transcription_inexact",
    },
    "code-D": {
        "grade": "实质修改",
        "notes": [
            "单个代码围栏内保留16行主体、TEXT、双引号和双反斜杠。",
            "第8行注释增加2空格；第6行逗号后空格、第13行 print 后空格丢失。",
            "只移除一个外层围栏，没有规范化主体空格；严格逐字转录仍不合格。",
        ],
        "fieldResponsibility": "correct_but_transcription_inexact",
    },
    "diagram-A": {
        "grade": "实质修改",
        "notes": [
            "imageType=chart、action=understand_chart，主体仍错放 chart 字段。",
            "三面板、136/200和第三面板向右箭头能识别，但把正文上下文/Agent用途混入描述和 keyFacts。",
            "keyFacts 写入136或137的正文歧义；图中136/200本身可读，不应混淆。",
        ],
        "fieldResponsibility": "wrong_specialist_fields",
    },
    "diagram-B": {
        "grade": "实质修改",
        "notes": [
            "三步骤和第二步136/200保留，但更接近正文摘要，遗漏面板内大部分图形和真实向右箭头。",
            "加入效率/风险用途解释，uncertainties 讨论未来实现问题，超出图片观察边界。",
            "这是已知结构类型控制组，不证明自动路由改善。",
        ],
        "fieldResponsibility": "minimal_fields_valid_content_incomplete",
    },
    "diagram-C": {
        "grade": "轻微修改",
        "notes": [
            "三面板标题、命令、第二面板目标200及进度136/200、第三面板向右箭头均保留。",
            "明确区分阅读顺序和没有跨面板箭头，没有虚构连接。",
            "开头断点续跑用途来自上下文，应明确标成上下文或省略；不涉及关键关系、数字错误。",
        ],
        "fieldResponsibility": "correct",
    },
    "diagram-D": {
        "grade": "轻微修改",
        "notes": [
            "三面板、第二步136/200、第三步向右箭头及无跨面板连线均准确，分区可读。",
            "断点续跑用途来自上下文；把完整简笔小人说成人头头像是次要描述误差。",
            "未见关键方向、数值或连接关系错误。",
        ],
        "fieldResponsibility": "correct",
    },
}
ANONYMOUS_ORDER = {"code": ["C", "A", "D", "B"], "diagram": ["D", "B", "C", "A"]}
REVIEWED_RUN = "qwen-format-ablation-v1-20261012"


def normalize_code(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").removesuffix("\n")


def code_comparison(actual: str, reference: str) -> dict:
    actual, reference = normalize_code(actual), normalize_code(reference)
    changes = [
        {
            "operation": operation,
            "referenceStart": i,
            "referenceEnd": j,
            "actualStart": a,
            "actualEnd": b,
            "expected": reference[i:j],
            "actual": actual[a:b],
        }
        for operation, i, j, a, b in difflib.SequenceMatcher(
            None, reference, actual, autojunk=False
        ).get_opcodes()
        if operation != "equal"
    ]
    return {
        "exactMatch": actual == reference,
        "referenceLineCount": len(reference.splitlines()),
        "actualLineCount": len(actual.splitlines()),
        "characterDifferences": changes,
        "lineDiff": list(
            difflib.unified_diff(
                reference.splitlines(),
                actual.splitlines(),
                fromfile="reference",
                tofile="model",
                lineterm="",
            )
        ),
    }


def fence(text: str, language: str = "") -> str:
    # Evidence is not executable source; formatters must not rewrite its whitespace.
    if language == "python":
        language = "text"
    runs = __import__("re").findall(r"`+", text)
    delimiter = "`" * max(3, 1 + max((len(r) for r in runs), default=0))
    return f"{delimiter}{language}\n{text}\n{delimiter}\n"


def build_review(run_name: str) -> dict:
    if run_name != REVIEWED_RUN:
        raise ValueError(
            "the path-aware Codex grades apply only to the reviewed frozen Round 11 run"
        )
    folder = runner.run_dir(run_name).resolve(strict=True)
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    manifest = json.loads((folder / "run-manifest.json").read_text(encoding="utf-8"))
    if manifest["responsesReceived"] != 8:
        raise ValueError("review requires all eight first-test responses")
    target = folder / "evaluation"
    if target.exists():
        raise FileExistsError(
            "evaluation already exists; preserve it and choose a new review directory"
        )
    target.mkdir()
    rows, benchmark_summary = read_benchmark(
        runner.PROJECT / "output/image-annotation/annotation-002/图片辅助标注.xlsx",
        runner.PROJECT / "test_pictures",
    )
    benchmark = {
        r["imageSha256"]: r["benchmark"]
        for r in rows
        if r["imageSha256"] in runner.SAMPLES.values()
    }
    runner.write_json(
        target / "benchmark-snapshot.json", {"summary": benchmark_summary, "rows": benchmark}
    )
    runner.write_text(target / "image-confirmed-code-reference.txt", IMAGE_CODE_REFERENCE)
    reviewer_rows = []
    by_key = {}
    readable = [
        "# 八格逐图对照",
        "",
        "评审者：Codex（已知路径）；用户人工验收：待完成。",
        "",
        "所有原文和解析保留在 cells 下；此文件仅便于阅读，未改写机器输出。",
        "",
    ]
    for cell in plan["cells"]:
        key = f"{cell['sample']}-{cell['arm']}"
        directory = folder / "cells" / cell["cellId"]
        response = json.loads((directory / "response.json").read_text(encoding="utf-8"))
        analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
        row = {
            "cellId": cell["cellId"],
            "sample": cell["sample"],
            "arm": cell["arm"],
            "imageSha256": cell["imageSha256"],
            "format": analysis["status"],
            "jsonSyntax": analysis["jsonSyntax"],
            "schema": analysis["schemaValidation"],
            "business": analysis["businessValidation"],
            "model": response["model"],
            "finishReason": response["finishReason"],
            "usage": response["usage"],
            "elapsedMs": response["elapsedMs"],
            "codexReview": ASSESSMENTS[key],
            "userReview": {"status": "pending", "grade": None},
            "links": {
                "response": (directory / "content.txt").relative_to(folder).as_posix(),
                "analysis": (directory / "analysis.json").relative_to(folder).as_posix(),
            },
        }
        display = response["content"]
        if cell["sample"] == "code":
            formal = analysis["formalCodeText"] or ""
            row["imageReferenceComparison"] = code_comparison(formal, IMAGE_CODE_REFERENCE)
            row["workbookReferenceComparison"] = code_comparison(
                formal, benchmark[cell["imageSha256"]]["referenceAnswer"] or ""
            )
            display = formal
            if cell["arm"] == "A":
                row["extractedTextComparison"] = code_comparison(
                    (analysis["parsed"] or {}).get("extractedText") or "", IMAGE_CODE_REFERENCE
                )
        reviewer_rows.append(row)
        by_key[key] = {
            "row": row,
            "display": display,
            "response": response,
            "analysis": analysis,
            "cell": cell,
        }
        readable.extend(
            [
                f"## {cell['sample']} / {cell['arm']}",
                "",
                "Codex 等级：" + ASSESSMENTS[key]["grade"],
                "",
            ]
        )
        readable.extend("- " + note for note in ASSESSMENTS[key]["notes"])
        readable.extend(
            [
                "",
                "### 正式内容",
                "",
                fence(display, "python" if cell["sample"] == "code" else "text"),
            ]
        )
        if cell["arm"] == "A" and cell["sample"] == "code":
            readable.extend(
                [
                    "### extractedText（不自动提升为代码字段）",
                    "",
                    fence(analysis["parsed"]["extractedText"], "python"),
                ]
            )
        if cell["sample"] == "code":
            readable.extend(
                [
                    "### 与原图逐行对照",
                    "",
                    fence("\n".join(row["imageReferenceComparison"]["lineDiff"]), "diff"),
                ]
            )
    report = {
        "schemaVersion": "1.0.0",
        "runName": run_name,
        "createdAt": runner.now(),
        "reviewer": "Codex visual review; path-aware",
        "userAcceptance": "pending",
        "baselineStatus": "v1 human benchmark is provisional; no workbook edits",
        "codeReferenceNote": "Workbook includes two extra blank lines absent from the numbered screenshot; both verbatim workbook and independently image-checked 16-line reference comparisons are retained. Only CR/LF and terminal newline normalization; no whitespace stripping.",
        "rows": reviewer_rows,
    }
    runner.write_json(target / "quality-review.json", report)
    runner.write_text(target / "comparison.md", "\n".join(readable))
    anonymous = [
        "# 匿名结果待用户评审",
        "",
        "请同时查看原图；仅按内容评审，不根据文字长度评分。",
        "",
        "可选等级：无需修改、轻微修改、实质修改、不可用、基准待复核。",
        "",
    ]
    anonymous_key = {}
    for sample, arms in ANONYMOUS_ORDER.items():
        cells = [c for c in plan["cells"] if c["sample"] == sample]
        image_path = runner.PROJECT / "test_pictures" / cells[0]["imageRelativePath"]
        image_link = Path(os.path.relpath(image_path, target)).as_posix()
        anonymous.extend([f"## {sample} 原图", "", f"![原图]({image_link})", ""])
        for number, arm in enumerate(arms, start=1):
            token = f"{sample}-{number}"
            entry = by_key[f"{sample}-{arm}"]
            anonymous_key[token] = entry["row"]["cellId"]
            anonymous.extend(
                [
                    f"### 结果 {number}",
                    "",
                    fence(entry["display"], "text"),
                    "模型完整原文（未合并或修补）：",
                    fence(entry["response"]["content"], "text"),
                    "用户等级：________；说明：________",
                    "",
                ]
            )
    runner.write_text(target / "anonymous-review.md", "\n".join(anonymous))
    runner.write_json(target / "anonymous-review-key.json", anonymous_key)
    runner.write_json(
        target / "index.json",
        {
            "run": "../run-manifest.json",
            "plan": "../plan.json",
            "readable": "comparison.md",
            "anonymous": "anonymous-review.md",
            "quality": "quality-review.json",
            "historicalEvidence": [
                "output/image-enrichment/runs/qwen37-flash-reliability6-v1/run-manifest.json",
                "output/image-enrichment/runs/qwen37-flash-quality6-v2/run-manifest.json",
                "docs/image-enrichment-round7-validation.md",
            ],
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Round 11 eight-cell visual assessment")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--anonymous-only", action="store_true")
    parser.add_argument("--verbatim-only", action="store_true")
    args = parser.parse_args()
    if args.verbatim_only:
        export_verbatim_views(args.run_name)
        return
    if args.anonymous_only:
        export_anonymous_full(args.run_name)
        return
    report = build_review(args.run_name)
    print(json.dumps({"rows": len(report["rows"]), "userAcceptance": report["userAcceptance"]}))


def export_anonymous_full(run_name: str) -> None:
    folder = runner.run_dir(run_name).resolve(strict=True)
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    target = folder / "evaluation" / "anonymous-review-full.md"
    if target.exists():
        raise FileExistsError("anonymous full review already exists; it may contain human entries")
    lines = [
        "# 匿名完整结果待用户评审",
        "",
        "用户等级与备注保持空白；请对照原图判断。",
        "",
        "模型完整原文与正式代码字段分别显示，不合并、不修补。",
        "",
    ]
    for sample, arms in ANONYMOUS_ORDER.items():
        cells = {c["arm"]: c for c in plan["cells"] if c["sample"] == sample}
        image = runner.PROJECT / "test_pictures" / cells[arms[0]]["imageRelativePath"]
        link = Path(os.path.relpath(image, target.parent)).as_posix()
        lines.extend([f"## {sample}", "", f"![原图]({link})", ""])
        for number, arm in enumerate(arms, 1):
            directory = folder / "cells" / cells[arm]["cellId"]
            response = json.loads((directory / "response.json").read_text(encoding="utf-8"))
            analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
            lines.extend(
                [f"### 结果 {number}", "", "模型完整原文：", "", fence(response["content"], "text")]
            )
            if sample == "code":
                lines.extend(
                    [
                        "单独代码消费字段（仅解析 JSON 或剥离一个围栏）：",
                        "",
                        fence(analysis["formalCodeText"] or "", "python"),
                    ]
                )
            lines.extend(["用户等级：________；备注：________", ""])
    runner.write_text(target, "\n".join(lines))
    print(target)


def export_verbatim_views(run_name: str) -> None:
    folder = runner.run_dir(run_name).resolve(strict=True)
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    quality = json.loads((folder / "evaluation/quality-review.json").read_text(encoding="utf-8"))
    grades = {row["cellId"]: row for row in quality["rows"]}
    target = folder / "evaluation"
    comparison_path = target / "comparison-verbatim.md"
    anonymous_path = target / "anonymous-review-verbatim.md"
    if comparison_path.exists() or anonymous_path.exists():
        raise FileExistsError("verbatim review already exists; preserve any human entries")
    comparison = [
        "# 八格逐字阅读稿",
        "",
        "只使用text围栏；内容逐字来自持久化响应和解析字段。",
        "",
        "Codex已知路径评审；用户人工验收待完成。",
        "",
    ]
    entries = {}
    for cell in plan["cells"]:
        directory = folder / "cells" / cell["cellId"]
        response = json.loads((directory / "response.json").read_text(encoding="utf-8"))
        analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
        entry = {"cell": cell, "response": response, "analysis": analysis}
        entries[(cell["sample"], cell["arm"])] = entry
        row = grades[cell["cellId"]]
        comparison.extend(
            [
                f"## {cell['sample']} / {cell['arm']}",
                "",
                "Codex等级：" + row["codexReview"]["grade"],
                "",
            ]
        )
        comparison.extend("- " + n for n in row["codexReview"]["notes"])
        comparison.extend(["", "### 模型完整原文", "", fence(response["content"], "text")])
        if cell["sample"] == "code":
            comparison.extend(
                [
                    "### 单独消费字段",
                    "",
                    fence(analysis["formalCodeText"] or "", "text"),
                    "### 字符及行差异",
                    "",
                    fence("\n".join(row["imageReferenceComparison"]["lineDiff"]), "diff"),
                ]
            )
            if cell["arm"] == "A":
                comparison.extend(
                    [
                        "### 单独extractedText字段",
                        "",
                        fence(analysis["parsed"]["extractedText"], "text"),
                    ]
                )
    anonymous = [
        "# 匿名逐字阅读稿",
        "",
        "请同时查看原图；用户等级和备注尚未填写。",
        "",
        "模型完整原文与消费字段分开保存，未做内容修补或代码格式化。",
        "",
    ]
    for sample, arms in ANONYMOUS_ORDER.items():
        image = (
            runner.PROJECT
            / "test_pictures"
            / entries[(sample, arms[0])]["cell"]["imageRelativePath"]
        )
        link = Path(os.path.relpath(image, target)).as_posix()
        anonymous.extend([f"## {sample}", "", f"![原图]({link})", ""])
        for number, arm in enumerate(arms, 1):
            entry = entries[(sample, arm)]
            anonymous.extend(
                [
                    f"### 结果 {number}",
                    "",
                    "模型完整原文：",
                    "",
                    fence(entry["response"]["content"], "text"),
                ]
            )
            if sample == "code":
                anonymous.extend(
                    [
                        "单独代码消费字段：",
                        "",
                        fence(entry["analysis"]["formalCodeText"] or "", "text"),
                    ]
                )
            anonymous.extend(["用户等级：________；备注：________", ""])
    rendered_comparison = "\n".join(comparison)
    rendered_anonymous = "\n".join(anonymous)
    for entry in entries.values():
        if entry["response"]["content"] not in rendered_comparison:
            raise ValueError("raw response missing from verbatim comparison")
        code = entry["analysis"].get("formalCodeText")
        if code and (code not in rendered_comparison or code not in rendered_anonymous):
            raise ValueError("code whitespace changed during verbatim rendering")
    runner.write_text(comparison_path, rendered_comparison)
    runner.write_text(anonymous_path, rendered_anonymous)
    runner.write_json(
        target / "verbatim-validation.json",
        {
            "rawResponsesPresent": 8,
            "codeFieldsByteFaithful": 4,
            "passed": True,
            "comparison": comparison_path.name,
            "anonymous": anonymous_path.name,
            "olderViews": "Earlier Python-fenced derived Markdown was exposed to Ruff formatting; prefer these verified text-fenced views. Raw response/analysis/diff records unchanged.",
        },
    )
    print(comparison_path)


if __name__ == "__main__":
    main()
