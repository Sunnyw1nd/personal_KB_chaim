"""Small synthetic local input fixtures, never alter real knowledge samples."""

from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from wechat_html_normalizer.application.image_tasks import prepare_revision_images
from wechat_html_normalizer.application.local_imports import (
    DuplicateCandidate,
    decode_text,
    extract_markdown_bundle,
    image_info,
    import_local_file,
)
from wechat_html_normalizer.models import Document, DocumentRecord
from wechat_html_normalizer.renderer import render_markdown


@pytest.fixture
def local_root(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    return root, tmp_path / "documents"


def raster(path, fmt="PNG", color="red"):
    Image.new("RGB", (32, 20), color).save(path, format=fmt)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gb18030"])
def test_text_encoding_literal_and_lines(local_root, encoding):
    root, out = local_root
    text = "# 不是标题\n  对齐文本  保留\n\n1. 不是列表\n第二行"
    (root / "中文.txt").write_bytes(text.encode(encoding))
    result = import_local_file(root, "中文.txt", out)
    doc = result.document
    assert doc.schema_version == "3.0.0"
    assert doc.metadata.published_at is None and doc.metadata.author is None
    assert doc.source.encoding == encoding
    assert doc.blocks[0].content == "# 不是标题\n  对齐文本  保留"
    assert doc.blocks[0].source_locator == "lines:1-2"
    assert doc.blocks[1].source_locator == "lines:4-5"
    assert all(b.type == "paragraph" and b.plain_text for b in doc.blocks)
    assert "```text\n# 不是标题" in render_markdown(doc)
    assert "htmlFileName" not in doc.source.model_dump(by_alias=True)


@pytest.mark.parametrize("data", [b"", b"   \n", b"\x00abc", b"\xff\x00\x01\x02"])
def test_invalid_text_rejected(data):
    with pytest.raises(ValueError):
        decode_text(data)


def test_markdown_tokens_formatting_code_table_sequence(local_root):
    root, out = local_root
    raster(root / "图.png")
    text = '# 中文标题\n\n加**粗**和`代码`[链接](https://example.com)。\n\n- 父项\n  1. 子项\n\n> 引用\n\n```python\nx = "TEXT"\n    y = x\\n\n```\n\n| 列一 | 列二 |\n| --- | --- |\n| **值** | 2 |\n\n前![图注](%E5%9B%BE.png)后\n\n---\n'
    (root / "a.md").write_text(text, encoding="utf-8")
    doc = import_local_file(root, "a.md", out).document
    assert doc.metadata.title == "中文标题"
    assert next(b for b in doc.blocks if b.type == "code").content == 'x = "TEXT"\n    y = x\\n\n'
    items = next(b for b in doc.blocks if b.type == "list").items
    assert [i.depth for i in items] == [0, 1] and items[1].ordered
    assert next(b for b in doc.blocks if b.type == "table").rows == [["列一", "列二"], ["值", "2"]]
    at = next(i for i, b in enumerate(doc.blocks) if b.type == "image")
    assert doc.blocks[at - 1].content == "前" and doc.blocks[at + 1].content == "后"
    assert doc.blocks[at].caption == "图注"
    rendered = render_markdown(doc)
    assert "加**粗**和`代码`[链接]" in rendered
    assert "    1. 子项" in rendered
    assert "未结构化的 `thematic_break`" not in rendered


def test_markdown_missing_completion_degradation_identity(local_root):
    root, out = local_root
    (root / "a.md").write_text("# 文章\n\n![缺图](missing.png)", encoding="utf-8")
    first = import_local_file(root, "a.md", out)
    raster(root / "missing.png")
    full = import_local_file(root, "a.md", out, target_document_id=first.document.document_id)
    assert full.decision == "resource_improved"
    assert first.document.document_id == full.document.document_id
    assert first.document.assets[0].asset_id == full.document.assets[0].asset_id
    assert first.document.source.input_sha256 == full.document.source.input_sha256
    assert first.document.source.input_set_sha256 != full.document.source.input_set_sha256
    assert full.document.asset_completeness.available == 1
    (root / "missing.png").unlink()  # This fixture only, not project samples.
    again = import_local_file(root, "a.md", out, target_document_id=first.document.document_id)
    assert again.decision == "resource_degraded"
    assert again.current_revision_id == full.current_revision_id
    record = DocumentRecord.model_validate_json(
        (full.document_directory / "document-record.json").read_text(encoding="utf-8")
    )
    assert len(record.revisions) == 2


def test_explicit_identity_duplicates_names_and_updates(local_root):
    root, out = local_root
    (root / "a.txt").write_text("内容一", encoding="utf-8")
    first = import_local_file(root, "a.txt", out)
    (root / "改名.txt").write_text("内容一", encoding="utf-8")
    with pytest.raises(DuplicateCandidate) as duplicate:
        import_local_file(root, "改名.txt", out)
    assert duplicate.value.candidates[0]["documentId"] == first.document.document_id
    same = import_local_file(root, "a.txt", out, target_document_id=first.document.document_id)
    assert same.decision == "unchanged"
    other = import_local_file(root, "改名.txt", out, create_new=True)
    assert other.document.document_id != first.document.document_id
    (root / "a.txt").write_text("内容二", encoding="utf-8")
    new = import_local_file(root, "a.txt", out)
    assert new.document.document_id != first.document.document_id
    changed = import_local_file(root, "a.txt", out, target_document_id=first.document.document_id)
    assert changed.decision == "content_updated"
    assert first.revision_directory.exists()
    assert first.document_directory == changed.document_directory


@pytest.mark.parametrize("fmt,ext", [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")])
def test_standalone_binary_and_bridge(local_root, fmt, ext):
    root, out = local_root
    raster(root / ("image." + ext), fmt)
    first = import_local_file(root, "image." + ext, out)
    assert len(first.document.blocks) == len(first.document.assets) == 1
    assert first.document.assets[0].sha256 == first.document.source.input_sha256
    prepared = prepare_revision_images(first.revision_directory)
    assert not prepared.groups[0]["prepared"].request.policy.allow_external
    assert (
        prepared.groups[0]["prepared"].request.asset_references[0].document_id.startswith("local-")
    )
    (root / ("renamed." + ext)).write_bytes((root / ("image." + ext)).read_bytes())
    with pytest.raises(DuplicateCandidate):
        import_local_file(root, "renamed." + ext, out)
    raster(root / ("image." + ext), fmt, "blue")
    update = import_local_file(
        root, "image." + ext, out, target_document_id=first.document.document_id
    )
    assert update.document.assets[0].asset_id == first.document.assets[0].asset_id
    assert update.document.assets[0].sha256 != first.document.assets[0].sha256


def test_bad_image_formats_and_pixels(local_root, monkeypatch):
    root, out = local_root
    (root / "fake.png").write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError):
        import_local_file(root, "fake.png", out)
    raster(root / "gif.png", "GIF")
    with pytest.raises(ValueError):
        image_info(root / "gif.png")
    raster(root / "large.png")
    monkeypatch.setattr("wechat_html_normalizer.application.local_imports.MAX_IMAGE_PIXELS", 20)
    with pytest.raises(ValueError, match="pixel"):
        image_info(root / "large.png")
    raster(root / "hidden.txt")
    with pytest.raises(ValueError, match="binary"):
        import_local_file(root, "hidden.txt", out)


def test_front_matter_footnotes_and_list_starts_literal(local_root):
    root, out = local_root
    source = "---\ntitle: !!python/object:os.system\n---\n\n# 正文\n\n3. 第三项\n4. 第四项\n\n引用[^1]\n\n[^1]: https://example.com\n"
    (root / "a.md").write_text(source, encoding="utf-8")
    doc = import_local_file(root, "a.md", out).document
    assert doc.metadata.title == "正文" and doc.metadata.author is None
    assert doc.blocks[0].type == "code" and "!!python" in doc.blocks[0].content
    assert next(b for b in doc.blocks if b.type == "list").items[0].number == 3
    assert any(b.plain_text and "[^1]: https://example.com" in b.content for b in doc.blocks)
    assert "3. 第三项" in render_markdown(doc)


def test_real_format_exif_and_no_original_changes(local_root):
    root, out = local_root
    raster(root / "disguised.jpg", "PNG")
    doc = import_local_file(root, "disguised.jpg", out).document
    assert doc.source.media_type == "image/png"
    assert doc.assets[0].output_path.endswith(".png")
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (32, 20)).save(root / "exif.jpg", exif=exif)
    before = (root / "exif.jpg").read_bytes()
    result = import_local_file(root, "exif.jpg", out)
    assert result.document.assets[0].width == 32
    assert (
        result.revision_directory / result.document.assets[0].output_path
    ).read_bytes() == before
    assert (root / "exif.jpg").read_bytes() == before


def test_roots_links_wrong_target_and_bounds(local_root, tmp_path, monkeypatch):
    root, out = local_root
    (root / "a.txt").write_text("内容", encoding="utf-8")
    with pytest.raises(ValueError, match="overlap"):
        import_local_file(root, "a.txt", root / "out")
    with pytest.raises(ValueError, match="unsafe"):
        import_local_file(root, "../a.txt", out)
    first = import_local_file(root, "a.txt", out)
    with pytest.raises(ValueError, match="not_both"):
        import_local_file(
            root, "a.txt", out, target_document_id=first.document.document_id, create_new=True
        )
    (root / "a.md").write_text("# 另一类型", encoding="utf-8")
    with pytest.raises(ValueError, match="format_mismatch"):
        import_local_file(root, "a.md", out, target_document_id=first.document.document_id)
    monkeypatch.setattr("wechat_html_normalizer.application.local_imports.MAX_TEXT_BYTES", 2)
    with pytest.raises(ValueError, match="size_limit"):
        import_local_file(root, "a.txt", out)


def test_local_ids_observer_mapping(local_root, tmp_path, monkeypatch):
    root, out = local_root
    raster(root / "a.png")
    result = import_local_file(root, "a.png", out)
    asset = result.document.assets[0]
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "files": [{"imageId": asset.sha256, "relativePath": "a.png", "fileName": "a.png"}],
                "relations": [
                    {
                        "imageId": asset.sha256,
                        "documentId": result.document.document_id,
                        "revisionId": result.document.revision.revision_id,
                        "assetId": asset.asset_id,
                        "title": result.document.metadata.title,
                        "documentJson": str(result.revision_directory / "document.json"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    enrich = tmp_path / "enrichment"
    enrich.mkdir()
    from wechat_html_normalizer.enrichment import observer

    monkeypatch.setattr(observer, "_default_cache_keys", lambda *_: set())
    index = observer.collect_index(
        project_root=tmp_path,
        image_root=root,
        inventory_path=inventory,
        workbook_path=None,
        enrichment_root=enrich,
        policy_path=None,
    )
    rows = index["images"]
    row = next(r for r in rows if r["imageSha256"] == asset.sha256)
    assert row["relations"][0]["documentId"] == result.document.document_id
    assert row["relations"][0]["blockOrder"] == 1


def test_local_cli_dry_offline_identity(local_root, capsys):
    root, out = local_root
    (root / "a.txt").write_text("中文", encoding="utf-8")
    from wechat_html_normalizer.application.local_imports import main

    assert main([str(root / "a.txt"), "--output-directory", str(out)]) == 0
    row = json.loads(capsys.readouterr().out)
    assert main([str(root / "a.txt"), "--output-directory", str(out)]) == 2
    assert "duplicate_candidate" in capsys.readouterr().out
    assert (
        main(
            [
                str(root / "a.txt"),
                "--output-directory",
                str(out),
                "--update-document",
                row["documentId"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["decision"] == "unchanged"


def test_local_publish_failure_preserves_current(local_root, monkeypatch):
    root, out = local_root
    (root / "a.txt").write_text("第一版", encoding="utf-8")
    first = import_local_file(root, "a.txt", out)
    record = first.document_directory / "document-record.json"
    original = record.read_bytes()
    (root / "a.txt").write_text("第二版", encoding="utf-8")

    def failed(*args):
        raise OSError("synthetic write failure")

    monkeypatch.setattr("wechat_html_normalizer.storage._atomic_record_write", failed)
    with pytest.raises(OSError):
        import_local_file(root, "a.txt", out, target_document_id=first.document.document_id)
    assert record.read_bytes() == original and first.revision_directory.exists()


def test_http_mixed_rejected_upload_does_not_abort_good_file(tmp_path):
    pytest.importorskip("flask")
    from test_workbench import wait

    from wechat_html_normalizer.application.workbench import Workbench
    from wechat_html_normalizer.workbench.server import create_app

    core = Workbench(tmp_path, key_reader=lambda: None)
    try:
        client = create_app(core).test_client()
        base = "http://127.0.0.1:8765"
        token = client.get("/api/bootstrap", base_url=base).json["csrfToken"]
        response = client.post(
            "/api/import",
            base_url=base,
            headers={"Origin": base, "X-Workbench-Token": token},
            data={
                "requestId": "mixed-upload",
                "files": [
                    (io.BytesIO(b"unsupported"), "without-extension"),
                    (io.BytesIO("正常文本".encode()), "good.txt"),
                ],
            },
        )
        assert response.status_code == 200
        task = wait(core, response.json["taskId"])
        assert task["state"] == "completed_with_errors"
        assert "error" in task["items"][0] and task["items"][1]["decision"] == "created"
    finally:
        core.close()


def test_copied_body_image_budget_includes_repeated_references(local_root, monkeypatch):
    root, out = local_root
    raster(root / "a.png")
    (root / "a.md").write_text("# A\n\n" + "![a](a.png)\n\n" * 4, encoding="utf-8")
    from wechat_html_normalizer.application import local_imports
    from wechat_html_normalizer.sample_cache import ArchiveLimits

    total = sum(p.stat().st_size for p in root.iterdir())
    monkeypatch.setattr(
        local_imports, "ArchiveLimits", lambda: ArchiveLimits(max_total_bytes=total + 1)
    )
    with pytest.raises(ValueError, match="body_asset_total_size_limit"):
        import_local_file(root, "a.md", out)


def test_remote_html_and_path_references_no_network(local_root, monkeypatch):
    root, out = local_root
    monkeypatch.setattr(
        "socket.create_connection", lambda *a, **k: pytest.fail("network forbidden")
    )
    (root / "nested").mkdir()
    raster(root / "ok.png")
    (root / "nested/a.md").write_text(
        "# A\n\n![ok](../ok.png)\n\n![remote](https://example.invalid/a.png)\n\n![escape](../../outside.png)\n\n![double](%252e%252e%252fa.png)\n\n<script>alert(1)</script>\n",
        encoding="utf-8",
    )
    doc = import_local_file(root, "nested/a.md", out).document
    assert doc.asset_completeness.available == 1 and doc.asset_completeness.missing == 3
    assert sum(m.code == "unsafe_image_reference" for m in doc.processing.messages) == 2
    assert any(b.plain_text and "<script>" in b.content for b in doc.blocks)
    assert not any(a.output_path for a in doc.assets[1:])


def test_zip_checks_explicit_main_and_atomic_history(local_root):
    root, out = local_root
    zipped = root / "test.zip"
    with ZipFile(zipped, "w") as z:
        z.writestr("a.md", "# A")
        z.writestr("b.md", "# B")
    work = root / "work"
    work.mkdir()
    extracted = extract_markdown_bundle(zipped, work)
    result = import_local_file(extracted, "b.md", out)
    assert result.document.metadata.title == "B"
    with ZipFile(root / "escape.zip", "w") as z:
        z.writestr("../a.md", "# X")
    with pytest.raises(RuntimeError, match="Unsafe"):
        extract_markdown_bundle(root / "escape.zip", work)
    assert result.revision_directory.exists()


def test_new_schema_reader_and_old_schema_intact(local_root):
    root, out = local_root
    (root / "a.txt").write_text("中文", encoding="utf-8")
    result = import_local_file(root, "a.txt", out)
    assert (
        Document.model_validate_json(
            (result.revision_directory / "document.json").read_text(encoding="utf-8")
        )
        == result.document
    )
    validate = pytest.importorskip("jsonschema").validate

    for name, value in [
        ("document-v3", result.document),
        (
            "document-record-v1.1",
            DocumentRecord.model_validate_json(
                (result.document_directory / "document-record.json").read_text(encoding="utf-8")
            ),
        ),
    ]:
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas" / (name + ".schema.json")).read_text(
                encoding="utf-8"
            )
        )
        validate(value.model_dump(mode="json", by_alias=True), schema)


def test_workbench_mixed_import_update_fake_and_restart(tmp_path):
    pytest.importorskip("flask")
    from test_workbench import Fake, wait

    from wechat_html_normalizer.application.workbench import Workbench

    fake = Fake()
    core = Workbench(
        tmp_path, provider_factory=lambda: fake, key_reader=lambda: "fake", actor="codex_test"
    )
    try:
        img = io.BytesIO()
        Image.new("RGB", (30, 20)).save(img, format="PNG")
        uploads = [
            core.save_upload(n, io.BytesIO(d))
            for n, d in [
                ("中文.txt", "内容".encode()),
                ("alone.png", img.getvalue()),
                ("bad.png", b"broken"),
            ]
        ]
        submitted = core.import_files(uploads, "mixed")
        duplicate = core.import_files(uploads, "mixed")
        assert submitted["taskId"] == duplicate["taskId"]
        task = wait(core, submitted["taskId"])
        assert len(task["items"]) == 3 and sum("error" in r for r in task["items"]) == 1
        assert fake.calls == 0
        row = next(r for r in task["items"] if r.get("sourceType") == "local_image")
        args = row["documentId"], row["revisionId"]
        key = core.preview(*args)["groups"][0]["key"]
        finished = wait(
            core, core.enrich(*args, [key], consent=True, request_id="recognize")["taskId"]
        )
        assert finished["items"][0]["metrics"]["apiCalls"] == 1
        core.review(*args, key, "needs_review", "Codex fixture only")
        core.save_human(*args, key, "Edited", ["Fact"], None)
        assert core.result(*args, key)["human"]["versions"][0]["description"] == "Edited"
        again = wait(core, core.import_files([uploads[1]], "duplicate-input")["taskId"])
        assert again["items"][0]["decision"] == "duplicate_candidate"
        same = wait(
            core,
            core.import_files([uploads[1]], "target-repeat", target_document_id=args[0])["taskId"],
        )
        assert same["items"][0]["decision"] == "unchanged"
    finally:
        core.close()
    reopened = Workbench(tmp_path, provider_factory=lambda: fake, key_reader=lambda: "fake")
    try:
        assert reopened.result(*args, key)["human"]["versions"]
        wait(
            reopened,
            reopened.enrich(*args, [key], consent=True, request_id="cache", new_run=True)["taskId"],
        )
        assert fake.calls == 1
    finally:
        reopened.close()
