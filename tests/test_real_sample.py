from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from wechat_html_normalizer import (
    normalize_sample_archives,
    normalize_saved_article,
    normalize_wechat_article,
    prepare_sample,
)
from wechat_html_normalizer.models import DocumentRecord

EXPECTED = {
    "html1.zip": {
        "zip_sha256": "3e915b558b07e272367b30c255afef02460489cce993e5bd0b7152f3f82514b1",
        "title_prefix": "京东二面：你的Agent跑到一半挂了",
        "account": "算法狗",
        "images": 7,
        "headings": 6,
        "tables": 0,
        "mismatches": 7,
        "scripts": 4,
        "document_id": "wechat-0d15bf414f2f1f89",
    },
    "html2.zip": {
        "zip_sha256": "2d4768c7021b0cc00a88b2c65d054c564a9d1b44b6ac4dc3c5daa1c9cfee9d5b",
        "title_prefix": "面试官问：5个OK全绿",
        "account": "吴师兄学大模型",
        "images": 4,
        "headings": 4,
        "tables": 1,
        "mismatches": 0,
        "scripts": 4,
        "document_id": "wechat-8cd143556ee72855",
    },
}

ZHIHU_EXPECTED = {
    "archive_name": "MCPzhihu.zip",
    "zip_sha256": "15bcd20ae793ab09787ba983d94e4bd0c033c5853291a173e1fcb8e5fc290f62",
    "html_sha256": "ef0808404d2029c7a18840cd853d9e423727d1d543f8e065725079ce73d953c3",
    "title": "MCP (Model Context Protocol)，一篇就够了。",
    "author": "LastWhisper",
    "document_id": "zhihu-e889fcd682a4c668",
}

