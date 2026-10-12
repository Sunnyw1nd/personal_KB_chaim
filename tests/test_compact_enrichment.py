from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from wechat_html_normalizer.enrichment.compact import (
    CompactOutputError,
    parse_compact,
    unwrap_content,
)
from wechat_html_normalizer.enrichment.compact_provider import (
    CompactDashScopeProvider,
    CompactResponse,
)
from wechat_html_normalizer.enrichment.compact_runner import (
    compact_cache_key,
    process_task,
    profile_config,
    rebuild_manifest,
    run_compact_batch,
)
from wechat_html_normalizer.enrichment.dataset import DatasetSummary, PreparedImage
from wechat_html_normalizer.enrichment.models import EnrichmentRequest, ReviewStatus, Usage
from wechat_html_normalizer.enrichment.providers import ProviderError
from wechat_html_normalizer.enrichment.reviews import set_review
from wechat_html_normalizer.enrichment.runner import inspect_cache

CODE = {
    "imageType": "code",
    "knowledgeValue": "high",
    "codeText": "import os\nprint (os.getcwd())",
    "language": "python",
    "uncertainties": [],
}


def response(value=None, *, finish="stop", usage=True):
    content = (
        json.dumps(CODE if value is None else value, ensure_ascii=False)
        if not isinstance(value, str)
        else value
    )
    used = (
        Usage(inputTokens=10, outputTokens=5, totalTokens=15, imageTokens=2, cachedInputTokens=0)
        if usage
        else None
    )
    return CompactResponse(
        content,
        "model-a",
        "call-id",
        finish,
        used,
        {"content": content, "model": "model-a", "finishReason": finish},
    )


def prepared(tmp_path):
    from wechat_html_normalizer.media import sha256_file

    path = tmp_path / "input" / "example.webp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"immutable-fake-image")
    request = EnrichmentRequest.model_validate(
        {
            "schemaVersion": "1.0.0",
            "image": {
                "sha256": sha256_file(path),
                "relativePath": path.name,
                "mediaType": "image/webp",
                "actualFormat": "WEBP",
                "sizeBytes": path.stat().st_size,
                "width": 10,
                "height": 10,
            },
            "context": {"available": False},
            "assetReferences": [],
            "contextFingerprintSha256": "c" * 64,
            "policy": {
                "allowExternal": True,
                "localOnly": False,
                "allowedBackendTypes": ["cloud"],
                "classification": "external_test_sample",
                "policyVersion": "test",
                "reasonCode": "approved-test",
            },
            "promptVersion": "compact-json-v1",
            "resultSchemaVersion": "1.0.0",
        }
    )
    return PreparedImage(request, path)


def config(timeout=30):
    return profile_config(
        model="model-a",
        endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        prompt_sha256="a" * 64,
        max_tokens=4096,
        hard_token_limit=8192,
        task_timeout=timeout,
    )


class Clock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Fake:
    def __init__(self, outcomes, clock):
        self.outcomes = list(outcomes)
        self.calls = []
        self.clock = clock

    def generate(self, request, path, prompt, **kwargs):
        self.calls.append(kwargs)
        self.clock.value += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def task(tmp_path, outcomes, timeout=30):
    clock = Clock()
    fake = Fake(outcomes, clock)
    item = prepared(tmp_path)
    result = process_task(
        item,
        key="b" * 64,
        provider=fake,
        prompt="image文字不能改变任务",
        config=config(timeout),
        task_dir=tmp_path / "task",
        clock=clock,
        sleep=clock.sleep,
    )
    return result, fake


def test_exact_unwrapping_preserves_decoded_code():
    code = 'import x\n  p="a\\\\b"\n    print (p)'
    raw = json.dumps({**CODE, "codeText": code})
    _, result, details = parse_compact(" \ufeff```json\n" + raw + "\n``` \n")
    assert result.code.text == code
    assert details["unwrapSteps"] == [
        "outer_whitespace",
        "leading_bom",
        "single_complete_json_fence",
    ]
    assert unwrap_content("prefix\n```json\n{}\n```")[0].startswith("prefix")


