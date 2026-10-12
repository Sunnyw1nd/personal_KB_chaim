from __future__ import annotations

import io
import json
import time
from threading import Event
from zipfile import ZipFile

import pytest

pytest.importorskip("flask")

from wechat_html_normalizer.application.image_tasks import prepare_revision_images, safe_child
from wechat_html_normalizer.application.workbench import Workbench
from wechat_html_normalizer.enrichment.compact_provider import CompactResponse
from wechat_html_normalizer.enrichment.models import Usage
from wechat_html_normalizer.enrichment.providers import DEFAULT_MODEL
from wechat_html_normalizer.normalizer import normalize_saved_article
from wechat_html_normalizer.workbench.server import create_app


class Fake:
    def __init__(self, gate=None):
        self.calls = 0
        self.gate = gate
        self.started = Event()

    def generate(self, request, image, prompt, **kwargs):
        self.calls += 1
        self.started.set()
        if self.gate:
            assert self.gate.wait(5)
        content = json.dumps(
            {
                "imageType": "architecture_flow",
                "knowledgeValue": "high",
                "description": "node A",
                "keyFacts": ["A"],
                "uncertainties": [],
            }
        )
        return CompactResponse(
            content,
            DEFAULT_MODEL,
            "fake-1",
            "stop",
            Usage(inputTokens=3, outputTokens=2, totalTokens=5),
            {"content": content, "model": DEFAULT_MODEL, "finishReason": "stop"},
        )


def archive(saved_article):
    html, resources, _ = saved_article
    output = io.BytesIO()
    with ZipFile(output, "w") as zip_file:
        zip_file.write(html, html.name)
        for path in resources.iterdir():
            zip_file.write(path, resources.name + "/" + path.name)
    return output.getvalue()


def wait(core, task_id):
    for _ in range(100):
        value = core.task(task_id)
        if value["state"] not in {"queued", "running"}:
            return value
        time.sleep(0.03)
    pytest.fail("task did not finish")


def setup(tmp_path, saved_article, fake=None, key="fake"):
    core = Workbench(tmp_path, provider_factory=lambda: fake, key_reader=lambda: key)
    uploaded = core.save_upload("sample.zip", io.BytesIO(archive(saved_article)))
    imported = wait(core, core.import_archives([uploaded], "import-1")["taskId"])
    assert imported["state"] == "completed"
    row = imported["items"][0]
    return core, row, uploaded


def test_formal_bridge_no_workbook_and_policy_default_closed(tmp_path, saved_article):
    result = normalize_saved_article(*saved_article[:2], tmp_path / "documents")
    images = prepare_revision_images(result.output_directory)
    assert len(images.groups) == 1
    request = images.groups[0]["prepared"].request
    assert not request.policy.allow_external
    assert request.policy.classification == "formal_asset"
    assert request.asset_references[0].document_id == result.document.document_id
    assert images.groups[0]["locations"][0]["order"] > 0


def test_bridge_distinct_context_and_equal_context_relations(tmp_path, saved_article):
    html, resources, _ = saved_article
    text = html.read_text(encoding="utf-8")
    text = text.replace(
        "</div>", '<p>second context</p><p><img src="示例文章_files/640"></p><p>tail</p></div>'
    )
    html.write_text(text, encoding="utf-8")
    result = normalize_saved_article(html, resources, tmp_path / "docs")
    images = prepare_revision_images(result.output_directory)
    assert len(images.groups) == 2
    assert len({g["prepared"].request.context_fingerprint_sha256 for g in images.groups}) == 2
    # A second adjacent image with identical context retains both location references.
    doc = result.document.model_copy(deep=True)
    block = next(b for b in doc.blocks if b.type == "image")
    at = doc.blocks.index(block)
    duplicate = block.model_copy(update={"block_id": "block-9999"})
    doc.blocks.insert(at + 1, duplicate)
    for order, b in enumerate(doc.blocks, 1):
        b.order = order
    (result.output_directory / "document.json").write_text(
        doc.model_dump_json(by_alias=True), encoding="utf-8"
    )
    regroup = prepare_revision_images(result.output_directory)
    assert any(len(g["locations"]) == 2 for g in regroup.groups)


def test_bridge_missing_changed_and_path_escape(tmp_path, saved_article):
    result = normalize_saved_article(*saved_article[:2], tmp_path / "docs")
    doc = result.document
    path = result.output_directory / doc.assets[0].output_path
    path.write_bytes(b"broken")
    images = prepare_revision_images(result.output_directory)
    assert not images.groups and images.excluded[0]["reason"] == "image_binary_mismatch"
    with pytest.raises(ValueError):
        safe_child(result.output_directory, "../.env")
    with pytest.raises(ValueError):
        safe_child(result.output_directory, "C:\\private")


