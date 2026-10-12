"""Offline, rebuildable HTML views over the enrichment observer index."""

from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageOps

from .observer import cleanup_preview


def _text(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _link(href: str, label: object, *, title: str | None = None) -> str:
    extra = f' title="{_text(title)}"' if title else ""
    return f'<a href="{_text(href)}"{extra}>{_text(label)}</a>'


def _copy(value: object, label: str = "复制") -> str:
    if value is None:
        return ""
    return f'<button type="button" class="copy" data-copy="{_text(value)}">{_text(label)}</button>'


def _relative_href(page: Path, target: Path) -> str:
    return quote(Path(os.path.relpath(target, page.parent)).as_posix(), safe="/.-_")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _page(title: str, body: str, *, prefix: str = "", active: str = "") -> str:
    nav = " ".join(
        f'<a class="{"active" if key == active else ""}" href="{prefix}{href}">{_text(label)}</a>'
        for key, href, label in (
            ("home", "index.html", "首页"),
            ("images", "images.html", "图片"),
            ("runs", "runs.html", "运行"),
            ("cache", "caches.html", "缓存"),
            ("compare", "compare.html", "运行对照"),
            ("storage", "storage.html", "存储审计"),
        )
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src 'self' file:; style-src 'self'; script-src 'self'; "
        "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">"
        f"<title>{_text(title)} · 图片观察台</title>"
        f'<link rel="stylesheet" href="{prefix}assets/site.css">'
        f'<script defer src="{prefix}assets/site.js"></script>'
        "</head><body>"
        f'<header><div class="brand">图片 Enrichment 观察台</div><nav>{nav}</nav></header>'
        f"<main><h1>{_text(title)}</h1>{body}</main>"
        "<footer>本地只读派生视图。清理预览不执行删除；机器结果不是人工真值。</footer>"
        "</body></html>"
    )


def _table(headers: list[str], rows: list[list[str]], *, class_name: str = "") -> str:
    head = "".join(f"<th>{_text(value)}</th>" for value in headers)
    content = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f'<div class="table-wrap"><table class="{_text(class_name)}"><thead><tr>{head}</tr></thead><tbody>{content}</tbody></table></div>'


def _definition(rows: list[tuple[str, object]]) -> str:
    return (
        "<dl>"
        + "".join(
            f"<dt>{_text(label)}</dt><dd>{_text(value)} {_copy(value)}</dd>"
            for label, value in rows
        )
        + "</dl>"
    )


def _badge(text: object, kind: str = "") -> str:
    return f'<span class="badge {_text(kind)}">{_text(text or "—")}</span>'


def _image_thumb(image: dict, *, prefix: str = "", alt: str = "缩略图") -> str:
    if image.get("thumbnail"):
        return f'<img class="thumb" src="{prefix}{_text(image["thumbnail"])}" alt="{_text(alt)}">'
    return '<div class="thumb placeholder">预览不可用</div>'


def _result_html(result: dict | None) -> str:
    if not result:
        return '<p class="muted">没有通过校验的结构化结果。</p>'
    lines = [
        '<div class="result-meta">'
        + _badge(result.get("imageType"))
        + _badge(result.get("action"))
        + _badge(result.get("knowledgeValue"))
        + _badge(result.get("retrievalDecision"))
        + "</div>"
    ]
    for label, key in (("描述", "description"), ("可见文字", "extractedText")):
        if result.get(key):
            lines.append(f'<h4>{label}</h4><pre class="wrap">{_text(result[key])}</pre>')
    if result.get("code"):
        code = result["code"]
        lines.append(
            f"<h4>正式代码字段 · {_text(code.get('language') or '语言未定')}</h4>"
            f'<pre class="code">{_text(code.get("text"))}</pre>'
        )
    if result.get("table"):
        table = result["table"]
        headers = table.get("headers") or []
        rows = table.get("rows") or []
        if headers and rows:
            lines.append(
                "<h4>表格</h4>"
                + _table(
                    [str(value) for value in headers],
                    [[_text(value) for value in row] for row in rows],
                )
            )
        elif table.get("textRepresentation"):
            lines.append(f'<pre class="wrap">{_text(table["textRepresentation"])}</pre>')
    if result.get("chart"):
        chart = result["chart"]
        lines.append(
            "<h4>图表字段</h4>"
            + _table(
                ["字段", "内容"],
                [
                    [
                        _text(key),
                        _text(", ".join(map(str, value)) if isinstance(value, list) else value),
                    ]
                    for key, value in chart.items()
                    if value
                ],
            )
        )
    for label, key in (
        ("关键事实", "keyFacts"),
        ("不确定项", "uncertainties"),
        ("警告", "warnings"),
    ):
        if result.get(key):
            lines.append(
                f"<h4>{label}</h4><ul>"
                + "".join(f"<li>{_text(value)}</li>" for value in result[key])
                + "</ul>"
            )
    return "".join(lines)


def _attempt_evidence_html(item: dict) -> str:
    blocks = []
    for attempt in item["attempts"]:
        if attempt.get("status") == "success":
            continue
        evidence = attempt.get("evidence")
        if not evidence:
            blocks.append(
                '<div class="notice warning">'
                f"第 {_text(attempt.get('attemptNumber'))} 次调用没有可读取的模型原文；"
                "可能是当时未留存、响应未到达或证据文件损坏。"
                "</div>"
            )
            continue
        errors = evidence.get("validationErrors") or []
        blocks.append(
            '<section class="raw-evidence">'
            f"<h4>第 {_text(attempt.get('attemptNumber'))} 次模型原始回答（脱敏纯文本）</h4>"
            + (
                "<h5>本地校验为什么拒绝</h5><ul>"
                + "".join(
                    "<li>"
                    + _text(" / ".join(str(part) for part in error.get("location", [])) or "根节点")
                    + "："
                    + _text(error.get("message") or error.get("type") or "未知校验错误")
                    + "</li>"
                    for error in errors
                )
                + "</ul>"
                if errors
                else '<p class="muted">没有留存结构化的本地校验详情。</p>'
            )
            + (
                f'<pre class="code raw-output">{_text(evidence.get("content"))}</pre>'
                if evidence.get("content") is not None
                else '<p class="muted">服务响应没有可显示的文本 content。</p>'
            )
            + f'<p class="muted">证据文件：{_text(attempt.get("rawResponseFile"))}</p>'
            + "</section>"
        )
    if not blocks and item.get("errorCode") == "invalid_structured_output":
        blocks.append(
            '<div class="notice warning">这条历史失败创建于逐次原文留存功能之前，'
            "当前目录只有错误摘要，模型原始回答无法恢复。</div>"
        )
    return "".join(blocks)


def _try_thumbnail(image: dict, site_root: Path) -> tuple[str | None, str | None]:
    if image["imageState"] != "available" or not image["originalPath"]:
        return None, "original_image_unavailable"
    target = site_root / "thumbnails" / (image["imageSha256"] + ".png")
    try:
        with Image.open(image["originalPath"]) as source:
            source = ImageOps.exif_transpose(source)
            source.thumbnail((180, 140), Image.Resampling.LANCZOS)
            preview = Image.new("RGBA", source.size, "white")
            preview.alpha_composite(source.convert("RGBA"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=target.parent, suffix=".png", delete=False
            ) as temp:
                temporary = Path(temp.name)
            try:
                preview.convert("RGB").save(temporary, "PNG", optimize=True)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return f"thumbnails/{image['imageSha256']}.png", None
    except (OSError, ValueError):
        return None, "thumbnail_generation_failed"


def _home(index: dict) -> str:
    summary = index["summary"]
    storage = index["storage"]
    cards = "".join(
        f'<div class="stat"><strong>{_text(value)}</strong><span>{_text(label)}</span></div>'
        for label, value in (
            ("图片", summary["images"]),
            ("Run", summary["runs"]),
            ("Cache", summary["caches"]),
            ("Evaluation", summary["evaluations"]),
            ("local-only", summary["localOnly"]),
            ("当前校验不通过缓存", summary["currentInvalidCaches"]),
        )
    )
    latest_run = index["runs"][-1] if index["runs"] else None
    run_dates = {run["runId"]: run["createdAt"] for run in index["runs"]}
    latest_eval = max(
        index["evaluations"],
        key=lambda row: (run_dates.get(row["runId"], ""), row["evaluationId"]),
        default=None,
    )
    defaults = index["defaults"]
    body = (
        '<p class="lead">从图片、run 或缓存进入同一份本地记录。页面不会发起模型调用。</p>'
        '<div class="notice warning">第七轮六图质量门槛未通过。v1 代码结果也可能字段不完整；'
        "请查看“当前校验”与人工记录，不要把历史 success 当作可直接入库。</div>"
        f'<div class="stats">{cards}</div>'
        + _definition(
            [
                ("当前默认模型", defaults["model"]),
                ("当前默认 Prompt", defaults["promptVersion"]),
                ("结果 Schema", defaults["resultSchemaVersion"]),
                ("当前校验", defaults["validationPolicy"]),
                ("来源目录占用", f"{storage['totalFiles']} 文件；{storage['totalBytes']:,} 字节"),
                ("生成时间", index["generatedAt"]),
                ("最新 Run", latest_run["runId"] if latest_run else "—"),
                ("最新 Evaluation", latest_eval["evaluationId"] if latest_eval else "—"),
            ]
        )
        + '<p class="muted">输入来源仅限测试图片、图片清单、人工工作簿、Enrichment cache/runs/evaluations、策略和项目文档；'
        "不扫描整盘。原始图片、人工答案与运行产物没有被改写。</p>"
    )
    return _page("总览", body, active="home")


def _image_listing(index: dict) -> str:
    types = sorted({row["humanType"] for row in index["images"] if row["humanType"]})
    values = sorted({row["humanValue"] for row in index["images"] if row["humanValue"]})
    grades = sorted(
        {row["latestManualGrade"] for row in index["images"] if row["latestManualGrade"]}
    )
    prompts = sorted({run["promptVersion"] for run in index["runs"]})

    def select(name: str, label: str, values: list[str]) -> str:
        return (
            f'<label>{label}<select data-filter="{name}"><option value="">全部</option>'
            + "".join(f'<option value="{_text(value)}">{_text(value)}</option>' for value in values)
            + "</select></label>"
        )

    controls = (
        '<div class="filters"><label>搜索<input data-search placeholder="名称、文件名、哈希、Asset 或 Document"></label>'
        + select("type", "图片类型", types)
        + select("value", "知识价值", values)
        + select("grade", "人工等级", grades)
        + select("prompt", "Prompt", prompts)
        + select("local", "外发策略", ["local-only", "允许候选"])
        + "</div>"
    )
    rows = []
    for image in index["images"]:
        digest = image["imageSha256"]
        all_ids = " ".join(
            f"{relation['assetId']} {relation['documentId']}" for relation in image["relations"]
        )
        search = " ".join(
            str(part) for part in (image["displayName"], image["fileName"], digest, all_ids)
        ).casefold()
        image_prompts = " ".join(
            run["promptVersion"]
            for run in index["runs"]
            if any(item["imageSha256"] == digest for item in run["items"])
        )
        local = "local-only" if image["localOnly"] else "允许候选"
        rows.append(
            f'<article class="image-card filter-item" data-search-value="{_text(search)}" '
            f'data-type="{_text(image["humanType"] or "")}" '
            f'data-value="{_text(image["humanValue"] or "")}" '
            f'data-grade="{_text(image["latestManualGrade"] or "")}" '
            f'data-prompt="{_text(image_prompts)}" data-local="{_text(local)}">'
            + f'<a href="images/{digest}.html" title="{_text(image["displayName"])}">'
            + _image_thumb(image)
            + "</a>"
            + f"<div><h2>{_link(f'images/{digest}.html', image['displayName'])}</h2>"
            + f'<p class="muted">{_text(image["shortHash"])} · {_text(image["format"])} · '
            + f"{_text(image['width'])} × {_text(image['height'])}</p>"
            + _badge(image["humanType"])
            + _badge(image["humanValue"])
            + _badge(image["latestManualGrade"])
            + (_badge("local-only", "danger") if image["localOnly"] else "")
            + f'<p class="muted">最新真实运行：{_text(image["latestActualRun"] or "无")}</p>'
            + f'<p class="muted">Run {len(image["runRefs"])} · Cache {len(image["cacheRefs"])} · '
            + f"Asset {len(image['relations'])}</p></div></article>"
        )
    return _page(
        "图片总览",
        controls + f'<p class="muted">{len(rows)} 张图片；筛选不会修改数据。</p>' + "".join(rows),
        active="images",
    )


def _image_detail(index: dict, image: dict, page: Path) -> str:
    digest = image["imageSha256"]
    preview = _image_thumb(image, prefix="../", alt=image["displayName"])
    original = (
        _link(_relative_href(page, Path(image["originalPath"])), "打开原图")
        if image["originalPath"] and image["imageState"] == "available"
        else "原图不可用"
    )
    body = (
        '<p><a href="../images.html">← 图片总览</a></p>'
        f'<div class="detail-hero">{preview}<div>{_badge(image["imageState"])} '
        f"{_badge('local-only', 'danger') if image['localOnly'] else ''}<p>{original}</p></div></div>"
        + _definition(
            [
                ("原文件名", image["fileName"]),
                ("完整 SHA-256", digest),
                ("原图路径", image["originalPath"]),
                (
                    "格式与大小",
                    f"{image['format']} · {image['width']} × {image['height']} · {image['sizeBytes']} 字节",
                ),
                ("人工别名", image["alias"] or "未设置"),
                (
                    "人工类型 / 价值 / 动作",
                    f"{image['humanType']} / {image['humanValue']} / {image['humanAction']}",
                ),
                ("工作簿审核", image["reviewStatus"]),
                (
                    "参考事实",
                    "已填写（不复制到观察索引）" if image["referenceAnswerPresent"] else "未填写",
                ),
                ("策略", image["policyReason"] if image["localOnly"] else "未标 local-only"),
            ]
        )
    )
    relation_rows = [
        [
            _text(row["title"]),
            _text(row["documentId"]),
            _text(row["revisionId"]),
            _text(row["assetId"]),
            _text(row["blockOrder"]),
            _text(row["sourceLocator"]),
        ]
        for row in image["relations"]
    ]
    body += "<h2>正式文档与 Asset</h2>" + _table(
        ["标题", "Document", "Revision", "Asset", "正文顺序", "来源定位"], relation_rows
    )
    run_by_id = {run["runId"]: run for run in index["runs"]}
    for ref in sorted(image["runRefs"], key=lambda row: run_by_id[row["runId"]]["createdAt"]):
        run = run_by_id[ref["runId"]]
        item = next(item for item in run["items"] if item["imageSha256"] == digest)
        attempts = item["attempts"]
        attempt_table = _table(
            ["第几次", "状态", "Request ID", "模型", "Tokens", "耗时 ms", "原文证据"],
            [
                [
                    _text(attempt.get("attemptNumber")),
                    _text(attempt.get("status")),
                    _text(attempt.get("requestId")),
                    _text(attempt.get("actualModel")),
                    _text((attempt.get("usage") or {}).get("totalTokens")),
                    _text(attempt.get("elapsedMs")),
                    _text(attempt.get("rawResponseFile") or "无"),
                ]
                for attempt in attempts
            ],
        )
        body += (
            f'<section class="timeline"><h3>{_link("../runs/" + _text(run["runId"]) + ".html", run["runId"])}</h3>'
            + _badge(item["status"])
            + f" · {_text(run['createdAt'])} · {_text(run['model'])} · {_text(run['promptVersion'])}"
            + f" · {_text((item['usage'] or {}).get('totalTokens'))} tokens · {_text(item['elapsedMs'])} ms"
            + (
                f'<p class="error">失败原因：{_text(item["errorCode"])}</p>'
                if item["errorCode"]
                else ""
            )
            + ("<details open>" if item["errorCode"] else "<details>")
            + "<summary>结构化结果与调用证据</summary>"
            + _result_html(item["result"])
            + attempt_table
            + _attempt_evidence_html(item)
            + _definition(
                [
                    ("缓存键", item["cacheKey"]),
                    ("Record 路径", item["recordPath"]),
                    ("Request 路径", item["requestPath"]),
                    ("运行配置", item.get("processingProfile") or "legacy"),
                    ("机器校验", item.get("validationStatus") or "历史记录未单独标记"),
                    ("当前质量审核", item.get("reviewStatus") or "pending"),
                ]
            )
            + "</details></section>"
        )
    if not image["runRefs"]:
        body += '<p class="muted">没有关联 run。</p>'
    body += "<h2>缓存与人工评测</h2>" + _table(
        ["Cache key", "当前校验", "路径"],
        [
            [
                _link(f"../caches/{cache['cacheKey']}.html", cache["cacheKey"][:16]),
                _badge(cache["currentValidation"]),
                _text(cache["path"]),
            ]
            for cache in index["caches"]
            if cache["cacheKey"] in image["cacheRefs"]
        ],
    )
    body += _table(
        ["Evaluation", "人工等级", "备注"],
        [
            [
                _link(f"../evaluations/{ref['evaluationId']}.html", ref["evaluationId"]),
                _badge(ref.get("grade")),
                _text(ref.get("note")),
            ]
            for ref in image["evaluationRefs"]
        ],
    )
    return _page(image["displayName"], body, prefix="../", active="images")


def _run_listing(index: dict) -> str:
    rows = []
    for run in reversed(index["runs"]):
        search = f"{run['runId']} {run['model']} {run['promptVersion']}".casefold()
        rows.append(
            f'<tr class="filter-item" data-search-value="{_text(search)}" '
            f'data-type="{_text(run["type"])}" data-prompt="{_text(run["promptVersion"])}" '
            f'data-status="{_text(run["status"])}">'
            f"<td>{_link('runs/' + run['runId'] + '.html', run['runId'])}</td>"
            f"<td>{_badge(run['type'])}</td><td>{_text(run['createdAt'])}</td>"
            f"<td>{_text(run['model'])}</td><td>{_text(run['promptVersion'])}</td>"
            f"<td>{len(run['items'])}</td><td>{_text(run['observedApiCalls'])}</td>"
            f"<td>{_text(run['summary'].get('success', 0))} / {_text(run['summary'].get('cached', 0))} / "
            f"{_text(sum(value for key, value in run['summary'].items() if key not in ('success', 'cached', 'dry_run')))}</td>"
            f"<td>{_text((run['usage'] or {}).get('totalTokens'))}</td>"
            f"<td>{len(run['evaluationRefs'])}</td></tr>"
        )
    controls = (
        '<div class="filters"><label>搜索<input data-search placeholder="Run、模型或 Prompt"></label>'
        '<label>类型<select data-filter="type"><option value="">全部</option>'
        + "".join(
            f"<option>{_text(value)}</option>"
            for value in sorted({r["type"] for r in index["runs"]})
        )
        + '</select></label><label>Prompt<select data-filter="prompt"><option value="">全部</option>'
        + "".join(
            f"<option>{_text(value)}</option>"
            for value in sorted({r["promptVersion"] for r in index["runs"]})
        )
        + '</select></label><label>状态<select data-filter="status"><option value="">全部</option>'
        + "".join(
            f"<option>{_text(value)}</option>"
            for value in sorted({r["status"] for r in index["runs"]})
        )
        + "</select></label></div>"
    )
    body = controls + _table(
        [
            "Run",
            "类型",
            "创建",
            "模型",
            "Prompt",
            "任务",
            "API 调用",
            "成功/缓存/其他",
            "Tokens",
            "评测",
        ],
        rows,
    )
    return _page("运行总览", body, active="runs")


def _run_detail(index: dict, run: dict) -> str:
    images = {image["imageSha256"]: image for image in index["images"]}
    body = '<p><a href="../runs.html">← 运行总览</a></p>' + _definition(
        [
            ("类型 / 状态", f"{run['type']} / {run['status']}"),
            ("模型", run["model"]),
            ("Prompt / Schema", f"{run['promptVersion']} / {run['resultSchemaVersion']}"),
            ("Prompt SHA-256", run["promptSha256"]),
            ("思考模式", run["enableThinking"]),
            ("创建 / 完成", f"{run['createdAt']} / {run['completedAt']}"),
            ("API 调用（记录可见）", run["observedApiCalls"]),
            (
                "汇总 usage",
                json.dumps(run["usage"], ensure_ascii=False, sort_keys=True)
                if run["usage"]
                else "未知",
            ),
            ("Manifest", run["manifestPath"]),
            ("项目文档引用", ", ".join(run["documentedBy"]) or "无"),
        ]
    )
    if run["evaluationRefs"]:
        body += (
            "<p>人工或基准评测："
            + "、".join(
                _link(f"../evaluations/{name}.html", name) for name in run["evaluationRefs"]
            )
            + "</p>"
        )
    for item in run["items"]:
        image = images.get(item["imageSha256"])
        image_link = (
            _link(f"../images/{item['imageSha256']}.html", image["displayName"])
            if image
            else _text(item["relativePath"])
        )
        thumb = (
            _image_thumb(image, prefix="../") if image else '<div class="placeholder">无图片</div>'
        )
        attempt_rows = [
            [
                _text(a.get("attemptNumber")),
                _text(a.get("status")),
                _text(a.get("requestId")),
                _text((a.get("usage") or {}).get("totalTokens")),
                _text(a.get("elapsedMs")),
                _text(a.get("rawResponseFile")),
                _text(a.get("errorCode")),
            ]
            for a in item["attempts"]
        ]
        body += (
            '<section class="run-item"><div class="run-item-head">'
            + thumb
            + f"<div><h2>{image_link}</h2>{_badge(item['status'])} "
            + _badge("审核：" + (item.get("reviewStatus") or "pending"))
            + f'<span class="muted">{_text(item["relativePath"])} · {_text(item["elapsedMs"])} ms · '
            + f"{_text((item['usage'] or {}).get('totalTokens'))} tokens</span>"
            + (f'<p class="error">{_text(item["errorCode"])}</p>' if item["errorCode"] else "")
            + "</div></div>"
            + ("<details open>" if item["errorCode"] else "<details>")
            + "<summary>结果、缓存与调用尝试</summary>"
            + _result_html(item["result"])
            + _table(
                ["序号", "状态", "Request ID", "Tokens", "耗时 ms", "原文文件", "错误"],
                attempt_rows,
            )
            + _table(
                ["调用", "恢复方式", "finish reason", "机器校验"],
                [
                    [
                        _text(a.get("attemptNumber")),
                        _text(a.get("recoveryMode")),
                        _text(a.get("finishReason")),
                        _text(a.get("validationStatus")),
                    ]
                    for a in item["attempts"]
                ],
            )
            + _attempt_evidence_html(item)
            + _definition(
                [
                    ("Cache key", item["cacheKey"]),
                    ("Record", item["recordPath"]),
                    ("Request", item["requestPath"]),
                    ("运行配置", item.get("processingProfile") or "legacy"),
                    ("机器校验", item.get("validationStatus") or "历史记录未单独标记"),
                    ("当前质量审核", item.get("reviewStatus") or "pending"),
                ]
            )
            + (
                f"<p>{_link('../caches/' + item['cacheKey'] + '.html', '查看缓存详情')}</p>"
                if any(c["cacheKey"] == item["cacheKey"] for c in index["caches"])
                else ""
            )
            + "</details></section>"
        )
    return _page(run["runId"], body, prefix="../", active="runs")


def _cache_listing(index: dict) -> str:
    rows = []
    images = {image["imageSha256"]: image for image in index["images"]}
    for cache in index["caches"]:
        image = images.get(cache["imageSha256"])
        search = f"{cache['cacheKey']} {cache['imageSha256']} {image['displayName'] if image else ''}".casefold()
        rows.append(
            f'<tr class="filter-item" data-search-value="{_text(search)}" '
            f'data-validation="{_text(cache["currentValidation"])}" '
            f'data-prompt="{_text(cache["promptVersion"])}">'
            f"<td>{_link('caches/' + cache['cacheKey'] + '.html', cache['cacheKey'][:16])}</td>"
            f"<td>{_link('images/' + cache['imageSha256'] + '.html', image['displayName']) if image else '未知'}</td>"
            f"<td>{_badge(cache['currentValidation'])}</td>"
            f"<td>{_text(cache['model'])}</td><td>{_text(cache['promptVersion'])}</td>"
            f"<td>{len(cache['runRefs'])}</td><td>{cache['sizeBytes']:,}</td>"
            f"<td>{_text('是' if cache['currentDefaultKey'] else '否')}</td></tr>"
        )
    controls = (
        '<div class="filters"><label>搜索<input data-search placeholder="图片、完整 SHA 或 Cache key"></label>'
        '<label>当前校验<select data-filter="validation"><option value="">全部</option>'
        + "".join(
            f"<option>{_text(value)}</option>"
            for value in sorted({c["currentValidation"] for c in index["caches"]})
        )
        + '</select></label><label>Prompt<select data-filter="prompt"><option value="">全部</option>'
        + "".join(
            f"<option>{_text(value)}</option>"
            for value in sorted({c["promptVersion"] for c in index["caches"] if c["promptVersion"]})
        )
        + "</select></label></div>"
    )
    return _page(
        "缓存总览",
        controls
        + _table(
            ["Cache key", "图片", "当前校验", "模型", "Prompt", "引用 Run", "字节", "默认键"], rows
        ),
        active="cache",
    )


def _cache_detail(index: dict, cache: dict) -> str:
    image = next((i for i in index["images"] if i["imageSha256"] == cache["imageSha256"]), None)
    body = '<p><a href="../caches.html">← 缓存总览</a></p>'
    if image:
        body += (
            _image_thumb(image, prefix="../")
            + " "
            + _link(f"../images/{image['imageSha256']}.html", image["displayName"])
        )
    body += _definition(
        [
            ("Cache key", cache["cacheKey"]),
            ("图片 SHA-256", cache["imageSha256"]),
            ("历史状态", cache["storedStatus"]),
            ("当前校验", cache["currentValidation"]),
            ("质量审核", cache.get("reviewStatus") or "pending"),
            ("缓存资格", cache.get("cacheEligibility") or "历史记录未单独标记"),
            ("模型", cache["model"]),
            ("Prompt", f"{cache['promptVersion']} / {cache['promptSha256']}"),
            ("Schema", cache["resultSchemaVersion"]),
            ("上下文指纹", cache["contextFingerprintSha256"]),
            ("处理时间", cache["processedAt"]),
            ("当前默认配置键", cache["currentDefaultKey"]),
            ("孤立", cache["orphan"]),
            ("路径", cache["path"]),
            ("Record", cache["recordPath"]),
            ("脱敏原始响应", cache["rawResponsePath"]),
            ("大小", f"{cache['sizeBytes']} 字节 / {cache['fileCount']} 文件"),
        ]
    )
    body += (
        "<h2>引用它的 Run</h2>"
        + "<ul>"
        + "".join(
            f"<li>{_link('../runs/' + run_id + '.html', run_id)}</li>"
            for run_id in cache["runRefs"]
        )
        + "</ul>"
    )
    if cache["currentValidation"] != "valid":
        body += '<p class="notice warning">历史记录仍可查看，但当前业务校验不允许把它作为成功缓存返回。</p>'
    return _page(cache["cacheKey"][:16], body, prefix="../", active="cache")


def _evaluation_detail(index: dict, evaluation: dict) -> str:
    image_by_sha = {image["imageSha256"]: image for image in index["images"]}
    rows = []
    for digest, grade in sorted(evaluation["grades"].items()):
        image = image_by_sha.get(digest)
        rows.append(
            [
                _link(f"../images/{digest}.html", image["displayName"]) if image else _text(digest),
                _badge(grade.get("grade")),
                _text(grade.get("note")),
            ]
        )
    body = _definition(
        [
            ("对应 Run", evaluation["runId"]),
            ("基准工作簿 SHA-256", evaluation["benchmarkSha256"]),
            ("已确认基准行", evaluation["benchmarkConfirmedRows"]),
            ("评审依据", evaluation["reviewBasis"]),
            ("目录", evaluation["path"]),
            ("评测记录", evaluation["reportPath"]),
            ("人工质量记录", evaluation["reviewPath"]),
        ]
    )
    if evaluation["runId"] and any(run["runId"] == evaluation["runId"] for run in index["runs"]):
        body += "<p>" + _link(f"../runs/{evaluation['runId']}.html", "返回对应 Run") + "</p>"
    body += "<h2>逐图人工等级</h2>" + _table(["图片", "等级", "备注"], rows)
    body += (
        '<p class="muted">人工参考答案没有复制到观察台索引；工作簿未确认行不能算作正式真值。</p>'
    )
    return _page(evaluation["evaluationId"], body, prefix="../")


def _storage(index: dict, preview: dict) -> str:
    groups = index["storage"]["categories"]
    input_images = index["inputStorage"]["testImages"]
    body = (
        '<p class="lead">这里只扫描 Enrichment 的 cache、runs、evaluations；观察台自身是可重建输出，不计入来源统计。</p>'
        + _table(
            ["类别", "文件", "目录", "字节"],
            [
                [
                    _text(name),
                    _text(value["files"]),
                    _text(value["directories"]),
                    f"{value['bytes']:,}",
                ]
                for name, value in groups.items()
            ],
        )
        + _definition(
            [
                (
                    "来源总计",
                    f"{index['storage']['totalFiles']} 文件 / {index['storage']['totalBytes']:,} 字节",
                ),
                (
                    "测试图片",
                    f"{input_images['files']} 文件 / {input_images['bytes']:,} 字节",
                ),
                ("人工工作簿", index["inputStorage"]["workbookBytes"]),
                ("重复内容组", len(index["storage"]["duplicateGroups"])),
                ("孤立缓存", index["summary"]["orphanCaches"]),
                ("当前校验不通过缓存", index["summary"]["currentInvalidCaches"]),
                ("引用或解析问题", len(index["issues"])),
            ]
        )
        + "<h2>最大文件</h2>"
        + _table(
            ["类别", "文件", "字节"],
            [
                [_text(item["category"]), _text(item["path"]), f"{item['bytes']:,}"]
                for item in index["storage"]["largestFiles"]
            ],
        )
        + "<h2>清理预览（没有删除按钮）</h2>"
        + _definition(
            [
                ("保留", preview["summary"]["keep"]),
                ("可清理候选", preview["summary"]["cleanupCandidate"]),
                ("待确认", preview["summary"]["review"]),
                ("候选释放空间", preview["summary"]["estimatedCandidateBytes"]),
            ]
        )
        + _table(
            ["建议", "类别 / ID", "大小", "理由", "被引用", "重建需要云端", "目标路径"],
            [
                [
                    _badge(item["action"]),
                    _text(item["kind"] + " / " + item["id"][:18]),
                    f"{item['bytes']:,}",
                    _text(item["reasonCode"]),
                    _text(", ".join(item["referencedBy"]) or "无"),
                    _text(
                        "未知"
                        if item["requiresCloud"] is None
                        else "是"
                        if item["requiresCloud"]
                        else "否"
                    ),
                    _text(item["path"]),
                ]
                for item in preview["items"]
            ],
        )
        + "<h2>解析与引用问题</h2>"
        + _table(
            ["级别", "代码", "位置"],
            [
                [_badge(issue["severity"]), _text(issue["code"]), _text(issue["path"])]
                for issue in index["issues"]
            ],
        )
        + '<p>完整理由、恢复方式和风险见 <a href="cleanup-preview.json">cleanup-preview.json</a>；'
        '<a href="storage-audit.json">storage-audit.json</a> 可用于重复比较。本轮不执行清理。</p>'
    )
    return _page("存储审计", body, active="storage")


def _compare(index: dict) -> tuple[str, str]:
    comparable = [run for run in index["runs"] if run["items"]]
    pairs = []
    actual_runs = [run for run in comparable if run["type"] == "api_run"]
    for first in actual_runs:
        for second in actual_runs:
            if first["runId"] >= second["runId"]:
                continue
            first_ids = {item["imageSha256"] for item in first["items"]}
            second_ids = {item["imageSha256"] for item in second["items"]}
            shared = len(first_ids & second_ids)
            if shared and first["model"] == second["model"]:
                pairs.append(
                    (
                        shared,
                        max(first["createdAt"], second["createdAt"]),
                        first["runId"],
                        second["runId"],
                    )
                )
    pair = max(pairs) if pairs else None
    options = "".join(
        f'<option value="{_text(run["runId"])}">{_text(run["runId"])} · {_text(run["promptVersion"])}'
        "</option>"
        for run in comparable
    )
    body = (
        '<p class="lead">选择同一模型的两个 run，按完整图片 SHA-256 对齐；不会生成新结果。</p>'
        '<div class="filters"><label>Run A<select id="compare-a">'
        + options
        + '</select></label><label>Run B<select id="compare-b">'
        + options
        + '</select></label></div><div id="compare-output"></div>'
        '<script defer src="assets/compare-data.js"></script>'
    )
    payload = {
        "defaultPair": [pair[2], pair[3]] if pair else None,
        "images": {
            image["imageSha256"]: {
                "displayName": image["displayName"],
                "thumbnail": image.get("thumbnail"),
            }
            for image in index["images"]
        },
        "runs": [
            {
                "runId": run["runId"],
                "model": run["model"],
                "promptVersion": run["promptVersion"],
                "items": [
                    {
                        "imageSha256": item["imageSha256"],
                        "status": item["status"],
                        "errorCode": item["errorCode"],
                        "cacheKey": item["cacheKey"],
                        "usage": item["usage"],
                        "elapsedMs": item["elapsedMs"],
                        "attempts": len(item["attempts"]),
                        "result": item["result"],
                    }
                    for item in run["items"]
                ],
                "grades": {
                    digest: evaluation["grades"][digest]
                    for evaluation in index["evaluations"]
                    if evaluation["runId"] == run["runId"]
                    for digest in evaluation["grades"]
                },
            }
            for run in comparable
        ],
    }
    return _page("运行对照", body, active="compare"), "window.OBSERVER_COMPARE = " + json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ) + ";\n"


def _audit_markdown(index: dict) -> str:
    lines = [
        "# 图片 Enrichment 存储审计",
        "",
        "此报告只读统计 cache、runs、evaluations，不把观察台自身混入来源。",
        "",
        "| 类别 | 文件 | 目录 | 字节 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, group in index["storage"]["categories"].items():
        lines.append(f"| {name} | {group['files']} | {group['directories']} | {group['bytes']} |")
    lines.extend(
        [
            "",
            f"总计：{index['storage']['totalFiles']} 文件，{index['storage']['totalBytes']} 字节。",
            f"当前校验不通过缓存：{index['summary']['currentInvalidCaches']}；孤立缓存：{index['summary']['orphanCaches']}。",
            "",
            "## 问题",
            "",
        ]
    )
    for issue in index["issues"]:
        lines.append(f"- {issue['severity']} `{issue['code']}`：`{issue['path']}`")
    return "\n".join(lines) + "\n"


def _preview_markdown(preview: dict) -> str:
    lines = [
        "# 图片 Enrichment 清理预览",
        "",
        "预览不删除文件。可清理候选仍需用户另行确认。",
        "",
        "| 建议 | 类别 | ID | 字节 | 理由 | 云端重建 |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for item in preview["items"]:
        lines.append(
            f"| {item['action']} | {item['kind']} | `{item['id']}` | {item['bytes']} | "
            f"{item['reasonCode']} | "
            f"{'未知' if item['requiresCloud'] is None else '是' if item['requiresCloud'] else '否'} |"
        )
        lines.append(f"  - 目标：`{item['path']}`；风险：{item['risk']}")
    return "\n".join(lines) + "\n"


def write_reports(index: dict, site_root: Path) -> dict:
    preview = cleanup_preview(index)
    _write(
        site_root / "observer-index.json",
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    write_audit(index, site_root)
    write_preview(preview, site_root)
    return preview


def write_audit(index: dict, site_root: Path) -> None:
    _write(
        site_root / "storage-audit.json",
        json.dumps(index["storage"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _write(site_root / "storage-audit.md", _audit_markdown(index))


def write_preview(preview: dict, site_root: Path) -> None:
    _write(
        site_root / "cleanup-preview.json",
        json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _write(site_root / "cleanup-preview.md", _preview_markdown(preview))


def build_site(index: dict, site_root: Path) -> dict:
    source_root = Path(index["source"]["enrichmentRoot"]).resolve()
    site_root = site_root.resolve()
    if site_root == source_root or not site_root.is_relative_to(source_root):
        raise ValueError("observer output must be a dedicated directory inside enrichment root")
    if site_root.parts[len(source_root.parts)] in ("cache", "runs", "evaluations"):
        raise ValueError("observer output cannot overlap evidence or cache directories")
    site_root.mkdir(parents=True, exist_ok=True)
    thumbnails = 0
    for image in index["images"]:
        thumbnail, warning = _try_thumbnail(image, site_root)
        image["thumbnail"] = thumbnail
        if thumbnail:
            thumbnails += 1
        elif warning:
            index["issues"].append(
                {"severity": "warning", "code": warning, "path": image["relativePath"]}
            )
    assets = Path(__file__).with_name("observer_assets")
    for name in ("site.css", "site.js"):
        _write(site_root / "assets" / name, (assets / name).read_text(encoding="utf-8"))
    preview = write_reports(index, site_root)
    _write(site_root / "index.html", _home(index))
    _write(site_root / "images.html", _image_listing(index))
    _write(site_root / "runs.html", _run_listing(index))
    _write(site_root / "caches.html", _cache_listing(index))
    _write(site_root / "storage.html", _storage(index, preview))
    comparison, comparison_data = _compare(index)
    _write(site_root / "compare.html", comparison)
    _write(site_root / "assets" / "compare-data.js", comparison_data)
    for image in index["images"]:
        page = site_root / "images" / f"{image['imageSha256']}.html"
        _write(page, _image_detail(index, image, page))
    for run in index["runs"]:
        _write(site_root / "runs" / f"{run['runId']}.html", _run_detail(index, run))
    for cache in index["caches"]:
        _write(site_root / "caches" / f"{cache['cacheKey']}.html", _cache_detail(index, cache))
    for evaluation in index["evaluations"]:
        _write(
            site_root / "evaluations" / f"{evaluation['evaluationId']}.html",
            _evaluation_detail(index, evaluation),
        )
    return {
        "entry": str(site_root / "index.html"),
        "thumbnails": thumbnails,
        "thumbnailFailures": len(index["images"]) - thumbnails,
        "pages": 6
        + len(index["images"])
        + len(index["runs"])
        + len(index["caches"])
        + len(index["evaluations"]),
        "cleanupPreview": preview["summary"],
    }