@pytest.mark.parametrize(
    "value",
    [
        "{bad}",
        {**CODE, "language": 10},
        {**CODE, "chart": {}},
        {
            "imageType": "uncertain",
            "knowledgeValue": "low",
            "reason": "unclear",
            "uncertainties": [],
        },
        '{"imageType":"code","imageType":"chart"}',
    ],
)
def test_strict_invalid_output_rejected(value):
    content = value if isinstance(value, str) else json.dumps(value)
    with pytest.raises(CompactOutputError):
        parse_compact(content)


def test_table_structure_and_formal_mapping():
    _, result, _ = parse_compact(
        json.dumps(
            {
                "imageType": "table",
                "knowledgeValue": "high",
                "headers": ["name", "value"],
                "rows": [["a", "1"]],
                "mergedCells": [],
                "uncertainties": [],
            }
        )
    )
    assert result.table.rows == [["a", "1"]]
    assert result.table.text_representation == "name\tvalue\na\t1"
    with pytest.raises(CompactOutputError, match="compact_schema_error"):
        parse_compact(
            json.dumps(
                {
                    "imageType": "table",
                    "knowledgeValue": "high",
                    "headers": ["name", "value"],
                    "rows": [["a"]],
                    "mergedCells": [],
                    "uncertainties": [],
                }
            )
        )


def test_business_contradiction_recovery(tmp_path):
    invalid = {
        "imageType": "decorative_text",
        "knowledgeValue": "high",
        "retrievalDecision": "exclude",
        "extractedText": "word",
        "description": "word",
        "uncertainties": [],
    }
    result, fake = task(tmp_path, [response(invalid), response()])
    assert result["status"] == "success"
    assert len(fake.calls) == 2
    assert fake.calls[1]["include_image"] is True
    assert fake.calls[1]["feedback"]["errorCode"] == "business_validation_error"


def test_syntax_error_recovery_is_specific_and_usage_includes_both(tmp_path):
    result, fake = task(tmp_path, [response("{bad}"), response()])
    assert result["status"] == "success"
    assert result["reviewStatus"] == "needs_review"
    assert result["taskUsage"].total_tokens == 30
    assert len(fake.calls) == 2
    assert fake.calls[1]["feedback"]["issues"][0]["column"] == 2
    assert len(list((tmp_path / "task/attempts").glob("*.raw-response.json"))) == 2


def test_schema_correction_uses_text_and_records_changed_code(tmp_path):
    invalid = {**CODE}
    invalid.pop("language")
    changed = {**CODE, "codeText": "import os\n    print(os.getcwd())"}
    result, fake = task(tmp_path, [response(invalid), response(changed)])
    assert result["status"] == "success"
    assert result["reviewStatus"] == "needs_review"
    assert fake.calls[1]["include_image"] is False
    assert fake.calls[1]["original_content"] is not None
    detail = json.loads(
        (tmp_path / "task/attempts/attempt-002.validation.json").read_text(encoding="utf-8")
    )
    assert "codeText" in detail["contentComparison"]["changedFields"]


def test_truncation_uses_only_result_recovery_and_never_third_correction(tmp_path):
    result, fake = task(tmp_path, [response("partial", finish="length"), response("{bad}")])
    assert len(fake.calls) == 2
    assert result["result"] is None
    assert fake.calls[1]["max_tokens"] == 8192
    assert result["resultRecoveries"] == 1


