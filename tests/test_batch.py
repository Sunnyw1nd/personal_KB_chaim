from __future__ import annotations

import io
import json
import sys
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from wechat_html_normalizer import BatchNormalizationError, normalize_sample_archives
from wechat_html_normalizer.cli import _configure_console_utf8, main


def _archive_article(archive: Path, html: Path, resources: Path) -> None:
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        output.write(html, html.name)
        for path in sorted(resources.rglob("*")):
            if path.is_file():
                output.write(path, f"{resources.name}/{path.relative_to(resources).as_posix()}")


def _archive_unknown(archive: Path) -> None:
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        output.writestr("unknown.html", "<html><body><article>未知来源</article></body></html>")
        output.writestr("unknown_files/item", b"item")


def test_batch_normalizes_every_zip_and_reuses_cache(
    tmp_path: Path,
    saved_article: tuple[Path, Path, Path],
    zhihu_article: tuple[Path, Path, Path],
) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    wechat_html, wechat_resources, _ = saved_article
    zhihu_html, zhihu_resources, _ = zhihu_article
    _archive_article(samples / "a-wechat.zip", wechat_html, wechat_resources)
    _archive_article(samples / "b-zhihu.ZIP", zhihu_html, zhihu_resources)
    archive_hashes = {
        path.name: sha256(path.read_bytes()).hexdigest() for path in samples.iterdir()
    }
    output = tmp_path / "batch-output"

    first = normalize_sample_archives(
        samples,
        tmp_path / ".work",
        output,
        imported_at="2026-08-24T00:00:00Z",
    )
    assert first.status == "completed"
    assert {
        key: first.statistics[key]
        for key in [
            "archives",
            "normalized",
            "failed",
            "cache_hits",
            "extracted",
            "documents_with_warnings",
        ]
    } == {
        "archives": 2,
        "normalized": 2,
        "failed": 0,
        "cache_hits": 0,
        "extracted": 2,
        "documents_with_warnings": 2,
    }
    assert first.statistics["created"] == 2
    assert [item.archive_file_name for item in first.items] == [
        "a-wechat.zip",
        "b-zhihu.ZIP",
    ]
    assert {item.adapter for item in first.items} == {
        "wechat_saved_html",
        "zhihu_column_saved_html",
    }
    for item in first.items:
        assert item.status == "normalized"
        assert item.output_path and (output / item.output_path / "document.json").is_file()
    before_documents = {
        item.document_id: (output / str(item.output_path) / "document.json").read_bytes()
        for item in first.items
    }

    second = normalize_sample_archives(samples, tmp_path / ".work", output)
    assert second.status == "completed"
    assert second.statistics["cache_hits"] == 2
    assert second.statistics["extracted"] == 0
    assert before_documents == {
        item.document_id: (output / str(item.output_path) / "document.json").read_bytes()
        for item in second.items
    }
    report = json.loads(second.report_path.read_text(encoding="utf-8"))
    assert report == second.as_dict()
    serialized_report = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in serialized_report and ".work" not in serialized_report
    assert archive_hashes == {
        path.name: sha256(path.read_bytes()).hexdigest() for path in samples.iterdir()
    }


def test_batch_continues_after_an_unsupported_sample(
    tmp_path: Path,
    saved_article: tuple[Path, Path, Path],
) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    html, resources, _ = saved_article
    _archive_article(samples / "valid.zip", html, resources)
    _archive_unknown(samples / "unsupported.zip")

    summary = normalize_sample_archives(
        samples,
        tmp_path / ".work",
        tmp_path / "output",
        imported_at="2026-08-24T00:00:00Z",
    )
    assert summary.status == "completed_with_errors"
    assert summary.statistics["normalized"] == 1
    assert summary.statistics["failed"] == 1
    failed = next(item for item in summary.items if item.status == "failed")
    assert failed.archive_file_name == "unsupported.zip"
    assert failed.error_code == "source_unsupported"
    assert next(item for item in summary.items if item.status == "normalized").document_id


def test_batch_cli_needs_no_article_file_names(
    tmp_path: Path,
    saved_article: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    html, resources, _ = saved_article
    _archive_article(samples / "one.zip", html, resources)
    output = tmp_path / "output"

    exit_code = main(
        [
            "--normalize-samples",
            "--samples-root",
            str(samples),
            "--work-root",
            str(tmp_path / ".work"),
            "--output-directory",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0 and not captured.err
    assert payload["statistics"]["normalized"] == 1
    assert payload["items"][0]["archiveFileName"] == "one.zip"
    assert (output / "batch-processing-report.json").is_file()


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("missing", "samples_root_invalid"),
        ("empty", "sample_archives_missing"),
        ("overlap", "batch_output_overlaps_samples"),
        ("work_overlap", "batch_output_overlaps_work_cache"),
    ],
)
def test_batch_input_errors_are_explicit(tmp_path: Path, case: str, code: str) -> None:
    samples = tmp_path / "samples"
    if case != "missing":
        samples.mkdir()
    if case == "overlap":
        _archive_unknown(samples / "sample.zip")
    if case == "work_overlap":
        _archive_unknown(samples / "sample.zip")
    output = (
        samples / "output"
        if case == "overlap"
        else tmp_path / ".work" / "output"
        if case == "work_overlap"
        else tmp_path / "output"
    )
    with pytest.raises(BatchNormalizationError) as caught:
        normalize_sample_archives(samples, tmp_path / ".work", output)
    assert caught.value.code == code


def test_cli_configures_real_windows_text_streams_as_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    _configure_console_utf8()
    assert stdout.encoding.casefold() == "utf-8"
    assert stderr.encoding.casefold() == "utf-8"