def test_duplicate_running_completed_restart_and_cache(tmp_path, saved_article):
    gate = Event()
    fake = Fake(gate)
    core, row, _ = setup(tmp_path, saved_article, fake)
    args = (row["documentId"], row["revisionId"])
    key = core.preview(*args)["groups"][0]["key"]
    first = core.enrich(*args, [key], consent=True, request_id="recognize-1")
    assert fake.started.wait(5)
    second = core.enrich(*args, [key], consent=True, request_id="recognize-2", new_run=True)
    assert second["taskId"] == first["taskId"]
    assert fake.calls == 1
    gate.set()
    finished = wait(core, first["taskId"])
    assert finished["items"][0]["metrics"]["apiCalls"] == 1
    assert (
        core.enrich(*args, [key], consent=True, request_id="recognize-1")["taskId"]
        == first["taskId"]
    )
    core.close()
    again = Workbench(tmp_path, provider_factory=lambda: fake, key_reader=lambda: "fake")
    try:
        assert (
            again.enrich(*args, [key], consent=True, request_id="recognize-1")["taskId"]
            == first["taskId"]
        )
        cached = wait(
            again,
            again.enrich(*args, [key], consent=True, request_id="recognize-3", new_run=True)[
                "taskId"
            ],
        )
        assert cached["items"][0]["metrics"]["apiCalls"] == 0
        assert fake.calls == 1
        assert cached["callBudget"] == 3
    finally:
        again.close()


def test_no_key_no_consent_local_only_and_rejected_cache(tmp_path, saved_article):
    fake = Fake()
    core, row, _ = setup(tmp_path, saved_article, fake, key=None)
    args = (row["documentId"], row["revisionId"])
    key = core.preview(*args)["groups"][0]["key"]
    with pytest.raises(ValueError):
        core.enrich(*args, [key], consent=False, request_id="bad")
    blocked = wait(
        core, core.enrich(*args, [key], consent=True, request_id="missing-key")["taskId"]
    )
    assert blocked["error"] == "api_key_missing" and fake.calls == 0
    core.key_reader = lambda: "fake"
    wait(core, core.enrich(*args, [key], consent=True, request_id="valid", new_run=True)["taskId"])
    core.review(*args, key, "rejected", "test refusal")
    assert core.result(*args, key)["record"]["reviewStatus"] == "rejected"
    assert core.preview(*args)["groups"][0]["reviewStatus"] == "rejected"
    denied = wait(
        core, core.enrich(*args, [key], consent=True, request_id="denied", new_run=True)["taskId"]
    )
    assert denied["items"][0]["error"] == "cache_review_rejected" and fake.calls == 1
    policy = tmp_path / "config/policy.json"
    policy.parent.mkdir(exist_ok=True)
    sha = core.preview(*args)["groups"][0]["image"]["sha256"]
    policy.write_text(
        json.dumps(
            {"schemaVersion": "1.0.0", "images": {sha: {"localOnly": True, "reasonCode": "test"}}}
        ),
        encoding="utf-8",
    )
    core.policy = policy
    with pytest.raises(ValueError):
        core.enrich(*args, [key], consent=True, request_id="local-only")
    core.close()


def test_human_edits_are_independent_and_preserve_code(tmp_path, saved_article):
    core, row, _ = setup(tmp_path, saved_article, Fake())
    args = (row["documentId"], row["revisionId"])
    key = core.preview(*args)["groups"][0]["key"]
    wait(core, core.enrich(*args, [key], consent=True, request_id="first")["taskId"])
    record = core.enrichment / "cache" / key / "record.json"
    before = record.read_bytes()
    code = 'if x:\n    print("<script>")\n'
    one = core.save_human(*args, key, "<script>alert(1)</script>", ["fact"], code)
    assert one["adopted"] is None
    saved = one["versions"][0]
    core.adopt_human(*args, key, saved["revisionId"])
    core.save_human(*args, key, "second", ["new"], code)
    assert record.read_bytes() == before
    core.close()
    again = Workbench(tmp_path, key_reader=lambda: None)
    try:
        human = again.result(*args, key)["human"]
        assert len(human["versions"]) == 2
        assert human["versions"][0]["codeText"] == code
        assert human["adopted"]["revisionId"] == saved["revisionId"]
    finally:
        again.close()


def test_restart_interrupted_does_not_requeue(tmp_path, saved_article):
    fake = Fake()
    core, _row, _ = setup(tmp_path, saved_article, fake)
    task = core.tasks()[0]
    task["state"] = "running"
    core._save_task(task)
    core.close()
    again = Workbench(tmp_path, provider_factory=lambda: fake, key_reader=lambda: "fake")
    assert again.task(task["taskId"])["state"] == "interrupted" and fake.calls == 0
    again.close()


