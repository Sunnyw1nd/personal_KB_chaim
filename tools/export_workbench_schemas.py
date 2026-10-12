"""Export only the new workbench contracts; never rewrite existing schemas."""

import json
from pathlib import Path

from wechat_html_normalizer.application.workbench_models import HumanRevision, WorkbenchTask

root = Path(__file__).resolve().parents[1] / "schemas"
for name, model in (
    ("workbench-task.schema.json", WorkbenchTask),
    ("workbench-human-revision.schema.json", HumanRevision),
):
    path = root / name
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != model.model_json_schema(by_alias=True):
            raise ValueError(
                "versioned workbench schema changed; do not overwrite without migration"
            )
        continue
    path.write_text(
        json.dumps(model.model_json_schema(by_alias=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(name)
