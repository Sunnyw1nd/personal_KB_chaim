from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.qwen_output_format_ablation import runner
from wechat_html_normalizer.enrichment.models import DataPolicy, ImageContext, ImageIdentity


def item(path: Path, sha: str = "a" * 64):
    return SimpleNamespace(
        path=path,
        request=SimpleNamespace(
            image=ImageIdentity(
                sha256=sha,
                relativePath=path.name,
                mediaType="image/webp",
                actualFormat="WEBP",
                sizeBytes=4,
                width=10,
                height=10,
            ),
            context=ImageContext(available=False),
            context_fingerprint_sha256="b" * 64,
            policy=DataPolicy(
                allowExternal=True,
                localOnly=False,
                allowedBackendTypes=["cloud"],
                classification="external_test_sample",
                policyVersion="test",
                reasonCode="allowed",
            ),
        ),
    )


def test_bc_messages_identical_and_cd_omit_format_and_tools(tmp_path):
    prepared = item(tmp_path / "image.webp")
    for sample in ("code", "diagram"):
        cells = {arm: runner.make_cell(prepared, sample, arm) for arm in "ABCD"}
        b = runner.generation_kwargs(cells["B"], "memory-image")
        c = runner.generation_kwargs(cells["C"], "memory-image")
        assert b.pop("response_format")["json_schema"]["strict"] is True
        assert b == c
        for arm in "CD":
            kwargs = runner.generation_kwargs(cells[arm], "memory-image")
            assert not {"response_format", "tools", "tool_choice"} & kwargs.keys()
        assert len({c["identitySha256"] for c in cells.values()}) == 4


def test_json_escapes_become_multiline_code_and_free_fence_conversion(tmp_path):
    cell = runner.make_cell(item(tmp_path / "image.webp"), "code", "C")
    code = 'import os.path\n    path="a\\\\b"'
    response = {
        "content": json.dumps({"codeText": code, "language": "python", "uncertainties": []}),
        "model": runner.DEFAULT_MODEL,
        "finishReason": "stop",
    }
    analysis = runner.parse_content(cell, response)
    assert analysis["formalCodeText"] == code
    assert len(analysis["formalCodeText"].splitlines()) == 2
    cell["arm"] = "D"
    response["content"] = "```python\n" + code + "\n```"
    assert runner.parse_content(cell, response)["formalCodeText"] == code


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("not-json", "invalid"),
        ("```json\n{}\n```", "invalid"),
        ('{"codeText":"x"}', "valid"),
    ],
)
def test_format_failure_separate_from_content_review(tmp_path, content, expected):
    cell = runner.make_cell(item(tmp_path / "image.webp"), "code", "C")
    analysis = runner.parse_content(
        cell, {"content": content, "model": runner.DEFAULT_MODEL, "finishReason": "stop"}
    )
    assert analysis["jsonSyntax"] == expected
    assert analysis["quality"] == "pending_review"
    assert analysis["status"] == "invalid_output"


def test_length_and_model_mismatch_not_success(tmp_path):
    cell = runner.make_cell(item(tmp_path / "image.webp"), "code", "D")
    assert (
        runner.parse_content(
            cell, {"content": "x", "model": runner.DEFAULT_MODEL, "finishReason": "length"}
        )["status"]
        == "truncated"
    )
    assert (
        runner.parse_content(cell, {"content": "x", "model": "other", "finishReason": "stop"})[
            "status"
        ]
        == "model_mismatch"
    )


class FakeClient:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"codeText":"a\\nb","language":null,"uncertainties":[]}'
                    ),
                    finish_reason="stop",
                )
            ],
            id=f"response-{len(self.calls)}",
            model=runner.DEFAULT_MODEL,
            usage=SimpleNamespace(
                model_dump=lambda **_: {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                }
            ),
        )


