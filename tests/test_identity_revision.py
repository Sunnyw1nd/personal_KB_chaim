from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from conftest import image_bytes

from wechat_html_normalizer import (
    NormalizationError,
    normalize_sample_archives,
    normalize_wechat_article,
)
from wechat_html_normalizer.identity import (
    document_id_for_identity,
    normalize_wechat_identity,
    normalize_zhihu_identity,
)
from wechat_html_normalizer.models import DocumentRecord
from wechat_html_normalizer.sample_cache import PreparedSample
from wechat_html_normalizer.storage import StorageError, document_directory_name


def _write_wechat(
    root: Path,
    *,
    token: str = "stable-token",
    title: str = "稳定身份示例",
    body: str = "<p>正文</p>",
    images: list[tuple[str, str]] | None = None,
) -> tuple[Path, Path]:
    html = root / "article.html"
    resources = root / "article_files"
    resources.mkdir(exist_ok=True)
    image_html = "".join(
        f'<img src="article_files/{local}" data-src="https://mmbiz.qpic.cn/{remote}?wx_fmt=png">'
        for local, remote in (images or [])
    )
    html.write_text(
        f"""<!doctype html><html><head><meta charset="utf-8">
        <meta property="og:url" content="https://mp.weixin.qq.com/s/{token}?scene=1">
        </head><body><h1 id="activity-name">{title}</h1><span id="js_name">账号</span>
        <div id="js_content">{body}{image_html}</div></body></html>""",
        encoding="utf-8",
    )
    return html, resources


def _prepared(html: Path, resources: Path, package_hash: str) -> PreparedSample:
    return PreparedSample(
        archive_path=html.parent / f"{package_hash[:4]}.zip",
        archive_sha256=package_hash,
        extraction_root=html.parent,
        html_path=html,
        resource_directory=resources,
        cache_hit=False,
        manifest_path=html.parent / ".sample-extraction.json",
        html_archive_path=html.name,
        resource_archive_path=resources.name,
    )


def _record(result: object) -> DocumentRecord:
    path = result.document_directory / "document-record.json"  # type: ignore[attr-defined]
    return DocumentRecord.model_validate_json(path.read_text(encoding="utf-8"))


def test_source_identity_normalization_is_stable_and_source_isolated() -> None:
    first = normalize_zhihu_identity("https://zhuanlan.zhihu.com/p/12345?utm_source=x#part")
    second = normalize_zhihu_identity("https://www.zhihu.com/p/12345?share_code=y")
    assert first and second and first.normalized_value == second.normalized_value
    assert first.native_id == "12345"

    wechat_a = normalize_wechat_identity("https://mp.weixin.qq.com/s/native-token?scene=1")
    wechat_b = normalize_wechat_identity("https://mp.weixin.qq.com/s/native-token?from=timeline")
    assert wechat_a and wechat_b and wechat_a.normalized_value == wechat_b.normalized_value
    assert document_id_for_identity("wechat", wechat_a) == document_id_for_identity(
        "wechat", wechat_b
    )
    other_wechat = normalize_wechat_identity("https://mp.weixin.qq.com/s/other-token")
    assert other_wechat
    assert document_id_for_identity("wechat", wechat_a) != document_id_for_identity(
        "wechat", other_wechat
    )
    assert document_id_for_identity("wechat", wechat_a) != document_id_for_identity("zhihu", first)

    query_a = normalize_wechat_identity(
        "https://mp.weixin.qq.com/s?scene=1&mid=2&idx=1&__biz=abc&sn=xyz"
    )
    query_b = normalize_wechat_identity(
        "https://mp.weixin.qq.com/s?sn=xyz&__biz=abc&idx=1&mid=2&utm_source=test"
    )
    assert query_a and query_b and query_a.normalized_value == query_b.normalized_value
    assert normalize_wechat_identity("https://mp.weixin.qq.com/s?id=only-a-title") is None


