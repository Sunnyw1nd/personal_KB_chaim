from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wechat_html_normalizer import NormalizationError, normalize_wechat_article
from wechat_html_normalizer.models import Document


def test_normalizes_metadata_blocks_assets_and_reports(
    saved_article: tuple[Path, Path, Path],
) -> None:
    html_path, resources, output = saved_article
    original_html = html_path.read_bytes()
    original_image = (resources / "640").read_bytes()

    result = normalize_wechat_article(
        html_path, resources, output, imported_at="2026-08-12T00:00:00Z"
    )
    document = result.document

    assert document.metadata.title == "标准化示例"
    assert document.metadata.account_name == "示例公众号"
    assert document.metadata.author == "张三"
    assert document.metadata.published_at == "2026-08-10T17:40:00+08:00"
    assert document.metadata.published_at_raw == "2026年8月10日 17:40"
    assert document.metadata.original_url == "https://mp.weixin.qq.com/s?id=abc"
    assert [block.type for block in document.blocks] == [
        "heading",
        "paragraph",
        "list",
        "quote",
        "code",
        "table",
        "paragraph",
        "image",
        "paragraph",
    ]
    assert [block.order for block in document.blocks] == list(range(1, 10))
    assert "不应出现" not in " ".join(block.content or "" for block in document.blocks)
    paragraph = document.blocks[1]
    assert paragraph.inlines and paragraph.inlines[1].marks == ["strong"]
    assert any(span.href == "https://example.com/docs" for span in paragraph.inlines)

    asset = document.assets[0]
    assert asset.asset_id.startswith("asset-")
    assert asset.identity_method == "remote_url:data-src"
    assert asset.found is True
    assert asset.original_file_name == "640"
    assert asset.declared_media_type == "image/png"
    assert asset.detected_media_type == "image/webp"
    assert (asset.width, asset.height) == (32, 18)
    assert asset.output_path == f"assets/{asset.asset_id}.webp"
    assert asset.description is None and asset.description_status == "pending"
    assert (result.output_directory / asset.output_path).read_bytes() == original_image
    assert any(message.code == "declared_type_mismatch" for message in document.processing.messages)

    manifest = {entry.path: entry for entry in result.manifest.resources}
    assert manifest["示例文章_files/640"].referenced_by_body is True
    assert manifest["示例文章_files/qr.png"].reason_code == "page_ui_asset"
    assert manifest["示例文章_files/index.js.下载"].decision == "refused"
    assert manifest["示例文章_files/index.js.下载"].reason_code == "executable_script"
    assert manifest["示例文章_files/appmsg.css"].reason_code == "stylesheet"
    assert manifest["示例文章_files/frame.html"].reason_code == "auxiliary_frame"
    assert html_path.read_bytes() == original_html
    assert (resources / "640").read_bytes() == original_image

    markdown = (result.output_directory / "document.md").read_text(encoding="utf-8")
    assert (
        f"![图片 {asset.asset_id}｜原文件：640｜待描述](assets/{asset.asset_id}.webp)" in markdown
    )
    assert f"图片 {asset.asset_id}：待描述" in markdown
    assert "不应出现" not in markdown
    Document.model_validate_json(
        (result.output_directory / "document.json").read_text(encoding="utf-8")
    )


def test_repeat_run_reuses_import_time_and_is_byte_stable(
    saved_article: tuple[Path, Path, Path],
) -> None:
    html_path, resources, output = saved_article
    first = normalize_wechat_article(
        html_path, resources, output, imported_at="2026-08-12T00:00:00Z"
    )
    paths = ["document.json", "document.md", "processing-report.json", "resource-manifest.json"]
    before = {name: (first.output_directory / name).read_bytes() for name in paths}

    second = normalize_wechat_article(html_path, resources, output)
    after = {name: (second.output_directory / name).read_bytes() for name in paths}
    assert before == after


