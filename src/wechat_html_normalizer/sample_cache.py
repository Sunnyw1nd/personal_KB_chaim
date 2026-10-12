from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo

STRATEGY_VERSION = "1"
MANIFEST_NAME = ".sample-extraction.json"
WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 2_000
    max_file_bytes: int = 50 * 1024 * 1024
    max_total_bytes: int = 250 * 1024 * 1024
    max_compression_ratio: float = 200.0


@dataclass(frozen=True)
class PreparedSample:
    archive_path: Path
    archive_sha256: str
    extraction_root: Path
    html_path: Path
    resource_directory: Path
    cache_hit: bool
    manifest_path: Path
    html_archive_path: str
    resource_archive_path: str
    warnings: tuple[str, ...] = ()


class SamplePreparationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_cache_name(archive: Path, archive_sha256: str) -> str:
    stem = unicodedata.normalize("NFKC", archive.stem)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or "sample"
    return f"{stem[:48]}-{archive_sha256[:16]}"


def _entry_path(info: ZipInfo) -> PurePosixPath:
    raw = info.filename.replace("\\", "/")
    if not raw or raw.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", raw):
        raise SamplePreparationError(
            "archive_unsafe_path", f"Unsafe absolute archive path: {info.filename!r}"
        )
    raw_parts = raw.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise SamplePreparationError(
            "archive_unsafe_path", f"Unsafe archive path component: {info.filename!r}"
        )
    for part in raw_parts:
        if any(ord(character) < 32 for character in part) or ":" in part:
            raise SamplePreparationError(
                "archive_unsafe_path", f"Unsafe Windows path component: {info.filename!r}"
            )
        normalized = unicodedata.normalize("NFC", part).rstrip(" .")
        if not normalized:
            raise SamplePreparationError(
                "archive_path_conflict",
                f"Archive path becomes empty under Windows rules: {info.filename!r}",
            )
        if normalized.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise SamplePreparationError(
                "archive_unsafe_path", f"Reserved Windows path component: {info.filename!r}"
            )
    return PurePosixPath(*raw_parts)


def _windows_key(path: PurePosixPath) -> str:
    return "/".join(
        unicodedata.normalize("NFC", part).rstrip(" .").casefold() for part in path.parts
    )


def _is_special_entry(info: ZipInfo) -> bool:
    if info.create_system != 3:
        return False
    file_type = (info.external_attr >> 16) & 0o170000
    return file_type not in {0, 0o040000, 0o100000}


def _validate_entries(
    infos: list[ZipInfo], limits: ArchiveLimits
) -> list[tuple[ZipInfo, PurePosixPath]]:
    if len(infos) > limits.max_entries:
        raise SamplePreparationError(
            "archive_entry_limit", "Archive exceeds the configured entry count limit."
        )
    checked: list[tuple[ZipInfo, PurePosixPath]] = []
    seen: dict[str, tuple[str, bool]] = {}
    total_size = 0
    file_keys: set[str] = set()
    for info in infos:
        if info.flag_bits & 0x1:
            raise SamplePreparationError(
                "archive_encrypted_entry", f"Encrypted entry is not accepted: {info.filename!r}"
            )
        if _is_special_entry(info):
            raise SamplePreparationError(
                "archive_special_entry",
                f"Symbolic links and special entries are not accepted: {info.filename!r}",
            )
        relative = _entry_path(info)
        key = _windows_key(relative)
        is_directory = info.is_dir()
        if key in seen:
            raise SamplePreparationError(
                "archive_path_conflict",
                f"Archive paths conflict under Windows rules: {seen[key][0]!r} and {info.filename!r}",
            )
        if not is_directory and any(existing.startswith(f"{key}/") for existing in seen):
            raise SamplePreparationError(
                "archive_path_conflict",
                f"A file is also used as a parent directory: {info.filename!r}",
            )
        for index in range(1, len(relative.parts)):
            if (
                "/".join(_windows_key(PurePosixPath(*relative.parts[:index])).split("/"))
                in file_keys
            ):
                raise SamplePreparationError(
                    "archive_path_conflict",
                    f"A file is also used as a parent directory: {info.filename!r}",
                )
        seen[key] = (info.filename, is_directory)
        if not is_directory:
            file_keys.add(key)
            if info.file_size > limits.max_file_bytes:
                raise SamplePreparationError(
                    "archive_file_size_limit",
                    f"Archive entry exceeds the per-file limit: {info.filename!r}",
                )
            total_size += info.file_size
            if total_size > limits.max_total_bytes:
                raise SamplePreparationError(
                    "archive_total_size_limit",
                    "Archive exceeds the configured total extraction size limit.",
                )
            if info.file_size and not info.compress_size:
                raise SamplePreparationError(
                    "archive_compression_ratio_limit",
                    f"Archive entry has an invalid compression ratio: {info.filename!r}",
                )
            if (
                info.file_size
                and info.file_size / info.compress_size > limits.max_compression_ratio
            ):
                raise SamplePreparationError(
                    "archive_compression_ratio_limit",
                    f"Archive entry exceeds the compression ratio limit: {info.filename!r}",
                )
        checked.append((info, relative))
    return checked


