from __future__ import annotations

from pathlib import Path

import pytest

from wechat_html_normalizer import NormalizationError, normalize_saved_article
from wechat_html_normalizer.models import Document


def test_zhihu_detection_metadata_blocks_and_special_nodes(
    zhihu_article: tuple[Path, Path, Path],
) -> None:
    html, resources, output = zhihu_article
    before_html = html.read_bytes()
    before_resources = {
        path.name: path.read_bytes() for path in resources.iterdir() if path.is_file()
    }
    result = normalize_saved_article(
        html,
        resources,
        output,
        imported_at="2026-08-24T00:00:00Z",
    )
    document = result.document
    assert document.schema_version == "2.0.0"
    assert document.document_id.startswith("zhihu-")
    assert document.processing.adapter == "zhihu_column_saved_html"
    assert document.processing.detection_evidence == [
        "og:url host zhuanlan.zhihu.com",
        "og:url path /p/",
        "structure:.Post-Title+.AuthorInfo-name+.Post-RichText",
    ]
    assert document.metadata.title == "知乎 专栏示例"
    assert document.metadata.account_name is None
    assert document.metadata.author == "李四"
    assert document.metadata.original_url == "https://zhuanlan.zhihu.com/p/123456"
    assert document.metadata.published_at == "2025-03-09T10:18:27+00:00"
    assert document.metadata.modified_at == "2025-04-11T17:54:57+00:00"
    assert document.metadata.published_at_raw == "2025-03-09T10:18:27.000Z"
    assert document.metadata.modified_at_raw == "2025-04-11T17:54:57.000Z"

    assert [block.type for block in document.blocks] == [
        "heading",
        "paragraph",
        "heading",
        "list",
        "quote",
        "code",
        "image",
        "paragraph",
        "paragraph",
        "unknown",
        "paragraph",
    ]
    assert [
        (block.level, block.content) for block in document.blocks if block.type == "heading"
    ] == [
        (2, "章节二"),
        (3, "章节三"),
    ]
    paragraph = document.blocks[1]
    assert paragraph.inlines
    assert any(span.marks == ["strong"] and span.text == "加粗" for span in paragraph.inlines)
    assert any(span.marks == ["emphasis"] and span.text == "强调" for span in paragraph.inlines)
    assert any(span.marks == ["code"] and span.text == "inline()" for span in paragraph.inlines)
    assert any(
        span.href == "https://example.com/doc" and span.text == "外链" for span in paragraph.inlines
    )
    assert "M0 0" not in (paragraph.content or "")
    assert "之后。\n必要换行" in (paragraph.content or "")
    assert not any(
        message.code == "metadata_last_modified_time_mismatch"
        for message in document.processing.messages
    )

    code = next(block for block in document.blocks if block.type == "code")
    assert code.language == "python"
    assert code.content == 'def hello():\n    print("``` inside")', repr(code.content)
    assert code.content.count("def hello") == 1

    image = next(block for block in document.blocks if block.type == "image")
    assert image.caption == "正文图注"
    assert image.asset_id and image.asset_id.startswith("asset-")
    assert len(document.assets) == 1
    asset = document.assets[0]
    assert asset.detected_media_type == "image/webp"
    assert asset.original_url == "https://pic.zhimg.com/original.jpg"
    assert asset.output_path == f"assets/{asset.asset_id}.webp"

    link_card = document.blocks[7]
    assert link_card.inlines and link_card.inlines[0].href == "https://example.com/repo"
    assert (
        link_card.inlines[0].source_href
        == "https://link.zhihu.com/?target=https%3A%2F%2Fexample.com%2Frepo"
    )
    video = document.blocks[9]
    assert video.original_tag == "video"
    assert video.source_url == "https://video.example/demo.mp4"
    assert any(
        message.code == "unsupported_embedded_video" for message in document.processing.messages
    )
    assert document.blocks[8].content == "视频之前"
    assert document.blocks[10].content == "视频之后"

    manifest = {entry.path: entry for entry in result.manifest.resources}
    assert manifest["知乎示例_files/body.jpg"].reason_code == "article_body_image"
    assert manifest["知乎示例_files/card.jpg"].reason_code == "link_card_thumbnail"
    assert manifest["知乎示例_files/poster.jpg"].reason_code == "video_poster"
    assert manifest["知乎示例_files/avatar.jpg"].reason_code == "page_ui_asset"
    assert manifest["知乎示例_files/bundle.js.下载"].reason_code == "executable_script"
    assert manifest["知乎示例_files/style.css.下载"].reason_code == "stylesheet"
    assert manifest["知乎示例_files/icon.svg"].reason_code == "page_ui_asset"

    markdown = (result.output_directory / "document.md").read_text(encoding="utf-8")
    assert "最后编辑时间" in markdown
    assert "> 图注：正文图注" in markdown
    assert "[示例仓库 — example.com](https://example.com/repo)" in markdown
    assert "嵌入视频待支持" in markdown
    assert "之后。  \n 必要换行" in markdown
    assert '````python\ndef hello():\n    print("``` inside")\n````' in markdown
    assert "评论噪声" not in markdown and "页面导航" not in markdown and "M0 0" not in markdown
    Document.model_validate_json(
        (result.output_directory / "document.json").read_text(encoding="utf-8")
    )
    assert html.read_bytes() == before_html
    assert before_resources == {
        path.name: path.read_bytes() for path in resources.iterdir() if path.is_file()
    }