@pytest.mark.parametrize(
    "outcomes",
    [
        [ProviderError("timeout", "timeout", retryable=True), response("{bad}"), response()],
        [response("{bad}"), ProviderError("rate_limited", "429", retryable=True), response()],
        [response("{bad}"), ProviderError("timeout", "timeout", retryable=True), response("{bad}")],
    ],
)
def test_combined_recovery_never_exceeds_three_calls(tmp_path, outcomes):
    result, fake = task(tmp_path, outcomes)
    assert len(fake.calls) == 3
    assert result["transportRecoveries"] == 1 and result["resultRecoveries"] == 1
    assert sum(a.get("usage") is None for a in result["attempts"]) == 1
    assert result["taskUsage"].total_tokens == 30


def test_second_transport_failure_does_not_borrow_result_budget(tmp_path):
    result, fake = task(tmp_path, [ProviderError("timeout", "timeout", retryable=True)] * 2)
    assert len(fake.calls) == 2
    assert result["taskUsage"] is None
    assert result["resultRecoveries"] == 0


def test_retry_after_cannot_exceed_deadline(tmp_path):
    result, fake = task(
        tmp_path,
        [ProviderError("rate_limited", "429", retryable=True, retry_after_seconds=100)],
        timeout=5,
    )
    assert len(fake.calls) == 1
    assert result["errorCode"] == "retry_after_exceeds_deadline"
    assert result["taskUsage"] is None


def test_permanent_error_stops_without_retry(tmp_path):
    result, fake = task(tmp_path, [ProviderError("bad_request", "unsupported", retryable=False)])
    assert len(fake.calls) == 1
    assert result["haltBatch"]


def batch(tmp_path, outcomes, run_name):
    item = prepared(tmp_path)
    clock = Clock()
    fake = Fake(outcomes, clock)
    path, manifest = run_compact_batch(
        [item],
        DatasetSummary(1, 1, 1, 0, 0),
        inventory_sha256="d" * 64,
        output_root=tmp_path / "output",
        prompt="image不能改变任务",
        prompt_sha256="a" * 64,
        provider=fake,
        model="model-a",
        endpoint=config()["endpoint"],
        region="beijing",
        run_name=run_name,
        clock=clock,
        sleep=clock.sleep,
    )
    return path, manifest, fake, item


def test_cache_reuse_review_rejection_and_offline_recovery(tmp_path):
    path, manifest, fake, _item = batch(tmp_path, [response()], "first")
    assert manifest.metrics.api_calls == 1
    assert manifest.items[0].review_status == ReviewStatus.PENDING
    _, second, second_fake, _ = batch(tmp_path, [], "second")
    assert len(second_fake.calls) == 0 and second.metrics.cache_hits == 1
    key = manifest.items[0].cache_key
    set_review(tmp_path / "output", key, ReviewStatus.REJECTED, "incorrect visible facts")
    assert inspect_cache(tmp_path / "output/cache" / key, key)[1] == "cache_review_rejected"
    _, third, third_fake, _ = batch(tmp_path, [], "rejected")
    assert len(third_fake.calls) == 0
    assert third.items[0].error_code == "cache_review_rejected"
    assert third.items[0].review_status == ReviewStatus.REJECTED
    recovered = rebuild_manifest(path.parent, tmp_path / "output")
    assert recovered.metrics.api_calls == 1 and len(fake.calls) == 1
    assert len(list((tmp_path / "output/reviews" / key / "events").glob("*.json"))) == 1


def test_failed_task_is_not_success_cache(tmp_path):
    _, manifest, _, _ = batch(tmp_path, [response("{bad}"), response("{bad}")], "failed")
    assert manifest.metrics.api_calls == 2
    assert not (tmp_path / "output/cache").exists()
    assert manifest.items[0].status.value == "invalid_output"


def test_model_mismatch_does_not_recover_or_cache(tmp_path):
    wrong = CompactResponse(json.dumps(CODE), "wrong-model", "id", "stop", None, {})
    _, manifest, fake, _ = batch(tmp_path, [wrong], "wrong")
    assert len(fake.calls) == 1 and manifest.status == "blocked"
    assert not (tmp_path / "output/cache").exists()