def test_missing_image_is_preserved_as_pending_asset(tmp_path: Path) -> None:
    html = tmp_path / "missing.html"
    resources = tmp_path / "missing_files"
    resources.mkdir()
    html.write_text(
        '<meta charset="utf-8"><h1 id="activity-name">缺图</h1><div id="js_content"><img src="missing_files/nope"></div>',
        encoding="utf-8",
    )
    result = normalize_wechat_article(
        html, resources, tmp_path / "out", imported_at="2026-01-01T00:00:00Z"
    )
    asset = result.document.assets[0]
    assert asset.found is False and asset.output_path is None
    assert any(
        message.code == "missing_resource" for message in result.document.processing.messages
    )
    missing_entry = next(
        entry for entry in result.manifest.resources if entry.reason_code == "missing_resource"
    )
    assert missing_entry.referenced_by_body is True
    assert "无法显示" in (result.output_directory / "document.md").read_text(encoding="utf-8")


def test_path_traversal_is_rejected_without_reading_outside_file(tmp_path: Path) -> None:
    html = tmp_path / "unsafe.html"
    resources = tmp_path / "unsafe_files"
    resources.mkdir()
    secret = tmp_path.parent / "outside-secret.png"
    secret.write_bytes(b"not relevant")
    html.write_text(
        '<meta charset="utf-8"><h1 id="activity-name">路径</h1><div id="js_content"><img src="../outside-secret.png"></div>',
        encoding="utf-8",
    )
    try:
        result = normalize_wechat_article(
            html, resources, tmp_path / "out", imported_at="2026-01-01T00:00:00Z"
        )
        asset = result.document.assets[0]
        assert asset.found is False and asset.sha256 is None
        assert any(message.code == "unsafe_path" for message in result.document.processing.messages)
    finally:
        secret.unlink(missing_ok=True)


def test_unsupported_body_resource_is_not_copied(tmp_path: Path) -> None:
    html = tmp_path / "unsupported.html"
    resources = tmp_path / "unsupported_files"
    resources.mkdir()
    (resources / "640").write_text("plain text", encoding="utf-8")
    html.write_text(
        '<meta charset="utf-8"><h1 id="activity-name">异常</h1><div id="js_content"><img src="unsupported_files/640" data-type="png"></div>',
        encoding="utf-8",
    )
    result = normalize_wechat_article(
        html, resources, tmp_path / "out", imported_at="2026-01-01T00:00:00Z"
    )
    asset = result.document.assets[0]
    assert asset.found is True
    assert asset.detected_media_type is None and asset.output_path is None
    assert any(
        message.code == "unsupported_resource_type"
        for message in result.document.processing.messages
    )


def test_missing_body_root_is_a_fatal_error(tmp_path: Path) -> None:
    html = tmp_path / "no-body.html"
    resources = tmp_path / "no-body_files"
    resources.mkdir()
    html.write_text("<html><body><p>Do not fall back to this.</p></body></html>", encoding="utf-8")
    with pytest.raises(NormalizationError, match="#js_content") as caught:
        normalize_wechat_article(html, resources, tmp_path / "out")
    assert caught.value.code == "body_root_missing"


def test_output_cannot_overlap_original_resource_directory(tmp_path: Path) -> None:
    html = tmp_path / "overlap.html"
    resources = tmp_path / "overlap_files"
    resources.mkdir()
    html.write_text(
        '<meta charset="utf-8"><div id="js_content"><p>正文</p></div>', encoding="utf-8"
    )
    with pytest.raises(NormalizationError) as caught:
        normalize_wechat_article(html, resources, resources / "generated")
    assert caught.value.code == "output_overlaps_input"


def test_schema_rejects_dangling_asset_reference(saved_article: tuple[Path, Path, Path]) -> None:
    html_path, resources, output = saved_article
    result = normalize_wechat_article(
        html_path, resources, output, imported_at="2026-08-12T00:00:00Z"
    )
    payload = result.document.model_dump(mode="json", by_alias=True)
    payload["assets"] = []
    with pytest.raises(ValidationError, match="missing assets"):
        Document.model_validate(payload)


def test_serialized_json_has_stable_sorted_keys(saved_article: tuple[Path, Path, Path]) -> None:
    html_path, resources, output = saved_article
    result = normalize_wechat_article(
        html_path, resources, output, imported_at="2026-08-12T00:00:00Z"
    )
    raw = (result.output_directory / "document.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert raw.index('"assets"') < raw.index('"blocks"') < raw.index('"documentId"')
    assert parsed["schemaVersion"] == "2.0.0"
