"""Small, offline observer fixtures; no real article or model service is required."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from PIL import Image

from wechat_html_normalizer.enrichment.observer import (
    _safe_relative,
    cleanup_preview,
    collect_index,
)
from wechat_html_normalizer.enrichment.observer_site import build_site


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path, monkeypatch) -> tuple[dict, Path, Path]:
    from wechat_html_normalizer.enrichment import observer

    monkeypatch.setattr(observer, "_default_cache_keys", lambda *_: set())
    image_root = tmp_path / "test_pictures"
    image_root.mkdir()
    # Windows forbids slash-bearing names, so test hostile text in metadata instead.
    picture = image_root / "中文 空格.png"
    Image.new("RGB", (18, 12), "white").save(picture)
    digest = hashlib.sha256(picture.read_bytes()).hexdigest()
    cache_key = "c" * 64  # Deliberately different from the image SHA.
    document = tmp_path / "output" / "documents" / "one" / "document.json"
    _json(
        document,
        {
            "blocks": [
                {"type": "image", "assetId": "asset-1", "order": 3, "sourceLocator": "#body/img"}
            ]
        },
    )
    inventory = tmp_path / "inventory.json"
    _json(
        inventory,
        {
            "schemaVersion": "1.0.0",
            "files": [
                {
                    "imageId": digest,
                    "relativePath": picture.name,
                    "fileName": "<script>unsafe</script>.png",
                    "format": "PNG",
                    "width": 18,
                    "height": 12,
                    "sizeBytes": picture.stat().st_size,
                }
            ],
            "relations": [
                {
                    "imageId": digest,
                    "documentJson": str(document),
                    "documentId": f"doc-{number}",
                    "revisionId": f"rev-{number}",
                    "assetId": "asset-1",
                    "title": f"文章 {number}",
                    "role": "article_body_image",
                }
                for number in (1, 2)
            ],
        },
    )
    root = tmp_path / "output" / "image-enrichment"
    cache = root / "cache" / cache_key
    record = {
        "schemaVersion": "1.0.0",
        "cacheKey": cache_key,
        "imageSha256": digest,
        "status": "success",
        "processor": "test",
        "provider": "fake",
        "requestedModel": "safe-model",
        "actualModel": "safe-model",
        "promptVersion": "image-enrichment-v1",
        "promptSha256": "a" * 64,
        "resultSchemaVersion": "1.0.0",
        "processedAt": "2026-01-01T00:00:00Z",
        "contextFingerprintSha256": "f" * 64,
        "elapsedMs": 5,
        "attempts": 1,
        "retryCount": 0,
        "enableThinking": False,
        "attemptRecords": [
            {
                "attemptNumber": 1,
                "structuredRetryNumber": 0,
                "transportRetryNumber": 0,
                "status": "success",
                "completedAt": "2026-01-01T00:00:01Z",
                "elapsedMs": 5,
                "providerResponseReceived": True,
                "requestId": "test-request",
                "actualModel": "safe-model",
                "usage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3},
                "rawResponsePath": "raw-response.json",
            }
        ],
        "result": {
            "schemaVersion": "1.0.0",
            "imageType": "architecture_flow",
            "knowledgeValue": "high",
            "action": "describe",
            "retrievalDecision": "include",
            "description": (
                "<script>alert('x')</script> visible text sk-abcdefghijklmnop "
                "data:image/png;base64,QUJDRA=="
            ),
            "keyFacts": ["Node A -> B"],
        },
    }
    _json(cache / "record.json", record)
    _json(cache / "raw-response.json", {"content": "safe"})
    run_id = "fixture-run"
    _json(root / "runs" / run_id / "requests" / f"{digest}.json", {"image": digest})
    _json(
        root / "runs" / run_id / "run-manifest.json",
        {
            "schemaVersion": "1.0.0",
            "runId": run_id,
            "status": "completed",
            "createdAt": "2026-01-01T00:00:00Z",
            "completedAt": "2026-01-01T00:00:01Z",
            "provider": "fake",
            "requestedModel": "safe-model",
            "region": "test",
            "promptVersion": "image-enrichment-v1",
            "promptSha256": "a" * 64,
            "resultSchemaVersion": "1.0.0",
            "inventorySha256": "b" * 64,
            "configurationFingerprintSha256": "d" * 64,
            "enableThinking": False,
            "force": False,
            "dryRun": False,
            "dataset": {"selected": 1},
            "summary": {"success": 1},
            "items": [
                {
                    "imageSha256": digest,
                    "relativePath": picture.name,
                    "cacheKey": cache_key,
                    "status": "success",
                    "requestPath": f"runs/{run_id}/requests/{digest}.json",
                    "recordPath": f"cache/{cache_key}/record.json",
                }
            ],
        },
    )
    _json(
        root / "evaluations" / run_id / "quality-review.json",
        {
            "runId": run_id,
            "reviewBasis": "test",
            "items": [{"imageSha256": digest, "grade": "无需修改", "note": "checked"}],
        },
    )
    index = collect_index(
        project_root=tmp_path,
        image_root=image_root,
        inventory_path=inventory,
        workbook_path=None,
        enrichment_root=root,
        policy_path=None,
    )
    return index, root, picture


def test_observer_bidirectional_mapping_and_read_only(tmp_path: Path, monkeypatch) -> None:
    index, root, picture = _fixture(tmp_path, monkeypatch)
    digest = hashlib.sha256(picture.read_bytes()).hexdigest()
    image = index["images"][0]
    assert image["imageSha256"] == digest
    assert len(image["relations"]) == 2
    assert image["relations"][0]["blockOrder"] == 3
    assert image["cacheRefs"] == ["c" * 64]
    assert image["runRefs"][0]["runId"] == "fixture-run"
    assert image["evaluationRefs"][0]["grade"] == "无需修改"
    assert index["caches"][0]["imageSha256"] == digest
    assert index["caches"][0]["runRefs"] == ["fixture-run"]
    assert index["runs"][0]["evaluationRefs"] == ["fixture-run"]
    assert index["runs"][0]["observedApiCalls"] == 1
    assert index["runs"][0]["items"][0]["attempts"][0]["rawResponseExists"]
    assert index["summary"]["currentInvalidCaches"] == 0
    assert index["storage"]["totalFiles"] == 5
    assert root.exists() and picture.exists()


def test_observer_html_is_offline_escaped_and_rebuildable(tmp_path: Path, monkeypatch) -> None:
    index, root, picture = _fixture(tmp_path, monkeypatch)
    before = hashlib.sha256(picture.read_bytes()).hexdigest()
    source_files = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }
    site = root / "observer"
    summary = build_site(index, site)
    assert summary["thumbnails"] == 1
    assert summary["thumbnailFailures"] == 0
    image_page = (site / "images" / f"{before}.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in image_page
    assert "&lt;script&gt;alert" in image_page
    assert "sk-abcdefghijklmnop" not in image_page
    assert "data:image/png;base64,QUJDRA==" not in image_page
    assert "[REDACTED_CREDENTIAL]" in image_page
    assert "Content-Security-Policy" in image_page
    assert "https://" not in image_page
    assert (site / "thumbnails" / f"{before}.png").is_file()
    for page in site.rglob("*.html"):
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        for tag in soup.find_all(["a", "img", "script", "link"]):
            value = tag.get("href") or tag.get("src")
            if value:
                assert not urlsplit(value).scheme
                assert (page.parent / unquote(value)).resolve().is_file(), (page, value)
    assert hashlib.sha256(picture.read_bytes()).hexdigest() == before
    assert source_files == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and not p.is_relative_to(site)
    }


def test_cleanup_preview_is_stable_and_conservative(tmp_path: Path, monkeypatch) -> None:
    index, root, _ = _fixture(tmp_path, monkeypatch)
    unknown = root / "mystery.bin"
    unknown.write_bytes(b"keep me")
    index["issues"].append(
        {"code": "unknown_enrichment_root_entry", "severity": "warning", "path": str(unknown)}
    )
    first = cleanup_preview(index)
    second = cleanup_preview(index)
    assert first == second
    assert first["mode"] == "preview_only"
    assert all(row["action"] != "cleanup_candidate" for row in first["items"])
    assert next(row for row in first["items"] if row["kind"] == "unknown")["action"] == "review"
    assert unknown.read_bytes() == b"keep me"


def test_safe_relative_rejects_escape_and_windows_paths(tmp_path: Path) -> None:
    import pytest

    for bad in ("../outside", "/absolute", "C:/Windows/file", "folder\\other"):
        with pytest.raises(ValueError, match="unsafe|escapes"):
            _safe_relative(tmp_path, bad)


def test_missing_preview_uses_placeholder(tmp_path: Path, monkeypatch) -> None:
    index, root, _ = _fixture(tmp_path, monkeypatch)
    index["images"][0]["imageState"] = "missing"
    summary = build_site(index, root / "observer")
    assert summary["thumbnailFailures"] == 1
    page = next((root / "observer" / "images").glob("*.html")).read_text(encoding="utf-8")
    assert "预览不可用" in page


def test_observer_reports_missing_image_and_corrupt_cache(tmp_path: Path, monkeypatch) -> None:
    index, root, picture = _fixture(tmp_path, monkeypatch)
    picture.unlink()
    (root / "cache" / ("c" * 64) / "record.json").write_text("{bad", encoding="utf-8")
    from wechat_html_normalizer.enrichment import observer

    monkeypatch.setattr(observer, "_default_cache_keys", lambda *_: set())
    rebuilt = collect_index(
        project_root=tmp_path,
        image_root=tmp_path / "test_pictures",
        inventory_path=tmp_path / "inventory.json",
        workbook_path=None,
        enrichment_root=root,
        policy_path=None,
    )
    assert rebuilt["images"][0]["imageState"] == "missing"
    assert rebuilt["summary"]["currentInvalidCaches"] == 1
    assert {issue["code"] for issue in rebuilt["issues"]} >= {
        "image_missing",
        "invalid_cached_record",
    }
    assert index["summary"]["currentInvalidCaches"] == 0


def test_dangling_evaluation_is_reported_without_broken_run_link(
    tmp_path: Path, monkeypatch
) -> None:
    _, root, _ = _fixture(tmp_path, monkeypatch)
    review = root / "evaluations" / "fixture-run" / "quality-review.json"
    payload = json.loads(review.read_text(encoding="utf-8"))
    payload["runId"] = "missing-run"
    _json(review, payload)
    from wechat_html_normalizer.enrichment import observer

    monkeypatch.setattr(observer, "_default_cache_keys", lambda *_: set())
    index = collect_index(
        project_root=tmp_path,
        image_root=tmp_path / "test_pictures",
        inventory_path=tmp_path / "inventory.json",
        workbook_path=None,
        enrichment_root=root,
        policy_path=None,
    )
    assert "evaluation_run_missing" in {issue["code"] for issue in index["issues"]}
    build_site(index, root / "observer")
    page = (root / "observer" / "evaluations" / "fixture-run.html").read_text(encoding="utf-8")
    assert "missing-run" in page
    assert 'href="../runs/missing-run.html"' not in page


def test_failed_model_text_and_validation_errors_are_visible_but_escaped(
    tmp_path: Path, monkeypatch
) -> None:
    _, root, picture = _fixture(tmp_path, monkeypatch)
    digest = hashlib.sha256(picture.read_bytes()).hexdigest()
    cache_key = "c" * 64
    failed_directory = root / "runs" / "fixture-run" / "failed-records" / digest
    source_record = json.loads(
        (root / "cache" / cache_key / "record.json").read_text(encoding="utf-8")
    )
    source_record.update(
        {
            "status": "invalid_output",
            "errorCode": "invalid_structured_output",
            "errorMessage": "strict validation failed",
            "rawResponsePath": "attempts/attempt-001.raw-response.json",
        }
    )
    source_record.pop("result")
    source_record["attemptRecords"][0].update(
        {
            "status": "invalid_output",
            "errorCode": "invalid_structured_output",
            "errorMessage": "strict validation failed",
            "rawResponsePath": "attempts/attempt-001.raw-response.json",
        }
    )
    _json(failed_directory / "record.json", source_record)
    _json(
        failed_directory / "attempts" / "attempt-001.raw-response.json",
        {
            "content": "<script>model output</script> sk-abcdefghijklmnop",
            "contentType": "str",
            "localValidation": {
                "status": "invalid",
                "errors": [
                    {
                        "location": ["code", "text"],
                        "message": "complete code is required",
                        "type": "value_error",
                    }
                ],
            },
        },
    )
    manifest_path = root / "runs" / "fixture-run" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["summary"] = {"invalid_output": 1}
    manifest["items"][0].update(
        {
            "status": "invalid_output",
            "errorCode": "invalid_structured_output",
            "recordPath": f"runs/fixture-run/failed-records/{digest}/record.json",
        }
    )
    _json(manifest_path, manifest)
    from wechat_html_normalizer.enrichment import observer

    monkeypatch.setattr(observer, "_default_cache_keys", lambda *_: set())
    index = collect_index(
        project_root=tmp_path,
        image_root=tmp_path / "test_pictures",
        inventory_path=tmp_path / "inventory.json",
        workbook_path=None,
        enrichment_root=root,
        policy_path=None,
    )
    evidence = index["runs"][0]["items"][0]["attempts"][0]["evidence"]
    assert evidence["content"] == "<script>model output</script> [REDACTED_CREDENTIAL]"
    assert evidence["validationErrors"][0]["message"] == "complete code is required"
    build_site(index, root / "observer")
    for page in (
        root / "observer" / "runs" / "fixture-run.html",
        root / "observer" / "images" / f"{digest}.html",
    ):
        content = page.read_text(encoding="utf-8")
        assert "模型原始回答（脱敏纯文本）" in content
        assert "complete code is required" in content
        assert "&lt;script&gt;model output&lt;/script&gt;" in content
        assert "sk-abcdefghijklmnop" not in content


def test_historical_failure_without_raw_evidence_is_explained(tmp_path: Path, monkeypatch) -> None:
    _, root, picture = _fixture(tmp_path, monkeypatch)
    digest = hashlib.sha256(picture.read_bytes()).hexdigest()
    record_path = root / "runs" / "fixture-run" / "failed-records" / f"{digest}.json"
    record = json.loads((root / "cache" / ("c" * 64) / "record.json").read_text(encoding="utf-8"))
    record.update(
        {
            "status": "invalid_output",
            "errorCode": "invalid_structured_output",
            "errorMessage": "historical failure",
            "attemptRecords": [],
        }
    )
    record.pop("result")
    _json(record_path, record)
    manifest_path = root / "runs" / "fixture-run" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0].update(
        {
            "status": "invalid_output",
            "errorCode": "invalid_structured_output",
            "recordPath": f"runs/fixture-run/failed-records/{digest}.json",
        }
    )
    _json(manifest_path, manifest)
    from wechat_html_normalizer.enrichment import observer

    monkeypatch.setattr(observer, "_default_cache_keys", lambda *_: set())
    index = collect_index(
        project_root=tmp_path,
        image_root=tmp_path / "test_pictures",
        inventory_path=tmp_path / "inventory.json",
        workbook_path=None,
        enrichment_root=root,
        policy_path=None,
    )
    build_site(index, root / "observer")
    page = (root / "observer" / "runs" / "fixture-run.html").read_text(encoding="utf-8")
    assert "创建于逐次原文留存功能之前" in page
    assert "模型原始回答无法恢复" in page