def test_wechat_saved_comment_path_url_can_supply_stable_identity(tmp_path: Path) -> None:
    html, resources = _write_wechat(tmp_path)
    text = html.read_text(encoding="utf-8")
    text = text.replace(
        '<meta property="og:url" content="https://mp.weixin.qq.com/s/stable-token?scene=1">',
        "<!-- saved from url=(0050)https://mp.weixin.qq.com/s/comment-token -->",
    )
    html.write_text(text, encoding="utf-8")
    result = normalize_wechat_article(
        html, resources, tmp_path / "output", imported_at="2026-01-01T00:00:00Z"
    )
    assert result.document.metadata.original_url == "https://mp.weixin.qq.com/s/comment-token"
    assert result.document.source_identity
    assert result.document.source_identity.native_id == "comment-token"


def test_unresolved_identity_never_merges_by_title(tmp_path: Path) -> None:
    ids = []
    for index, body in enumerate(("正文甲", "正文乙")):
        root = tmp_path / str(index)
        root.mkdir()
        html, resources = _write_wechat(root, token="")
        text = (
            html.read_text(encoding="utf-8")
            .replace("https://mp.weixin.qq.com/s/?scene=1", "https://mp.weixin.qq.com/s?id=unknown")
            .replace("<p>正文</p>", f"<p>{body}</p>")
        )
        html.write_text(text, encoding="utf-8")
        result = normalize_wechat_article(
            html, resources, tmp_path / "output", imported_at="2026-01-01T00:00:00Z"
        )
        assert result.document.source_identity
        assert result.document.source_identity.confidence == "unresolved"
        ids.append(result.document.document_id)
    assert ids[0] != ids[1]


def test_same_package_and_equivalent_package_do_not_duplicate_revision(tmp_path: Path) -> None:
    html, resources = _write_wechat(tmp_path)
    first = normalize_wechat_article(
        html,
        resources,
        tmp_path / "output",
        imported_at="2026-01-01T00:00:00Z",
        source_sample=_prepared(html, resources, "a" * 64),
    )
    repeated = normalize_wechat_article(
        html,
        resources,
        tmp_path / "output",
        source_sample=_prepared(html, resources, "a" * 64),
    )
    equivalent = normalize_wechat_article(
        html,
        resources,
        tmp_path / "output",
        source_sample=_prepared(html, resources, "b" * 64),
    )
    assert (first.import_decision, repeated.import_decision, equivalent.import_decision) == (
        "created",
        "unchanged",
        "equivalent",
    )
    record = _record(first)
    assert len(record.revisions) == 1
    assert record.revisions[0].package_sha256s == ["a" * 64, "b" * 64]
    assert first.output_directory == repeated.output_directory == equivalent.output_directory


def test_missing_image_completion_and_degradation_preserve_history(tmp_path: Path) -> None:
    html, resources = _write_wechat(
        tmp_path,
        images=[("640", "logical-image")],
    )
    output = tmp_path / "output"
    missing = normalize_wechat_article(html, resources, output, imported_at="2026-01-01T00:00:00Z")
    missing_asset = missing.document.assets[0]
    assert missing.document.asset_completeness
    assert missing.document.asset_completeness.model_dump(by_alias=True) == {
        "expected": 1,
        "available": 0,
        "missing": 1,
        "invalid": 0,
        "completenessRate": 0.0,
        "missingAssetIds": [missing_asset.asset_id],
        "invalidAssetIds": [],
    }

    (resources / "640").write_bytes(image_bytes("WEBP"))
    completed = normalize_wechat_article(
        html, resources, output, imported_at="2026-01-02T00:00:00Z"
    )
    assert completed.document.document_id == missing.document.document_id
    assert completed.document.assets[0].asset_id == missing_asset.asset_id
    assert completed.import_decision == "resource_improved"
    assert completed.current_changed
    assert completed.document.asset_completeness
    assert completed.document.asset_completeness.available == 1
    completed_current = completed.current_revision_id

    (resources / "640").unlink()
    degraded = normalize_wechat_article(html, resources, output, imported_at="2026-01-03T00:00:00Z")
    assert degraded.import_decision == "resource_degraded"
    assert degraded.current_revision_id == completed_current
    assert not degraded.current_changed
    record = _record(degraded)
    assert len(record.revisions) == 2
    assert record.current_revision_id == completed_current
    assert missing.output_directory.is_dir() and completed.output_directory.is_dir()