def test_zhihu_repeat_run_is_stable(zhihu_article: tuple[Path, Path, Path]) -> None:
    html, resources, output = zhihu_article
    first = normalize_saved_article(html, resources, output, imported_at="2026-08-24T00:00:00Z")
    names = ["document.json", "document.md", "processing-report.json", "resource-manifest.json"]
    before = {name: (first.output_directory / name).read_bytes() for name in names}
    second = normalize_saved_article(html, resources, output)
    assert before == {name: (second.output_directory / name).read_bytes() for name in names}


def test_unknown_source_does_not_fall_back_to_whole_page(tmp_path: Path) -> None:
    html = tmp_path / "unknown.html"
    resources = tmp_path / "unknown_files"
    resources.mkdir()
    html.write_text("<html><body><article>不可回退正文</article></body></html>", encoding="utf-8")
    with pytest.raises(NormalizationError) as caught:
        normalize_saved_article(html, resources, tmp_path / "output")
    assert caught.value.code == "source_unsupported"


def test_conflicting_high_confidence_sources_are_ambiguous(tmp_path: Path) -> None:
    html = tmp_path / "ambiguous.html"
    resources = tmp_path / "ambiguous_files"
    resources.mkdir()
    html.write_text(
        """<meta property="og:url" content="https://zhuanlan.zhihu.com/p/1">
        <h1 class="Post-Title" id="activity-name">标题</h1><span id="js_name">账号</span>
        <span class="AuthorInfo-name">作者</span><div id="js_content">微信</div>
        <div class="Post-RichText">知乎</div>""",
        encoding="utf-8",
    )
    with pytest.raises(NormalizationError) as caught:
        normalize_saved_article(html, resources, tmp_path / "output")
    assert caught.value.code == "source_ambiguous"


def test_zhihu_visible_and_structured_time_mismatch_is_reported(
    zhihu_article: tuple[Path, Path, Path],
) -> None:
    html, resources, output = zhihu_article
    text = html.read_text(encoding="utf-8").replace(
        "编辑于 2025-04-12 01:54 · 上海",
        "编辑于 2025-04-13 01:54 · 上海",
    )
    html.write_text(text, encoding="utf-8")
    result = normalize_saved_article(
        html,
        resources,
        output,
        imported_at="2026-08-24T00:00:00Z",
    )
    assert result.document.metadata.modified_at == "2025-04-11T17:54:57+00:00"
    assert any(
        message.code == "metadata_last_modified_time_mismatch"
        for message in result.document.processing.messages
    )


def test_zhihu_missing_and_unsupported_body_images_are_reported(
    zhihu_article: tuple[Path, Path, Path],
) -> None:
    html, resources, output = zhihu_article
    (resources / "broken.bin").write_bytes(b"not an image")
    text = html.read_text(encoding="utf-8").replace(
        "<p>视频之后</p>",
        """<p>视频之后</p>
        <figure><img src="知乎示例_files/missing.jpg"></figure>
        <figure><img src="知乎示例_files/broken.bin"></figure>""",
    )
    html.write_text(text, encoding="utf-8")
    result = normalize_saved_article(
        html,
        resources,
        output,
        imported_at="2026-08-24T00:00:00Z",
    )
    assert result.document.processing.statistics["body_image_references"] == 3
    assert result.document.processing.statistics["body_images_missing"] == 1
    assert result.document.processing.statistics["body_images_copied"] == 1
    codes = [message.code for message in result.document.processing.messages]
    assert codes.count("missing_resource") == 1
    assert codes.count("unsupported_resource_type") == 1
    entries = {entry.path: entry for entry in result.manifest.resources}
    assert entries["知乎示例_files/missing.jpg"].decision == "missing"
    assert entries["知乎示例_files/broken.bin"].reason_code == "unsupported_resource_type"
