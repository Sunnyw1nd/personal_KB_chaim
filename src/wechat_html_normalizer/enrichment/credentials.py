from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

API_KEY_NAME = "DASHSCOPE_API_KEY"
DEFAULT_ENV_FILE = Path(".env")
_MAX_ENV_FILE_BYTES = 64 * 1024
_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _parse_value(raw_value: str, *, line_number: int) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise ValueError(f"invalid quoted value in env file at line {line_number}")
        return value[1:-1]
    return value


def load_api_key(
    env_file: Path = DEFAULT_ENV_FILE,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the API key without evaluating shell syntax or variable expansion.

    A non-empty process environment value takes precedence. The second return value
    identifies the source without exposing the credential itself.
    """

    active_environment = os.environ if environ is None else environ
    environment_value = active_environment.get(API_KEY_NAME, "").strip()
    if environment_value:
        return environment_value, "environment"

    path = Path(env_file)
    if path.is_symlink():
        raise ValueError(f"refusing symbolic-link env file: {path}")
    if not path.exists():
        return None, None
    if not path.is_file():
        raise ValueError(f"env file is not a regular file: {path}")
    if path.stat().st_size > _MAX_ENV_FILE_BYTES:
        raise ValueError(f"env file exceeds {_MAX_ENV_FILE_BYTES} bytes: {path}")

    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"env file is not valid UTF-8: {path}") from exc

    api_key: str | None = None
    api_key_seen = False
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid env file entry at line {line_number}")
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if not _NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid variable name in env file at line {line_number}")
        if name != API_KEY_NAME:
            continue
        if api_key_seen:
            raise ValueError(f"duplicate {API_KEY_NAME} entry in env file")
        api_key_seen = True
        api_key = _parse_value(raw_value, line_number=line_number).strip() or None

    return api_key, str(path)