def _find_article_pair(extraction_root: Path) -> tuple[Path, Path]:
    candidates: list[tuple[Path, Path]] = []
    for html_path in sorted(
        (
            path
            for path in extraction_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".html", ".htm"}
        ),
        key=lambda path: path.relative_to(extraction_root).as_posix(),
    ):
        resource_directory = html_path.with_name(f"{html_path.stem}_files")
        if resource_directory.is_dir():
            candidates.append((html_path, resource_directory))
    if not candidates:
        raise SamplePreparationError(
            "archive_article_pair_missing",
            "Archive does not contain an HTML file with a matching _files directory.",
        )
    if len(candidates) != 1:
        names = [html.relative_to(extraction_root).as_posix() for html, _ in candidates]
        raise SamplePreparationError(
            "archive_article_pair_ambiguous",
            f"Archive contains multiple valid article pairs: {names}",
        )
    return candidates[0]


def _manifest_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _validate_cache(
    cache_directory: Path,
    archive: Path,
    archive_sha256: str,
) -> tuple[Path, Path] | None:
    manifest_path = cache_directory / MANIFEST_NAME
    extraction_root = cache_directory / "extracted"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("status") != "complete":
            return None
        if payload.get("strategyVersion") != STRATEGY_VERSION:
            return None
        if (
            payload.get("archiveFileName") != archive.name
            or payload.get("archiveSha256") != archive_sha256
        ):
            return None
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return None
        expected_files: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("kind") not in {"file", "directory"}:
                return None
            relative_text = entry.get("path")
            if not isinstance(relative_text, str):
                return None
            relative = _entry_path(
                ZipInfo(relative_text + ("/" if entry["kind"] == "directory" else ""))
            )
            target = extraction_root.joinpath(*relative.parts)
            if not _is_within(target.resolve(strict=False), extraction_root.resolve()):
                return None
            if target.is_symlink():
                return None
            if entry["kind"] == "directory":
                if not target.is_dir():
                    return None
                continue
            expected_files.add(relative.as_posix())
            if not target.is_file() or target.stat().st_size != entry.get("sizeBytes"):
                return None
            if _sha256_file(target) != entry.get("sha256"):
                return None
        actual_files = {
            path.relative_to(extraction_root).as_posix()
            for path in extraction_root.rglob("*")
            if path.is_file()
        }
        if actual_files != expected_files:
            return None
        html_relative = payload.get("htmlPath")
        resources_relative = payload.get("resourceDirectory")
        if not isinstance(html_relative, str) or not isinstance(resources_relative, str):
            return None
        html_path = extraction_root.joinpath(*PurePosixPath(html_relative).parts)
        resource_directory = extraction_root.joinpath(*PurePosixPath(resources_relative).parts)
        if not html_path.is_file() or not resource_directory.is_dir():
            return None
        if html_path.with_name(f"{html_path.stem}_files") != resource_directory:
            return None
        return html_path, resource_directory
    except (OSError, ValueError, KeyError, SamplePreparationError):
        return None


def _remove_controlled_cache(path: Path, cache_root: Path) -> None:
    resolved_root = cache_root.resolve()
    resolved = path.resolve(strict=False)
    if resolved.parent != resolved_root or not _is_within(resolved, resolved_root):
        raise SamplePreparationError(
            "cache_path_unsafe",
            "Refused to remove a cache outside the controlled sample cache root.",
        )
    if path.exists():
        shutil.rmtree(path)