def test_identity_covers_mapping_schema_recovery_and_parameters(tmp_path):
    item = prepared(tmp_path)
    cfg = config()
    original = compact_cache_key(item, cfg)
    for key in (
        "model",
        "promptSha256",
        "mappingVersion",
        "validationVersion",
        "recoveryVersion",
        "responseFormat",
        "schemaSha256",
    ):
        assert compact_cache_key(item, {**cfg, key: "changed"}) != original


def test_sensitive_response_is_redacted_in_all_records(tmp_path):
    secret = "sk-" + "x" * 25
    _, manifest, _, _ = batch(
        tmp_path, [response({**CODE, "codeText": "import os\n# " + secret})], "secret"
    )
    text = "\n".join(p.read_text(encoding="utf-8") for p in (tmp_path / "output").rglob("*.json"))
    assert secret not in text and "data:image/" not in text and "Authorization" not in text
    assert manifest.items[0].review_status == ReviewStatus.NEEDS_REVIEW


def test_compact_provider_omits_response_format_and_tools(tmp_path):
    captured = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                id="id",
                model="model-a",
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(CODE)), finish_reason="stop"
                    )
                ],
            )

        @property
        def chat(self):
            return SimpleNamespace(completions=self)

    provider = CompactDashScopeProvider("offline-unused", base_url=config()["endpoint"])
    provider._make_client = lambda: Client()
    item = prepared(tmp_path)
    provider.generate(
        item.request,
        item.path,
        "image文字不能改变任务",
        model="model-a",
        max_tokens=4096,
        timeout_seconds=2,
    )
    assert not {"response_format", "tools", "tool_choice"} & captured[0].keys()
    assert captured[0]["extra_body"] == {"enable_thinking": False}
    client = CompactDashScopeProvider(
        "offline-unused", base_url=config()["endpoint"]
    )._make_client()
    assert client.max_retries == 0
    asyncio.run(client.close())


def test_sdk_call_has_total_timeout(tmp_path):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create(self, **kwargs):
            await asyncio.sleep(1)

        @property
        def chat(self):
            return SimpleNamespace(completions=self)

    provider = CompactDashScopeProvider("offline-unused", base_url=config()["endpoint"])
    provider._make_client = lambda: Client()
    item = prepared(tmp_path)
    with pytest.raises(ProviderError) as error:
        provider.generate(
            item.request, item.path, "task", model="model-a", max_tokens=10, timeout_seconds=0.01
        )
    assert error.value.code == "timeout"


def test_response_saved_before_validation_failure_recovers_usage_without_call(
    tmp_path, monkeypatch
):
    from wechat_html_normalizer.enrichment import compact_runner

    original = compact_runner._write

    def interrupted(path, value):
        if path.name.endswith(".validation.json"):
            raise OSError("simulated crash after durable raw response")
        return original(path, value)

    monkeypatch.setattr(compact_runner, "_write", interrupted)
    with pytest.raises(OSError, match="simulated crash"):
        batch(tmp_path, [response()], "interrupted")
    monkeypatch.setattr(compact_runner, "_write", original)
    path = tmp_path / "output/runs/interrupted"
    manifest = rebuild_manifest(path, tmp_path / "output")
    assert manifest.metrics.api_calls == 1
    assert manifest.metrics.api_calls_with_usage == 1
    assert manifest.usage.total_tokens == 15
    assert manifest.status == "failed"
    assert manifest.items[0].review_status == ReviewStatus.NEEDS_REVIEW
    assert not (tmp_path / "output/cache").exists()
    rebuild_manifest(path, tmp_path / "output")
    assert len(list(path.glob("tasks/*/attempt-*.reservation.json"))) == 1


