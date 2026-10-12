from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MEDIA_BY_PIL_FORMAT = {
    "BMP": ("image/bmp", "bmp"),
    "GIF": ("image/gif", "gif"),
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "TIFF": ("image/tiff", "tiff"),
    "WEBP": ("image/webp", "webp"),
    "AVIF": ("image/avif", "avif"),
}


@dataclass(frozen=True)
class MediaInfo:
    media_type: str | None
    extension: str | None
    width: int | None
    height: int | None
    decodable_image: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_media(path: Path) -> MediaInfo:
    with path.open("rb") as stream:
        prefix = stream.read(512)

    # SVG is text and may contain scripts/external references. Detect it for inventory only.
    stripped = prefix.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    if stripped.startswith(b"<svg") or (stripped.startswith(b"<?xml") and b"<svg" in stripped):
        return MediaInfo("image/svg+xml", "svg", None, None, False)

    try:
        with Image.open(path) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
            image.verify()
        if image_format in MEDIA_BY_PIL_FORMAT:
            media_type, extension = MEDIA_BY_PIL_FORMAT[image_format]
            return MediaInfo(media_type, extension, width, height, True)
    except (UnidentifiedImageError, OSError, ValueError):
        pass

    if prefix.startswith(b"%PDF-"):
        return MediaInfo("application/pdf", "pdf", None, None, False)
    if stripped.startswith((b"<!doctype html", b"<html")):
        return MediaInfo("text/html", "html", None, None, False)
    return MediaInfo(None, None, None, None, False)


def declared_media_type(data_type: str | None, type_attribute: str | None) -> str | None:
    value = (data_type or type_attribute or "").strip().lower()
    if not value:
        return None
    if "/" in value:
        return value.split(";", 1)[0].strip()
    aliases = {"jpg": "jpeg", "svg": "svg+xml"}
    return f"image/{aliases.get(value, value)}"