def test_eight_call_budget_resume_and_offline_recovery(tmp_path, monkeypatch):
    experiment = tmp_path / "experiment"
    monkeypatch.setattr(runner, "ROOT", experiment)
    # Keep real Prompt sources available while directing run artifacts to a sandbox.
    original_make = runner.make_cell
    original_root = Path(__file__).parents[1]
    monkeypatch.setattr(
        runner,
        "make_cell",
        lambda *args: _make_with_prompts(original_make, original_root, experiment, *args),
    )
    images = {}
    for sample in runner.SAMPLES:
        path = tmp_path / f"{sample}.webp"
        path.write_bytes(b"test")
        images[sample] = item(path, runner.file_sha(path))
    monkeypatch.setattr(runner, "get_dataset", lambda: (images, None, "inventory"))
    monkeypatch.setattr(runner, "authority_snapshot", dict)
    args = argparse.Namespace(
        run_name="run", base_url=runner.DEFAULT_DASHSCOPE_BASE_URL, allow_external=True
    )
    assert runner.dry_run(args) == 0
    fake = FakeClient()
    runner.execute(args, client=fake)
    assert len(fake.calls) == 8
    runner.execute(args, client=fake)
    assert len(fake.calls) == 8
    runner.recover(args)
    assert len(fake.calls) == 8
    folder = experiment / "runs/run"
    manifest = json.loads((folder / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["receivedUsage"]["total_tokens"] == 40
    all_text = "\n".join(p.read_text(encoding="utf-8") for p in folder.rglob("*.json"))
    assert "data:image/" not in all_text
    assert "Authorization" not in all_text
    assert "referenceAnswer" not in (folder / "plan.json").read_text(encoding="utf-8")


def _make_with_prompts(make, prompt_root, artifact_root, *args):
    previous = runner.ROOT
    runner.ROOT = prompt_root
    try:
        return make(*args)
    finally:
        runner.ROOT = previous


def test_response_evidence_survives_manifest_failure(tmp_path, monkeypatch):
    folder = tmp_path / "run"
    cell = runner.make_cell(item(tmp_path / "a.webp"), "code", "C")
    directory = folder / "cells" / cell["cellId"]
    runner.write_json(
        directory / "response.json",
        {
            "content": '{"codeText":"a\\nb","language":null,"uncertainties":[]}',
            "elapsedMs": 3,
            "model": runner.DEFAULT_MODEL,
            "finishReason": "stop",
            "usage": {"total_tokens": 9},
        },
    )
    runner.write_json(folder / "authority-before.json", {})
    runner.write_json(folder / "plan.json", {})
    monkeypatch.setattr(runner, "authority_snapshot", dict)
    manifest = runner.summarize(folder, {"cells": [cell]})
    assert manifest["receivedUsage"]["total_tokens"] == 9
    assert (directory / "content.txt").exists()
    assert (
        json.loads((directory / "analysis.json").read_text(encoding="utf-8"))["formalCodeText"]
        == "a\nb"
    )


def test_sanitization_is_applied_to_raw_model_text(tmp_path):
    path = tmp_path / "response.json"
    runner.write_json(path, {"content": "sk-abcdefghijklmnopqrstuv data:image/png;base64,AAAA"})
    text = path.read_text(encoding="utf-8")
    assert "abcdefghijklmnopqrstuv" not in text
    assert "data:image/" not in text


def test_client_initialization_ignores_unused_socks_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:9999")
    monkeypatch.setenv("all_proxy", "socks5h://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    client = runner.make_client("offline-unused", runner.DEFAULT_DASHSCOPE_BASE_URL)
    assert client.max_retries == 0
    client.close()


def test_run_without_external_consent_never_constructs_client(tmp_path, monkeypatch):
    folder = tmp_path / "runs/run"
    prepared = item(tmp_path / "image.webp")
    cells = [runner.make_cell(prepared, sample, arm) for sample, arm in runner.ORDER]
    runner.write_json(folder / "plan.json", {"cells": cells})
    monkeypatch.setattr(runner, "run_dir", lambda _: folder)
    monkeypatch.setattr(runner, "make_client", lambda *_: pytest.fail("no client allowed"))
    with pytest.raises(ValueError, match="explicit --allow-external"):
        runner.execute(argparse.Namespace(run_name="run", allow_external=False))


def test_review_preserves_indentation_differences():
    from experiments.qwen_output_format_ablation.review import code_comparison

    result = code_comparison("import x\n  # comment", "import x\n# comment")
    assert not result["exactMatch"]
    assert result["characterDifferences"]
    assert result["actualLineCount"] == 2


def test_verbatim_code_evidence_uses_nonformatting_fence():
    from experiments.qwen_output_format_ablation.review import fence

    code = "index_chunk=pickle.load(f)\n  # comment\n    print (chunk)"
    view = fence(code, "python")
    assert view.startswith("```text\n")
    assert code in view