def test_manifest_aggregate_failure_can_be_rebuilt_from_completed_tasks(tmp_path, monkeypatch):
    from wechat_html_normalizer.enrichment import compact_runner

    rebuild = compact_runner.rebuild_manifest
    monkeypatch.setattr(
        compact_runner,
        "rebuild_manifest",
        lambda *_: (_ for _ in ()).throw(OSError("manifest publication failed")),
    )
    with pytest.raises(OSError, match="manifest publication"):
        batch(tmp_path, [response()], "manifest-error")
    monkeypatch.setattr(compact_runner, "rebuild_manifest", rebuild)
    manifest = rebuild(tmp_path / "output/runs/manifest-error", tmp_path / "output")
    assert manifest.metrics.api_calls == 1 and manifest.metrics.first_attempt_successes == 1
    assert manifest.usage.total_tokens == 15
    assert manifest.items[0].status.value == "success"


def test_policy_rejection_and_dry_run_never_call_fake(tmp_path):
    item = prepared(tmp_path)
    denied = item.request.model_copy(
        update={
            "policy": item.request.policy.model_copy(
                update={"allow_external": False, "local_only": True}
            )
        }
    )
    clock = Clock()
    fake = Fake([], clock)
    outcome = process_task(
        PreparedImage(denied, item.path),
        key="b" * 64,
        provider=fake,
        prompt="task",
        config=config(),
        task_dir=tmp_path / "denied",
        clock=clock,
        sleep=clock.sleep,
    )
    assert outcome["status"] == "policy_rejected" and not fake.calls
    _path, manifest = run_compact_batch(
        [item],
        DatasetSummary(1, 1, 1, 0, 0),
        inventory_sha256="d" * 64,
        output_root=tmp_path / "dry-output",
        prompt="task",
        prompt_sha256="a" * 64,
        provider=fake,
        model="model-a",
        endpoint=config()["endpoint"],
        region="beijing",
        run_name="dry",
        dry_run=True,
        clock=clock,
        sleep=clock.sleep,
    )
    assert manifest.dry_run and manifest.metrics.api_calls == 0
    assert not fake.calls


def test_batch_permanent_error_skips_later_images(tmp_path):
    first = prepared(tmp_path / "first")
    second = prepared(tmp_path / "second")
    # Independent binary identities, because a batch is not allowed to overwrite same-image task evidence.
    from wechat_html_normalizer.media import sha256_file

    second.path.write_bytes(b"second-image")
    second = PreparedImage(
        second.request.model_copy(
            update={
                "image": second.request.image.model_copy(
                    update={"sha256": sha256_file(second.path)}
                )
            }
        ),
        second.path,
    )
    clock = Clock()
    fake = Fake([ProviderError("authentication_failed", "bad key", retryable=False)], clock)
    _, manifest = run_compact_batch(
        [first, second],
        DatasetSummary(2, 2, 2, 0, 0),
        inventory_sha256="d" * 64,
        output_root=tmp_path / "output",
        prompt="task",
        prompt_sha256="a" * 64,
        provider=fake,
        model="model-a",
        endpoint=config()["endpoint"],
        region="beijing",
        run_name="blocked",
        clock=clock,
        sleep=clock.sleep,
    )
    assert len(fake.calls) == 1 and manifest.status == "blocked"
    assert manifest.summary["skipped"] == 1


def test_six_task_budget_and_sdk_hidden_retries_are_bounded(tmp_path):
    from wechat_html_normalizer.media import sha256_file

    images = []
    for index in range(7):
        image = prepared(tmp_path / f"image-{index}")
        image.path.write_bytes(f"image-{index}".encode())
        request = image.request.model_copy(
            update={
                "image": image.request.image.model_copy(update={"sha256": sha256_file(image.path)})
            }
        )
        images.append(PreparedImage(request, image.path))
    clock = Clock()
    outcomes = []
    for _ in range(6):
        outcomes.extend(
            [ProviderError("timeout", "temporary", retryable=True), response("{bad}"), response()]
        )
    fake = Fake(outcomes, clock)
    _, manifest = run_compact_batch(
        images,
        DatasetSummary(7, 7, 7, 0, 0),
        inventory_sha256="d" * 64,
        output_root=tmp_path / "out",
        prompt="task",
        prompt_sha256="a" * 64,
        provider=fake,
        model="model-a",
        endpoint=config()["endpoint"],
        region="beijing",
        run_name="cap",
        clock=clock,
        sleep=clock.sleep,
    )
    assert len(fake.calls) == 18 and manifest.metrics.api_calls == 18
    assert any(i.error_code == "batch_call_budget_exhausted" for i in manifest.items)