def test_http_guard_static_and_controlled_file_access(tmp_path, saved_article):
    core, row, _ = setup(tmp_path, saved_article, Fake())
    client = create_app(core).test_client()
    base = "http://127.0.0.1:8765"
    token = client.get("/api/bootstrap", base_url=base).json["csrfToken"]
    assert client.get("/api/bootstrap").status_code == 403
    assert client.post("/api/enrich", base_url=base, json={}).status_code == 403
    headers = {"Origin": base, "X-Workbench-Token": token}
    assert (
        client.post(
            "/api/enrich",
            base_url=base,
            headers={**headers, "Origin": "https://evil.example"},
            json={},
        ).status_code
        == 403
    )
    assert client.get("/api/asset?doc=../.env&rev=x&asset=x", base_url=base).status_code == 400
    assert client.get("/static/../.env", base_url=base).status_code == 404
    index = client.get("/", base_url=base)
    assert "frame-ancestors" in index.headers["Content-Security-Policy"]
    assert client.get("/static/app.js", base_url=base).status_code == 200
    doc = client.get("/api/document", base_url=base, query_string={"doc": row["documentId"]}).json
    assert doc["groups"] and doc["profile"] == "compact-json-v1"
    core.close()


def test_http_upload_larger_than_multipart_buffer_and_bad_zip_isolated(tmp_path, saved_article):
    core = Workbench(tmp_path, key_reader=lambda: None)
    client = create_app(core).test_client()
    base = "http://127.0.0.1:8765"
    token = client.get("/api/bootstrap", base_url=base).json["csrfToken"]
    good = io.BytesIO(archive(saved_article))
    response = client.post(
        "/api/import",
        base_url=base,
        headers={"Origin": base, "X-Workbench-Token": token},
        data={
            "files": [(good, "valid.zip"), (io.BytesIO(b"bad" * 100000), "bad.zip")],
            "requestId": "multipart",
        },
    )
    assert response.status_code == 200
    done = wait(core, response.json["taskId"])
    assert done["state"] == "completed_with_errors" and len(done["items"]) == 2
    assert sum("error" not in item for item in done["items"]) == 1
    core.close()


def test_exclusive_instance_and_explicit_offline_recovery(tmp_path, saved_article):
    fake = Fake()
    core, row, _ = setup(tmp_path, saved_article, fake)
    with pytest.raises(ValueError, match="another workbench"):
        Workbench(tmp_path, key_reader=lambda: None)
    args = row["documentId"], row["revisionId"]
    key = core.preview(*args)["groups"][0]["key"]
    job = wait(core, core.enrich(*args, [key], consent=True, request_id="one")["taskId"])
    recovered = core.recover(job["taskId"])
    assert recovered["state"] == "recovered" and fake.calls == 1
    with pytest.raises(ValueError, match="credential_like_human"):
        core.save_human(*args, key, "sk-1234567890abcdefghijklmnop", [], None)
    core.close()


def test_busy_cross_document_alias_remains_idempotent_after_completion(tmp_path, saved_article):
    gate = Event()
    fake = Fake(gate)
    core, first, _ = setup(tmp_path, saved_article, fake)
    html = saved_article[0]
    html.write_text(
        html.read_text(encoding="utf-8").replace(
            "https://mp.weixin.qq.com/s?id=abc", "https://mp.weixin.qq.com/s/second-article"
        ),
        encoding="utf-8",
    )
    uploaded = core.save_upload("other.zip", io.BytesIO(archive(saved_article)))
    second = wait(core, core.import_archives([uploaded], "import-2")["taskId"])["items"][0]
    keys = [
        core.preview(r["documentId"], r["revisionId"])["groups"][0]["key"] for r in (first, second)
    ]
    assert keys[0] == keys[1]
    job = core.enrich(
        first["documentId"], first["revisionId"], [keys[0]], consent=True, request_id="one"
    )
    assert fake.started.wait(5)
    alias = core.enrich(
        second["documentId"], second["revisionId"], [keys[1]], consent=True, request_id="two"
    )
    assert alias["taskId"] == job["taskId"]
    gate.set()
    wait(core, job["taskId"])
    assert (
        core.enrich(
            second["documentId"], second["revisionId"], [keys[1]], consent=True, request_id="two"
        )["taskId"]
        == job["taskId"]
    )
    assert len(list((core.data / "bindings").glob("*/*.json"))) >= 4
    assert fake.calls == 1
    core.close()


def test_failed_original_visible_without_success_cache(tmp_path, saved_article):
    class Invalid:
        calls = 0

        def generate(self, *args, **kwargs):
            self.calls += 1
            return CompactResponse(
                "not valid JSON",
                DEFAULT_MODEL,
                "invalid",
                "stop",
                Usage(totalTokens=5),
                {"content": "not valid JSON", "model": DEFAULT_MODEL, "finishReason": "stop"},
            )

    fake = Invalid()
    core, row, _ = setup(tmp_path, saved_article, fake)
    args = row["documentId"], row["revisionId"]
    key = core.preview(*args)["groups"][0]["key"]
    job = wait(core, core.enrich(*args, [key], consent=True, request_id="invalid")["taskId"])
    assert job["state"] == "completed_with_errors" and fake.calls == 2
    detail = core.result(*args, key)
    assert detail["record"]["status"] == "invalid_output"
    assert len(detail["attempts"]) == 2
    assert detail["attempts"][0]["raw"]["content"] == "not valid JSON"
    assert not (core.enrichment / "cache" / key).exists()
    assert core.recover(job["taskId"])["state"] == "recovered" and fake.calls == 2
    core.close()