def test_binary_change_and_body_update_form_traceable_revisions(tmp_path: Path) -> None:
    html, resources = _write_wechat(tmp_path, images=[("640", "logical-image")])
    (resources / "640").write_bytes(image_bytes("WEBP", (20, 10)))
    output = tmp_path / "output"
    first = normalize_wechat_article(html, resources, output, imported_at="2026-01-01T00:00:00Z")
    asset_id = first.document.assets[0].asset_id
    first_binary = first.document.assets[0].sha256

    (resources / "640").write_bytes(image_bytes("WEBP", (30, 15)))
    changed_binary = normalize_wechat_article(
        html, resources, output, imported_at="2026-01-02T00:00:00Z"
    )
    assert changed_binary.import_decision == "resource_updated"
    assert changed_binary.document.assets[0].asset_id == asset_id
    assert changed_binary.document.assets[0].sha256 != first_binary

    html.write_text(
        html.read_text(encoding="utf-8").replace("<p>正文</p>", "<p>正文已修订</p>"),
        encoding="utf-8",
    )
    changed_body = normalize_wechat_article(
        html, resources, output, imported_at="2026-01-03T00:00:00Z"
    )
    assert changed_body.import_decision == "content_updated"
    assert changed_body.document.document_id == first.document.document_id
    assert changed_body.document_directory == first.document_directory
    assert len(_record(changed_body).revisions) == 3


def test_existing_asset_ids_survive_an_inserted_middle_image(tmp_path: Path) -> None:
    html, resources = _write_wechat(
        tmp_path,
        images=[("a", "remote-a"), ("c", "remote-c")],
    )
    for name in ("a", "b", "c"):
        (resources / name).write_bytes(image_bytes("PNG"))
    output = tmp_path / "output"
    first = normalize_wechat_article(html, resources, output, imported_at="2026-01-01T00:00:00Z")
    ids = {asset.original_url: asset.asset_id for asset in first.document.assets}
    text = html.read_text(encoding="utf-8").replace(
        '<img src="article_files/c"',
        '<img src="article_files/b" data-src="https://mmbiz.qpic.cn/remote-b?wx_fmt=png">'
        '<img src="article_files/c"',
    )
    html.write_text(text, encoding="utf-8")
    second = normalize_wechat_article(html, resources, output, imported_at="2026-01-02T00:00:00Z")
    updated_ids = {asset.original_url: asset.asset_id for asset in second.document.assets}
    assert (
        updated_ids["https://mmbiz.qpic.cn/remote-a?wx_fmt=png"]
        == ids["https://mmbiz.qpic.cn/remote-a?wx_fmt=png"]
    )
    assert (
        updated_ids["https://mmbiz.qpic.cn/remote-c?wx_fmt=png"]
        == ids["https://mmbiz.qpic.cn/remote-c?wx_fmt=png"]
    )


@pytest.mark.parametrize(
    "title",
    [
        "中文标题",
        "English title",
        '混合<>:"/\\|?*标点',
        "CON",
        "",
        "很长" * 100,
        "标题. ",
    ],
)
def test_readable_directory_name_is_windows_safe_and_stable(tmp_path: Path, title: str) -> None:
    first = document_directory_name(
        "wechat_saved_html", title, "wechat-0123456789abcdef", output_root=tmp_path
    )
    second = document_directory_name(
        "wechat_saved_html", title, "wechat-0123456789abcdef", output_root=tmp_path
    )
    assert first == second
    assert first.startswith("weixin-") and first.endswith("--0123456789")
    assert not any(character in first for character in '<>:"/\\|?*')
    assert not first.endswith((" ", "."))
    assert len(first) <= 96


