"""Append-only review evidence with a small atomic latest-decision sidecar."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from .dataset import _linked
from .models import QualityReviewDecision, ReviewStatus
from .security import redact_sensitive_object


def read_review(output_root: Path, cache_key: str) -> tuple[ReviewStatus | None, str | None]:
    if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
        return None, "unsafe_review_key"
    path = output_root / "reviews" / cache_key / "decision.json"
    for entry in (output_root / "reviews", path.parent, path):
        if _linked(entry):
            return None, "unsafe_review_path"
    if not path.exists():
        return None, None
    try:
        value = QualityReviewDecision.model_validate_json(path.read_text(encoding="utf-8"))
        if value.cache_key != cache_key:
            raise ValueError("review identity mismatch")
        status = value.review_status
    except (OSError, ValueError, KeyError):
        return None, "invalid_review_decision"
    return status, None


def set_review(output_root: Path, cache_key: str, status: ReviewStatus, reason: str) -> Path:
    from .runner import _atomic_json, utc_now

    if any(_linked(p) for p in (output_root, *output_root.parents)):
        raise ValueError("linked review output is not allowed")
    output_root = output_root.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
        raise ValueError("review requires a full cache key")
    record_path = output_root / "cache" / cache_key / "record.json"
    if (
        any(_linked(p) for p in (record_path, record_path.parent, record_path.parent.parent))
        or not record_path.is_file()
    ):
        raise ValueError("review target must be an existing machine result")
    from .models import EnrichmentRunRecord

    record = EnrichmentRunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    if record.cache_key != cache_key:
        raise ValueError("review/cache identity mismatch")
    if status == ReviewStatus.APPROVED:
        from .models import validate_current_result

        if record.result is None:
            raise ValueError("approval requires a machine result")
        validate_current_result(record.result)
    target = output_root / "reviews" / cache_key
    if any(_linked(p) for p in (output_root / "reviews", target, target / "events")):
        raise ValueError("linked review directories are not allowed")
    value = {
        "schemaVersion": "1.0.0",
        "cacheKey": cache_key,
        "imageSha256": record.image_sha256,
        "reviewStatus": status.value,
        "reason": reason,
        "reviewer": "user",
        "createdAt": utc_now(),
        "eventId": uuid.uuid4().hex,
    }
    safe, _ = redact_sensitive_object(value)
    _atomic_json(target / "events" / f"{value['eventId']}.json", safe)
    _atomic_json(target / "decision.json", safe)
    return target / "decision.json"
