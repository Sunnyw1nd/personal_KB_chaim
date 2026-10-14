"""Persistent single-worker local operations. Presentation never implements model retries."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..enrichment.compact_provider import CompactDashScopeProvider
from ..enrichment.compact_runner import (
    compact_cache_key,
    profile_config,
    rebuild_manifest,
    run_compact_batch,
)
from ..enrichment.credentials import load_api_key
from ..enrichment.dataset import DatasetSummary, _linked
from ..enrichment.models import EnrichmentRunRecord, ReviewStatus
from ..enrichment.providers import DEFAULT_DASHSCOPE_BASE_URL, DEFAULT_MODEL
from ..enrichment.reviews import read_review, set_review
from ..enrichment.runner import _atomic_json, inspect_cache, load_prompt, stable_sha, utc_now
from ..enrichment.security import redact_sensitive_object
from ..enrichment.settings import DEFAULT_PROMPT_PATH, FROZEN_PROMPT_SHA256
from ..models import DOCUMENT_ID_PATTERN, DocumentRecord
from ..normalizer import normalize_saved_article
from ..sample_cache import prepare_sample
from .image_tasks import prepare_revision_images, safe_child
from .local_imports import DuplicateCandidate, extract_markdown_bundle, import_local_file
from .workbench_models import HumanRevision, WorkbenchTask


def read_json(path: Path):
    if path.stat().st_size > 10 * 1024 * 1024 or _linked(path):
        raise ValueError("unsafe or oversized record")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    if any(_linked(p) for p in (path, *path.parents)):
        raise ValueError("linked record path")
    safe, _ = redact_sensitive_object(value)
    _atomic_json(path, safe)


class InstanceLock:
    def __init__(self, path):
        self.stream = path.open("a+b")
        try:
            self.stream.seek(0)
            if not self.stream.read(1):
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if __import__("os").name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise ValueError("another workbench owns this data root") from None

    def close(self):
        self.stream.close()


class Workbench:
    def __init__(
        self,
        project_root: Path,
        *,
        data_root: Path | None = None,
        documents_root: Path | None = None,
        enrichment_root: Path | None = None,
        provider_factory=None,
        key_reader=None,
        actor="user",
        cloud_call_cap=None,
    ):
        if any(_linked(p) for p in (project_root, *project_root.parents)):
            raise ValueError("linked project root")
        self.project = project_root.resolve(strict=True)
        self.data = self._root(data_root or self.project / "output/workbench")
        self.documents = self._root(documents_root or self.project / "output/documents")
        self.enrichment = self._root(enrichment_root or self.project / "output/image-enrichment")
        self.work = self._root(self.project / ".work/wb")
        roots = [self.data, self.documents, self.enrichment, self.work]
        if any(
            a == b or a.is_relative_to(b) or b.is_relative_to(a)
            for i, a in enumerate(roots)
            for b in roots[i + 1 :]
        ):
            raise ValueError("workbench roots must not overlap")
        self.policy = self.project / "config/image-enrichment-policy.json"
        if not self.policy.exists():
            self.policy = None
        self.actor = actor
        if actor not in {"user", "codex_test"}:
            raise ValueError("unknown actor")
        self.lock = threading.RLock()
        self.instance = InstanceLock(safe_child(self.data, "instance.lock", exists=False))
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-workbench")
        self.stopping = False
        self.provider_factory = provider_factory
        self.key_reader = key_reader or (lambda: load_api_key(self.project / ".env")[0])
        self.cloud_call_cap = cloud_call_cap
        self.observed_calls = 0
        self.prompt, self.prompt_sha = load_prompt(
            DEFAULT_PROMPT_PATH.with_name("compact-json-v1.md")
        )
        if self.prompt_sha != FROZEN_PROMPT_SHA256["compact-json-v1"]:
            raise ValueError("frozen compact Prompt changed")
        self.config = profile_config(
            model=DEFAULT_MODEL,
            endpoint=DEFAULT_DASHSCOPE_BASE_URL,
            prompt_sha256=self.prompt_sha,
            max_tokens=4096,
            hard_token_limit=8192,
            task_timeout=180,
        )
        self.observed_calls = sum(
            len(list((self.enrichment / "runs" / run).glob("tasks/*/attempt-*.reservation.json")))
            for task in self.tasks()
            for run in task["runIds"]
        )
        for task in self.tasks():
            if task["state"] in {"queued", "running"}:
                task.update(
                    state="interrupted",
                    error="restart_requires_offline_recovery_or_explicit_new_action",
                )
                self._save_task(task)

    def _root(self, path):
        path = path.absolute()
        if any(_linked(p) for p in (path, *path.parents)):
            raise ValueError("linked data root")
        path = path.resolve()
        if not path.is_relative_to(self.project) or path == self.project:
            raise ValueError("data roots must be controlled project subdirectories")
        if path.relative_to(self.project).parts[0] in {
            "samples",
            "test_pictures",
            "src",
            "config",
            "docs",
            ".git",
        }:
            raise ValueError("data root overlaps protected inputs or source")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def close(self):
        self.stopping = True
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.instance.close()

    def _save_task(self, value):
        with self.lock:
            value["updatedAt"] = utc_now()
            validated = WorkbenchTask.model_validate(value)
            write_json(
                safe_child(self.data, f"tasks/{validated.task_id}.json", exists=False),
                validated.model_dump(mode="json", by_alias=True),
            )

    def tasks(self):
        with self.lock:
            values = [
                WorkbenchTask.model_validate(
                    read_json(safe_child(self.data, f"tasks/{p.name}"))
                ).model_dump(mode="json", by_alias=True)
                for p in sorted((self.data / "tasks").glob("*.json"))
            ]
            return sorted(values, key=lambda task: (task["createdAt"], task["taskId"]))

    def task(self, task_id):
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise ValueError("invalid task ID")
        with self.lock:
            value = read_json(safe_child(self.data, f"tasks/{task_id}.json"))
        # Observe durable progress without writing or making requests.
        if value["operation"] == "enrichment":
            value["progress"] = []
            for run in value["runIds"]:
                directory = safe_child(self.enrichment, f"runs/{run}", exists=False)
                value["progress"].append(
                    {
                        "runId": run,
                        "callsReserved": len(
                            list(directory.glob("tasks/*/attempt-*.reservation.json"))
                        ),
                        "finishedImages": len(list(directory.glob("tasks/*/batch-item.json"))),
                    }
                )
        return value

    def documents_list(self):
        result = []
        for path in sorted(self.documents.glob("*/document-record.json")):
            try:
                checked = safe_child(self.documents, path.relative_to(self.documents).as_posix())
                record = DocumentRecord.model_validate(read_json(checked))
                revision = next(
                    r for r in record.revisions if r.revision_id == record.current_revision_id
                )
                result.append(
                    {
                        "documentId": record.document_id,
                        "title": read_json(
                            safe_child(checked.parent, revision.path + "/document.json")
                        )["metadata"]["title"],
                        "source": record.source_identity.source_type,
                        "currentRevisionId": record.current_revision_id,
                        "completeness": revision.asset_completeness.model_dump(
                            mode="json", by_alias=True
                        ),
                    }
                )
            except (OSError, ValueError):
                result.append(
                    {"error": "unreadable_document_record", "directory": path.parent.name}
                )
        return result

    def revision(self, document_id, revision_id=None):
        if not re.fullmatch(DOCUMENT_ID_PATTERN, document_id):
            raise ValueError("invalid document ID")
        candidates = []
        for path in self.documents.glob("*/document-record.json"):
            checked = safe_child(self.documents, path.relative_to(self.documents).as_posix())
            record = DocumentRecord.model_validate(read_json(checked))
            if record.document_id == document_id:
                candidates.append((checked.parent, record))
        if len(candidates) != 1:
            raise ValueError("document not unique or missing")
        directory, record = candidates[0]
        revision_id = revision_id or record.current_revision_id
        selected = next((r for r in record.revisions if r.revision_id == revision_id), None)
        if not selected:
            raise ValueError("unknown revision")
        return safe_child(directory, selected.path), record

    def preview(self, document_id, revision_id=None, *, consent=False):
        directory, record = self.revision(document_id, revision_id)
        images = prepare_revision_images(directory, policy_path=self.policy, allow_external=consent)
        groups = []
        for group in images.groups:
            item = group["prepared"]
            key = compact_cache_key(item, self.config)
            cached, issue = inspect_cache(self.enrichment / "cache" / key, key)
            override, _ = read_review(self.enrichment, key)
            groups.append(
                {
                    "key": key,
                    "image": item.request.image.model_dump(mode="json", by_alias=True),
                    "context": item.request.context.model_dump(mode="json", by_alias=True),
                    "locations": group["locations"],
                    "localOnly": item.request.policy.local_only,
                    "cache": "available" if cached else issue or "not_processed",
                    "reviewStatus": override.value
                    if override
                    else (
                        cached.review_status.value if cached and cached.review_status else "pending"
                    ),
                }
            )
        return {
            "document": images.document.model_dump(mode="json", by_alias=True),
            "record": record.model_dump(mode="json", by_alias=True),
            "groups": groups,
            "excluded": images.excluded,
            "keyConfigured": bool(self.key_reader()),
            "profile": "compact-json-v1",
        }

    def save_upload(self, name, stream):
        name = name.replace("\\", "/").split("/")[-1]
        if len(name) > 1000:
            raise ValueError("upload name too long")
        extension = Path(name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,12}", extension):
            raise ValueError("unsupported upload type")
        temp = safe_child(self.data, f"uploads/tmp-{uuid.uuid4().hex}.zip", exists=False)
        temp.parent.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256()
        size = 0
        with temp.open("xb") as output:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise ValueError("upload exceeds 50 MiB")
                sha.update(chunk)
                output.write(chunk)
        digest = sha.hexdigest()
        # Keep the ZIP basename short: browser resource names can already be near MAX_PATH.
        target = safe_child(self.data, f"uploads/{digest}/a{extension}", exists=False)
        with self.lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                from ..media import sha256_file

                if sha256_file(target) != digest:
                    raise ValueError("immutable upload integrity mismatch")
                temp.unlink()  # Only this newly generated redundant upload, never original input.
            else:
                temp.rename(target)
            write_json(
                safe_child(
                    self.data, f"upload-events/{digest}-{uuid.uuid4().hex}.json", exists=False
                ),
                {
                    "schemaVersion": "1.0.0",
                    "sha256": digest,
                    "originalName": name,
                    "sizeBytes": size,
                    "createdAt": utc_now(),
                },
            )
            return {"sha256": digest, "originalName": name, "sizeBytes": size}

    def import_files(
        self,
        uploads,
        request_id,
        *,
        target_document_id=None,
        main_markdown=None,
        create_new=False,
        encoding_hint=None,
    ):
        if not uploads or len(uploads) > 30:
            raise ValueError("select 1 to 30 supported files")
        if target_document_id and len(uploads) != 1:
            raise ValueError("update requires exactly one file or Markdown ZIP")
        if target_document_id:
            self.revision(target_document_id)
        # Name is an input-format/display fact, not logical identity. Preserve mixed aliases explicitly.
        selected = []
        for row in uploads:
            if row.get("error"):
                selected.append((row, None))
                continue
            sha, name = row["sha256"], row["originalName"]
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ValueError("invalid upload identity")
            suffix = Path(name).suffix.lower()
            path = safe_child(self.data, f"uploads/{sha}/a{suffix}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("immutable upload integrity mismatch")
            selected.append((row, path))
        scope = {
            "files": uploads,
            "targetDocumentId": target_document_id,
            "mainMarkdown": main_markdown,
            "createNew": create_new,
            "encoding": encoding_hint,
        }

        def worker(task):
            for uploaded, path in selected:
                if path is None:
                    task["items"].append(uploaded)
                    self._save_task(task)
                    continue
                row = {"inputSha256": uploaded["sha256"], "originalName": uploaded["originalName"]}
                try:
                    if path.suffix == ".zip" and not main_markdown:
                        if target_document_id:
                            raise ValueError(
                                "HTML identity is source-derived; local updates require TXT/MD/image"
                            )
                        sample = prepare_sample(path, self.data / "uploads", self.work)
                        result = normalize_saved_article(
                            sample.html_path,
                            sample.resource_directory,
                            self.documents,
                            source_sample=sample,
                        )
                        decision = result.import_decision
                    else:
                        if path.suffix == ".zip":
                            root = extract_markdown_bundle(path, self.work)
                            main = main_markdown
                            if not main or Path(main).suffix.lower() not in {".md", ".markdown"}:
                                raise ValueError("explicit_main_markdown_required")
                            name = Path(main).name
                        else:
                            root, main, name = path.parent, path.name, uploaded["originalName"]
                        result = import_local_file(
                            root,
                            main,
                            self.documents,
                            original_name=name,
                            target_document_id=target_document_id,
                            create_new=create_new,
                            package_sha256=uploaded["sha256"] if path.suffix == ".zip" else None,
                            encoding_hint=encoding_hint,
                        )
                        decision = result.decision
                    row.update(
                        title=result.document.metadata.title,
                        sourceType=result.document.metadata.source_type,
                        status=result.document.processing.status,
                        documentId=result.document.document_id,
                        revisionId=result.document.revision.revision_id,
                        currentRevisionId=result.current_revision_id,
                        decision=decision,
                        completeness=result.document.asset_completeness.model_dump(
                            mode="json", by_alias=True
                        ),
                        warningCount=len(result.document.processing.messages),
                    )
                except DuplicateCandidate as error:
                    row.update(decision="duplicate_candidate", candidates=error.candidates)
                except (OSError, ValueError, RuntimeError) as error:
                    row.update(
                        error=getattr(
                            error,
                            "code",
                            str(error) if isinstance(error, ValueError) else type(error).__name__,
                        )
                    )
                task["items"].append(row)
                self._save_task(task)

        return self._submit("import", scope, request_id, 0, worker, new_run=True)

    def _submit(self, operation, scope, request_id, budget, worker, *, new_run=False):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
            raise ValueError("explicit idempotency request ID required")
        scope_sha = stable_sha({"operation": operation, "scope": scope, "config": self.config})
        with self.lock:
            alias = safe_child(self.data, f"requests/{stable_sha(request_id)}.json", exists=False)
            if alias.exists():
                known = read_json(alias)
                if known["scopeFingerprint"] != scope_sha:
                    raise ValueError("request ID reused for a different scope")
                return {**self.task(known["taskId"]), "reused": True}

            def register(task):
                write_json(alias, {"scopeFingerprint": scope_sha, "taskId": task["taskId"]})
                path = safe_child(
                    self.data, f"bindings/{scope_sha}/{task['taskId']}.json", exists=False
                )
                if not path.exists():
                    write_json(
                        path,
                        {
                            "schemaVersion": "1.0.0",
                            "scope": scope,
                            "taskId": task["taskId"],
                            "createdAt": utc_now(),
                        },
                    )
                return task

            tasks = self.tasks()
            for task in tasks:
                if task["requestId"] == request_id:
                    if task["scopeFingerprint"] != scope_sha:
                        raise ValueError("request ID reused for a different scope")
                    return {**register(task), "reused": True}
            for task in reversed(tasks):
                same = task["scopeFingerprint"] == scope_sha
                busy_overlap = (
                    operation == task["operation"] == "enrichment"
                    and task["state"] in {"queued", "running"}
                    and bool(set(scope["keys"]) & set(task["scope"].get("keys", [])))
                )
                if busy_overlap or (
                    same and (not new_run or task["state"] in {"queued", "running"})
                ):
                    return {**register(task), "reused": True}
            task = WorkbenchTask(
                taskId=uuid.uuid4().hex,
                operation=operation,
                scopeFingerprint=scope_sha,
                requestId=request_id,
                state="queued",
                createdAt=utc_now(),
                updatedAt=utc_now(),
                scope=scope,
                callBudget=budget,
            ).model_dump(mode="json", by_alias=True)
            self._save_task(task)  # Durable before queue dispatch.
            register(task)
            self.executor.submit(self._execute, task, worker)
            return task.copy()

    def _execute(self, task, worker):
        try:
            task["state"] = "running"
            self._save_task(task)
            worker(task)
            if task["state"] == "running":
                task["state"] = (
                    "completed_with_errors"
                    if any(i.get("error") for i in task["items"])
                    else "completed"
                )
        except Exception as exc:  # noqa: BLE001 -- persist unexpected worker failures, never a traceback/secret
            task.update(state="blocked", error=f"operation_failed:{type(exc).__name__}")
        self._save_task(task)

    def import_archives(self, uploads, request_id):
        if not uploads or len(uploads) > 30:
            raise ValueError("select 1 to 30 ZIP files")
        ids = sorted({row["sha256"] for row in uploads})
        for sha in ids:
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ValueError("invalid upload identity")

        def worker(task):
            for sha in ids:
                try:
                    sample = prepare_sample(
                        safe_child(self.data, f"uploads/{sha}/a.zip"),
                        self.data / "uploads",
                        self.work,
                    )
                    result = normalize_saved_article(
                        sample.html_path,
                        sample.resource_directory,
                        self.documents,
                        source_sample=sample,
                    )
                    row = {
                        "archiveSha256": sha,
                        "originalName": next(
                            r["originalName"] for r in uploads if r["sha256"] == sha
                        ),
                        "title": result.document.metadata.title,
                        "sourceType": result.document.metadata.source_type,
                        "status": result.document.processing.status,
                        "warningCount": sum(
                            m.level.value in {"warning", "error"}
                            for m in result.document.processing.messages
                        ),
                        "documentId": result.document.document_id,
                        "revisionId": result.document.revision.revision_id,
                        "currentRevisionId": result.current_revision_id,
                        "decision": result.import_decision,
                        "completeness": result.document.asset_completeness.model_dump(
                            mode="json", by_alias=True
                        ),
                    }
                except (OSError, ValueError, RuntimeError) as exc:
                    row = {"archiveSha256": sha, "error": getattr(exc, "code", type(exc).__name__)}
                task["items"].append(row)
                self._save_task(task)

        return self._submit("import", {"archives": ids}, request_id, 0, worker, new_run=True)

    def enrich(self, document_id, revision_id, keys, *, consent, request_id, new_run=False):
        if consent is not True:
            raise ValueError("explicit external selection consent required")
        directory, _ = self.revision(document_id, revision_id)
        groups = prepare_revision_images(
            directory, policy_path=self.policy, allow_external=True
        ).groups
        selected = [g for g in groups if compact_cache_key(g["prepared"], self.config) in keys]
        if (
            not isinstance(keys, list)
            or not 1 <= len(set(keys)) <= 12
            or len(selected) != len(set(keys))
        ):
            raise ValueError("select 1 to 12 known image/context groups")
        if any(g["prepared"].request.policy.local_only for g in selected):
            raise ValueError("local-only images cannot be externally selected")
        scope = {
            "documentId": document_id,
            "revisionId": revision_id,
            "keys": sorted(set(keys)),
            "consent": True,
            "locations": [g["locations"] for g in selected],
        }

        def worker(task):
            # Each unique image/context group gets its own normal run; no duplicate-SHA task collision.
            for index, group in enumerate(selected):
                if self.stopping:
                    task.update(state="interrupted", error="stopped_between_images")
                    break
                item = group["prepared"]
                key = compact_cache_key(item, self.config)
                cached, issue = inspect_cache(self.enrichment / "cache" / key, key)
                provider = None
                if not cached and issue is None:
                    if (
                        self.cloud_call_cap is not None
                        and self.observed_calls + 3 > self.cloud_call_cap
                    ):
                        task.update(state="blocked", error="local_validation_call_cap_exhausted")
                        break
                    secret = self.key_reader()
                    if not secret:
                        task.update(state="blocked", error="api_key_missing")
                        break
                    provider = (
                        self.provider_factory()
                        if self.provider_factory
                        else CompactDashScopeProvider(secret, base_url=DEFAULT_DASHSCOPE_BASE_URL)
                    )
                run = f"wb-{task['taskId'][:12]}-{index:02d}"
                task["runIds"].append(run)
                self._save_task(task)
                summary = DatasetSummary(
                    len(group["locations"]), 1, 1, int(item.request.context.available), 1
                )
                path, manifest = run_compact_batch(
                    [item],
                    summary,
                    inventory_sha256=stable_sha(scope),
                    output_root=self.enrichment,
                    prompt=self.prompt,
                    prompt_sha256=self.prompt_sha,
                    provider=provider,
                    model=DEFAULT_MODEL,
                    endpoint=DEFAULT_DASHSCOPE_BASE_URL,
                    region="beijing",
                    run_name=run,
                    policy_path=self.policy,
                    execution_call_limit=3,
                    requests_per_minute=12,
                )
                self.observed_calls += manifest.metrics.api_calls
                task["items"].append(
                    {
                        "key": key,
                        "runId": run,
                        "manifest": path.relative_to(self.enrichment).as_posix(),
                        "status": manifest.items[0].status.value,
                        "error": manifest.items[0].error_code,
                        "metrics": manifest.metrics.model_dump(mode="json", by_alias=True),
                        "usage": manifest.usage.model_dump(mode="json", by_alias=True)
                        if manifest.usage
                        else None,
                    }
                )
                self._save_task(task)
                if manifest.status == "blocked":
                    task.update(state="blocked", error="provider_configuration_blocked")
                    break

        return self._submit(
            "enrichment", scope, request_id, 3 * len(selected), worker, new_run=new_run
        )

    def recover(self, task_id):
        with self.lock:
            task = self.task(task_id)
            task.pop("progress", None)
            if task["state"] in {"queued", "running"}:
                raise ValueError("cannot recover a running task")
            if task["operation"] != "enrichment" or not task["runIds"]:
                raise ValueError("no stored model run to recover")
            for run in task["runIds"]:
                directory = safe_child(self.enrichment, f"runs/{run}")
                manifest = rebuild_manifest(directory, self.enrichment)
                existing = next((x for x in task["items"] if x.get("runId") == run), None)
                row = {
                    "runId": run,
                    "status": manifest.status,
                    "metrics": manifest.metrics.model_dump(mode="json", by_alias=True),
                    "recoveredWithoutCalls": True,
                }
                if existing is None:
                    task["items"].append(row)
            task["state"] = "recovered"
            self._save_task(task)
            return task

    def _binding(self, document_id, revision_id, key):
        preview = self.preview(document_id, revision_id)
        group = next((g for g in preview["groups"] if g["key"] == key), None)
        if group is None:
            raise ValueError("result does not belong to selected document/context")
        return {
            "documentId": document_id,
            "revisionId": revision_id,
            "cacheKey": key,
            "locations": group["locations"],
        }

    def result(self, document_id, revision_id, key):
        binding = self._binding(document_id, revision_id, key)
        cached, issue = inspect_cache(self.enrichment / "cache" / key, key)
        record_path = self.enrichment / "cache" / key / "record.json"
        if not record_path.is_file():
            for task in reversed(self.tasks()):
                if task["state"] in {"queued", "running"}:
                    continue
                for run in reversed(task["runIds"]):
                    manifest_path = safe_child(
                        self.enrichment, f"runs/{run}/run-manifest.json", exists=False
                    )
                    if not manifest_path.is_file():
                        continue
                    manifest = read_json(manifest_path)
                    item = next(
                        (
                            i
                            for i in manifest.get("items", [])
                            if i["cacheKey"] == key and i.get("recordPath")
                        ),
                        None,
                    )
                    if item:
                        record_path = safe_child(self.enrichment, item["recordPath"])
                        break
                if record_path.is_file():
                    break
        if not record_path.is_file():
            return {
                "binding": binding,
                "record": None,
                "issue": issue,
                "attempts": [],
                "human": self.human(binding),
            }
        record_path = safe_child(
            self.enrichment, record_path.relative_to(self.enrichment).as_posix()
        )
        record = cached or EnrichmentRunRecord.model_validate(read_json(record_path))
        override, _ = read_review(self.enrichment, key)
        if override is not None:
            record = record.model_copy(update={"review_status": override})
        decision_path = safe_child(self.enrichment, f"reviews/{key}/decision.json", exists=False)
        decision = read_json(decision_path) if decision_path.exists() else None
        attempts = []
        for attempt in record.attempt_records or []:
            raw = None
            if attempt.raw_response_path:
                try:
                    raw = read_json(safe_child(record_path.parent, attempt.raw_response_path))
                except (OSError, ValueError):
                    raw = {"note": "raw_response_unavailable"}
            attempts.append({"attempt": attempt.model_dump(mode="json", by_alias=True), "raw": raw})
        return {
            "binding": binding,
            "record": record.model_dump(mode="json", by_alias=True),
            "issue": issue,
            "machineReviewDecision": decision,
            "attempts": attempts,
            "human": self.human(binding),
        }

    def review(self, document_id, revision_id, key, status, reason):
        self._binding(document_id, revision_id, key)
        if self.actor == "codex_test":
            if status == "approved":
                raise ValueError("verification mode cannot register user approval")
            reason = "[Codex功能验证，非用户认可] " + reason
        with self.lock:
            set_review(self.enrichment, key, ReviewStatus(status), reason)
        return self.result(document_id, revision_id, key)

    def human(self, binding):
        folder = safe_child(self.data, f"human/{stable_sha(binding)}", exists=False)
        versions = (
            [
                read_json(safe_child(folder, f"revisions/{p.name}"))
                for p in sorted((folder / "revisions").glob("*.json"))
            ]
            if folder.exists()
            else []
        )
        adopted = (
            read_json(safe_child(folder, "adoption.json"))
            if (folder / "adoption.json").exists()
            else None
        )
        return {"versions": sorted(versions, key=lambda v: v["createdAt"]), "adopted": adopted}

    def save_human(self, document_id, revision_id, key, description, key_facts, code_text):
        with self.lock:
            detail = self.result(document_id, revision_id, key)
            if not detail["record"]:
                raise ValueError("human edits require existing machine evidence")
            binding = detail["binding"]
            versions = detail["human"]["versions"]
            version = HumanRevision(
                revisionId=uuid.uuid4().hex,
                parentRevisionId=versions[-1]["revisionId"] if versions else None,
                binding=binding,
                originalResultSha256=stable_sha(detail["record"].get("result")),
                description=description,
                keyFacts=key_facts,
                codeText=code_text,
                createdAt=utc_now(),
                actor=self.actor,
            )
            _, redactions = redact_sensitive_object(version.model_dump(mode="json", by_alias=True))
            if redactions:
                raise ValueError(
                    "credential_like_human_content_rejected; remove secrets before saving"
                )
            path = safe_child(
                self.data,
                f"human/{stable_sha(binding)}/revisions/{version.revision_id}.json",
                exists=False,
            )
            write_json(path, version.model_dump(mode="json", by_alias=True))
            return self.human(binding)

    def adopt_human(self, document_id, revision_id, key, human_revision_id):
        with self.lock:
            binding = self._binding(document_id, revision_id, key)
            versions = self.human(binding)["versions"]
            version = next((v for v in versions if v["revisionId"] == human_revision_id), None)
            if version is None:
                raise ValueError("unknown saved human revision; unsaved text cannot be adopted")
            event = {
                "schemaVersion": "1.0.0",
                "eventId": uuid.uuid4().hex,
                "revisionId": human_revision_id,
                "binding": binding,
                "actor": self.actor,
                "createdAt": utc_now(),
            }
            folder = f"human/{stable_sha(binding)}"
            write_json(
                safe_child(self.data, f"{folder}/events/{event['eventId']}.json", exists=False),
                event,
            )
            write_json(safe_child(self.data, f"{folder}/adoption.json", exists=False), event)
            return self.human(binding)