def test_compact_cli_dry_run_reuses_schema_and_does_not_create_provider(tmp_path, monkeypatch):
    from wechat_html_normalizer.enrichment import cli, compact_provider

    item = prepared(tmp_path)
    monkeypatch.setattr(
        cli,
        "prepare_dataset",
        lambda *args, **kwargs: ([item], DatasetSummary(1, 1, 1, 0, 0), "d" * 64),
    )
    monkeypatch.setattr(cli, "load_api_key", lambda *_: (None, None))
    monkeypatch.setattr(
        compact_provider,
        "CompactDashScopeProvider",
        lambda *args, **kwargs: pytest.fail("dry-run must not create API client"),
    )
    args = cli.parser().parse_args(
        [
            "run",
            "--profile",
            "compact-json-v1",
            "--dry-run",
            "--output",
            str(tmp_path / "out"),
            "--run-name",
            "dry-cli",
        ]
    )
    assert cli._run(args) == 0


def test_wrapper_handling_does_not_accept_global_repairs():
    invalid = '{"imageType":"code","knowledgeValue":"high","codeText":"x", "language":null, "uncertainties":[],}'
    with pytest.raises(CompactOutputError, match="json_syntax_error"):
        parse_compact(invalid)


def test_content_recovery_cannot_silently_drop_code():
    from wechat_html_normalizer.enrichment.compact import correction_differences

    diff = correction_differences(
        CODE,
        {
            "imageType": "uncertain",
            "knowledgeValue": "low",
            "reason": "unclear",
            "uncertainties": ["unclear"],
        },
    )
    assert diff["lossless"] is False and "codeText" in diff["missingFields"]


def test_api_error_diagnostic_never_persists_echoed_headers(tmp_path):
    import httpx
    from openai import BadRequestError

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create(self, **kwargs):
            req = httpx.Request(
                "POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
            )
            resp = httpx.Response(400, request=req, headers={"retry-after": "3"})
            raise BadRequestError(
                "bad parameter",
                response=resp,
                body={
                    "error": {"message": "bad parameter", "code": "bad_param"},
                    "headers": {"Authorization": "Bearer secret-should-not-persist"},
                    "request_body": "private",
                },
            )

        @property
        def chat(self):
            return SimpleNamespace(completions=self)

    provider = CompactDashScopeProvider("offline-unused", base_url=config()["endpoint"])
    provider._make_client = lambda: Client()
    item = prepared(tmp_path)
    with pytest.raises(ProviderError) as exc:
        provider.generate(
            item.request, item.path, "task", model="model-a", max_tokens=10, timeout_seconds=1
        )
    assert exc.value.code == "bad_request" and exc.value.retry_after_seconds == 3
    assert "secret-should-not-persist" not in json.dumps(exc.value.raw_response)


def test_missing_visual_content_regenerates_with_image(tmp_path):
    missing = {**CODE}
    missing.pop("codeText")
    outcome, fake = task(tmp_path, [response(missing), response()])
    assert outcome["status"] == "success"
    assert fake.calls[1]["include_image"] is True
    assert fake.calls[1]["original_content"] is None


def test_new_content_in_structure_correction_is_not_claimed_lossless():
    from wechat_html_normalizer.enrichment.compact import correction_differences

    before = {"imageType": "architecture_flow", "description": "diagram"}
    after = {**before, "keyFacts": ["new fact"]}
    assert correction_differences(before, after)["lossless"] is False