def prepare_sample(
    archive_path: Path,
    samples_root: Path,
    work_root: Path,
    *,
    limits: ArchiveLimits | None = None,
) -> PreparedSample:
    limits = limits or ArchiveLimits()
    samples_root = samples_root.absolute().resolve()
    archive_path = archive_path.absolute()
    if not archive_path.is_file() or archive_path.is_symlink():
        raise SamplePreparationError(
            "archive_not_readable",
            "Sample archive does not exist, is unreadable, or is a symbolic link.",
        )
    resolved_archive = archive_path.resolve()
    if not _is_within(resolved_archive, samples_root):
        raise SamplePreparationError(
            "archive_outside_samples",
            "Sample archive must be located inside the configured samples root.",
        )
    archive_sha256 = _sha256_file(resolved_archive)
    cache_root = work_root.absolute() / "samples"
    cache_root.mkdir(parents=True, exist_ok=True)
    if cache_root.is_symlink():
        raise SamplePreparationError(
            "cache_root_symlink", "The sample cache root must not be a symbolic link."
        )
    cache_directory = cache_root / _safe_cache_name(resolved_archive, archive_sha256)
    cached_pair = _validate_cache(cache_directory, resolved_archive, archive_sha256)
    if cached_pair:
        html_path, resource_directory = cached_pair
        extraction_root = cache_directory / "extracted"
        return PreparedSample(
            archive_path=resolved_archive,
            archive_sha256=archive_sha256,
            extraction_root=extraction_root,
            html_path=html_path,
            resource_directory=resource_directory,
            cache_hit=True,
            manifest_path=cache_directory / MANIFEST_NAME,
            html_archive_path=html_path.relative_to(extraction_root).as_posix(),
            resource_archive_path=resource_directory.relative_to(extraction_root).as_posix(),
        )

    # Keep the staging prefix deliberately short. Saved-page resource names can
    # already approach Windows' legacy path limit, and the staging directory is
    # an implementation detail rather than part of the cache identity.
    temporary = cache_root / f".tmp-{uuid.uuid4().hex[:8]}"
    extraction_root = temporary / "extracted"
    temporary.mkdir()
    extraction_root.mkdir()
    manifest_entries: list[dict[str, object]] = []
    try:
        try:
            with ZipFile(resolved_archive) as archive:
                checked = _validate_entries(archive.infolist(), limits)
                for info, relative in checked:
                    destination = extraction_root.joinpath(*relative.parts)
                    if not _is_within(destination.resolve(strict=False), extraction_root.resolve()):
                        raise SamplePreparationError(
                            "archive_unsafe_path",
                            f"Archive entry escapes extraction root: {info.filename!r}",
                        )
                    if info.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        manifest_entries.append({"path": relative.as_posix(), "kind": "directory"})
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    written = 0
                    with archive.open(info, "r") as source, destination.open("xb") as target:
                        while chunk := source.read(1024 * 1024):
                            written += len(chunk)
                            if written > info.file_size or written > limits.max_file_bytes:
                                raise SamplePreparationError(
                                    "archive_file_size_limit",
                                    f"Archive entry exceeded its declared or configured size: {info.filename!r}",
                                )
                            target.write(chunk)
                            digest.update(chunk)
                    if written != info.file_size:
                        raise SamplePreparationError(
                            "archive_size_mismatch",
                            f"Archive entry size did not match its declaration: {info.filename!r}",
                        )
                    manifest_entries.append(
                        {
                            "path": relative.as_posix(),
                            "kind": "file",
                            "sizeBytes": written,
                            "sha256": digest.hexdigest(),
                        }
                    )
        except BadZipFile as error:
            raise SamplePreparationError(
                "archive_corrupt", f"ZIP archive is corrupt or failed CRC validation: {error}"
            ) from error

        html_path, resource_directory = _find_article_pair(extraction_root)
        payload: dict[str, object] = {
            "strategyVersion": STRATEGY_VERSION,
            "status": "complete",
            "archiveFileName": resolved_archive.name,
            "archiveSha256": archive_sha256,
            "entryCount": len(manifest_entries),
            "totalExtractedBytes": sum(
                int(entry.get("sizeBytes", 0)) for entry in manifest_entries
            ),
            "htmlPath": html_path.relative_to(extraction_root).as_posix(),
            "resourceDirectory": resource_directory.relative_to(extraction_root).as_posix(),
            "entries": sorted(manifest_entries, key=lambda entry: str(entry["path"])),
        }
        (temporary / MANIFEST_NAME).write_bytes(_manifest_bytes(payload))
        if _validate_cache(temporary, resolved_archive, archive_sha256) is None:
            raise SamplePreparationError(
                "cache_validation_failed",
                "Newly extracted sample cache failed its integrity validation.",
            )
        if cache_directory.exists():
            refreshed = _validate_cache(cache_directory, resolved_archive, archive_sha256)
            if refreshed:
                _remove_controlled_cache(temporary, cache_root)
                html_path, resource_directory = refreshed
                extraction_root = cache_directory / "extracted"
                return PreparedSample(
                    archive_path=resolved_archive,
                    archive_sha256=archive_sha256,
                    extraction_root=extraction_root,
                    html_path=html_path,
                    resource_directory=resource_directory,
                    cache_hit=True,
                    manifest_path=cache_directory / MANIFEST_NAME,
                    html_archive_path=html_path.relative_to(extraction_root).as_posix(),
                    resource_archive_path=resource_directory.relative_to(
                        extraction_root
                    ).as_posix(),
                )
            _remove_controlled_cache(cache_directory, cache_root)
        os.replace(temporary, cache_directory)
        extraction_root = cache_directory / "extracted"
        html_path = extraction_root / str(payload["htmlPath"])
        resource_directory = extraction_root / str(payload["resourceDirectory"])
        return PreparedSample(
            archive_path=resolved_archive,
            archive_sha256=archive_sha256,
            extraction_root=extraction_root,
            html_path=html_path,
            resource_directory=resource_directory,
            cache_hit=False,
            manifest_path=cache_directory / MANIFEST_NAME,
            html_archive_path=str(payload["htmlPath"]),
            resource_archive_path=str(payload["resourceDirectory"]),
        )
    except Exception:
        if temporary.exists():
            _remove_controlled_cache(temporary, cache_root)
        raise