COMPLETION_PAIR_STEM = (
    "字节二面若有所思：_你说模仿Claude Code来精简提示词——删完你拿什么验证没翻车？_ "
    "我刚想说_跑一遍就行_，突然觉得不对"
)
COMPLETION_PAIR = {
    "missing_archive": f"{COMPLETION_PAIR_STEM}_files.zip",
    "complete_archive": f"{COMPLETION_PAIR_STEM}.zip",
    "missing_zip_sha256": "615ce9a58ac224feda6213278c66fbdc2d9fb91a37aa8e1a75b8877c4db9df44",
    "complete_zip_sha256": "87b835f38a58bb2d28d6d655617e088ac027a625d639220d2ca42fb8eef44803",
    "document_id": "wechat-4a498ea1280780fd",
    "content_fingerprint": "db828a353110f4f31f6df2915dc64e77650b0077bbbb142c75fd5b3132db4103",
    "missing_revision": "revision-257aa1197ecc9f28",
    "complete_revision": "revision-88eabcd4f0bd163d",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.real_sample
@pytest.mark.parametrize("archive_name", sorted(EXPECTED))
def test_real_wechat_archives(tmp_path: Path, archive_name: str) -> None:
    archive = Path("samples") / archive_name
    if not archive.is_file():
        pytest.skip(f"Real fixture {archive} is not available.")
    expected = EXPECTED[archive_name]
    before_hash = _sha256(archive)
    assert before_hash == expected["zip_sha256"]

    prepared = prepare_sample(archive, Path("samples"), Path(".work"))
    repeated = prepare_sample(archive, Path("samples"), Path(".work"))
    assert repeated.cache_hit
    assert repeated.manifest_path == prepared.manifest_path

    result = normalize_wechat_article(
        prepared.html_path,
        prepared.resource_directory,
        tmp_path / "output",
        imported_at="2026-08-12T00:00:00Z",
        source_sample=prepared,
    )
    document = result.document
    assert document.schema_version == "2.0.0"
    assert document.document_id == expected["document_id"]
    assert document.source_identity and document.source_identity.reliable
    assert document.revision and document.asset_completeness
    assert document.asset_completeness.available == expected["images"]
    assert document.asset_completeness.expected == expected["images"]
    assert result.document_directory.name.startswith("weixin-")
    assert document.metadata.title.startswith(expected["title_prefix"])
    assert document.metadata.account_name == expected["account"]
    assert sum(block.type == "heading" for block in document.blocks) == expected["headings"]
    assert sum(block.type == "table" for block in document.blocks) == expected["tables"]
    assert len(document.assets) == expected["images"]
    assert all(
        asset.found and asset.detected_media_type == "image/webp" for asset in document.assets
    )
    assert all(
        asset.output_path and (result.output_directory / asset.output_path).is_file()
        for asset in document.assets
    )
    assert (
        sum(message.code == "declared_type_mismatch" for message in document.processing.messages)
        == expected["mismatches"]
    )
    assert (
        sum(entry.reason_code == "executable_script" for entry in result.manifest.resources)
        == expected["scripts"]
    )
    assert document.source.archive_file_name == archive.name
    assert document.source.archive_sha256 == before_hash
    repeated_import = normalize_wechat_article(
        prepared.html_path,
        prepared.resource_directory,
        tmp_path / "output",
        source_sample=prepared,
    )
    assert repeated_import.import_decision == "unchanged"
    assert repeated_import.current_revision_id == result.current_revision_id
    assert _sha256(archive) == before_hash


@pytest.mark.real_sample
def test_real_zhihu_archive(tmp_path: Path) -> None:
    archive = Path("samples") / ZHIHU_EXPECTED["archive_name"]
    if not archive.is_file():
        pytest.skip(f"Real fixture {archive} is not available.")
    before_hash = _sha256(archive)
    assert before_hash == ZHIHU_EXPECTED["zip_sha256"]

    prepared = prepare_sample(archive, Path("samples"), Path(".work"))
    repeated_prepare = prepare_sample(archive, Path("samples"), Path(".work"))
    assert repeated_prepare.cache_hit
    assert repeated_prepare.manifest_path == prepared.manifest_path

    result = normalize_saved_article(
        prepared.html_path,
        prepared.resource_directory,
        tmp_path / "output-a",
        imported_at="2026-08-24T00:00:00Z",
        source_sample=prepared,
    )
    document = result.document
    assert document.schema_version == "2.0.0"
    assert document.document_id == ZHIHU_EXPECTED["document_id"]
    assert document.source_identity and document.source_identity.reliable
    assert document.revision and document.asset_completeness
    assert document.asset_completeness.available == 10
    assert document.asset_completeness.expected == 10
    assert result.document_directory.name.startswith("zhihu-MCP-")
    assert document.processing.adapter == "zhihu_column_saved_html"
    assert document.source.html_sha256 == ZHIHU_EXPECTED["html_sha256"]
    assert document.metadata.title == ZHIHU_EXPECTED["title"]
    assert document.metadata.author == ZHIHU_EXPECTED["author"]
    assert document.metadata.published_at == "2025-03-09T10:18:27+00:00"
    assert document.metadata.modified_at == "2025-04-11T17:54:57+00:00"
    assert sum(block.type == "heading" and block.level == 2 for block in document.blocks) == 9
    assert sum(block.type == "heading" and block.level == 3 for block in document.blocks) == 5
    assert sum(block.type == "paragraph" for block in document.blocks) == 73
    assert sum(block.type == "list" and block.ordered is False for block in document.blocks) == 12
    assert sum(block.type == "list" and block.ordered is True for block in document.blocks) == 4
    assert sum(block.type == "quote" for block in document.blocks) == 3
    assert sum(block.type == "code" for block in document.blocks) == 11
    assert sum(block.type == "image" for block in document.blocks) == 10
    assert sum(bool(block.caption) for block in document.blocks) == 2
    assert (
        sum(any(span.source_href for span in (block.inlines or [])) for block in document.blocks)
        == 1
    )
    assert sum(block.type == "unknown" and bool(block.source_url) for block in document.blocks) == 1
    assert len(document.assets) == 10
    assert all(
        asset.found and asset.detected_media_type == "image/jpeg" for asset in document.assets
    )
    assert document.processing.statistics["link_card_thumbnails"] == 1
    assert document.processing.statistics["video_posters"] == 1
    assert document.processing.statistics["embedded_videos"] == 1
    reasons = [entry.reason_code for entry in result.manifest.resources]
    assert reasons.count("article_body_image") == 10
    assert reasons.count("link_card_thumbnail") == 1
    assert reasons.count("video_poster") == 1
    assert reasons.count("executable_script") == 25
    assert reasons.count("auxiliary_frame") == 1
    assert (
        sum(
            message.code == "unsupported_embedded_video" for message in document.processing.messages
        )
        == 1
    )

    rerun = normalize_saved_article(
        prepared.html_path,
        prepared.resource_directory,
        tmp_path / "output-b",
        imported_at="2026-08-24T00:00:00Z",
        source_sample=prepared,
    )
    assert (result.output_directory / "document.json").read_bytes() == (
        rerun.output_directory / "document.json"
    ).read_bytes()
    assert (result.output_directory / "document.md").read_bytes() == (
        rerun.output_directory / "document.md"
    ).read_bytes()
    repeated_import = normalize_saved_article(
        prepared.html_path,
        prepared.resource_directory,
        tmp_path / "output-a",
        source_sample=prepared,
    )
    assert repeated_import.import_decision == "unchanged"
    assert repeated_import.current_revision_id == result.current_revision_id
    assert _sha256(archive) == before_hash


@pytest.mark.real_sample
def test_real_missing_and_complete_pair_share_document_history(tmp_path: Path) -> None:
    samples = Path("samples")
    missing_archive = samples / COMPLETION_PAIR["missing_archive"]
    complete_archive = samples / COMPLETION_PAIR["complete_archive"]
    if not missing_archive.is_file() or not complete_archive.is_file():
        pytest.skip("The configured real missing/complete ZIP pair is not available.")

    before = {
        missing_archive.name: _sha256(missing_archive),
        complete_archive.name: _sha256(complete_archive),
    }
    assert before[missing_archive.name] == COMPLETION_PAIR["missing_zip_sha256"]
    assert before[complete_archive.name] == COMPLETION_PAIR["complete_zip_sha256"]

    missing_sample = prepare_sample(missing_archive, samples, Path(".work"))
    complete_sample = prepare_sample(complete_archive, samples, Path(".work"))
    assert prepare_sample(missing_archive, samples, Path(".work")).cache_hit
    assert prepare_sample(complete_archive, samples, Path(".work")).cache_hit

    forward_output = tmp_path / "missing-then-complete"
    missing = normalize_saved_article(
        missing_sample.html_path,
        missing_sample.resource_directory,
        forward_output,
        imported_at="2026-08-24T00:00:00Z",
        source_sample=missing_sample,
    )
    complete = normalize_saved_article(
        complete_sample.html_path,
        complete_sample.resource_directory,
        forward_output,
        imported_at="2026-08-24T00:01:00Z",
        source_sample=complete_sample,
    )

    assert missing.document.document_id == complete.document.document_id
    assert complete.document.document_id == COMPLETION_PAIR["document_id"]
    assert missing.document.source_identity and complete.document.source_identity
    assert (
        missing.document.source_identity.normalized_value
        == complete.document.source_identity.normalized_value
    )
    assert missing.document.revision and complete.document.revision
    assert (
        missing.document.revision.content_fingerprint_sha256
        == complete.document.revision.content_fingerprint_sha256
        == COMPLETION_PAIR["content_fingerprint"]
    )
    assert missing.document.revision.revision_id == COMPLETION_PAIR["missing_revision"]
    assert complete.document.revision.revision_id == COMPLETION_PAIR["complete_revision"]
    assert missing.import_decision == "created"
    assert complete.import_decision == "resource_improved"
    assert complete.current_changed
    assert complete.current_revision_id == COMPLETION_PAIR["complete_revision"]

    missing_completeness = missing.document.asset_completeness
    complete_completeness = complete.document.asset_completeness
    assert missing_completeness and complete_completeness
    assert (
        missing_completeness.expected,
        missing_completeness.available,
        missing_completeness.missing,
    ) == (7, 5, 2)
    assert (
        complete_completeness.expected,
        complete_completeness.available,
        complete_completeness.missing,
    ) == (7, 7, 0)
    assert [asset.asset_id for asset in missing.document.assets] == [
        asset.asset_id for asset in complete.document.assets
    ]
    assert set(missing_completeness.missing_asset_ids) == {
        "asset-cc4178391b48",
        "asset-65f0ccaa683a",
    }
    assert all(asset.found for asset in complete.document.assets)

    record_path = complete.document_directory / "document-record.json"
    record = DocumentRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    assert record.current_revision_id == COMPLETION_PAIR["complete_revision"]
    assert {revision.revision_id for revision in record.revisions} == {
        COMPLETION_PAIR["missing_revision"],
        COMPLETION_PAIR["complete_revision"],
    }

    degraded = normalize_saved_article(
        missing_sample.html_path,
        missing_sample.resource_directory,
        forward_output,
        imported_at="2026-08-24T00:02:00Z",
        source_sample=missing_sample,
    )
    unchanged = normalize_saved_article(
        complete_sample.html_path,
        complete_sample.resource_directory,
        forward_output,
        imported_at="2026-08-24T00:03:00Z",
        source_sample=complete_sample,
    )
    assert degraded.import_decision == "resource_degraded"
    assert degraded.current_revision_id == COMPLETION_PAIR["complete_revision"]
    assert not degraded.current_changed
    assert unchanged.import_decision == "unchanged"
    assert unchanged.current_revision_id == COMPLETION_PAIR["complete_revision"]
    assert (
        len(DocumentRecord.model_validate_json(record_path.read_text(encoding="utf-8")).revisions)
        == 2
    )

    reverse_output = tmp_path / "complete-then-missing"
    complete_first = normalize_saved_article(
        complete_sample.html_path,
        complete_sample.resource_directory,
        reverse_output,
        imported_at="2026-08-24T00:00:00Z",
        source_sample=complete_sample,
    )
    missing_second = normalize_saved_article(
        missing_sample.html_path,
        missing_sample.resource_directory,
        reverse_output,
        imported_at="2026-08-24T00:01:00Z",
        source_sample=missing_sample,
    )
    assert complete_first.import_decision == "created"
    assert missing_second.import_decision == "resource_degraded"
    assert missing_second.current_revision_id == COMPLETION_PAIR["complete_revision"]
    assert not missing_second.current_changed
    assert {path.name: _sha256(path) for path in (missing_archive, complete_archive)} == before


@pytest.mark.real_sample
def test_real_batch_normalizes_all_archives(tmp_path: Path) -> None:
    samples = Path("samples")
    expected_hashes = {
        **{name: values["zip_sha256"] for name, values in EXPECTED.items()},
        ZHIHU_EXPECTED["archive_name"]: ZHIHU_EXPECTED["zip_sha256"],
    }
    if not all((samples / name).is_file() for name in expected_hashes):
        pytest.skip("The three configured real ZIP samples are not all available.")
    archives = sorted(
        (path for path in samples.iterdir() if path.suffix.casefold() == ".zip"),
        key=lambda path: (path.name.casefold(), path.name),
    )
    before = {path.name: _sha256(path) for path in archives}
    assert {name: before[name] for name in expected_hashes} == expected_hashes

    summary = normalize_sample_archives(
        samples,
        Path(".work"),
        tmp_path / "batch-output",
        imported_at="2026-08-24T00:00:00Z",
    )
    assert summary.statistics["archives"] == len(archives)
    assert summary.statistics["normalized"] >= 3
    items = {item.archive_file_name: item for item in summary.items}
    assert set(items) == set(before)
    assert all(items[name].status == "normalized" for name in expected_hashes)
    assert all(items[name].import_decision == "created" for name in expected_hashes)
    assert all(
        items[name].document_path
        and "--" in items[name].document_path
        and not items[name].document_path.startswith(("wechat-", "zhihu-ef"))
        for name in expected_hashes
    )
    assert {items[name].adapter for name in expected_hashes} == {
        "wechat_saved_html",
        "zhihu_column_saved_html",
    }
    pair_names = {
        COMPLETION_PAIR["missing_archive"],
        COMPLETION_PAIR["complete_archive"],
    }
    if pair_names <= set(items):
        missing_item = items[COMPLETION_PAIR["missing_archive"]]
        complete_item = items[COMPLETION_PAIR["complete_archive"]]
        assert complete_item.import_decision == "created"
        assert missing_item.import_decision == "resource_degraded"
        assert (
            missing_item.document_id == complete_item.document_id == COMPLETION_PAIR["document_id"]
        )
        assert complete_item.revision_id == COMPLETION_PAIR["complete_revision"]
        assert missing_item.revision_id == COMPLETION_PAIR["missing_revision"]
        assert missing_item.current_revision_id == complete_item.current_revision_id
        assert complete_item.current_revision_id == COMPLETION_PAIR["complete_revision"]
    assert all(item.error_code for item in summary.items if item.status == "failed")
    assert all(
        item.output_path
        and (tmp_path / "batch-output" / item.output_path / "document.json").is_file()
        for item in summary.items
        if item.status == "normalized"
    )
    assert {path.name: _sha256(path) for path in archives} == before
