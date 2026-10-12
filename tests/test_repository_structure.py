from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from wechat_html_normalizer import normalize_sample_archives
from wechat_html_normalizer.application import importing
from wechat_html_normalizer.batch import BatchItemResult, BatchNormalizationError
from wechat_html_normalizer.enrichment import cli, settings


def test_legacy_imports_are_the_same_batch_implementation():
    assert normalize_sample_archives is importing.normalize_sample_archives
    assert BatchNormalizationError is importing.BatchNormalizationError
    assert BatchItemResult is importing.BatchItemResult


def test_prompt_configuration_imports_keep_exact_identity():
    assert cli.DEFAULT_PROMPT_PATH == settings.DEFAULT_PROMPT_PATH
    assert cli.FROZEN_PROMPT_SHA256 is settings.FROZEN_PROMPT_SHA256
    assert settings.DEFAULT_PROMPT_PATH.parent.name == "prompts"


def test_production_has_no_dependency_on_experiments_or_presentation():
    root = Path(__file__).parents[1] / "src/wechat_html_normalizer"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith("experiments") for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("experiments"), path
                if path.name == "observer.py":
                    assert node.module != "cli", "observation must not depend on CLI parsing"


def test_inventory_snapshot_does_not_read_credentials_or_hash_observer(tmp_path, monkeypatch):
    from tools import repository_inventory as audit

    (tmp_path / "experiments").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config/.env").write_text("private fixture", encoding="utf-8")
    (tmp_path / "config/policy.json").write_text("{}", encoding="utf-8")
    observer = tmp_path / "output/image-enrichment/observer"
    observer.mkdir(parents=True)
    (observer / "index.html").write_text("derived", encoding="utf-8")
    real_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path.name != ".env", "the audit must not open credential contents"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    snapshot = audit.protected(tmp_path)
    assert list(snapshot) == ["config/policy.json"]


def test_inventory_report_cannot_escape_work_directory(tmp_path, monkeypatch):
    from tools import repository_inventory as audit

    target = tmp_path / ".work/../samples/report.json"
    monkeypatch.setattr(
        sys, "argv", ["inventory", "--root", str(tmp_path), "--output", str(target)]
    )
    with pytest.raises(SystemExit) as caught:
        audit.main()
    assert caught.value.code == 2
    assert not (tmp_path / "samples/report.json").exists()
