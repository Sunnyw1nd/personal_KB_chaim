"""Export new multi-format reader contracts; preserve every earlier formal schema."""

import json
from pathlib import Path

from wechat_html_normalizer.models import (
    Document,
    DocumentRecord,
    ProcessingReport,
    ResourceManifest,
)

root = Path(__file__).resolve().parents[1] / "schemas"
for name, model in (
    ("document-v3", Document),
    ("document-record-v1.1", DocumentRecord),
    ("processing-report-v3", ProcessingReport),
    ("resource-manifest-v3", ResourceManifest),
):
    path = root / (name + ".schema.json")
    payload = model.model_json_schema(by_alias=True)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(
                "New contract changed; review version instead of silently replacing schema"
            )
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(name)