def test_same_title_different_documents_have_distinct_names() -> None:
    first = document_directory_name("wechat_saved_html", "相同标题", "wechat-0123456789abcdef")
    second = document_directory_name("wechat_saved_html", "相同标题", "wechat-fedcba9876543210")
    assert first != second


def test_excessively_deep_output_root_is_rejected() -> None:
    with pytest.raises(StorageError) as caught:
        document_directory_name(
            "wechat_saved_html",
            "标题",
            "wechat-0123456789abcdef",
            output_root=Path("C:/") / ("x" * 180),
        )
    assert caught.value.code == "output_path_too_long"


def test_title_change_does_not_move_existing_document_directory(tmp_path: Path) -> None:
    html, resources = _write_wechat(tmp_path, title="旧标题")
    first = normalize_wechat_article(
        html, resources, tmp_path / "output", imported_at="2026-01-01T00:00:00Z"
    )
    html.write_text(html.read_text(encoding="utf-8").replace("旧标题", "新标题"), encoding="utf-8")
    second = normalize_wechat_article(
        html, resources, tmp_path / "output", imported_at="2026-01-02T00:00:00Z"
    )
    assert second.document_directory == first.document_directory
    assert "旧标题" in second.document_directory.name


def test_two_recompressed_archives_share_document_and_revision(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    html, resources = _write_wechat(source)
    (resources / "style.css").write_text("body{}", encoding="utf-8")
    samples = tmp_path / "samples"
    samples.mkdir()
    entries = [(html.name, html.read_bytes())]
    entries.extend(
        (f"{resources.name}/{path.name}", path.read_bytes()) for path in resources.iterdir()
    )
    with ZipFile(samples / "a.zip", "w", ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    with ZipFile(samples / "b.zip", "w", ZIP_STORED) as archive:
        for name, content in reversed(entries):
            archive.writestr(name, content)
    summary = normalize_sample_archives(
        samples, tmp_path / ".work", tmp_path / "output", imported_at="2026-01-01T00:00:00Z"
    )
    assert summary.statistics["created"] == 1
    assert summary.statistics["equivalent"] == 1
    assert len({item.document_id for item in summary.items}) == 1
    assert len({item.revision_id for item in summary.items}) == 1


def test_failed_record_update_does_not_replace_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    html, resources = _write_wechat(tmp_path)
    first = normalize_wechat_article(
        html, resources, tmp_path / "output", imported_at="2026-01-01T00:00:00Z"
    )
    original_record = (first.document_directory / "document-record.json").read_bytes()
    html.write_text(
        html.read_text(encoding="utf-8").replace("<p>正文</p>", "<p>更新</p>"),
        encoding="utf-8",
    )

    def fail_write(path: Path, record: DocumentRecord) -> None:
        raise OSError("simulated record failure")

    monkeypatch.setattr("wechat_html_normalizer.storage._atomic_record_write", fail_write)
    with pytest.raises(NormalizationError) as caught:
        normalize_wechat_article(
            html, resources, tmp_path / "output", imported_at="2026-01-02T00:00:00Z"
        )
    assert caught.value.code == "output_write_failed"
    assert (first.document_directory / "document-record.json").read_bytes() == original_record
    assert not list(first.document_directory.glob(".document-record.*.tmp"))


def test_failed_first_publish_leaves_no_managed_half_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    html, resources = _write_wechat(tmp_path)

    def fail_write(path: Path, record: DocumentRecord) -> None:
        raise OSError("simulated first publish failure")

    monkeypatch.setattr("wechat_html_normalizer.storage._atomic_record_write", fail_write)
    output = tmp_path / "output"
    with pytest.raises(NormalizationError) as caught:
        normalize_wechat_article(html, resources, output, imported_at="2026-01-01T00:00:00Z")
    assert caught.value.code == "output_write_failed"
    assert not list(output.glob("*/document-record.json"))
    assert not [path for path in output.iterdir() if path.is_dir()]


def test_batch_reports_created_updated_unchanged_equivalent_conflict_and_failed(
    tmp_path: Path,
) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    output = tmp_path / "output"
    work = tmp_path / ".work"

    unchanged_root = tmp_path / "unchanged-source"
    unchanged_root.mkdir()
    unchanged_html, unchanged_resources = _write_wechat(unchanged_root, token="batch-unchanged")
    (unchanged_resources / "style.css").write_text("body{}", encoding="utf-8")

    def archive_article(
        target: Path,
        html: Path,
        resources: Path,
        *,
        compression: int = ZIP_DEFLATED,
        reverse: bool = False,
    ) -> None:
        entries = [(html.name, html.read_bytes())]
        entries.extend(
            (f"{resources.name}/{path.name}", path.read_bytes())
            for path in sorted(resources.iterdir())
            if path.is_file()
        )
        if reverse:
            entries.reverse()
        with ZipFile(target, "w", compression) as archive:
            for name, content in entries:
                archive.writestr(name, content)

    archive_article(samples / "a-unchanged.zip", unchanged_html, unchanged_resources)
    initial = normalize_sample_archives(samples, work, output, imported_at="2026-01-01T00:00:00Z")
    assert initial.statistics["created"] == 1
    archive_article(
        samples / "b-equivalent.zip",
        unchanged_html,
        unchanged_resources,
        compression=ZIP_STORED,
        reverse=True,
    )

    created_root = tmp_path / "created-source"
    created_root.mkdir()
    created_html, created_resources = _write_wechat(created_root, token="batch-created")
    (created_resources / "style.css").write_text("body{}", encoding="utf-8")
    archive_article(samples / "c-created.zip", created_html, created_resources)

    improved_root = tmp_path / "improved-source"
    improved_root.mkdir()
    improved_html, improved_resources = _write_wechat(
        improved_root,
        token="batch-improved",
        images=[("640", "batch-improved-image")],
    )
    normalize_wechat_article(
        improved_html,
        improved_resources,
        output,
        imported_at="2026-01-01T00:00:00Z",
    )
    (improved_resources / "640").write_bytes(image_bytes("WEBP"))
    archive_article(samples / "d-improved.zip", improved_html, improved_resources)

    conflict_root = tmp_path / "conflict-source"
    conflict_root.mkdir()
    conflict_html, conflict_resources = _write_wechat(conflict_root, token="batch-conflict")
    (conflict_resources / "style.css").write_text("body{}", encoding="utf-8")
    conflict_result = normalize_wechat_article(
        conflict_html,
        conflict_resources,
        output,
        imported_at="2026-01-01T00:00:00Z",
    )
    record_path = conflict_result.document_directory / "document-record.json"
    record_payload = json.loads(record_path.read_text(encoding="utf-8"))
    record_payload["sourceIdentity"]["normalizedValue"] = "wechat:article:s:different"
    record_path.write_text(
        json.dumps(record_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    archive_article(samples / "e-conflict.zip", conflict_html, conflict_resources)

    with ZipFile(samples / "f-failed.zip", "w", ZIP_DEFLATED) as archive:
        archive.writestr("unknown.html", "<html><article>unsupported</article></html>")
        archive.writestr("unknown_files/item", "x")

    summary = normalize_sample_archives(samples, work, output, imported_at="2026-01-02T00:00:00Z")
    assert summary.statistics["archives"] == 6
    assert summary.statistics["created"] == 1
    assert summary.statistics["updated"] == 1
    assert summary.statistics["unchanged"] == 1
    assert summary.statistics["equivalent"] == 1
    assert summary.statistics["conflicts"] == 1
    assert summary.statistics["failed"] == 1
    statuses = {item.archive_file_name: item for item in summary.items}
    assert statuses["d-improved.zip"].import_decision == "resource_improved"
    assert statuses["e-conflict.zip"].status == "conflict"
    assert statuses["f-failed.zip"].error_code == "source_unsupported"
