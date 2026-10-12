from __future__ import annotations

import json
import stat
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from wechat_html_normalizer.sample_cache import (
    ArchiveLimits,
    SamplePreparationError,
    prepare_sample,
)


def _write_sample_zip(path: Path, *, body: str = "正文", extra_pairs: int = 0) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "article.html",
            f'<meta charset="utf-8"><div id="js_content"><p>{body}</p></div>',
        )
        archive.writestr("article_files/image.bin", b"image")
        for index in range(extra_pairs):
            archive.writestr(f"nested{index}/other.html", "<html></html>")
            archive.writestr(f"nested{index}/other_files/item", b"item")


def _prepare(archive: Path, samples: Path, work: Path, **kwargs: object):
    return prepare_sample(archive, samples, work, **kwargs)


def test_first_prepare_extracts_and_second_prepare_hits_verified_cache(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    work = tmp_path / ".work"
    samples.mkdir()
    archive = samples / "fixture.zip"
    _write_sample_zip(archive)
    before = sha256(archive.read_bytes()).hexdigest()

    first = _prepare(archive, samples, work)
    assert first.cache_hit is False
    assert first.html_path.is_file() and first.resource_directory.is_dir()
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["archiveSha256"] == before
    assert manifest["htmlPath"] == "article.html"

    marker_mtime = first.manifest_path.stat().st_mtime_ns
    second = _prepare(archive, samples, work)
    assert second.cache_hit is True
    assert second.extraction_root == first.extraction_root
    assert second.manifest_path.stat().st_mtime_ns == marker_mtime
    assert sha256(archive.read_bytes()).hexdigest() == before


def test_changed_zip_uses_a_new_content_addressed_cache(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    work = tmp_path / ".work"
    samples.mkdir()
    archive = samples / "fixture.zip"
    _write_sample_zip(archive, body="版本一")
    first = _prepare(archive, samples, work)
    _write_sample_zip(archive, body="版本二")
    second = _prepare(archive, samples, work)
    assert second.cache_hit is False
    assert second.extraction_root != first.extraction_root
    assert "版本二" in second.html_path.read_text(encoding="utf-8")
    assert first.extraction_root.is_dir()


@pytest.mark.parametrize(
    "damage", ["missing_manifest", "incomplete_manifest", "missing_file", "changed_file"]
)
def test_invalid_cache_is_not_reused_and_is_safely_rebuilt(tmp_path: Path, damage: str) -> None:
    samples = tmp_path / "samples"
    work = tmp_path / ".work"
    samples.mkdir()
    archive = samples / "fixture.zip"
    _write_sample_zip(archive)
    first = _prepare(archive, samples, work)
    if damage == "missing_manifest":
        first.manifest_path.unlink()
    elif damage == "incomplete_manifest":
        payload = json.loads(first.manifest_path.read_text(encoding="utf-8"))
        payload["status"] = "incomplete"
        first.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        target = first.resource_directory / "image.bin"
        if damage == "missing_file":
            target.unlink()
        else:
            target.write_bytes(b"tampered")

    rebuilt = _prepare(archive, samples, work)
    assert rebuilt.cache_hit is False
    assert (rebuilt.resource_directory / "image.bin").read_bytes() == b"image"
    assert json.loads(rebuilt.manifest_path.read_text(encoding="utf-8"))["status"] == "complete"


def test_interrupted_temporary_directory_is_never_treated_as_cache(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    work = tmp_path / ".work"
    samples.mkdir()
    archive = samples / "fixture.zip"
    _write_sample_zip(archive)
    stale = work / "samples" / ".fixture-dead.tmp-interrupted"
    stale.mkdir(parents=True)
    (stale / "extracted").mkdir()
    result = _prepare(archive, samples, work)
    assert result.cache_hit is False
    assert stale.is_dir()


@pytest.mark.parametrize(
    ("entry_name", "code"),
    [
        ("../escape.txt", "archive_unsafe_path"),
        ("/absolute.txt", "archive_unsafe_path"),
        ("C:/drive.txt", "archive_unsafe_path"),
    ],
)
def test_unsafe_archive_paths_fail_before_writing(
    tmp_path: Path, entry_name: str, code: str
) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "unsafe.zip"
    with ZipFile(archive, "w") as source:
        source.writestr(entry_name, b"unsafe")
        source.writestr("article.html", "<html></html>")
        source.writestr("article_files/item", b"item")
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work")
    assert caught.value.code == code
    assert not list((tmp_path / ".work" / "samples").glob("*/extracted/escape.txt"))


def test_windows_case_and_trailing_space_collision_is_rejected(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "collision.zip"
    with ZipFile(archive, "w") as source:
        source.writestr("A.txt", b"one")
        source.writestr("a.TXT ", b"two")
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work")
    assert caught.value.code == "archive_path_conflict"


@pytest.mark.parametrize("parent_first", [False, True])
def test_file_and_directory_name_conflict_is_rejected(
    tmp_path: Path,
    parent_first: bool,
) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "file-directory-conflict.zip"
    entries = [("same", b"file"), ("same/child", b"child")]
    if not parent_first:
        entries.reverse()
    with ZipFile(archive, "w") as source:
        for name, content in entries:
            source.writestr(name, content)
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work")
    assert caught.value.code == "archive_path_conflict"


def test_symbolic_link_entry_is_rejected(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "symlink.zip"
    link = ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(archive, "w") as source:
        source.writestr(link, "target")
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work")
    assert caught.value.code == "archive_special_entry"


def test_encrypted_flag_is_rejected_before_read(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "encrypted.zip"
    with ZipFile(archive, "w", ZIP_STORED) as source:
        source.writestr("article.html", "<html></html>")
    data = bytearray(archive.read_bytes())
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    data[local + 6 : local + 8] = (
        int.from_bytes(data[local + 6 : local + 8], "little") | 1
    ).to_bytes(2, "little")
    data[central + 8 : central + 10] = (
        int.from_bytes(data[central + 8 : central + 10], "little") | 1
    ).to_bytes(2, "little")
    archive.write_bytes(data)
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work")
    assert caught.value.code == "archive_encrypted_entry"


def test_limits_and_compression_ratio_are_enforced(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "large.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as source:
        source.writestr("article.html", b"0" * 10_000)
        source.writestr("article_files/item", b"item")
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(
            archive,
            samples,
            tmp_path / ".work",
            limits=ArchiveLimits(
                max_entries=10,
                max_file_bytes=20_000,
                max_total_bytes=30_000,
                max_compression_ratio=2,
            ),
        )
    assert caught.value.code == "archive_compression_ratio_limit"


@pytest.mark.parametrize(
    ("limits", "code"),
    [
        (ArchiveLimits(max_entries=1), "archive_entry_limit"),
        (ArchiveLimits(max_file_bytes=4), "archive_file_size_limit"),
        (ArchiveLimits(max_total_bytes=8), "archive_total_size_limit"),
    ],
)
def test_entry_and_size_limits_are_enforced(
    tmp_path: Path,
    limits: ArchiveLimits,
    code: str,
) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "limits.zip"
    _write_sample_zip(archive, body="足够长的正文")
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work", limits=limits)
    assert caught.value.code == code


def test_crc_corruption_is_reported_and_never_published(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "corrupt.zip"
    with ZipFile(archive, "w", ZIP_STORED) as source:
        source.writestr("article.html", b"<html>body</html>")
        source.writestr("article_files/item", b"item")
    data = bytearray(archive.read_bytes())
    local = data.index(b"PK\x03\x04")
    name_length = int.from_bytes(data[local + 26 : local + 28], "little")
    extra_length = int.from_bytes(data[local + 28 : local + 30], "little")
    payload_start = local + 30 + name_length + extra_length
    data[payload_start] ^= 0x01
    archive.write_bytes(data)

    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work")
    assert caught.value.code == "archive_corrupt"
    cache_root = tmp_path / ".work" / "samples"
    assert not list(cache_root.glob("*/.sample-extraction.json"))


@pytest.mark.parametrize(
    ("extra_pairs", "code"),
    [(0, "archive_article_pair_missing"), (2, "archive_article_pair_ambiguous")],
)
def test_missing_or_ambiguous_article_pair_is_explicit(
    tmp_path: Path, extra_pairs: int, code: str
) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = samples / "pair.zip"
    if extra_pairs == 0:
        with ZipFile(archive, "w") as source:
            source.writestr("orphan.html", "<html></html>")
    else:
        _write_sample_zip(archive, extra_pairs=extra_pairs)
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work")
    assert caught.value.code == code


def test_archive_outside_samples_root_is_refused(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    archive = tmp_path / "outside.zip"
    _write_sample_zip(archive)
    with pytest.raises(SamplePreparationError) as caught:
        _prepare(archive, samples, tmp_path / ".work")
    assert caught.value.code == "archive_outside_samples"
